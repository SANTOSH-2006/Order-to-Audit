# Order-to-Audit: small food manufacturer prototype

One system from a salesman's order to the auditor's monthly report. Every transaction is entered once, at source, and
flows into stock, production, costing and accounts. **Sales loop** and **production loop** meet in one append-only stock ledger.

## Run it

```bash
# Option 1: Docker
docker compose up --build            # http://localhost:5000

# Option 2: local (Python 3.10+)
pip install -r requirements.txt
python run.py                        # http://localhost:5000  (DB auto-created and seeded on first start)

python -m pytest -q                  # 10 tests: stock maths, GST maths, whole benchmark (service layer + over HTTP)
python -m app.benchmark              # replays section 5 on a scratch DB and prints expected vs actual
python -m app.seed --reset           # wipe and reload the section 5 master data
```

| Role | Login | Lands on |
|---|---|---|
| Factory admin | `admin / admin123` | orders, invoices, payments, masters, BOM, reports, audit |
| Salesman A / B | `salesA / sales123`, `salesB / sales123` | mobile order form |
| Production | `prod / prod123` | stock, purchases, runs |
| Accountant (read-only) | `accountant / acc123` | reports, invoices, audit trail |
| Owner | `owner / owner123` | dashboard, reports |

Seed data = section 5 exactly (BOM, opening stock, Retailer A in TN, Distributor B in Karnataka, 5% GST, price ₹50).

## Stack and why

Python 3 + Flask + SQLite (stdlib `sqlite3`), server-rendered HTML, a little vanilla JS, ReportLab (PDF), openpyxl (Excel).
I chose it because the hard part of this problem is the data model and stock maths, not the UI: SQLite gives a real relational
schema, constraints and triggers with zero setup (one command to run), and server-rendered pages work on a weak-network phone
without a JS framework. The order form is a small PWA-style page (localStorage outbox + service worker) for offline capture.
Production use would swap SQLite for Postgres; all SQL is standard apart from `INSERT OR REPLACE` and the trigger syntax.

## What is built (against section 6)

**Must have: all done.** Login with 5 roles and route-level access control. Item/customer/supplier/BOM masters (add + edit BOM, copy BOM
to another product). Mobile order entry and admin approval. GST invoice PDF. Production runs that post raw/packaging out and finished
goods in. Planned-vs-actual and yield variance. Stock summary + ledger. Sales register and stock report exportable to Excel/CSV/PDF.

**Should have: all done.** Purchases and suppliers; payments (against an invoice or on account), outstanding and ageing; cost per
packet with overheads; GST summary shaped like GSTR-1 (B2B/B2C by place of supply, HSN, rate) with a 3B 3.1(a) net row; variance
alerts at a configurable tolerance (default 3%); owner dashboard.

**Stretch done:** offline order capture with idempotent sync · batch number and expiry on every run · reorder levels with LOW flags ·
customer-specific prices · stock reservation by approved-but-undispatched orders · WhatsApp share link for invoices (unguessable token URL).
Not done: FEFO dispatch, production planning, GPS visits, Tally export, e-invoice, Tamil UI.

## Key design choices (details in `DESIGN.md`)

* **Stock = SUM(ledger).** There is no balance column anywhere. `stock_ledger` is append-only (SQLite triggers reject UPDATE/DELETE);
  every row carries `doc_type` + `doc_id` (purchase, production_issue, production_output, invoice, credit_note, adjustment, opening).
* **No floats.** Quantities are integers in a base unit (g, ml, pc); `kg`, `l`, `carton` are conversions at the edge. Document money is
  integer paise; ledger valuation is `Decimal`. GST rounds half-up per invoice line.
* **Valuation: moving weighted average** (justified in `DESIGN.md`). **Overheads:** per-run, charged 100% to that run's good packets.
* **Atomicity.** A service call and every ledger row it creates commit or roll back together; negative stock is refused.
* **Audit.** `audit_log` (append-only) records who/when/what for every create, edit (with before/after for orders), approve, cancel,
  BOM change. Invoices are never deleted: cancel = credit note + stock return at original cost.

