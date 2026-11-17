"""Replays the section-5 scenario on a fresh database and prints expected-vs-actual.
    python -m app.benchmark          (uses a temp DB; your real data is untouched)"""
import os, tempfile
from decimal import Decimal as D
from . import db, seed, services as S

def main():
    c = db.connect(os.path.join(tempfile.mkdtemp(), "bench.db")); db.init(c); seed.seed(c)
    I = lambda code: c.execute("SELECT id FROM items WHERE code=?", (code,)).fetchone()[0]
    C = lambda n: c.execute("SELECT id FROM customers WHERE name=?", (n,)).fetchone()[0]
    disp = lambda code: S.fq(S.get_item(c, I(code)), S.balance(c, I(code))[0])
    S.create_purchase(c, "prod", 1, "B-1", "2026-11-10", [(I("FLOUR"), 60, "kg", 45)])
    rid = S.start_run(c, "prod", I("MUR200"), 1, [(I("FLOUR"), 40, "kg"), (I("OIL"), "10.5", "l"), (I("SPICE"), "1.2", "kg")], "2026-11-10")
    S.complete_run(c, "prod", rid, 188, 6, {I("CARTON20"): 9}, "2026-11-10")
    fg_run = disp("MUR200")
    oa = S.create_order(c, "salesA", 2, C("Retailer A"), [(I("MUR200"), 150, "packet")]); S.approve_order(c, "admin", oa)
    inv = S.make_invoice(c, "admin", oa, "2026-11-10"); S.dispatch(c, "admin", inv, "2026-11-10")
    ob = S.create_order(c, "salesB", 3, C("Distributor B"), [(I("MUR200"), 120, "packet")])
    S.record_payment(c, "admin", C("Retailer A"), inv, 400000, "2026-11-11")
    S.add_overhead(c, "prod", rid, "labour", 1200); S.add_overhead(c, "prod", rid, "gas/power", 600)
    d = S.run_detail(c, rid); i = c.execute("SELECT * FROM invoices WHERE id=?", (inv,)).fetchone()
    by = {l["item"]: l for l in d["lines"]}
    short = [s for s in S.stock_check(c, ob) if s["short"]][0]["short"]
    rows = [("Flour closing (kg)", "120", disp("FLOUR")), ("Oil closing (L)", "19.5", disp("OIL")), ("Pouches closing", "306", disp("POUCH200")),
            ("FG after run", "238", fg_run), ("FG after Retailer A dispatch", "88", disp("MUR200")),
            ("Yield variance %", "-6 flagged", f"{d['yield']['var']} {'flagged' if d['yield']['flag'] else ''}"),
            ("Oil variance %", "+5 flagged", f"{by['Cooking oil']['var']} {'flagged' if by['Cooking oil']['flag'] else ''}"),
            ("Invoice taxable/CGST/SGST/total", "7500/187.50/187.50/7875", f"{S.rs(i['taxable'])}/{S.rs(i['cgst'])}/{S.rs(i['sgst'])}/{S.rs(i['total'])}"),
            ("Retailer A outstanding", "3875", str(S.rs(S.customer_outstanding(c, C("Retailer A"))))),
            ("Distributor B shortage", "32", str(short)), ("Cost per packet (Rs)", "(own figure)", str(d["cost"]["per_packet"]))]
    for k, e, a in rows: print(f"{k:38} expected {e:26} actual {a}")

if __name__ == "__main__": main()
