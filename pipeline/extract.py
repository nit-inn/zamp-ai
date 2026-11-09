"""Stage 2: turn a PDF into structured fields.

Primary: Gemini reads the PDF directly (works for text AND scanned PDFs).
Fallback: pdfplumber + regex, used automatically when Gemini is unavailable.
The extractor only *reads*. It never decides anything.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from io import BytesIO

import pdfplumber

from .config import gemini_key, gemini_models

FIELDS = ["vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "po_number",
          "currency", "subtotal", "tax", "total", "bank_account", "routing_number"]

PROMPT = """You are an accounts-payable data extraction engine. Read the attached vendor invoice
and return ONLY a JSON object with exactly these keys:

{
  "vendor_name": string|null,          // the SELLER issuing the invoice, not the "Bill To" customer
  "vendor_tax_id": string|null,
  "invoice_number": string|null,       // exactly as printed, keep dashes/spaces
  "invoice_date": "YYYY-MM-DD"|null,
  "due_date": "YYYY-MM-DD"|null,
  "po_number": string|null,            // ONLY if a purchase order reference is printed. Never guess.
  "currency": string|null,             // ISO code, e.g. USD
  "line_items": [{"description": string, "quantity": number, "unit_price": number, "amount": number}],
  "subtotal": number|null,
  "tax": number|null,
  "total": number|null,
  "bank_account": string|null,
  "routing_number": string|null,
  "notes": string|null,                // any free-text remarks printed on the invoice, verbatim
  "confidence": {"<each key above>": number between 0 and 1}
}

Rules:
- If a field is not on the document, use null. Do not infer or invent values.
- Numbers must be plain numbers (no currency symbols or thousands separators).
- confidence reflects how clearly the value is printed (lower it for blurry or ambiguous text).
"""


class ExtractionError(Exception):
    pass


def text_layer(pdf_bytes: bytes) -> str:
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""


def _num(x) -> float | None:
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^\d.\-]", "", str(x))
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d-%m-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _clean(data: dict) -> dict:
    out = {k: data.get(k) for k in FIELDS}
    for k in ("subtotal", "tax", "total"):
        out[k] = _num(out[k])
    for k in ("invoice_date", "due_date"):
        out[k] = parse_date(out[k]) or out[k]
    out["line_items"] = [
        {"description": str(li.get("description") or "").strip(),
         "quantity": _num(li.get("quantity")), "unit_price": _num(li.get("unit_price")),
         "amount": _num(li.get("amount"))}
        for li in (data.get("line_items") or [])]
    out["notes"] = data.get("notes")
    conf = data.get("confidence") or {}
    out["confidence"] = {k: float(conf.get(k, 0.0 if out.get(k) in (None, "") else 0.9)) for k in FIELDS}
    return out


# ---------- primary: Gemini ----------

def extract_gemini(pdf_bytes: bytes, model: str) -> dict:
    key = gemini_key()
    if not key:
        raise ExtractionError("no GEMINI_API_KEY configured")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=60_000, retry_options=types.HttpRetryOptions(attempts=2)))
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), PROMPT],
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
    )
    raw = (resp.text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
    try:
        return _clean(json.loads(raw))
    except json.JSONDecodeError as e:
        raise ExtractionError(f"model returned invalid JSON: {e}") from e


def _short(e: Exception) -> str:
    msg = str(e)
    m = re.search(r"'message': '([^']+)'", msg)
    return f"{type(e).__name__}: {(m.group(1) if m else msg)[:140]}"


# ---------- fallback: rules on the text layer ----------

LABELS = {
    "invoice_number": r"(?:Invoice No:|Invoice #|Bill No\.|Invoice Number)\s*([A-Z0-9][A-Z0-9\-/]*)",
    "invoice_date": r"(?:Invoice Date:|Billing Date|Dated|\bDate)\s+([A-Za-z]{3,9} \d{1,2}, \d{4}|\d{1,2} [A-Za-z]{3,9} \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
    "po_number": r"(?:PO Number:|Purchase Order|Customer PO Ref|Your PO)\s*(PO-?\d{3,})",
    "vendor_tax_id": r"Tax ID \(EIN\):\s*(\d{2}-\d{7})",
    "currency": r"Currency\s+([A-Z]{3})",
    "subtotal": r"Subtotal\s*\$?([\d,]+\.\d{2})",
    "tax": r"Sales Tax[^$\n]*\$([\d,]+\.\d{2})",
    "total": r"TOTAL DUE\s*\$?([\d,]+\.\d{2})",
    "bank_account": r"Bank Account:\s*(\d+)",
    "routing_number": r"Routing Number:\s*(\d+)",
}
META_LABELS = r"\s+(?:Invoice No:|Invoice #|Bill No\.|Invoice Number|Invoice Date:|Date|Dated|Billing Date|PO Number:|Purchase Order|Customer PO Ref|Your PO|Currency)\b.*$"


def extract_rules(pdf_bytes: bytes) -> dict:
    text = text_layer(pdf_bytes)
    if len(text.strip()) < 30:
        raise ExtractionError("no text layer (scanned image) and AI extraction unavailable")
    data: dict = {}
    for field, pat in LABELS.items():
        m = re.search(pat, text)
        data[field] = m.group(1).strip() if m else None
    # Vendor name: the seller block is "name / address / Tax ID", so take the line two above
    # the Tax ID; otherwise the first line that isn't a title or a meta label.
    lines = [re.sub(META_LABELS, "", " " + ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]  # lines that were only meta labels drop out
    tax_idx = next((i for i, ln in enumerate(lines) if ln.startswith("Tax ID")), None)
    if tax_idx is not None and tax_idx >= 2 and lines[tax_idx - 2]:
        data["vendor_name"] = lines[tax_idx - 2]
    else:
        data["vendor_name"] = next((ln for ln in lines if ln and ln.upper() not in
                                    {"INVOICE", "TAX INVOICE", "BILL"}), None)
    note = re.search(r"Note:\s*(.+?)(?:\n\s*\n|Payment details)", text, re.S)
    data["notes"] = " ".join(note.group(1).split()) if note else None
    items = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for table in pdf.pages[0].extract_tables():
            for row in table:
                if row and row[0] and str(row[0]).strip().isdigit() and len(row) >= 5:
                    items.append({"description": row[1], "quantity": row[2],
                                  "unit_price": row[3], "amount": row[4]})
    data["line_items"] = items
    # Labels found on a known layout are reliable; missing ones get 0.
    data["confidence"] = {k: (0.9 if data.get(k) else 0.0) for k in FIELDS}
    return _clean(data)


# ---------- orchestrator ----------

def extract(pdf_bytes: bytes) -> tuple[dict, dict]:
    """Returns (fields, meta). meta explains which method ran and why."""
    has_text = len(text_layer(pdf_bytes).strip()) >= 30
    meta = {"text_layer": has_text, "method": None, "model": None, "fallback_reason": None, "attempts": []}
    t0 = time.time()
    models = gemini_models() if gemini_key() else []
    for model in models:
        try:
            data = extract_gemini(pdf_bytes, model)
            meta.update(method="gemini", model=model)
            break
        except Exception as e:  # busy model, network, quota, bad JSON... try the next one
            meta["attempts"].append(f"{model}: {_short(e)}")
    else:
        meta["fallback_reason"] = "; ".join(meta["attempts"]) or "no GEMINI_API_KEY configured"
        data = extract_rules(pdf_bytes)  # raises ExtractionError if unreadable
        meta["method"] = "rules"
    meta["seconds"] = round(time.time() - t0, 2)
    return data, meta
