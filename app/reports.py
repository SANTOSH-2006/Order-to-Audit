"""Report builders. Each returns (title, headers, rows); rows hold Decimals/ints/strings so the
same data feeds HTML, CSV, Excel and PDF without re-formatting."""
import datetime as dt
from decimal import Decimal
from . import services as S
from .services import D, rs, q2, dq

def _inv_rows(conn, d1, d2, status=None):
    sql = ("SELECT i.*, c.name cname, c.gstin cgstin, c.state FROM invoices i JOIN customers c ON c.id=i.customer_id "
           "WHERE i.date BETWEEN ? AND ?" + (" AND i.status=?" if status else "") + " ORDER BY i.date, i.id")
    return conn.execute(sql, (d1, d2) + ((status,) if status else ())).fetchall()

def sales_register(conn, d1, d2):
    rows = [[r["date"], r["number"], r["cname"], r["cgstin"] or "", r["state"], rs(r["taxable"]), rs(r["cgst"]), rs(r["sgst"]),
             rs(r["igst"]), rs(r["total"]), r["status"]] for r in _inv_rows(conn, d1, d2)]
    live = [r for r in rows if r[10] == "posted"]
    rows.append(["", "TOTAL (posted)", "", "", ""] + [sum((r[i] for r in live), Decimal(0)) for i in range(5, 10)] + [""])
    return ("Sales register", ["Date", "Invoice", "Customer", "GSTIN", "Place of supply", "Taxable", "CGST", "SGST", "IGST", "Total", "Status"], rows)

def customer_sales(conn, d1, d2):
    rows = [[r["name"], r["n"], rs(r["taxable"]), rs(r["tax"]), rs(r["total"])] for r in conn.execute(
        "SELECT c.name, COUNT(*) n, SUM(i.taxable) taxable, SUM(i.cgst+i.sgst+i.igst) tax, SUM(i.total) total FROM invoices i "
        "JOIN customers c ON c.id=i.customer_id WHERE i.status='posted' AND i.date BETWEEN ? AND ? GROUP BY c.id ORDER BY total DESC", (d1, d2))]
    return ("Customer-wise sales", ["Customer", "Invoices", "Taxable", "Tax", "Total"], rows)

def product_sales(conn, d1, d2):
    rows = [[r["name"], r["hsn"], r["qty"], rs(r["taxable"])] for r in conn.execute(
        "SELECT it.name, l.hsn, SUM(l.qty_base) qty, SUM(l.taxable) taxable FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id "
        "JOIN items it ON it.id=l.item_id WHERE i.status='posted' AND i.date BETWEEN ? AND ? GROUP BY l.item_id ORDER BY taxable DESC", (d1, d2))]
    return ("Product-wise sales", ["Product", "HSN", "Qty (packets)", "Taxable"], rows)

def gst_summary(conn, d1, d2):
    """Outward supplies, shaped for GSTR-1 (B2B/B2C by HSN & rate) and 3B table 3.1(a) totals.
    Invoices (incl. later-cancelled) are reported; credit notes are shown as negatives, so Net = live sales."""
    rows = []
    for r in conn.execute(
        "SELECT CASE WHEN c.gstin IS NULL OR c.gstin='' THEN 'B2C' ELSE 'B2B' END sect, i.place_code pos, i.intra, l.hsn, l.gst_rate, "
        "SUM(l.qty_base) qty, SUM(l.taxable) t, SUM(l.cgst) c, SUM(l.sgst) s, SUM(l.igst) g FROM invoice_lines l "
        "JOIN invoices i ON i.id=l.invoice_id JOIN customers c ON c.id=i.customer_id WHERE i.date BETWEEN ? AND ? "
        "GROUP BY sect, pos, l.hsn, l.gst_rate ORDER BY sect, pos", (d1, d2)):
        rows.append([r["sect"] + " invoices", r["pos"], "Intra" if r["intra"] else "Inter", r["hsn"], r["gst_rate"], r["qty"],
                     rs(r["t"]), rs(r["c"]), rs(r["s"]), rs(r["g"])])
    for r in conn.execute(
        "SELECT i.place_code pos, i.intra, SUM(n.taxable) t, SUM(n.cgst) c, SUM(n.sgst) s, SUM(n.igst) g FROM credit_notes n "
        "JOIN invoices i ON i.id=n.invoice_id WHERE n.date BETWEEN ? AND ? GROUP BY pos, i.intra", (d1, d2)):
        rows.append(["Credit notes (9B)", r["pos"], "Intra" if r["intra"] else "Inter", "", "", "", -rs(r["t"]), -rs(r["c"]), -rs(r["s"]), -rs(r["g"])])
    rows.append(["NET OUTWARD (3B 3.1a)", "", "", "", "", "", *[sum((x[i] for x in rows), Decimal(0)) for i in range(6, 10)]])
    return ("GST summary - outward supplies", ["Section", "Place of supply", "Type", "HSN", "Rate %", "Qty", "Taxable", "CGST", "SGST", "IGST"], rows)

