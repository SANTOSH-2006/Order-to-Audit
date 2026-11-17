# Demo script (section 5, in order, about 7 minutes)

Start: `docker compose up --build` or `python run.py`, open http://localhost:5000. Use a phone-width window for steps 4 and 6.

1. Login `prod/prod123` → **+ Purchase** → Kongu Flour Mills, Flour 60 kg @ 45 → Save. (Flour now 160 kg.)
2. **+ Production run** → Murukku 200 g, 1 batch → *Load BOM* → set Oil to **10.5** → *Issue materials & start run*.
3. On the run page enter good packets **188**, damaged pouches **6**, cartons used **9** → *Complete run*. Show: flour 120, oil 19.5, pouches 306, FG 238; yield −6% FLAG, oil +5% FLAG.
4. Logout, login `salesA/sales123` (phone view) → **New** → Retailer A → 150 packets → Submit. Optionally toggle browser offline first to show the outbox.
5. Login `admin/admin123` → Orders → open ORD-00001 (stock check OK, CGST+SGST) → **Approve** → **Raise GST invoice** → **Record dispatch**. Open the invoice PDF (₹7,875). FG now 88.
6. Login `salesB/sales123` → Distributor B → 120 packets. Back as admin: order shows **SHORT by 32**, IGST noted, Approve is refused. Click *Reduce to available stock* to show the alternative.
7. Admin → Payments → against INV/…/0001 → ₹4,000 → outstanding ₹3,875.
8. `prod` → open the run → overheads: Labour 1200, Gas/power 600 → cost per packet **₹31.14**, margin ₹18.86.
9. Admin → Reports → open each, export Excel/CSV/PDF; show Stock ledger tracing every movement to its document, and the Audit page. Log in as `accountant` (read-only) and `owner` (dashboard with variance alerts).
