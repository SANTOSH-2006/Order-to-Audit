"""End-to-end run of the section-5 benchmark through the service layer + unit tests for the maths."""
from decimal import Decimal as D
import pytest
from app import services as S

def iid(c, code): return c.execute("SELECT id FROM items WHERE code=?", (code,)).fetchone()[0]
def bal(c, code): return S.balance(c, iid(c, code))
def disp(c, code): return S.fq(S.get_item(c, iid(c, code)), bal(c, code)[0])
def cust(c, name): return c.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone()[0]
def uid(c, n): return c.execute("SELECT id FROM users WHERE username=?", (n,)).fetchone()[0]

def run_benchmark(c):
    ctx = {}
    sup = 1
    S.create_purchase(c, "prod", sup, "B-1", "2026-11-10", [(iid(c, "FLOUR"), 60, "kg", 45)])                       # step 1
    rid = S.start_run(c, "prod", iid(c, "MUR200"), 1, [(iid(c, "FLOUR"), 40, "kg"), (iid(c, "OIL"), "10.5", "l"),
                                                         (iid(c, "SPICE"), "1.2", "kg")], "2026-11-10")            # step 2
    S.complete_run(c, "prod", rid, good=188, damaged=6, pack_used={iid(c, "CARTON20"): 9}, date="2026-11-10")       # step 3
    ctx["fg_after_run"] = bal(c, "MUR200")[0]
    oa = S.create_order(c, "salesA", uid(c, "salesA"), cust(c, "Retailer A"), [(iid(c, "MUR200"), 150, "packet")])  # step 4
    S.approve_order(c, "admin", oa)                                                                                 # step 5
    inv = S.make_invoice(c, "admin", oa, "2026-11-10")
    S.dispatch(c, "admin", inv, "2026-11-10")
    ob = S.create_order(c, "salesB", uid(c, "salesB"), cust(c, "Distributor B"), [(iid(c, "MUR200"), 120, "packet")])  # step 6
    ctx.update(rid=rid, oa=oa, inv=inv, ob=ob)
    S.record_payment(c, "admin", cust(c, "Retailer A"), inv, 400000, "2026-11-11")                                  # step 7
    S.add_overhead(c, "prod", rid, "labour", 1200); S.add_overhead(c, "prod", rid, "gas/power", 600)                # step 8
    return ctx

def test_benchmark_numbers(conn):
    c = conn; x = run_benchmark(c)
    assert disp(c, "FLOUR") == "120" and disp(c, "OIL") == "19.5" and disp(c, "SPICE") == "3.8"
    assert disp(c, "POUCH200") == "306" and disp(c, "CARTON20") == "21"
    assert x["fg_after_run"] == 238 and bal(c, "MUR200")[0] == 88
    d = S.run_detail(c, x["rid"])
    assert d["yield"]["var"] == D("-6.00") and d["yield"]["flag"]
    by = {l["item"]: l for l in d["lines"]}
    assert by["Cooking oil"]["var"] == D("5.00") and by["Cooking oil"]["flag"]
    assert not by["Flour"]["flag"] and by["200 g pouch"]["var"] == D("-3.00") and not by["200 g pouch"]["flag"]
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (x["inv"],)).fetchone()
    assert (inv["taxable"], inv["cgst"], inv["sgst"], inv["igst"], inv["total"]) == (750000, 18750, 18750, 0, 787500)
    assert inv["number"] == "INV/2026-27/0001"
    assert S.customer_outstanding(c, cust(c, "Retailer A")) == 387500
    short = [s for s in S.stock_check(c, x["ob"]) if s["short"]]
    assert short[0]["short"] == 32
    with pytest.raises(S.StockError): S.approve_order(c, "admin", x["ob"])
    # costing: (1725 flour WAC + 1470 + 240 + 485 pouches + 135 cartons + 1800 overhead) / 188
    assert d["cost"]["material"] == D("4055.00") and d["cost"]["total"] == D("5855.00") and d["cost"]["per_packet"] == D("31.14")

def test_distributor_gets_igst_after_reducing_order(conn):
    c = conn; x = run_benchmark(c)
    S.reduce_to_available(c, "admin", x["ob"]); S.approve_order(c, "admin", x["ob"])
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (S.make_invoice(c, "admin", x["ob"], "2026-11-12"),)).fetchone()
    assert (inv["cgst"], inv["sgst"], inv["igst"], inv["taxable"]) == (0, 0, 22000, 440000)   # 88 x 50 = 4400 ; 5% = 220

def test_ledger_reconciles_and_is_append_only(conn):
    c = conn; run_benchmark(c)
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError): c.execute("UPDATE stock_ledger SET qty=0 WHERE id=1")
    with pytest.raises(sqlite3.DatabaseError): c.execute("DELETE FROM invoices")
    opening = c.execute("SELECT SUM(qty) FROM stock_ledger WHERE doc_type='opening' AND item_id=?", (iid(c, "FLOUR"),)).fetchone()[0]
    assert opening == 100000                      # opening + receipts - issues == closing
    assert bal(c, "FLOUR")[0] == 100000 + 60000 - 40000

def test_cancel_invoice_issues_credit_note_and_returns_stock(conn):
    c = conn; x = run_benchmark(c)
    with pytest.raises(S.AppError): S.cancel_invoice(c, "admin", x["inv"], "wrong rate")   # has a payment
    c.execute("DELETE FROM payments")
    S.cancel_invoice(c, "admin", x["inv"], "wrong rate", "2026-11-12")
    assert bal(c, "MUR200")[0] == 238
    assert c.execute("SELECT status FROM invoices WHERE id=?", (x["inv"],)).fetchone()[0] == "cancelled"
    assert c.execute("SELECT number FROM credit_notes").fetchone()[0] == "CN/2026-27/0001"

def test_offline_order_is_idempotent(conn):
    c = conn
    a = S.create_order(c, "salesA", 2, 1, [(iid(c, "MUR200"), 10, "packet")], client_uuid="abc")
    b = S.create_order(c, "salesA", 2, 1, [(iid(c, "MUR200"), 10, "packet")], client_uuid="abc")
    assert a == b

def test_unit_conversion_and_no_float_drift(conn):
    c = conn; flour, oil, mur = (S.get_item(c, iid(c, k)) for k in ("FLOUR", "OIL", "MUR200"))
    assert S.to_base(flour, 40, "kg") == 40000 and S.to_base(flour, 250, "g") == 250
    assert S.to_base(oil, "10.5", "l") == 10500 and S.to_base(mur, 2, "carton") == 40
    with pytest.raises(S.AppError): S.to_base(flour, 1, "l")
    with pytest.raises(S.AppError): S.to_base(flour, "0.0001", "g")

def test_gst_maths():
    assert S.compute_tax(750000, "5", True) == (18750, 18750, 0)
    assert S.compute_tax(750000, "5", False) == (0, 0, 37500)
    assert S.compute_tax(10001, "5", True) == (250, 250, 0)       # 500.05 paise/2 -> 250.025 -> 250 each
    assert S.fy_of("2027-03-31") == "2026-27" and S.fy_of("2027-04-01") == "2027-28"

def test_negative_stock_refused(conn):
    c = conn
    with pytest.raises(S.StockError): S.post(c, iid(c, "FLOUR"), -10**9, "adjustment", 0, "x")

def test_ageing_fifo_payment_on_account(conn):
    c = conn; x = run_benchmark(c)
    assert S.open_invoices(c, cust(c, "Retailer A"))[0][1] == 387500
