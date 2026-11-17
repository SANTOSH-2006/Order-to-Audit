"""Business logic. Functions take an open sqlite connection and DO NOT commit; the caller
(web layer / tests) commits on success or rolls back on error, so every document and the
stock movements it causes are saved atomically."""
import datetime as dt, json, uuid
from decimal import Decimal, ROUND_HALF_UP

SELLER = {"name": "Sri Murugan Snacks (Demo)", "gstin": "33AAAAA0000A1Z5", "state": "Tamil Nadu",
          "state_code": "33", "address": "Industrial Estate, Tiruchirappalli - 620 001"}

def D(x): return x if isinstance(x, Decimal) else Decimal(str(x))
def today(): return dt.date.today().isoformat()
def q4(x): return D(x).quantize(Decimal("0.0001"), ROUND_HALF_UP)
def q2(x): return D(x).quantize(Decimal("0.01"), ROUND_HALF_UP)
def rs(paise): return q2(D(paise) / 100)

class AppError(Exception): pass
class StockError(AppError): pass

# ---------------------------------------------------------------- units
UNIT_FACTORS = {"kg": ("g", 1000), "g": ("g", 1), "l": ("ml", 1000), "ml": ("ml", 1),
                "pc": ("pc", 1), "packet": ("pc", 1)}

def to_base(item, qty, unit):
    """Convert qty in `unit` to the item's integer base unit (g, ml, pc). Never floats."""
    if unit == "carton":
        if not item["carton_size"]: raise AppError(f"{item['name']} has no carton size")
        base, f = "pc", item["carton_size"]
    elif unit in UNIT_FACTORS:
        base, f = UNIT_FACTORS[unit]
    else:
        raise AppError(f"Unknown unit {unit}")
    if base != item["base_unit"]: raise AppError(f"Unit '{unit}' is not valid for {item['name']}")
    v = D(qty) * f
    if v != v.to_integral_value(): raise AppError("Quantity is smaller than the smallest unit")
    return int(v)

def dq(item, base_qty):  # base -> display Decimal
    return (D(base_qty) / item["disp_factor"]).normalize() if base_qty else Decimal(0)

def fq(item, base_qty):  # base -> display string
    return format(dq(item, base_qty), "f")

def get_item(conn, item_id):
    it = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not it: raise AppError("Item not found")
    return it

def setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def audit(conn, user, action, entity, entity_id, detail=""):
    conn.execute("INSERT INTO audit_log(ts,user,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?)",
                 (dt.datetime.now().isoformat(timespec="seconds"), user, action, entity, entity_id,
                  detail if isinstance(detail, str) else json.dumps(detail)))

def fy_of(date_iso):
    d = dt.date.fromisoformat(date_iso)
    s = d.year if d.month >= 4 else d.year - 1
    return f"{s}-{(s + 1) % 100:02d}"

def next_seq(conn, kind, fy):
    r = conn.execute("SELECT last FROM seq WHERE kind=? AND fy=?", (kind, fy)).fetchone()
    n = (r["last"] if r else 0) + 1
    conn.execute("INSERT OR REPLACE INTO seq(kind,fy,last) VALUES(?,?,?)", (kind, fy, n))
    return n

# ---------------------------------------------------------------- ledger (single source of truth for stock)
def balance(conn, item_id):
    rows = conn.execute("SELECT qty,value FROM stock_ledger WHERE item_id=?", (item_id,)).fetchall()
    return sum(r["qty"] for r in rows), sum((D(r["value"]) for r in rows), Decimal(0))

def avg_cost(conn, item_id):
    q, v = balance(conn, item_id)
    return (v / q) if q > 0 else Decimal(0)  # cost per BASE unit

def post(conn, item_id, qty, doc_type, doc_id, user, note="", value=None, date=None):
    """Append one stock movement. Issues are valued at moving weighted-average cost unless
    `value` is given (e.g. returns reversing an earlier issue). Negative stock is refused."""
    item = get_item(conn, item_id)
    q, v = balance(conn, item_id)
    if qty < 0:
        if q + qty < 0:
            raise StockError(f"Insufficient stock of {item['name']}: have {fq(item, q)} {item['disp_unit']}, "
                             f"need {fq(item, -qty)}")
        val = -q4(v * D(-qty) / q) if value is None else -abs(q4(value))
    else:
        val = q4(value if value is not None else 0)
    conn.execute("INSERT INTO stock_ledger(date,ts,item_id,qty,value,doc_type,doc_id,note,user) VALUES(?,?,?,?,?,?,?,?,?)",
                 (date or today(), dt.datetime.now().isoformat(timespec="seconds"), item_id, qty, str(val),
                  doc_type, doc_id, note, user))
    return val

