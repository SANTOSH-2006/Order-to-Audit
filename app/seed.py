"""Loads the section-5 master data. Run:  python -m app.seed [--reset]"""
import os, sys
from werkzeug.security import generate_password_hash
from . import db, services as S

USERS = [("admin", "Factory Admin", "admin", "admin123"), ("salesA", "Salesman A", "salesman", "sales123"),
         ("salesB", "Salesman B", "salesman", "sales123"), ("prod", "Production Supervisor", "production", "prod123"),
         ("accountant", "Accountant", "accountant", "acc123"), ("owner", "Owner", "owner", "owner123")]

def seed(conn, date=None):
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone(): return False
    for u, n, r, p in USERS:
        conn.execute("INSERT INTO users(username,name,role,pw_hash) VALUES(?,?,?,?)", (u, n, r, generate_password_hash(p)))
    conn.execute("INSERT INTO settings VALUES('tolerance_pct','3')")
    it = lambda *a: conn.execute("INSERT INTO items(code,name,kind,base_unit,disp_unit,disp_factor,carton_size,hsn,gst_rate,price_paise,reorder_base,shelf_life_days) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", a).lastrowid
    flour = it("FLOUR", "Flour", "raw", "g", "kg", 1000, None, "1101", "0", 0, 50000, None)
    oil = it("OIL", "Cooking oil", "raw", "ml", "l", 1000, None, "1512", "0", 0, 10000, None)
    spice = it("SPICE", "Salt and spice mix", "raw", "g", "kg", 1000, None, "0910", "0", 0, 2000, None)
    pouch = it("POUCH200", "200 g pouch", "pack", "pc", "pc", 1, None, "3923", "0", 0, 200, None)
    carton = it("CARTON20", "Carton (holds 20 packets)", "pack", "pc", "pc", 1, None, "4819", "0", 0, 10, None)
    mur = it("MUR200", "Murukku 200 g", "fg", "pc", "packet", 1, 20, "21069099", "5", 5000, 0, 90)
    conn.execute("INSERT INTO boms VALUES(?,?)", (mur, 200))
    for item, qty, basis in [(flour, 40000, "batch"), (oil, 10000, "batch"), (spice, 1200, "batch"), (pouch, 200, "packet"), (carton, 10, "batch")]:
        conn.execute("INSERT INTO bom_lines(product_id,item_id,qty_base,basis) VALUES(?,?,?,?)", (mur, item, qty, basis))
    d = date or "2000-04-01"
    # Opening stock goes through the ledger like everything else (doc_type='opening').
    for item, q, unit, rate in [(flour, 100, "kg", 42), (oil, 30, "l", 140), (spice, 5, "kg", 200),
                                (pouch, 500, "pc", "2.50"), (carton, 30, "pc", 15), (mur, 50, "packet", 30)]:
        S.post(conn, item, S.to_base(S.get_item(conn, item), q, unit), "opening", 0, "seed", "Opening balance", S.D(q) * S.D(rate), d)
    conn.execute("INSERT INTO suppliers(name,gstin,phone) VALUES('Kongu Flour Mills','33DDDDD3333D1Z5','9000000001')")
    conn.execute("INSERT INTO suppliers(name,gstin,phone) VALUES('Madurai Oil Traders','33EEEEE4444E1Z5','9000000002')")
    sa = conn.execute("SELECT id FROM users WHERE username='salesA'").fetchone()[0]
    sb = conn.execute("SELECT id FROM users WHERE username='salesB'").fetchone()[0]
    conn.execute("INSERT INTO customers(name,type,gstin,state,state_code,address,phone,salesman_id) VALUES(?,?,?,?,?,?,?,?)",
                 ("Retailer A", "retailer", "33BBBBB1111B1Z5", "Tamil Nadu", "33", "12 Big Bazaar St, Tiruchirappalli", "9000000011", sa))
    conn.execute("INSERT INTO customers(name,type,gstin,state,state_code,address,phone,salesman_id) VALUES(?,?,?,?,?,?,?,?)",
                 ("Distributor B", "distributor", "29CCCCC2222C1Z5", "Karnataka", "29", "5 Market Rd, Bengaluru", "9000000022", sb))
    conn.commit()
    return True

if __name__ == "__main__":
    path = os.environ.get("DB_PATH") or db.DEFAULT_PATH
    if "--reset" in sys.argv and os.path.exists(path): os.remove(path)
    c = db.connect(); db.init(c)
    print("seeded" if seed(c) else "already seeded (use --reset)")
