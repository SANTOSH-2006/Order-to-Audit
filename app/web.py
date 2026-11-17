import os, datetime as dt
from functools import wraps
from decimal import Decimal
from flask import Flask, g, session, request, redirect, url_for, render_template, flash, abort, Response, jsonify, send_from_directory
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash
from . import db, services as S, reports as R, exports as X, seed

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True)

with app.app_context():
    _c = db.connect(); db.init(_c); seed.seed(_c); _c.close()

# ------------------------------------------------------------------ helpers
def conn():
    if "db" not in g: g.db = db.connect()
    return g.db

@app.teardown_appcontext
def _close(_):
    c = g.pop("db", None)
    if c: c.close()

HOME = {"admin": "orders", "salesman": "sm_home", "production": "prod_home", "accountant": "report_index", "owner": "owner"}

def access(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            u = session.get("user")
            if not u: return redirect(url_for("login", next=request.path))
            if roles and u["role"] not in roles: abort(403)
            return f(*a, **k)
        return w
    return deco

def me(): return session["user"]["username"]

def act(fn, ok, back):
    """Run a state-changing action atomically; flash result; redirect."""
    try:
        fn(); conn().commit(); flash(ok, "ok")
    except (S.AppError, ValueError, ArithmeticError, KeyError) as e:
        conn().rollback(); flash(str(e) or "Invalid input", "err")
    return redirect(back)

def link(text, href): return Markup('<a href="%s">%s</a>') % (href, text)
def btn_form(action, label, cls="", fields=None):
    h = "".join(Markup('<input type="hidden" name="%s" value="%s">') % (k, v) for k, v in (fields or {}).items())
    return Markup('<form method="post" action="%s" class="inline">%s<button class="%s">%s</button></form>') % (action, Markup(h), cls, label)

def page(title, *sections, back=None):
    return render_template("page.html", title=title, sections=sections, back=back)
def table(headers, rows, caption=None): return {"type": "table", "headers": headers, "rows": rows, "caption": caption}
def form(action, fields, submit="Save", title=None, lines=None, method="post"):
    return {"type": "form", "action": action, "fields": fields, "submit": submit, "title": title, "lines": lines, "method": method}
def F(name, label, type="text", options=None, value="", **kw): return dict(name=name, label=label, type=type, options=options or [], value=value, **kw)
def html(h): return {"type": "html", "html": Markup(h)}

def inr(paise): return f"{S.rs(paise):,}"
def num(x): return "" if x is None else format(x, "f") if isinstance(x, Decimal) else x
app.jinja_env.filters["inr"] = inr
app.jinja_env.filters["cell"] = lambda c: "" if c is None else (format(c, "f") if isinstance(c, Decimal) else c)

def items_opts(kinds):
    return [(r["id"], f"{r['name']} ({r['disp_unit']})") for r in conn().execute(
        f"SELECT * FROM items WHERE kind IN ({','.join('?'*len(kinds))}) ORDER BY name", kinds)]
def cust_opts(): return [(r["id"], r["name"]) for r in conn().execute("SELECT id,name FROM customers ORDER BY name")]
UNITS = [(u, u) for u in ("kg", "g", "l", "ml", "pc", "packet", "carton")]

def parse_lines(f, n, cols):
    out = []
    for i in range(n):
        vals = [f.get(f"{c}_{i}", "").strip() for c in cols]
        if any(vals[1:3]) or vals[0] and vals[1]: out.append(vals)
    return out

# ------------------------------------------------------------------ auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = conn().execute("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),)).fetchone()
        if u and check_password_hash(u["pw_hash"], request.form["password"]):
            session["user"] = {"id": u["id"], "username": u["username"], "role": u["role"], "name": u["name"]}
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for(HOME[u["role"]]))
        flash("Wrong username or password", "err")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
def index():
    u = session.get("user")
    return redirect(url_for(HOME[u["role"]]) if u else url_for("login"))

@app.errorhandler(403)
def _403(e): return page("Not allowed", html("<p>Your role cannot open this page.</p>")), 403

@app.route("/sw.js")
def sw(): return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

# ------------------------------------------------------------------ salesman (mobile)
@app.route("/m")
@access("salesman")
def sm_home():
    uid = session["user"]["id"]
    rows = [[link(r["no"], url_for("sm_order", oid=r["id"])), r["created"][:16].replace("T", " "), r["cname"], r["status"]] for r in conn().execute(
        "SELECT o.*, c.name cname FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.salesman_id=? ORDER BY o.id DESC LIMIT 30", (uid,))]
    return page("My orders", html('<p><a class="big" href="%s">+ New order</a> <a href="%s">Customer-wise sales</a></p><p id="queue" class="muted"></p>' % (url_for("sm_new"), url_for("sm_sales"))),
                table(["Order", "When", "Customer", "Status"], rows), html("<script src='/static/queue.js'></script><script>flushQueue(true)</script>"))

