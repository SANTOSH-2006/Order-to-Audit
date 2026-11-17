"""SQLite schema + connection helper.

Design rules enforced here:
  * stock_ledger is APPEND-ONLY (triggers block UPDATE/DELETE). Balances are always SUM(ledger).
  * Money on documents is integer paise. Ledger valuation is Decimal (TEXT) because weighted
    average cost produces fractions of a paisa.
  * Posted invoices cannot be deleted (trigger); they are cancelled via credit note.
"""
import os, sqlite3

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "factory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, name TEXT, role TEXT, pw_hash TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, kind TEXT CHECK(kind IN('raw','pack','fg')),
  base_unit TEXT, disp_unit TEXT, disp_factor INTEGER DEFAULT 1, carton_size INTEGER,
  hsn TEXT, gst_rate TEXT DEFAULT '0', price_paise INTEGER DEFAULT 0, reorder_base INTEGER DEFAULT 0, shelf_life_days INTEGER);
CREATE TABLE IF NOT EXISTS boms(product_id INTEGER PRIMARY KEY REFERENCES items(id), yield_packets INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bom_lines(
  id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES items(id), item_id INTEGER REFERENCES items(id),
  qty_base INTEGER NOT NULL, basis TEXT DEFAULT 'batch' CHECK(basis IN('batch','packet')), UNIQUE(product_id,item_id));
CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT, gstin TEXT, phone TEXT);
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT, type TEXT, gstin TEXT, state TEXT, state_code TEXT, address TEXT, phone TEXT, salesman_id INTEGER);
CREATE TABLE IF NOT EXISTS customer_prices(customer_id INTEGER, item_id INTEGER, price_paise INTEGER, PRIMARY KEY(customer_id,item_id));
CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY, supplier_id INTEGER, bill_no TEXT, date TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS purchase_lines(id INTEGER PRIMARY KEY, purchase_id INTEGER, item_id INTEGER, qty_base INTEGER, value TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, product_id INTEGER, date TEXT, batches TEXT, expected_output INTEGER, good_output INTEGER,
  damaged INTEGER DEFAULT 0, status TEXT DEFAULT 'open', material_cost TEXT, batch_no TEXT, expiry TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS run_lines(id INTEGER PRIMARY KEY, run_id INTEGER, item_id INTEGER, planned INTEGER, actual INTEGER, basis TEXT);
CREATE TABLE IF NOT EXISTS run_costs(id INTEGER PRIMARY KEY, run_id INTEGER, kind TEXT, amount TEXT, date TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS stock_ledger(
  id INTEGER PRIMARY KEY, date TEXT, ts TEXT, item_id INTEGER REFERENCES items(id), qty INTEGER NOT NULL, value TEXT NOT NULL,
  doc_type TEXT NOT NULL, doc_id INTEGER, note TEXT, user TEXT);
CREATE INDEX IF NOT EXISTS ix_ledger_item ON stock_ledger(item_id);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, no TEXT, customer_id INTEGER, salesman_id INTEGER, status TEXT, created TEXT,
  client_uuid TEXT UNIQUE, reject_reason TEXT);
CREATE TABLE IF NOT EXISTS order_lines(id INTEGER PRIMARY KEY, order_id INTEGER, item_id INTEGER, qty_base INTEGER, price_paise INTEGER);
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY, number TEXT UNIQUE, fy TEXT, order_id INTEGER, customer_id INTEGER, date TEXT, place_code TEXT,
  intra INTEGER, taxable INTEGER, cgst INTEGER, sgst INTEGER, igst INTEGER, total INTEGER, status TEXT DEFAULT 'posted', token TEXT);
CREATE TABLE IF NOT EXISTS invoice_lines(
  id INTEGER PRIMARY KEY, invoice_id INTEGER, item_id INTEGER, hsn TEXT, qty_base INTEGER, rate_paise INTEGER, taxable INTEGER,
  gst_rate TEXT, cgst INTEGER, sgst INTEGER, igst INTEGER, cogs TEXT);
CREATE TABLE IF NOT EXISTS credit_notes(
  id INTEGER PRIMARY KEY, number TEXT UNIQUE, fy TEXT, invoice_id INTEGER, date TEXT, reason TEXT,
  taxable INTEGER, cgst INTEGER, sgst INTEGER, igst INTEGER, total INTEGER, user TEXT);
CREATE TABLE IF NOT EXISTS dispatches(id INTEGER PRIMARY KEY, invoice_id INTEGER UNIQUE, date TEXT, note TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, customer_id INTEGER, invoice_id INTEGER, amount INTEGER, date TEXT, mode TEXT, ref TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS seq(kind TEXT, fy TEXT, last INTEGER, PRIMARY KEY(kind,fy));
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, ts TEXT, user TEXT, action TEXT, entity TEXT, entity_id INTEGER, detail TEXT);

CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON stock_ledger BEGIN SELECT RAISE(ABORT,'stock_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON stock_ledger BEGIN SELECT RAISE(ABORT,'stock_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS inv_no_delete BEFORE DELETE ON invoices BEGIN SELECT RAISE(ABORT,'invoices cannot be deleted; cancel with a credit note'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log is append-only'); END;
"""

def connect(path=None):
    path = path or os.environ.get("DB_PATH") or DEFAULT_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()