def stock_rows(conn, kinds=("raw", "pack", "fg")):
    out = []
    ph = ",".join("?" * len(kinds))
    for it in conn.execute(f"SELECT * FROM items WHERE kind IN ({ph}) ORDER BY CASE kind WHEN 'raw' THEN 1 WHEN 'pack' THEN 2 ELSE 3 END, name", kinds):
        q, v = balance(conn, it["id"])
        out.append({"item": it, "qty": q, "disp": fq(it, q), "value": q2(v), "low": q <= it["reorder_base"] and it["reorder_base"] > 0,
                    "avg": q2(avg_cost(conn, it["id"]) * it["disp_factor"])})
    return out

def adjust_stock(conn, user, item_id, qty, unit, reason, rate=None):
    if not reason.strip(): raise AppError("A reason is required for stock adjustments")
    item = get_item(conn, item_id)
    base = to_base(item, qty, unit)
    if base == 0: raise AppError("Quantity cannot be zero")
    value = None
    if base > 0:
        value = (avg_cost(conn, item_id) * base) if balance(conn, item_id)[0] > 0 else None
        if rate not in (None, ""): value = D(rate) * D(qty)
        if value is None: raise AppError("Give a rate: no existing stock to value this at")
    post(conn, item_id, base, "adjustment", 0, user, reason, value)
    audit(conn, user, "adjust", "item", item_id, f"{qty} {unit}: {reason}")

# ---------------------------------------------------------------- purchases
def create_purchase(conn, user, supplier_id, bill_no, date, lines):
    """lines: [(item_id, qty, unit, rate_per_that_unit)]. Receipts are valued at purchase rate."""
    lines = [l for l in lines if l]
    if not lines: raise AppError("Add at least one line")
    cur = conn.execute("INSERT INTO purchases(supplier_id,bill_no,date,user) VALUES(?,?,?,?)", (supplier_id, bill_no, date, user))
    pid = cur.lastrowid
    for item_id, qty, unit, rate in lines:
        item = get_item(conn, item_id)
        if item["kind"] == "fg": raise AppError("Finished goods are produced, not purchased")
        base = to_base(item, qty, unit)
        if base <= 0 or D(rate) < 0: raise AppError("Quantity must be positive")
        value = q4(D(qty) * D(rate))
        conn.execute("INSERT INTO purchase_lines(purchase_id,item_id,qty_base,value) VALUES(?,?,?,?)", (pid, item_id, base, str(value)))
        post(conn, item_id, base, "purchase", pid, user, f"Bill {bill_no}", value, date)
    audit(conn, user, "create", "purchase", pid, f"bill {bill_no}")
    return pid

# ---------------------------------------------------------------- BOM + production
def bom_lines(conn, product_id):
    return conn.execute("SELECT bl.*, i.name, i.kind, i.base_unit, i.disp_unit, i.disp_factor FROM bom_lines bl "
                        "JOIN items i ON i.id=bl.item_id WHERE bl.product_id=? ORDER BY i.kind, i.name", (product_id,)).fetchall()

def var_pct(planned, actual):
    if not planned: return None
    return q2((D(actual) - D(planned)) / D(planned) * 100)

def tolerance(conn): return D(setting(conn, "tolerance_pct", "3"))
def flagged(pct, tol): return pct is not None and abs(pct) > tol