## Benchmark results (`python -m app.benchmark`)

| Check | Expected | Mine |
|---|---|---|
| Flour closing | 120 kg | **120 kg** |
| Oil closing | 19.5 L | **19.5 L** |
| Pouches closing | 306 | **306** |
| Finished goods after run | 238 | **238** |
| After Retailer A dispatch | 88 | **88** |
| Yield variance | −6%, flagged | **−6.00%, flagged** |
| Oil variance | +5%, flagged | **+5.00%, flagged** |
| Retailer A invoice | 7,500 + 187.50 + 187.50 = 7,875 | **identical**, `INV/2026-27/0001` |
| Retailer A outstanding | 3,875 | **3,875.00** |
| Distributor B | shortage 32, IGST if invoiced | **shortage 32**, approval blocked; "Reduce to available" then invoice gives IGST ₹220 on 88 packets |
| Cost per packet | own figure | **₹31.14** (margin ₹18.86, 37.7%) |

**Cost per packet derivation (₹31.14):** flour 40 kg × ₹43.125 (weighted avg of 100 kg @ 42 and 60 kg @ 45) = 1,725 · oil 10.5 L × 140 = 1,470 ·
spice 1.2 kg × 200 = 240 · pouches 194 (188 good + 6 damaged) × 2.50 = 485 · cartons 9 × 15 = 135 → material **4,055**; labour 1,200 + gas/power 600 =
**1,800**; total 5,855 ÷ 188 good packets = **31.14**. Damaged pouches and the 12 missing packets are absorbed by the good output, so the
cost reflects real yield. Under FIFO the flour would cost 40 × 42 = 1,680 and the figure would be ₹30.90; I chose weighted average because it
is stable when purchase prices move month to month.

**Differences / things worth knowing (all explained, none hidden):**
* Cartons: 9 used vs 10 standard is −10%, so it is **flagged** too. That is correct per the data (the BOM says 10 per 200 packets).
* Pouches: 194 vs 200 standard is exactly −3.00%; the rule is *strictly greater than* tolerance, so it is **not** flagged.
* Spice closing 3.8 kg and cartons closing 21 are not in the check list but follow from the same data.
* Opening finished goods (50 packets) have no cost in the brief; I assumed ₹30/packet. It only affects COGS, not any checked number.

## Assumptions
* Seller GSTIN, customer GSTINs, addresses are invented. Place of supply = customer's state code; seller is TN (33).
* Murukku HSN 21069099 at 5% (per the brief). Raw materials carry no sales GST fields since they are never sold.
* Price list: one selling price per product; optional per-customer override.
* Approval is blocked when stock is short; the admin can edit quantities, "reduce to available", or reject. Approved-but-undispatched orders reserve stock.
* Per-packet BOM lines (pouches) consume `good + damaged`; per-batch lines (cartons) are entered by the supervisor, defaulting to the BOM.
* Production run is two steps: *issue* (raw materials leave stock) then *complete* (packaging leaves, finished goods arrive).
* Variance tolerance is one global setting (`settings.tolerance_pct`, default 3), applied to every line and to yield.

## Known gaps
* No partial dispatch/returns beyond full-invoice cancellation. No FEFO (expiry is recorded, not enforced).
* Overheads are not capitalised into finished-goods stock value (stock valued at material cost); the cost report shows full cost.
* Purchase GST / input credit not tracked. No CSRF tokens (cookie is `SameSite=Lax`); no password reset; seed passwords are demo-only.
* Completed production runs cannot be edited, only corrected with a stock adjustment (audited). Ledger entries cannot be edited by design.
* Excel exports write numbers as numbers; PDF reports are plain tables. SQLite single-writer is fine for this scale (5-15 salesmen).
* **Not included (I cannot produce these from here):** the demo video and a hosted URL. `DEMO.md` is a step-by-step script that follows section 5
  in order, and `docker compose up` gives a local instance in one command.