@app.route("/m/order/<int:oid>")
@access("salesman")
def sm_order(oid):
    o = conn().execute("SELECT o.*, c.name cname FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=? AND salesman_id=?", (oid, session["user"]["id"])).fetchone() or abort(404)
    inv = conn().execute("SELECT number FROM invoices WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
    rows = [[l["name"], l["qty_base"], inr(l["price_paise"]), inr(l["qty_base"] * l["price_paise"])] for l in conn().execute(
        "SELECT l.*, i.name FROM order_lines l JOIN items i ON i.id=l.item_id WHERE order_id=?", (oid,))]
    steps = ["submitted", "approved", "invoiced", "dispatched"]
    bar = " &rarr; ".join(("<b>%s</b>" if s == o["status"] else "%s") % s for s in steps)
    extra = f"<p>Rejected: {escape(o['reject_reason'])}</p>" if o["status"] == "rejected" else ""
    return page(f"{o['no']} - {o['cname']}", html(f"<p>{bar}</p>{extra}<p>Invoice: {inv['number'] if inv else '-'}</p>"), table(["Product", "Qty", "Rate", "Amount"], rows), back=url_for("sm_home"))

@app.route("/m/sales")
@access("salesman")
def sm_sales():
    rows = [[r["name"], r["n"], inr(r["t"])] for r in conn().execute(
        "SELECT c.name, COUNT(*) n, SUM(i.total) t FROM invoices i JOIN orders o ON o.id=i.order_id JOIN customers c ON c.id=i.customer_id "
        "WHERE o.salesman_id=? AND i.status='posted' GROUP BY c.id ORDER BY t DESC", (session["user"]["id"],))]
    return page("My customer-wise sales (invoiced)", table(["Customer", "Invoices", "Amount incl. GST (Rs)"], rows), back=url_for("sm_home"))

@app.route("/m/new")
@access("salesman")
def sm_new(): return render_template("sm_new.html")

@app.route("/api/catalog")
@access("salesman")
def api_catalog():
    c = conn()
    return jsonify(customers=[dict(id=r["id"], name=r["name"], type=r["type"]) for r in c.execute("SELECT * FROM customers ORDER BY name")],
                   items=[dict(id=r["id"], name=r["name"], price=r["price_paise"], carton=r["carton_size"]) for r in c.execute("SELECT * FROM items WHERE kind='fg' ORDER BY name")],
                   prices={f"{r['customer_id']}:{r['item_id']}": r["price_paise"] for r in c.execute("SELECT * FROM customer_prices")})

@app.post("/api/orders")
@access("salesman")
def api_orders():
    j = request.get_json(force=True)
    try:
        oid = S.create_order(conn(), me(), session["user"]["id"], int(j["customer_id"]),
                             [(int(l["item_id"]), l["qty"], l.get("unit", "packet")) for l in j["lines"]], j.get("client_uuid"))
        conn().commit()
        return jsonify(ok=True, order_no=conn().execute("SELECT no FROM orders WHERE id=?", (oid,)).fetchone()[0])
    except (S.AppError, ValueError, KeyError, ArithmeticError) as e:
        conn().rollback(); return jsonify(ok=False, error=str(e)), 422

# ------------------------------------------------------------------ admin: orders, invoices, payments
@app.route("/admin/orders")
@access("admin")
def orders():
    st = request.args.get("status", "")
    rows = []
    for r in conn().execute("SELECT o.*, c.name cname, u.name sname, (SELECT SUM(qty_base*price_paise) FROM order_lines WHERE order_id=o.id) amt "
                            "FROM orders o JOIN customers c ON c.id=o.customer_id LEFT JOIN users u ON u.id=o.salesman_id "
                            + ("WHERE o.status=? " if st else "") + "ORDER BY o.id DESC LIMIT 100", (st,) if st else ()):
        rows.append([link(r["no"], url_for("order", oid=r["id"])), r["created"][:16].replace("T", " "), r["cname"], r["sname"], inr(r["amt"] or 0), r["status"]])
    flt = " | ".join(f'<a href="?status={s}">{s or "all"}</a>' for s in ["", "submitted", "approved", "invoiced", "dispatched", "rejected", "cancelled"])
    return page("Orders", html(f"<p>{flt}</p>"), table(["Order", "When", "Customer", "Salesman", "Amount (ex-GST)", "Status"], rows))

@app.route("/admin/orders/<int:oid>")
@access("admin")
def order(oid):
    c = conn(); o = S._order(c, oid)
    cust = c.execute("SELECT * FROM customers WHERE id=?", (o["customer_id"],)).fetchone()
    intra = cust["state_code"] == S.SELLER["state_code"]
    chk = S.stock_check(c, oid); secs = []
    secs.append(html(f"<p><b>{escape(cust['name'])}</b> - {cust['state']} ({cust['state_code']}) - GSTIN {cust['gstin'] or 'n/a'}<br>"
                     f"Status: <b>{o['status']}</b> | Tax if invoiced: <b>{'CGST + SGST (intra-state)' if intra else 'IGST (inter-state)'}</b> | "
                     f"Customer outstanding: Rs {inr(S.customer_outstanding(c, cust['id']))}</p>"))
    rows = [[k["name"], k["qty"], inr(k["price"]), inr(k["qty"] * k["price"]), k["available"],
             Markup('<span class="bad">SHORT by %s</span>') % k["short"] if k["short"] else Markup('<span class="good">OK</span>')] for k in chk]
    secs.append(table(["Product", "Ordered", "Rate", "Amount", "FG available", "Stock check"], rows, "Stock coverage (available = FG balance minus other approved orders)"))
    st, back = o["status"], url_for("order", oid=oid)
    if st == "submitted":
        secs.append(form(url_for("order_edit", oid=oid), [F(f"qty_{k['line_id']}", f"{k['name']} qty", "number", value=k["qty"], step="1") for k in chk] +
                         [F(f"price_{k['line_id']}", f"{k['name']} rate (Rs)", "number", value=S.rs(k["price"]), step="0.01") for k in chk], "Save edits", "Edit quantities / prices (set qty 0 to remove)"))
        acts = btn_form(url_for("order_do", oid=oid, a="approve"), "Approve", "primary")
        if any(k["short"] for k in chk): acts += btn_form(url_for("order_do", oid=oid, a="reduce"), "Reduce to available stock")
        secs.append(html(f"<p>{acts}</p>"))
        secs.append(form(url_for("order_do", oid=oid, a="reject"), [F("reason", "Reject reason")], "Reject"))
    elif st == "approved": secs.append(html("<p>%s</p>" % btn_form(url_for("order_do", oid=oid, a="invoice"), "Raise GST invoice", "primary")))
    elif st in ("invoiced", "dispatched"):
        inv = c.execute("SELECT * FROM invoices WHERE order_id=? ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
        s = f"<p>Invoice <a href='{url_for('invoice_view', iid=inv['id'])}'>{inv['number']}</a> (Rs {inr(inv['total'])})</p>"
        if st == "invoiced": s += "<p>%s</p>" % btn_form(url_for("order_do", oid=oid, a="dispatch"), "Record dispatch (reduces stock)", "primary")
        secs.append(html(s))
    return page(f"Order {o['no']}", *secs, back=url_for("orders"))

@app.post("/admin/orders/<int:oid>/edit")
@access("admin")
def order_edit(oid):
    f = request.form; ids = [int(k[4:]) for k in f if k.startswith("qty_")]
    return act(lambda: S.edit_order(conn(), me(), oid, {i: (f[f"qty_{i}"], f[f"price_{i}"]) for i in ids}), "Order updated", url_for("order", oid=oid))

@app.post("/admin/orders/<int:oid>/<a>")
@access("admin")
def order_do(oid, a):
    c, u, back = conn(), me(), url_for("order", oid=oid)
    def inv_id(): return c.execute("SELECT id FROM invoices WHERE order_id=? AND status='posted' ORDER BY id DESC LIMIT 1", (oid,)).fetchone()[0]
    acts = {"approve": (lambda: S.approve_order(c, u, oid), "Order approved"), "reduce": (lambda: S.reduce_to_available(c, u, oid), "Reduced to available stock"),
            "reject": (lambda: S.reject_order(c, u, oid, request.form.get("reason", "")), "Order rejected"),
            "invoice": (lambda: S.make_invoice(c, u, oid), "GST invoice raised"), "dispatch": (lambda: S.dispatch(c, u, inv_id()), "Dispatched; stock reduced")}
    if a not in acts: abort(404)
    return act(*acts[a], back)

@app.route("/admin/invoices")
@access("admin", "accountant")
def invoices():
    rows = []
    for r in conn().execute("SELECT i.*, c.name cname, (SELECT 1 FROM dispatches d WHERE d.invoice_id=i.id) disp FROM invoices i JOIN customers c ON c.id=i.customer_id ORDER BY i.id DESC"):
        paid = S.invoice_paid(conn(), r["id"]); share = request.host_url + f"i/{r['token']}.pdf"
        wa = Markup('<a href="https://wa.me/?text=%s">WhatsApp</a>') % ("Invoice " + r["number"] + " " + share)
        rows.append([link(r["number"], url_for("invoice_view", iid=r["id"])), r["date"], r["cname"], inr(r["total"]), inr(paid),
                     inr(r["total"] - paid) if r["status"] == "posted" else "-", r["status"] + ("" if r["disp"] else " / not dispatched"), link("PDF", url_for("invoice_pdf", iid=r["id"])), wa])
    return page("Invoices", table(["Invoice", "Date", "Customer", "Total", "Paid", "Outstanding", "Status", "PDF", "Share"], rows))

@app.route("/admin/invoices/<int:iid>")
@access("admin", "accountant")
def invoice_view(iid):
    c = conn(); inv = c.execute("SELECT i.*, cu.name cname FROM invoices i JOIN customers cu ON cu.id=i.customer_id WHERE i.id=?", (iid,)).fetchone() or abort(404)
    rows = [[l["name"], l["hsn"], l["qty_base"], inr(l["rate_paise"]), inr(l["taxable"]), l["gst_rate"] + "%", inr(l["cgst"]), inr(l["sgst"]), inr(l["igst"])] for l in c.execute(
        "SELECT l.*, i.name FROM invoice_lines l JOIN items i ON i.id=l.item_id WHERE invoice_id=?", (iid,))]
    secs = [html(f"<p>{inv['number']} | {inv['date']} | {escape(inv['cname'])} | Status <b>{inv['status']}</b> | Total <b>Rs {inr(inv['total'])}</b> "
                 f"| <a href='{url_for('invoice_pdf', iid=iid)}'>Download PDF</a></p>"),
            table(["Item", "HSN", "Qty", "Rate", "Taxable", "GST", "CGST", "SGST", "IGST"], rows)]
    if session["user"]["role"] == "admin" and inv["status"] == "posted":
        secs.append(form(url_for("invoice_cancel", iid=iid), [F("reason", "Reason for cancellation")], "Cancel invoice (issues credit note)"))
    cn = c.execute("SELECT * FROM credit_notes WHERE invoice_id=?", (iid,)).fetchone()
    if cn: secs.append(html(f"<p>Credit note <b>{cn['number']}</b> dated {cn['date']}: {escape(cn['reason'])}</p>"))
    return page("Invoice " + inv["number"], *secs, back=url_for("invoices"))

@app.post("/admin/invoices/<int:iid>/cancel")
@access("admin")
def invoice_cancel(iid): return act(lambda: S.cancel_invoice(conn(), me(), iid, request.form.get("reason", "")), "Invoice cancelled with credit note", url_for("invoice_view", iid=iid))

@app.route("/admin/invoices/<int:iid>.pdf")
@access("admin", "accountant")
def invoice_pdf(iid):
    n = conn().execute("SELECT number FROM invoices WHERE id=?", (iid,)).fetchone() or abort(404)
    return Response(X.invoice_pdf(conn(), iid), mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename={n[0].replace('/', '_')}.pdf"})

@app.route("/i/<token>.pdf")   # unguessable share link used in the WhatsApp message
def invoice_public(token):
    r = conn().execute("SELECT id FROM invoices WHERE token=?", (token,)).fetchone() or abort(404)
    return Response(X.invoice_pdf(conn(), r["id"]), mimetype="application/pdf")

@app.route("/admin/payments", methods=["GET", "POST"])
@access("admin")
def payments():
    c = conn()
    if request.method == "POST":
        f = request.form
        return act(lambda: S.record_payment(c, me(), int(f.get("customer_id") or 0), int(f["invoice_id"]) if f.get("invoice_id") else None,
                                            int((Decimal(f["amount"]) * 100).to_integral_value()), f.get("date") or S.today(), f.get("mode", "cash"), f.get("ref", "")),
                   "Payment recorded", url_for("payments"))
    inv_opts = [("", "On account (no invoice)")] + [(i["id"], f"{i['number']} - {c.execute('SELECT name FROM customers WHERE id=?', (i['customer_id'],)).fetchone()[0]} - due Rs {inr(due)}") for i, due in S.open_invoices(c) if due]
    out = [[r["name"], inr(S.customer_outstanding(c, r["id"]))] for r in c.execute("SELECT * FROM customers ORDER BY name")]
    pays = [[r["date"], r["name"], r["number"] or "On account", r["mode"], inr(r["amount"])] for r in c.execute(
        "SELECT p.*, c.name, i.number FROM payments p JOIN customers c ON c.id=p.customer_id LEFT JOIN invoices i ON i.id=p.invoice_id ORDER BY p.id DESC LIMIT 30")]
    return page("Payments", form(url_for("payments"), [F("invoice_id", "Against invoice", "select", inv_opts), F("customer_id", "Customer (only for on-account)", "select", [("", "-")] + cust_opts()),
                                                       F("amount", "Amount (Rs)", "number", step="0.01", required=True), F("date", "Date", "date", value=S.today()),
                                                       F("mode", "Mode", "select", [(m, m) for m in ("cash", "upi", "bank", "cheque")]), F("ref", "Reference")], "Record payment", "Record payment"),
                table(["Customer", "Outstanding (Rs)"], out), table(["Date", "Customer", "Against", "Mode", "Amount"], pays, "Recent payments"))

# ------------------------------------------------------------------ masters
@app.route("/admin/masters", methods=["GET", "POST"])
@access("admin")
def masters():
    c = conn()
    if request.method == "POST":
        f, kind = request.form, request.form["kind"]
        def go():
            if kind == "item":
                c.execute("INSERT INTO items(code,name,kind,base_unit,disp_unit,disp_factor,carton_size,hsn,gst_rate,price_paise,reorder_base,shelf_life_days) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f["code"], f["name"], f["itemkind"], f["base_unit"], {"g": "kg", "ml": "l", "pc": "packet" if f["itemkind"] == "fg" else "pc"}[f["base_unit"]],
                           1000 if f["base_unit"] in ("g", "ml") else 1, int(f["carton_size"]) if f.get("carton_size") else None, f.get("hsn", ""), f.get("gst_rate") or "0",
                           int((Decimal(f.get("price") or 0) * 100).to_integral_value()), 0, 90))
            elif kind == "customer":
                c.execute("INSERT INTO customers(name,type,gstin,state,state_code,address,phone,salesman_id) VALUES(?,?,?,?,?,?,?,?)",
                          (f["name"], f["type"], f.get("gstin", ""), f["state"], f["state_code"], f.get("address", ""), f.get("phone", ""), f.get("salesman_id") or None))
            elif kind == "supplier": c.execute("INSERT INTO suppliers(name,gstin,phone) VALUES(?,?,?)", (f["name"], f.get("gstin", ""), f.get("phone", "")))
            elif kind == "price":
                c.execute("INSERT OR REPLACE INTO customer_prices VALUES(?,?,?)", (f["customer_id"], f["item_id"], int((Decimal(f["price"]) * 100).to_integral_value())))
            S.audit(c, me(), "master_" + kind, kind, 0, dict(f))
        return act(go, "Saved", url_for("masters"))
    items = [[r["code"], r["name"], r["kind"], r["disp_unit"], r["hsn"], r["gst_rate"], inr(r["price_paise"]) if r["kind"] == "fg" else "",
              link("BOM", url_for("bom", pid=r["id"])) if r["kind"] == "fg" else ""] for r in c.execute("SELECT * FROM items ORDER BY kind, name")]
    custs = [[r["name"], r["type"], r["gstin"], r["state"], r["state_code"]] for r in c.execute("SELECT * FROM customers")]
    sales = [(r["id"], r["name"]) for r in c.execute("SELECT * FROM users WHERE role='salesman'")]
    kind = lambda k: F("kind", "", "hidden", value=k)
    return page("Masters", table(["Code", "Item", "Kind", "Unit", "HSN", "GST %", "Price", ""], items), table(["Customer", "Type", "GSTIN", "State", "Code"], custs),
                form(url_for("masters"), [kind("item"), F("code", "Code", required=True), F("name", "Name", required=True), F("itemkind", "Kind", "select", [("raw", "Raw material"), ("pack", "Packaging"), ("fg", "Finished product")]),
                                          F("base_unit", "Measured in", "select", [("g", "weight (kg)"), ("ml", "volume (litre)"), ("pc", "pieces / packets")]), F("carton_size", "Packets per carton (FG)", "number"),
                                          F("hsn", "HSN"), F("gst_rate", "GST %", "number", value="5"), F("price", "Selling price Rs/packet (FG)", "number", step="0.01")], "Add item", "Add item"),
                form(url_for("masters"), [kind("customer"), F("name", "Name", required=True), F("type", "Type", "select", [("retailer", "retailer"), ("distributor", "distributor")]), F("gstin", "GSTIN"),
                                          F("state", "State", required=True), F("state_code", "State code (e.g. 33)", required=True), F("address", "Address"), F("phone", "Phone"),
                                          F("salesman_id", "Salesman", "select", [("", "-")] + sales)], "Add customer", "Add customer"),
                form(url_for("masters"), [kind("price"), F("customer_id", "Customer", "select", cust_opts()), F("item_id", "Product", "select", items_opts(("fg",))), F("price", "Special price Rs", "number", step="0.01")], "Set price", "Customer-specific price"),
                form(url_for("masters"), [kind("supplier"), F("name", "Supplier name", required=True), F("gstin", "GSTIN"), F("phone", "Phone")], "Add supplier", "Add supplier"))

@app.route("/admin/bom/<int:pid>", methods=["GET", "POST"])
@access("admin")
def bom(pid):
    c = conn(); p = S.get_item(c, pid)
    if request.method == "POST":
        f = request.form
        def go():
            if f["kind"] == "yield":
                c.execute("INSERT OR REPLACE INTO boms VALUES(?,?)", (pid, int(f["yield"])))
            elif f["kind"] == "copy":
                src = int(f["src"]); c.execute("INSERT OR REPLACE INTO boms SELECT ?, yield_packets FROM boms WHERE product_id=?", (pid, src))
                c.execute("DELETE FROM bom_lines WHERE product_id=?", (pid,))
                c.execute("INSERT INTO bom_lines(product_id,item_id,qty_base,basis) SELECT ?,item_id,qty_base,basis FROM bom_lines WHERE product_id=?", (pid, src))
            else:
                it = S.get_item(c, int(f["item_id"]))
                c.execute("INSERT OR REPLACE INTO bom_lines(product_id,item_id,qty_base,basis) VALUES(?,?,?,?)", (pid, it["id"], S.to_base(it, f["qty"], f["unit"]), f["basis"]))
            S.audit(c, me(), "bom_edit", "item", pid, dict(f))
        return act(go, "BOM saved", url_for("bom", pid=pid))
    b = c.execute("SELECT * FROM boms WHERE product_id=?", (pid,)).fetchone()
    rows = [[l["name"], S.fq(l, l["qty_base"]), l["disp_unit"], "per packet" if l["basis"] == "packet" else "per batch"] for l in S.bom_lines(c, pid)]
    others = [(r["id"], r["name"]) for r in c.execute("SELECT id,name FROM items WHERE kind='fg' AND id!=?", (pid,))]
    return page(f"BOM - {p['name']}", html(f"<p>Expected yield per batch: <b>{b['yield_packets'] if b else 'not set'}</b> packets</p>"), table(["Item", "Qty", "Unit", "Basis"], rows),
                form(url_for("bom", pid=pid), [F("kind", "", "hidden", value="line"), F("item_id", "Raw / packaging item", "select", items_opts(("raw", "pack"))), F("qty", "Qty", "number", step="any"), F("unit", "Unit", "select", UNITS),
                                               F("basis", "Basis", "select", [("batch", "per batch"), ("packet", "per packet (e.g. pouch)")])], "Add / replace line", "BOM line"),
                form(url_for("bom", pid=pid), [F("kind", "", "hidden", value="yield"), F("yield", "Packets per batch", "number", value=b["yield_packets"] if b else "")], "Set yield", "Yield"),
                form(url_for("bom", pid=pid), [F("kind", "", "hidden", value="copy"), F("src", "Copy BOM from", "select", others)], "Copy BOM", "Share a BOM (e.g. new pack size)") if others else html(""),
                back=url_for("masters"))

# ------------------------------------------------------------------ production
@app.route("/prod")
@access("production", "admin")
def prod_home():
    c = conn()
    stock = [[s["item"]["name"], s["disp"], s["item"]["disp_unit"], s["avg"], s["value"], Markup('<span class="bad">LOW</span>') if s["low"] else ""] for s in S.stock_rows(c)]
    runs = [[link(r["batch_no"], url_for("run", rid=r["id"])), r["date"], r["pname"], r["expected_output"], r["good_output"] if r["good_output"] is not None else "-", r["status"]] for r in c.execute(
        "SELECT r.*, i.name pname FROM runs r JOIN items i ON i.id=r.product_id ORDER BY r.id DESC LIMIT 15")]
    nav = " ".join(f'<a class="big" href="{url_for(e)}">{t}</a>' for e, t in [("purchase", "+ Purchase"), ("run_new", "+ Production run"), ("adjust", "Stock adjustment")])
    return page("Production", html(f"<p>{nav} <a href='{url_for('report', name='stock_ledger')}'>Stock ledger</a></p>"),
                table(["Item", "Balance", "Unit", "Avg cost / unit", "Value", ""], stock, "Current stock (derived from ledger)"), table(["Batch", "Date", "Product", "Expected", "Good output", "Status"], runs, "Production runs"))

@app.route("/prod/purchase", methods=["GET", "POST"])
@access("production", "admin")
def purchase():
    c = conn()
    if request.method == "POST":
        f = request.form
        ls = [(int(i), q, u, r) for i, q, u, r in parse_lines(f, 5, ["item", "qty", "unit", "rate"])]
        return act(lambda: S.create_purchase(c, me(), int(f["supplier_id"]), f["bill_no"], f["date"], ls), "Purchase recorded; stock increased", url_for("prod_home"))
    sup = [(r["id"], r["name"]) for r in c.execute("SELECT * FROM suppliers")]
    lines = {"n": 4, "fields": [F("item", "Item", "select", [("", "-")] + items_opts(("raw", "pack"))), F("qty", "Qty", "number", step="any"), F("unit", "Unit", "select", UNITS[:5]), F("rate", "Rate Rs per unit", "number", step="any")]}
    return page("Record purchase", form(url_for("purchase"), [F("supplier_id", "Supplier", "select", sup), F("bill_no", "Bill no", required=True), F("date", "Date", "date", value=S.today())], "Save purchase", lines=lines), back=url_for("prod_home"))

@app.route("/prod/run/new", methods=["GET", "POST"])
@access("production", "admin")
def run_new():
    c = conn()
    if request.method == "POST":
        f = request.form
        iss = [(int(k[4:]), f[k], f["unit_" + k[4:]]) for k in f if k.startswith("qty_") and f[k].strip()]
        try:
            rid = S.start_run(c, me(), int(f["product_id"]), f["batches"], iss, f.get("date") or S.today())
            c.commit(); flash("Run started; raw materials issued", "ok")
            return redirect(url_for("run", rid=rid))
        except (S.AppError, ValueError, ArithmeticError) as e:
            c.rollback(); flash(str(e), "err")
            return redirect(url_for("run_new", product_id=f["product_id"], batches=f["batches"]))
    pid, batches = request.args.get("product_id"), request.args.get("batches", "1")
    secs = [form(url_for("run_new"), [F("product_id", "Product", "select", items_opts(("fg",)), value=pid or ""), F("batches", "Batches", "number", step="any", value=batches)], "Load BOM", "Start production run", method="get")]
    if pid:
        B = Decimal(batches); fields = [F("product_id", "", "hidden", value=pid), F("batches", "", "hidden", value=batches), F("date", "Date", "date", value=S.today())]
        for l in S.bom_lines(c, int(pid)):
            if l["kind"] == "raw":
                fields += [F(f"qty_{l['item_id']}", f"Issue {l['name']} (standard {S.fq(l, int(l['qty_base'] * B))} {l['disp_unit']})", "number", step="any", value=S.fq(l, int(l["qty_base"] * B))),
                           F(f"unit_{l['item_id']}", "Unit", "select", UNITS[:4], value=l["disp_unit"])]
        secs.append(form(url_for("run_new"), fields, "Issue materials & start run", "Raw material issue (edit to record the actual quantity issued)"))
    return page("New production run", *secs, back=url_for("prod_home"))

@app.route("/prod/run/<int:rid>")
@access("production", "admin", "owner", "accountant")
def run(rid):
    c = conn(); d = S.run_detail(c, rid); r = d["run"]; secs = []
    secs.append(html(f"<p><b>{r['batch_no']}</b> | {r['product']} | {r['batches']} batch(es) | expected {r['expected_output']} packets | status <b>{r['status']}</b> | expiry {r['expiry']}</p>"))
    mark = lambda f: Markup('<span class="bad">FLAG</span>') if f else Markup('<span class="good">ok</span>')
    rows = [[l["item"], l["unit"], num(l["planned"]), num(l["actual"]), "" if l["var"] is None else f"{l['var']:+}%", mark(l["flag"]) if l["var"] is not None else ""] for l in d["lines"]]
    if d["yield"]: y = d["yield"]; rows.append(["YIELD", "packets", y["expected"], y["actual"], f"{y['var']:+}%", mark(y["flag"])])
    secs.append(table(["Item", "Unit", "Planned", "Actual", "Variance", f"vs tolerance {d['tol']}%"], rows, "Planned vs actual"))
    if d["yield"]: secs.append(html(f"<p>Wastage (expected - good): <b>{d['yield']['wastage']}</b> packets | damaged pouches: <b>{d['yield']['damaged_pouches']}</b></p>"))
    role = session["user"]["role"]
    if r["status"] == "open" and role in ("production", "admin"):
        fields = [F("good", "Good packets produced", "number", step="1", required=True), F("damaged", "Damaged pouches", "number", step="1", value="0")]
        for l in S.bom_lines(c, r["product_id"]):
            if l["kind"] == "pack" and l["basis"] == "batch":
                rl = c.execute("SELECT planned FROM run_lines WHERE run_id=? AND item_id=?", (rid, l["item_id"])).fetchone()
                fields.append(F(f"pack_{l['item_id']}", f"{l['name']} used", "number", step="1", value=rl["planned"]))
        secs.append(form(url_for("run_complete", rid=rid), fields, "Complete run (posts stock)", "Record actual output (pouches = good + damaged are consumed automatically)"))
    if role in ("production", "admin"):
        secs.append(form(url_for("run_cost", rid=rid), [F("kind", "Type", "select", [("labour", "Labour"), ("gas/power", "Gas / power"), ("other", "Other")]), F("amount", "Amount Rs", "number", step="0.01", required=True)], "Add overhead", "Overheads for this run"))
    cs = d["cost"]
    secs.append(table(["Material", "Overheads", "Total", "Cost / packet", "Selling price", "Margin / packet", "Margin %"],
                      [[cs["material"], cs["overhead"], cs["total"], cs["per_packet"], cs["price"], cs["margin"], cs["margin_pct"]]],
                      "Costing: material at moving weighted-average cost; overheads charged 100% to this run's good output"))
    return page("Production run", *secs, back=url_for("prod_home"))

@app.post("/prod/run/<int:rid>/complete")
@access("production", "admin")
def run_complete(rid):
    f = request.form
    return act(lambda: S.complete_run(conn(), me(), rid, f["good"], f.get("damaged") or 0, {int(k[5:]): int(f[k]) for k in f if k.startswith("pack_")}), "Run completed; stock updated", url_for("run", rid=rid))

@app.post("/prod/run/<int:rid>/cost")
@access("production", "admin")
def run_cost(rid): return act(lambda: S.add_overhead(conn(), me(), rid, request.form["kind"], request.form["amount"]), "Overhead added", url_for("run", rid=rid))

@app.route("/prod/adjust", methods=["GET", "POST"])
@access("production", "admin")
def adjust():
    f = request.form
    if request.method == "POST":
        return act(lambda: S.adjust_stock(conn(), me(), int(f["item_id"]), f["qty"], f["unit"], f["reason"], f.get("rate")), "Adjustment posted", url_for("prod_home"))
    return page("Stock adjustment (wastage, count correction)", form(url_for("adjust"), [F("item_id", "Item", "select", items_opts(("raw", "pack", "fg"))), F("qty", "Qty (negative to reduce)", "number", step="any"),
                F("unit", "Unit", "select", UNITS), F("rate", "Rate per unit (only needed to add stock to an empty item)", "number", step="any"), F("reason", "Reason", required=True)], "Post adjustment"), back=url_for("prod_home"))

# ------------------------------------------------------------------ reports, owner, audit
TITLES = {"sales_register": "Sales register", "customer_sales": "Customer-wise sales", "product_sales": "Product-wise sales", "gst_summary": "GST summary (GSTR-1 / 3B shape)",
          "purchase_register": "Purchase register", "stock_summary": "Stock summary", "stock_ledger": "Stock ledger", "production_report": "Production: planned vs actual",
          "cost_report": "Cost per packet", "collections": "Payments & collections", "outstanding": "Outstanding & ageing"}

def _range():
    t = dt.date.today(); return request.args.get("from") or t.replace(day=1).isoformat(), request.args.get("to") or t.isoformat()

@app.route("/reports")
@access("admin", "accountant", "owner", "production")
def report_index():
    d1, d2 = _range()
    names = TITLES if session["user"]["role"] != "production" else {k: v for k, v in TITLES.items() if k in ("stock_summary", "stock_ledger", "production_report", "cost_report", "purchase_register")}
    return page("Reports", html(f"<form method='get' class='row'><label>From <input type='date' name='from' value='{d1}'></label><label>To <input type='date' name='to' value='{d2}'></label><button>Set range</button></form>"),
                table(["Report", "Open", "Excel", "CSV", "PDF"], [[t, *[link(l, url_for("report", name=n, **{"from": d1, "to": d2, "fmt": f})) for l, f in [("view", "html"), ("xlsx", "xlsx"), ("csv", "csv"), ("pdf", "pdf")]]] for n, t in names.items()]))

@app.route("/reports/<name>")
@access("admin", "accountant", "owner", "production")
def report(name):
    if name not in R.REPORTS: abort(404)
    d1, d2 = _range(); title, headers, rows = R.REPORTS[name](conn(), d1, d2)
    fmt = request.args.get("fmt", "html")
    if fmt in X.EXPORTERS:
        fn, ext = X.EXPORTERS[fmt]; data, mime = fn(f"{title} ({d1} to {d2})", headers, rows)
        return Response(data, mimetype=mime, headers={"Content-Disposition": f"attachment; filename={name}_{d1}_{d2}.{ext}"})
    ex = " | ".join(f'<a href="{url_for("report", name=name, **{"from": d1, "to": d2, "fmt": f})}">{f}</a>' for f in ("xlsx", "csv", "pdf"))
    return page(title, html(f"<form method='get' class='row'><label>From <input type='date' name='from' value='{d1}'></label><label>To <input type='date' name='to' value='{d2}'></label><button>Apply</button><span>Export: {ex}</span></form>"),
                table(headers, rows), back=url_for("report_index"))

@app.route("/owner")
@access("owner", "admin")
def owner():
    d = R.dashboard(conn())
    cards = f"<div class='cards'><div><small>Orders today</small><b>{d['orders_today']}</b></div><div><small>Awaiting approval</small><b>{d['pending']}</b></div><div><small>Outstanding dues (Rs)</small><b>{d['outstanding']:,}</b></div></div>"
    return page("Owner dashboard", html(cards), table(["Item", "Balance", "Unit", "Value", ""], [[s["item"]["name"], s["disp"], s["item"]["disp_unit"], s["value"], Markup('<span class="bad">LOW</span>') if s["low"] else ""] for s in d["stock"]], "Stock levels"),
                table(["Batch", "Variance beyond tolerance"], [[link(a, "#"), b] for a, b in d["alerts"]] or [["none", ""]], "Variance alerts"),
                table(["Product", "Material", "Overhead", "Cost / packet", "Price", "Margin %"], [[p, c["material"], c["overhead"], c["per_packet"], c["price"], c["margin_pct"]] for p, c in d["costs"]], "Latest cost per packet"))

@app.route("/admin/audit")
@access("admin", "accountant")
def audit_log():
    rows = [[r["ts"], r["user"], r["action"], f"{r['entity']} #{r['entity_id']}", (r["detail"] or "")[:200]] for r in conn().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200")]
    return page("Audit trail (append-only)", table(["Time", "User", "Action", "Entity", "Detail"], rows))