def purchase_register(conn, d1, d2):
    rows = []
    for r in conn.execute("SELECT p.date, p.bill_no, s.name sname, i.name, i.disp_unit, i.disp_factor, l.qty_base, l.value FROM purchase_lines l "
                          "JOIN purchases p ON p.id=l.purchase_id LEFT JOIN suppliers s ON s.id=p.supplier_id JOIN items i ON i.id=l.item_id "
                          "WHERE p.date BETWEEN ? AND ? ORDER BY p.date, p.id", (d1, d2)):
        q = dq(r, r["qty_base"])
        rows.append([r["date"], r["bill_no"], r["sname"] or "", r["name"], q, r["disp_unit"], q2(D(r["value"]) / q), q2(r["value"])])
    rows.append(["", "TOTAL", "", "", "", "", "", sum((x[7] for x in rows), Decimal(0))])
    return ("Purchase register", ["Date", "Bill", "Supplier", "Item", "Qty", "Unit", "Rate", "Value"], rows)

def stock_summary(conn, d1, d2):
    rows = []
    for it in conn.execute("SELECT * FROM items ORDER BY CASE kind WHEN 'raw' THEN 1 WHEN 'pack' THEN 2 ELSE 3 END, name"):
        g = lambda sql, *a: conn.execute(sql, (it["id"],) + a).fetchone()[0]
        op = g("SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE item_id=? AND date<?", d1)
        rec = g("SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE item_id=? AND date BETWEEN ? AND ? AND qty>0", d1, d2)
        iss = g("SELECT COALESCE(SUM(-qty),0) FROM stock_ledger WHERE item_id=? AND date BETWEEN ? AND ? AND qty<0", d1, d2)
        val = sum((D(r[0]) for r in conn.execute("SELECT value FROM stock_ledger WHERE item_id=? AND date<=?", (it["id"], d2))), Decimal(0))
        rows.append([{"raw": "Raw", "pack": "Packaging", "fg": "Finished"}[it["kind"]], it["name"], it["disp_unit"],
                     dq(it, op), dq(it, rec), dq(it, iss), dq(it, op + rec - iss), q2(val)])
    return ("Stock summary (opening + receipts - issues = closing)", ["Type", "Item", "Unit", "Opening", "Receipts", "Issues/Sales", "Closing", "Value"], rows)

DOC_LABEL = {"opening": "Opening balance", "purchase": "Purchase", "production_issue": "Production issue", "production_output": "Production output",
             "invoice": "Sales dispatch", "credit_note": "Credit note return", "adjustment": "Adjustment"}

def stock_ledger(conn, d1, d2):
    rows = []
    for it in conn.execute("SELECT * FROM items ORDER BY CASE kind WHEN 'raw' THEN 1 WHEN 'pack' THEN 2 ELSE 3 END, name"):
        bal = conn.execute("SELECT COALESCE(SUM(qty),0) FROM stock_ledger WHERE item_id=? AND date<?", (it["id"], d1)).fetchone()[0]
        rows.append([d1, it["name"], "Brought forward", "", None, None, dq(it, bal), it["disp_unit"]])
        for r in conn.execute("SELECT * FROM stock_ledger WHERE item_id=? AND date BETWEEN ? AND ? ORDER BY id", (it["id"], d1, d2)):
            bal += r["qty"]
            rows.append([r["date"], it["name"], f"{DOC_LABEL.get(r['doc_type'], r['doc_type'])} #{r['doc_id']}", r["note"] or "",
                         dq(it, r["qty"]) if r["qty"] > 0 else None, dq(it, -r["qty"]) if r["qty"] < 0 else None, dq(it, bal), it["disp_unit"]])
    return ("Stock ledger", ["Date", "Item", "Source document", "Note", "In", "Out", "Balance", "Unit"], rows)

