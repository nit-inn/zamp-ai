"""Generate realistic test invoice PDFs (happy path + edge cases) into samples/.

Four vendor layouts with different labels, date formats and styling, so the
extractor can't rely on one template. E3 is rendered as a noisy, slightly
rotated *image-only* PDF to simulate a scanned invoice.

Run:  python generate_invoices.py
"""
from __future__ import annotations

import io
import json
import random
from datetime import date
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).parent / "samples"
BUYER = "Zamp Demo Corp<br/>500 Market Street, Suite 400<br/>San Francisco, CA 94105"

VENDORS = {
    "acme": dict(name="ACME Office Supplies", addr="221 Industrial Pkwy, Newark, NJ 07105",
                 tax_id="12-3456789", bank="004512345678", routing="021000021", tax_rate=0.08),
    "brightline": dict(name="Brightline Logistics LLC", addr="88 Freight Way, Chicago, IL 60607",
                       tax_id="34-5678901", bank="007788990011", routing="026009593", tax_rate=0.0),
    "nimbus": dict(name="Nimbus Cloud Services Inc", addr="1 Cloud Plaza, Seattle, WA 98101",
                   tax_id="56-7890123", bank="009911223344", routing="121000358", tax_rate=0.08),
    "quantum": dict(name="Quantum Electricals Corp", addr="47 Voltage Ave, Boston, MA 02118",
                    tax_id="78-9012345", bank="001122334455", routing="011000138", tax_rate=0.08),
    "orbit": dict(name="Orbit Facility Services LLC", addr="12 Harbor Blvd, Oakland, CA 94607",
                  tax_id="23-4567890", bank="003344556677", routing="031000053", tax_rate=0.08),
}

# Each layout: (title, invoice-no label, date label, PO label, date format, accent colour)
LAYOUTS = {
    "A": ("INVOICE", "Invoice No:", "Invoice Date:", "PO Number:", "%Y-%m-%d", colors.HexColor("#1f4e79")),
    "B": ("TAX INVOICE", "Invoice #", "Date", "Purchase Order", "%d %b %Y", colors.HexColor("#7a1f1f")),
    "C": ("BILL", "Bill No.", "Billing Date", "Customer PO Ref", "%m/%d/%Y", colors.HexColor("#2e6b3a")),
    "D": ("INVOICE", "Invoice Number", "Dated", "Your PO", "%b %d, %Y", colors.HexColor("#444444")),
}

SCENARIOS = [
    dict(file="H1_brightline_happy_path.pdf", scenario="H1 - Happy path",
         expect="APPROVED", order=1,
         explain="Clean invoice, explicit PO, amounts match exactly.",
         vendor="brightline", layout="A", number="BL-2026-0418", date=date(2026, 11, 2),
         po="PO-1002", lines=[("Full truckload freight Chicago to Dallas", 4, 1150.00),
                              ("Pallet packaging and shrink wrap", 1, 400.00)]),
    dict(file="E1a_acme_split_po_part1.pdf", scenario="E1 - Split PO (part 1 of 3)",
         expect="APPROVED", order=2,
         explain="First of three invoices against PO-1001 ($10,000). Bills $5,000.",
         vendor="acme", layout="B", number="ACM-2041", date=date(2026, 10, 10),
         po="PO-1001", lines=[("Ergonomic office chair", 20, 250.00)]),
    dict(file="E1b_acme_split_po_part2.pdf", scenario="E1 - Split PO (part 2 of 3)",
         expect="APPROVED", order=3,
         explain="Second invoice against PO-1001. Bills $4,000; $1,000 left on the PO.",
         vendor="acme", layout="B", number="ACM-2042", date=date(2026, 10, 24),
         po="PO-1001", lines=[("Height-adjustable standing desk", 10, 400.00)]),
    dict(file="E1c_acme_split_po_overbilled.pdf", scenario="E1 - Split PO (part 3, over-billed)",
         expect="NEEDS_REVIEW", order=4,
         explain="Third invoice bills 30 monitor arms ($1,500) but the PO allows 20 and only $1,000 remains.",
         vendor="acme", layout="B", number="ACM-2043", date=date(2026, 11, 5),
         po="PO-1001", lines=[("Dual monitor arm", 30, 50.00)]),
    dict(file="E2a_nimbus_original.pdf", scenario="E2 - Duplicate (original)",
         expect="APPROVED", order=5,
         explain="Original Nimbus invoice NCS-1042 for Jul-Sep hosting.",
         vendor="nimbus", layout="C", number="NCS-1042", date=date(2026, 10, 1),
         po="PO-1003", lines=[("Managed cloud hosting (monthly) - Jul to Sep 2026", 3, 1000.00)]),
    dict(file="E2b_nimbus_resubmitted.pdf", scenario="E2 - Duplicate (resubmitted)",
         expect="REJECTED", order=6,
         explain="Same invoice resubmitted as 'NCS1042' with a new date. Not the same file, so a hash check alone would miss it.",
         vendor="nimbus", layout="C", number="NCS1042", date=date(2026, 10, 20),
         po="PO-1003", lines=[("Managed cloud hosting (monthly) - Jul to Sep 2026", 3, 1000.00)],
         note="Reminder: payment pending. Please process at the earliest."),
    dict(file="E3_quantum_scanned_no_po.pdf", scenario="E3 - Scanned, no PO number",
         expect="NEEDS_REVIEW", order=7,
         explain="Scanned image with no PO reference. Needs AI vision to read it; the PO is inferred and suggested, never assumed.",
         vendor="quantum", layout="D", number="QE-5531", date=date(2026, 11, 3),
         po=None, po_ref="PO-1004",  # not printed on the invoice; the PO it really belongs to
         lines=[("LED panel light 2x2 ft", 40, 45.00), ("Installation labour", 1, 700.00)],
         scanned=True),
    dict(file="E4_orbit_bank_changed.pdf", scenario="E4 - Bank details changed",
         expect="ON_HOLD", order=8,
         explain="Everything matches the PO, but the bank account differs from the vendor master: classic payment-fraud pattern.",
         vendor="orbit", layout="A", number="OFS-3310", date=date(2026, 11, 1),
         po="PO-1006", lines=[("Housekeeping services (monthly)", 3, 1500.00)],
         bank_override=("008877665544", "026013673"),
         note="IMPORTANT: Our bank details have changed. Please remit to the new account below with immediate effect."),
    dict(file="T1_quantum_within_tolerance.pdf", scenario="T1 - Small overage within tolerance",
         expect="APPROVED", order=9,
         explain="Adds a $40 freight charge not on the PO. Within tolerance (min of 2% / $50), so approved with a note.",
         vendor="quantum", layout="D", number="QE-5560", date=date(2026, 11, 6),
         po="PO-1005", lines=[("Cat6 structured cabling (per metre)", 500, 12.00),
                              ("Freight and handling", 1, 40.00)]),
]


