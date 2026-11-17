"""CSV / Excel / PDF exporters for report tables, and the GST invoice PDF."""
import csv, io, re
from decimal import Decimal
from . import services as S
from .services import rs

def _s(v): return "" if v is None else str(v)

def to_csv(title, headers, rows):
    b = io.StringIO(); w = csv.writer(b); w.writerow([title]); w.writerow(headers)
    for r in rows: w.writerow([_s(c) for c in r])
    return b.getvalue().encode("utf-8-sig"), "text/csv"

def to_xlsx(title, headers, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = re.sub(r"[\\/*?:\[\]]", "-", title)[:31]
    ws.append(headers)
    for c in ws[1]: c.font = Font(bold=True)
    for r in rows:
        ws.append([float(c) if isinstance(c, Decimal) else c for c in r])
    for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = 16
    b = io.BytesIO(); wb.save(b)
    return b.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def to_pdf(title, headers, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    b = io.BytesIO(); doc = SimpleDocTemplate(b, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    t = Table([headers] + [[_s(c) for c in r] for r in rows], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    st = getSampleStyleSheet()
    doc.build([Paragraph(S.SELLER["name"] + " - " + title, st["Heading3"]), Spacer(1, 6), t])
    return b.getvalue(), "application/pdf"

EXPORTERS = {"csv": (to_csv, "csv"), "xlsx": (to_xlsx, "xlsx"), "pdf": (to_pdf, "pdf")}

def invoice_pdf(conn, invoice_id):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    inv = conn.execute("SELECT i.*, c.name cname, c.gstin cgstin, c.address caddr, c.state cstate FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?", (invoice_id,)).fetchone()
    st = getSampleStyleSheet(); sm = st["BodyText"]; sm.fontSize = 9
    el = [Paragraph("TAX INVOICE" + (" - CANCELLED" if inv["status"] == "cancelled" else ""), st["Title"]),
          Paragraph(f"<b>{S.SELLER['name']}</b><br/>{S.SELLER['address']}<br/>GSTIN: {S.SELLER['gstin']} | State: {S.SELLER['state']} ({S.SELLER['state_code']})", sm), Spacer(1, 8),
          Paragraph(f"<b>Invoice No:</b> {inv['number']} &nbsp;&nbsp; <b>Date:</b> {inv['date']} &nbsp;&nbsp; <b>Place of supply:</b> {inv['cstate']} ({inv['place_code']})", sm),
          Paragraph(f"<b>Bill to:</b> {inv['cname']}<br/>{inv['caddr'] or ''}<br/>GSTIN: {inv['cgstin'] or 'Unregistered'}", sm), Spacer(1, 10)]
    intra = inv["intra"]
    head = ["#", "Description", "HSN", "Qty", "Rate", "Taxable"] + (["CGST", "SGST"] if intra else ["IGST"]) + ["Amount"]
    data = [head]
    for n, l in enumerate(conn.execute("SELECT l.*, i.name FROM invoice_lines l JOIN items i ON i.id=l.item_id WHERE invoice_id=?", (invoice_id,)), 1):
        tax = [f"{rs(l['cgst'])} ({S.D(l['gst_rate'])/2}%)", f"{rs(l['sgst'])} ({S.D(l['gst_rate'])/2}%)"] if intra else [f"{rs(l['igst'])} ({l['gst_rate']}%)"]
        data.append([n, l["name"], l["hsn"], l["qty_base"], rs(l["rate_paise"]), rs(l["taxable"])] + tax + [rs(l["taxable"] + l["cgst"] + l["sgst"] + l["igst"])])
    data.append(["", "Total", "", "", "", rs(inv["taxable"])] + ([rs(inv["cgst"]), rs(inv["sgst"])] if intra else [rs(inv["igst"])]) + [rs(inv["total"])])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    el += [t, Spacer(1, 10), Paragraph(f"<b>Invoice total: Rs. {rs(inv['total'])}</b> (amounts in INR)", sm), Spacer(1, 16),
           Paragraph("Declaration: we declare that this invoice shows the actual price of the goods and that all particulars are true. "
                     "This is a computer-generated invoice. Prototype with dummy data.", sm)]
    b = io.BytesIO(); SimpleDocTemplate(b, pagesize=A4).build(el)
    return b.getvalue()