def production_report(conn, d1, d2):
    rows = []
    for r in conn.execute("SELECT id FROM runs WHERE date BETWEEN ? AND ? ORDER BY id", (d1, d2)):
        d = S.run_detail(conn, r["id"]); run = d["run"]
        for l in d["lines"]:
            rows.append([run["batch_no"], run["product"], l["item"], l["unit"], l["planned"], l["actual"], l["var"], "FLAG" if l["flag"] else ""])
        if d["yield"]:
            y = d["yield"]
            rows.append([run["batch_no"], run["product"], "YIELD (packets)", "packet", y["expected"], y["actual"], y["var"], "FLAG" if y["flag"] else ""])
    return (f"Production: planned vs actual (tolerance {S.tolerance(conn)}%)", ["Batch", "Product", "Item", "Unit", "Planned", "Actual", "Variance %", "Flag"], rows)

def cost_report(conn, d1, d2):
    rows = []
    for r in conn.execute("SELECT id FROM runs WHERE date BETWEEN ? AND ? AND status='completed' ORDER BY id", (d1, d2)):
        d = S.run_detail(conn, r["id"]); c = d["cost"]
        rows.append([d["run"]["batch_no"], d["run"]["product"], d["run"]["good_output"], c["material"], c["overhead"], c["total"],
                     c["per_packet"], c["price"], c["margin"], c["margin_pct"]])
    return ("Cost per packet by run", ["Batch", "Product", "Good packets", "Material", "Overheads", "Total cost", "Cost/packet", "Selling price", "Margin/packet", "Margin %"], rows)

def collections(conn, d1, d2):
    rows = [[r["date"], r["name"], r["number"] or "On account", r["mode"], r["ref"] or "", rs(r["amount"])] for r in conn.execute(
        "SELECT p.*, c.name, i.number FROM payments p JOIN customers c ON c.id=p.customer_id LEFT JOIN invoices i ON i.id=p.invoice_id "
        "WHERE p.date BETWEEN ? AND ? ORDER BY p.date, p.id", (d1, d2))]
    rows.append(["", "TOTAL", "", "", "", sum((r[5] for r in rows), Decimal(0))])
    return ("Payments and collections", ["Date", "Customer", "Against", "Mode", "Reference", "Amount"], rows)

def outstanding(conn, d1, d2):
    """Receivables with ageing as of d2. Payments received on account are applied FIFO to the oldest invoices."""
    as_of = dt.date.fromisoformat(d2); rows = []
    for c in conn.execute("SELECT * FROM customers ORDER BY name"):
        pool = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE customer_id=? AND invoice_id IS NULL", (c["id"],)).fetchone()[0]
        b = [0, 0, 0, 0]
        for inv, due in S.open_invoices(conn, c["id"]):
            if inv["date"] > d2: continue
            use = min(pool, due); pool -= use; due -= use
            age = (as_of - dt.date.fromisoformat(inv["date"])).days
            b[0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3] += due
        if sum(b) or pool: rows.append([c["name"], *[rs(x) for x in b], rs(sum(b))])
    rows.append(["TOTAL", *[sum((r[i] for r in rows), Decimal(0)) for i in range(1, 6)]])
    return (f"Outstanding receivables with ageing (as of {d2})", ["Customer", "0-30 days", "31-60", "61-90", "90+", "Outstanding"], rows)

REPORTS = {"sales_register": sales_register, "customer_sales": customer_sales, "product_sales": product_sales, "gst_summary": gst_summary,
           "purchase_register": purchase_register, "stock_summary": stock_summary, "stock_ledger": stock_ledger,
           "production_report": production_report, "cost_report": cost_report, "collections": collections, "outstanding": outstanding}

def dashboard(conn):
    t = S.today()
    out = {"orders_today": conn.execute("SELECT COUNT(*) FROM orders WHERE substr(created,1,10)=?", (t,)).fetchone()[0],
           "pending": conn.execute("SELECT COUNT(*) FROM orders WHERE status='submitted'").fetchone()[0],
           "outstanding": rs(sum(S.customer_outstanding(conn, c["id"]) for c in conn.execute("SELECT id FROM customers"))),
           "stock": S.stock_rows(conn), "alerts": [], "costs": []}
    for r in conn.execute("SELECT id FROM runs WHERE status='completed' ORDER BY id DESC LIMIT 10"):
        d = S.run_detail(conn, r["id"])
        bad = [f"{l['item']} {l['var']:+}%" for l in d["lines"] if l["flag"]] + ([f"Yield {d['yield']['var']:+}%"] if d["yield"]["flag"] else [])
        if bad: out["alerts"].append((d["run"]["batch_no"], ", ".join(bad)))
    seen = set()
    for r in conn.execute("SELECT id, product_id FROM runs WHERE status='completed' ORDER BY id DESC"):
        if r["product_id"] in seen: continue
        seen.add(r["product_id"]); d = S.run_detail(conn, r["id"]); out["costs"].append((d["run"]["product"], d["cost"]))
    return out