def money(x: float) -> str:
    return f"${x:,.2f}"


def build_pdf(s: dict) -> bytes:
    v = VENDORS[s["vendor"]]
    title, no_lbl, date_lbl, po_lbl, dfmt, accent = LAYOUTS[s["layout"]]
    bank, routing = s.get("bank_override") or (v["bank"], v["routing"])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Title"], textColor=accent, alignment=2 if s["layout"] in "BD" else 0)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13)
    bold = ParagraphStyle("bb", parent=body, fontName="Helvetica-Bold", fontSize=13, leading=16)

    story = []
    vendor_block = Paragraph(f"<b>{v['name']}</b><br/>{v['addr']}<br/>Tax ID (EIN): {v['tax_id']}", body)
    meta_rows = [[no_lbl, s["number"]], [date_lbl, s["date"].strftime(dfmt)]]
    if s["po"]:
        meta_rows.append([po_lbl, s["po"]])
    meta_rows.append(["Currency", "USD"])
    meta = Table(meta_rows, colWidths=[38 * mm, 40 * mm])
    meta.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
                              ("FONT", (1, 0), (1, -1), "Helvetica", 9.5)]))

    if s["layout"] in "BD":
        story += [Paragraph(title, h), Spacer(1, 4)]
        story.append(Table([[vendor_block, meta]], colWidths=[95 * mm, 80 * mm]))
    else:
        story += [Paragraph(v["name"], bold), Paragraph(f"{v['addr']}<br/>Tax ID (EIN): {v['tax_id']}", body),
                  Spacer(1, 6), Paragraph(title, h), meta]
    story += [Spacer(1, 10), Paragraph(f"<b>Bill To</b><br/>{BUYER}", body), Spacer(1, 12)]

    rows = [["#", "Description", "Qty", "Unit Price", "Amount"]]
    subtotal = 0.0
    for i, (desc, qty, price) in enumerate(s["lines"], 1):
        amt = round(qty * price, 2)
        subtotal += amt
        rows.append([str(i), desc, str(qty), money(price), money(amt)])
    tax = round(subtotal * v["tax_rate"], 2)
    total = round(subtotal + tax, 2)
    rows += [["", "", "", "Subtotal", money(subtotal)],
             ["", "", "", f"Sales Tax ({v['tax_rate'] * 100:.0f}%)", money(tax)],
             ["", "", "", "TOTAL DUE", money(total)]]
    t = Table(rows, colWidths=[10 * mm, 82 * mm, 16 * mm, 32 * mm, 30 * mm])
    n = len(s["lines"])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), accent), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
        ("GRID", (0, 0), (-1, n), 0.5, colors.grey),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("FONT", (3, -1), (-1, -1), "Helvetica-Bold", 10.5),
        ("LINEABOVE", (3, -1), (-1, -1), 1, accent),
    ]))
    story += [t, Spacer(1, 16)]
    if s.get("note"):
        story += [Paragraph(f"<b>Note:</b> {s['note']}", body), Spacer(1, 8)]
    story.append(Paragraph(
        f"<b>Payment details</b><br/>Bank Account: {bank}<br/>Routing Number: {routing}<br/>"
        f"Terms: Net 30. Please quote the invoice number with your payment.", body))
    doc.build(story)
    return buf.getvalue()


def make_scanned(pdf_bytes: bytes) -> bytes:
    """Render to a noisy, slightly rotated greyscale image and wrap it back in a PDF (no text layer)."""
    rnd = random.Random(42)
    page = fitz.open(stream=pdf_bytes, filetype="pdf")[0]
    pix = page.get_pixmap(dpi=130)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    img = img.rotate(1.3, expand=False, fillcolor=245, resample=Image.BICUBIC)
    px = img.load()
    for _ in range(img.width * img.height // 60):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice([90, 140, 200])
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    out = io.BytesIO()
    img.save(out, "PDF", resolution=130)
    return out.getvalue()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    manifest = []
    for s in SCENARIOS:
        data = build_pdf(s)
        if s.get("scanned"):
            data = make_scanned(data)
        (OUT / s["file"]).write_bytes(data)
        manifest.append({**{k: s[k] for k in ("file", "scenario", "expect", "explain", "order")},
                         "po": s.get("po") or s.get("po_ref")})
        print(f"  wrote {s['file']}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{len(manifest)} invoices + manifest.json in {OUT}")


if __name__ == "__main__":
    main()
