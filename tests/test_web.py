"""Drives the benchmark through real HTTP routes, as each role would."""
import os, importlib, pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "w.db"))
    from app import web; importlib.reload(web)
    web.app.config["TESTING"] = True
    return web.app.test_client()

def login(c, u, p): return c.post("/login", data={"username": u, "password": p}, follow_redirects=True)

def test_full_benchmark_over_http(client):
    c = client
    # --- production supervisor
    login(c, "prod", "prod123")
    assert c.post("/prod/purchase", data={"supplier_id": 1, "bill_no": "B1", "date": "2026-11-10", "item_0": 1, "qty_0": 60, "unit_0": "kg", "rate_0": 45}).status_code == 302
    r = c.post("/prod/run/new", data={"product_id": 6, "batches": 1, "qty_1": 40, "unit_1": "kg", "qty_2": "10.5", "unit_2": "l", "qty_3": "1.2", "unit_3": "kg"})
    assert r.status_code == 302 and r.location.endswith("/run/1")
    c.post("/prod/run/1/complete", data={"good": 188, "damaged": 6, "pack_5": 9})
    c.post("/prod/run/1/cost", data={"kind": "labour", "amount": "1200"}); c.post("/prod/run/1/cost", data={"kind": "gas/power", "amount": "600"})
    page = c.get("/prod/run/1").get_data(as_text=True)
    assert "31.14" in page and "-6.00%" in page and "+5.00%" in page
    assert c.get("/admin/orders").status_code == 403            # role isolation
    c.get("/logout")
    # --- salesman A on mobile API, with offline-retry idempotency
    login(c, "salesA", "sales123")
    body = {"client_uuid": "u-1", "customer_id": 1, "lines": [{"item_id": 6, "qty": 150, "unit": "packet"}]}
    a = c.post("/api/orders", json=body).get_json(); b = c.post("/api/orders", json=body).get_json()
    assert a["ok"] and a["order_no"] == b["order_no"] == "ORD-00001"
    assert c.get("/api/catalog").get_json()["items"][0]["price"] == 5000
    assert c.get("/admin/invoices").status_code == 403
    c.get("/logout")
    # --- admin
    login(c, "admin", "admin123")
    for a_ in ("approve", "invoice", "dispatch"): c.post(f"/admin/orders/1/{a_}")
    inv = c.get("/admin/invoices/1").get_data(as_text=True)
    assert "7875.00" in inv or "7,875.00" in inv
    pdf = c.get("/admin/invoices/1.pdf"); assert pdf.data[:4] == b"%PDF"
    c.post("/admin/payments", data={"invoice_id": 1, "amount": "4000", "date": "2026-11-11", "mode": "upi"})
    assert "3875.00" in c.get("/admin/payments").get_data(as_text=True) or "3,875.00" in c.get("/admin/payments").get_data(as_text=True)
    c.get("/logout")
    # --- salesman B: shortage
    login(c, "salesB", "sales123")
    c.post("/api/orders", json={"client_uuid": "u-2", "customer_id": 2, "lines": [{"item_id": 6, "qty": 120, "unit": "packet"}]})
    c.get("/logout"); login(c, "admin", "admin123")
    o = c.get("/admin/orders/2").get_data(as_text=True)
    assert "SHORT by 32" in o and "IGST" in o
    c.post("/admin/orders/2/approve", follow_redirects=True)
    from app import db; cn = db.connect(); assert cn.execute("SELECT status FROM orders WHERE id=2").fetchone()[0] == "submitted"
    # --- reports in every format
    for name in ("sales_register", "gst_summary", "stock_ledger", "production_report", "outstanding", "cost_report", "purchase_register"):
        for fmt, sig in (("csv", b""), ("xlsx", b"PK"), ("pdf", b"%PDF")):
            r = c.get(f"/reports/{name}?from=2026-11-01&to=2026-11-30&fmt={fmt}"); assert r.status_code == 200 and r.data.startswith(sig)
    c.get("/logout"); login(c, "accountant", "acc123")
    assert c.get("/reports/sales_register").status_code == 200 and c.post("/admin/orders/2/approve").status_code == 403
    c.get("/logout"); login(c, "owner", "owner123")
    assert "Variance alerts" in c.get("/owner").get_data(as_text=True)