def start_run(conn, user, product_id, batches, issues, date=None):
    """Create a run and issue the raw materials. issues: [(item_id, qty, unit)] (raw items)."""
    date = date or today()
    b = conn.execute("SELECT * FROM boms WHERE product_id=?", (product_id,)).fetchone()
    if not b: raise AppError("This product has no BOM")
    batches = D(batches)
    if batches <= 0: raise AppError("Batches must be positive")
    product = get_item(conn, product_id)
    expected = int((b["yield_packets"] * batches).to_integral_value(ROUND_HALF_UP))
    expiry = (dt.date.fromisoformat(date) + dt.timedelta(days=product["shelf_life_days"] or 90)).isoformat()
    cur = conn.execute("INSERT INTO runs(product_id,date,batches,expected_output,status,user,expiry) VALUES(?,?,?,?,?,?,?)",
                       (product_id, date, str(batches), expected, "open", user, expiry))
    rid = cur.lastrowid
    batch_no = f"{product['code']}-{date.replace('-', '')}-{rid:03d}"
    conn.execute("UPDATE runs SET batch_no=? WHERE id=?", (batch_no, rid))
    issued = {}
    for item_id, qty, unit in issues:
        item = get_item(conn, item_id)
        if item["kind"] != "raw": raise AppError(f"{item['name']} is not a raw material")
        issued[item_id] = issued.get(item_id, 0) + to_base(item, qty, unit)
    for bl in bom_lines(conn, product_id):
        planned = int((D(bl["qty_base"]) * batches).to_integral_value(ROUND_HALF_UP))
        actual = issued.pop(bl["item_id"], 0) if bl["kind"] == "raw" else None
        conn.execute("INSERT INTO run_lines(run_id,item_id,planned,actual,basis) VALUES(?,?,?,?,?)",
                     (rid, bl["item_id"], planned, actual, bl["basis"]))
    for item_id, base in issued.items():   # extra raw items not in the BOM
        conn.execute("INSERT INTO run_lines(run_id,item_id,planned,actual,basis) VALUES(?,?,?,?,?)", (rid, item_id, 0, base, "batch"))
    for rl in conn.execute("SELECT * FROM run_lines WHERE run_id=? AND actual>0", (rid,)).fetchall():
        post(conn, rl["item_id"], -rl["actual"], "production_issue", rid, user, f"Run {batch_no}", date=date)
    audit(conn, user, "start", "run", rid, f"{product['name']} x{batches}, expected {expected}")
    return rid

def complete_run(conn, user, run_id, good, damaged=0, pack_used=None, date=None):
    """Record actual output. Packaging is consumed here: per-packet items (pouches) = good + damaged;
    per-batch items (cartons) = the quantity the supervisor entered (default: planned).
    pack_used: {item_id: base_qty}."""
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["status"] != "open": raise AppError("Run is not open")
    good, damaged = int(good), int(damaged)
    if good < 0 or damaged < 0: raise AppError("Quantities cannot be negative")
    date = date or today()
    pack_used = pack_used or {}
    for rl in conn.execute("SELECT rl.*, i.kind FROM run_lines rl JOIN items i ON i.id=rl.item_id WHERE run_id=?", (run_id,)).fetchall():
        if rl["kind"] != "pack": continue
        used = good + damaged if rl["basis"] == "packet" else int(pack_used.get(rl["item_id"], rl["planned"]))
        conn.execute("UPDATE run_lines SET actual=? WHERE id=?", (used, rl["id"]))
        if used: post(conn, rl["item_id"], -used, "production_issue", run_id, user, f"Run {run['batch_no']}", date=date)
    cost = -sum((D(r["value"]) for r in conn.execute(
        "SELECT value FROM stock_ledger WHERE doc_type='production_issue' AND doc_id=?", (run_id,))), Decimal(0))
    conn.execute("UPDATE runs SET good_output=?, damaged=?, status='completed', material_cost=? WHERE id=?",
                 (good, damaged, str(q4(cost)), run_id))
    post(conn, run["product_id"], good, "production_output", run_id, user, f"Batch {run['batch_no']}", q4(cost), date)
    audit(conn, user, "complete", "run", run_id, f"good={good} damaged_pouches={damaged} material_cost={q2(cost)}")

def add_overhead(conn, user, run_id, kind, amount, date=None):
    amount = D(amount)
    if amount <= 0: raise AppError("Amount must be positive")
    if not conn.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone(): raise AppError("Run not found")
    conn.execute("INSERT INTO run_costs(run_id,kind,amount,date,user) VALUES(?,?,?,?,?)", (run_id, kind, str(amount), date or today(), user))
    audit(conn, user, "overhead", "run", run_id, f"{kind} {amount}")

def run_detail(conn, run_id):
    """Everything the supervisor/owner needs: variance lines, yield variance, costing."""
    run = conn.execute("SELECT r.*, i.name AS product, i.price_paise FROM runs r JOIN items i ON i.id=r.product_id WHERE r.id=?", (run_id,)).fetchone()
    if not run: raise AppError("Run not found")
    tol = tolerance(conn)
    lines = []
    for rl in conn.execute("SELECT rl.*, i.name, i.disp_unit, i.disp_factor, i.kind FROM run_lines rl JOIN items i ON i.id=rl.item_id "
                           "WHERE run_id=? ORDER BY i.kind, i.name", (run_id,)):
        pct = var_pct(rl["planned"], rl["actual"]) if rl["actual"] is not None else None
        lines.append({"item": rl["name"], "unit": rl["disp_unit"], "planned": dq(rl, rl["planned"]),
                      "actual": dq(rl, rl["actual"]) if rl["actual"] is not None else None, "var": pct, "flag": flagged(pct, tol)})
    y = None
    if run["status"] == "completed":
        pct = var_pct(run["expected_output"], run["good_output"])
        y = {"expected": run["expected_output"], "actual": run["good_output"], "var": pct, "flag": flagged(pct, tol),
             "wastage": run["expected_output"] - run["good_output"], "damaged_pouches": run["damaged"]}
    mat = D(run["material_cost"] or 0)
    oh = sum((D(r["amount"]) for r in conn.execute("SELECT amount FROM run_costs WHERE run_id=?", (run_id,))), Decimal(0))
    cost = {"material": q2(mat), "overhead": q2(oh), "total": q2(mat + oh), "per_packet": None, "price": rs(run["price_paise"]),
            "margin": None, "margin_pct": None}
    if run["status"] == "completed" and run["good_output"]:
        cpp = (mat + oh) / run["good_output"]
        cost["per_packet"] = q2(cpp)
        cost["margin"] = q2(rs(run["price_paise"]) - cpp)
        cost["margin_pct"] = q2((rs(run["price_paise"]) - cpp) / rs(run["price_paise"]) * 100) if run["price_paise"] else None
    return {"run": run, "lines": lines, "yield": y, "cost": cost, "tol": tol}

# ---------------------------------------------------------------- sales: orders
def price_for(conn, customer_id, item_id):
    r = conn.execute("SELECT price_paise FROM customer_prices WHERE customer_id=? AND item_id=?", (customer_id, item_id)).fetchone()
    return r["price_paise"] if r else get_item(conn, item_id)["price_paise"]

def create_order(conn, user, salesman_id, customer_id, lines, client_uuid=None):
    """lines: [(item_id, qty, unit)]. Idempotent on client_uuid so offline retries never duplicate."""
    if client_uuid:
        ex = conn.execute("SELECT id FROM orders WHERE client_uuid=?", (client_uuid,)).fetchone()
        if ex: return ex["id"]
    if not conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone(): raise AppError("Choose a customer")
    clean = [l for l in lines if D(l[1]) > 0]
    if not clean: raise AppError("Add at least one product with a quantity")
    cur = conn.execute("INSERT INTO orders(no,customer_id,salesman_id,status,created,client_uuid) VALUES('',?,?,?,?,?)",
                       (customer_id, salesman_id, "submitted", dt.datetime.now().isoformat(timespec="seconds"), client_uuid))
    oid = cur.lastrowid
    conn.execute("UPDATE orders SET no=? WHERE id=?", (f"ORD-{oid:05d}", oid))
    for item_id, qty, unit in clean:
        item = get_item(conn, item_id)
        if item["kind"] != "fg": raise AppError("Only finished products can be ordered")
        conn.execute("INSERT INTO order_lines(order_id,item_id,qty_base,price_paise) VALUES(?,?,?,?)",
                     (oid, item_id, to_base(item, qty, unit), price_for(conn, customer_id, item_id)))
    audit(conn, user, "create", "order", oid, f"customer {customer_id}")
    return oid

def reserved(conn, item_id, exclude_order=0):
    return conn.execute("SELECT COALESCE(SUM(ol.qty_base),0) FROM order_lines ol JOIN orders o ON o.id=ol.order_id "
                        "WHERE ol.item_id=? AND o.status IN('approved','invoiced') AND o.id!=?", (item_id, exclude_order)).fetchone()[0]

def stock_check(conn, order_id):
    out = []
    for l in conn.execute("SELECT ol.*, i.name, i.disp_unit FROM order_lines ol JOIN items i ON i.id=ol.item_id WHERE order_id=?", (order_id,)):
        avail = balance(conn, l["item_id"])[0] - reserved(conn, l["item_id"], order_id)
        out.append({"line_id": l["id"], "item_id": l["item_id"], "name": l["name"], "qty": l["qty_base"], "price": l["price_paise"],
                    "available": max(avail, 0), "short": max(l["qty_base"] - max(avail, 0), 0)})
    return out

def _order(conn, order_id, *statuses):
    o = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o: raise AppError("Order not found")
    if statuses and o["status"] not in statuses: raise AppError(f"Order is {o['status']}; action not allowed")
    return o

def edit_order(conn, user, order_id, edits):
    """edits: {line_id: (qty_packets, price_rupees)}. qty 0 removes the line. Logged before/after."""
    _order(conn, order_id, "submitted")
    before = [dict(r) for r in conn.execute("SELECT id,item_id,qty_base,price_paise FROM order_lines WHERE order_id=?", (order_id,))]
    for lid, (qty, price) in edits.items():
        qty = int(D(qty))
        if qty <= 0: conn.execute("DELETE FROM order_lines WHERE id=? AND order_id=?", (lid, order_id))
        else: conn.execute("UPDATE order_lines SET qty_base=?, price_paise=? WHERE id=? AND order_id=?",
                           (qty, int((D(price) * 100).to_integral_value(ROUND_HALF_UP)), lid, order_id))
    after = [dict(r) for r in conn.execute("SELECT id,item_id,qty_base,price_paise FROM order_lines WHERE order_id=?", (order_id,))]
    if not after: raise AppError("An order needs at least one line; reject it instead")
    audit(conn, user, "edit", "order", order_id, {"before": before, "after": after})

def reduce_to_available(conn, user, order_id):
    _order(conn, order_id, "submitted")
    for c in stock_check(conn, order_id):
        if c["short"]:
            if c["available"] > 0: conn.execute("UPDATE order_lines SET qty_base=? WHERE id=?", (c["available"], c["line_id"]))
            else: conn.execute("DELETE FROM order_lines WHERE id=?", (c["line_id"],))
    if not conn.execute("SELECT 1 FROM order_lines WHERE order_id=?", (order_id,)).fetchone():
        raise AppError("No stock available for any line; reject the order instead")
    audit(conn, user, "reduce_to_available", "order", order_id)

def approve_order(conn, user, order_id):
    _order(conn, order_id, "submitted")
    short = [c for c in stock_check(conn, order_id) if c["short"]]
    if short:
        raise StockError("Cannot approve - shortage: " + "; ".join(f"{c['name']} short by {c['short']} (need {c['qty']}, available {c['available']})" for c in short))
    conn.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
    audit(conn, user, "approve", "order", order_id)

def reject_order(conn, user, order_id, reason):
    _order(conn, order_id, "submitted", "approved")
    conn.execute("UPDATE orders SET status='rejected', reject_reason=? WHERE id=?", (reason or "-", order_id))
    audit(conn, user, "reject", "order", order_id, reason)

# ---------------------------------------------------------------- GST invoice
def _r(x): return int(x.quantize(Decimal("1"), ROUND_HALF_UP))

def compute_tax(taxable_paise, rate, intra):
    """Returns (cgst, sgst, igst) in integer paise. Intra-state splits the rate half/half."""
    t = D(taxable_paise) * D(rate) / 100
    if intra:
        h = _r(t / 2)
        return h, h, 0
    return 0, 0, _r(t)

def make_invoice(conn, user, order_id, date=None):
    o = _order(conn, order_id, "approved")
    date = date or today()
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (o["customer_id"],)).fetchone()
    intra = 1 if cust["state_code"] == SELLER["state_code"] else 0
    fy = fy_of(date)
    n = next_seq(conn, "INV", fy)
    number = f"INV/{fy}/{n:04d}"
    cur = conn.execute("INSERT INTO invoices(number,fy,order_id,customer_id,date,place_code,intra,taxable,cgst,sgst,igst,total,status,token) "
                       "VALUES(?,?,?,?,?,?,?,0,0,0,0,0,'posted',?)",
                       (number, fy, order_id, o["customer_id"], date, cust["state_code"], intra, uuid.uuid4().hex))
    iid = cur.lastrowid
    T = [0, 0, 0, 0]
    for l in conn.execute("SELECT ol.*, i.hsn, i.gst_rate FROM order_lines ol JOIN items i ON i.id=ol.item_id WHERE order_id=?", (order_id,)):
        taxable = l["qty_base"] * l["price_paise"]
        c, s, g = compute_tax(taxable, l["gst_rate"], intra)
        conn.execute("INSERT INTO invoice_lines(invoice_id,item_id,hsn,qty_base,rate_paise,taxable,gst_rate,cgst,sgst,igst) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (iid, l["item_id"], l["hsn"], l["qty_base"], l["price_paise"], taxable, l["gst_rate"], c, s, g))
        T[0] += taxable; T[1] += c; T[2] += s; T[3] += g
    conn.execute("UPDATE invoices SET taxable=?,cgst=?,sgst=?,igst=?,total=? WHERE id=?", (T[0], T[1], T[2], T[3], sum(T), iid))
    conn.execute("UPDATE orders SET status='invoiced' WHERE id=?", (order_id,))
    audit(conn, user, "create", "invoice", iid, f"{number} total {rs(sum(T))}")
    return iid

def dispatch(conn, user, invoice_id, date=None, note=""):
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not inv or inv["status"] != "posted": raise AppError("Only posted invoices can be dispatched")
    if conn.execute("SELECT 1 FROM dispatches WHERE invoice_id=?", (invoice_id,)).fetchone(): raise AppError("Already dispatched")
    for l in conn.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchall():
        val = post(conn, l["item_id"], -l["qty_base"], "invoice", invoice_id, user, inv["number"], date=date)
        conn.execute("UPDATE invoice_lines SET cogs=? WHERE id=?", (str(-val), l["id"]))
    conn.execute("INSERT INTO dispatches(invoice_id,date,note,user) VALUES(?,?,?,?)", (invoice_id, date or today(), note, user))
    conn.execute("UPDATE orders SET status='dispatched' WHERE id=?", (inv["order_id"],))
    audit(conn, user, "dispatch", "invoice", invoice_id, inv["number"])

def invoice_paid(conn, invoice_id):
    return conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?", (invoice_id,)).fetchone()[0]

def cancel_invoice(conn, user, invoice_id, reason, date=None):
    """Posted invoices are never deleted: cancel = full credit note + stock returned if dispatched."""
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not inv or inv["status"] != "posted": raise AppError("Invoice is not in a cancellable state")
    if not reason.strip(): raise AppError("Reason required")
    if invoice_paid(conn, invoice_id): raise AppError("Payments exist against this invoice; reverse them first")
    date = date or today()
    fy = fy_of(date)
    number = f"CN/{fy}/{next_seq(conn, 'CN', fy):04d}"
    conn.execute("INSERT INTO credit_notes(number,fy,invoice_id,date,reason,taxable,cgst,sgst,igst,total,user) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (number, fy, invoice_id, date, reason, inv["taxable"], inv["cgst"], inv["sgst"], inv["igst"], inv["total"], user))
    if conn.execute("SELECT 1 FROM dispatches WHERE invoice_id=?", (invoice_id,)).fetchone():
        for l in conn.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchall():
            post(conn, l["item_id"], l["qty_base"], "credit_note", invoice_id, user, f"{number} return", D(l["cogs"] or 0), date)
    conn.execute("UPDATE invoices SET status='cancelled' WHERE id=?", (invoice_id,))
    conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (inv["order_id"],))
    audit(conn, user, "cancel", "invoice", invoice_id, f"{inv['number']} -> {number}: {reason}")

# ---------------------------------------------------------------- payments / receivables
def record_payment(conn, user, customer_id, invoice_id, amount_paise, date=None, mode="cash", ref=""):
    amount_paise = int(amount_paise)
    if amount_paise <= 0: raise AppError("Amount must be positive")
    if invoice_id:
        inv = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not inv or inv["status"] != "posted": raise AppError("Invoice not payable")
        customer_id = inv["customer_id"]
        if amount_paise > inv["total"] - invoice_paid(conn, invoice_id): raise AppError("Payment exceeds invoice outstanding")
    cur = conn.execute("INSERT INTO payments(customer_id,invoice_id,amount,date,mode,ref,user) VALUES(?,?,?,?,?,?,?)",
                       (customer_id, invoice_id or None, amount_paise, date or today(), mode, ref, user))
    audit(conn, user, "create", "payment", cur.lastrowid, f"{rs(amount_paise)} {mode}")
    return cur.lastrowid

def customer_outstanding(conn, customer_id):
    inv = conn.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE customer_id=? AND status='posted'", (customer_id,)).fetchone()[0]
    paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE customer_id=?", (customer_id,)).fetchone()[0]
    return inv - paid

def open_invoices(conn, customer_id=None):
    """Posted invoices with outstanding after direct payments."""
    sql = "SELECT * FROM invoices WHERE status='posted'" + (" AND customer_id=?" if customer_id else "") + " ORDER BY date,id"
    rows = conn.execute(sql, (customer_id,) if customer_id else ()).fetchall()
    return [(r, r["total"] - invoice_paid(conn, r["id"])) for r in rows]
