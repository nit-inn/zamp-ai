"""The 7-step invoice pipeline.

run_pipeline() is a generator: it yields a "start" event before each step and a
"done" event after it, so the UI can show each stage live. AI is used only in
step 2 (reading). Every decision is made by the deterministic rules below.
"""
from __future__ import annotations

import hashlib
import time
from datetime import date
from typing import Iterator

from rapidfuzz import fuzz, process

from . import store
from .config import UPLOADS, load_rules
from .extract import ExtractionError, extract

STEPS = [
    ("intake", "Intake"),
    ("extract", "Extract fields (AI)"),
    ("validate", "Validate extraction"),
    ("vendor", "Vendor match"),
    ("po", "PO match"),
    ("checks", "Business checks"),
    ("decision", "Decision"),
]

# Severity ladder. The worst level across all checks decides the outcome.
LEVELS = {"pass": 0, "info": 1, "warn": 2, "hold": 3, "fail": 4}
DECISION_FOR = {0: "APPROVED", 1: "APPROVED", 2: "NEEDS_REVIEW", 3: "ON_HOLD", 4: "REJECTED"}


def money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def mask(acct: str | None) -> str:
    return "n/a" if not acct else "****" + str(acct)[-4:]


def chk(level: str, title: str, detail: str = "") -> dict:
    return {"level": level, "title": title, "detail": detail}


class Ctx:
    def __init__(self, pdf_bytes: bytes, filename: str):
        self.pdf = pdf_bytes
        self.filename = filename
        self.rules = load_rules()
        self.hash = hashlib.sha256(pdf_bytes).hexdigest()
        self.inv: dict = {}
        self.meta: dict = {}
        self.vendor: dict | None = None
        self.po: dict | None = None
        self.po_inferred = False
        self.checks: list[dict] = []

    def tolerance(self, base: float) -> float:
        r = self.rules
        return min(base * r["amount_tolerance_pct"] / 100, r["amount_tolerance_abs"])


# ---------------- steps: each returns (checks, summary, data, halt) ----------------

def step_intake(c: Ctx):
    prior = store.find_by_hash(c.hash)
    data = {"file": c.filename, "size_kb": round(len(c.pdf) / 1024, 1), "sha256": c.hash[:16] + "..."}
    if prior:
        return [chk("fail", "Exact duplicate file",
                    f"This exact file was already processed in run #{prior['id']} "
                    f"({prior['decision']}, {prior['created_at']}).")], "Duplicate file", data, True
    return [chk("pass", "File received", f"{data['size_kb']} KB, fingerprint {data['sha256']}")], \
        "File accepted", data, False


def step_extract(c: Ctx):
    try:
        c.inv, c.meta = extract(c.pdf)
    except ExtractionError as e:
        return [chk("warn", "Could not read invoice",
                    f"{e}. Route to an AP clerk for manual entry.")], "Unreadable", {}, True
    m = c.meta
    src = "text PDF" if m["text_layer"] else "scanned image (no text layer)"
    if m["method"] == "gemini":
        checks = [chk("pass", f"Read by {m['model']}", f"Source: {src}. {m['seconds']}s.")]
        if m.get("attempts"):
            checks.append(chk("info", "Switched to backup model", "; ".join(m["attempts"])))
    else:
        checks = [chk("info", "AI unavailable: used rule-based fallback",
                      f"Reason: {m['fallback_reason']}. Source: {src}.")]
    n = len(c.inv.get("line_items") or [])
    summary = f"{c.inv.get('invoice_number') or '?'} · {c.inv.get('vendor_name') or '?'} · " \
              f"{money(c.inv.get('total'))} · {n} line(s)"
    return checks, summary, {"fields": c.inv, "extractor": m}, False


def step_validate(c: Ctx):
    inv, r, out = c.inv, c.rules, []
    missing = [f for f in ("vendor_name", "invoice_number", "invoice_date", "total") if not inv.get(f)]
    if missing:
        out.append(chk("warn", "Required fields missing",
                       f"Missing: {', '.join(missing)}. Ask the vendor for a corrected invoice."))
    else:
        out.append(chk("pass", "All required fields present"))

    lines = inv.get("line_items") or []
    for li in lines:
        if None not in (li["quantity"], li["unit_price"], li["amount"]) and \
                abs(li["quantity"] * li["unit_price"] - li["amount"]) > 0.01:
            out.append(chk("warn", "Line maths doesn't add up",
                           f"'{li['description']}': {li['quantity']} x {money(li['unit_price'])} "
                           f"!= {money(li['amount'])}"))
    if inv.get("subtotal") is None and lines and all(li["amount"] is not None for li in lines):
        inv["subtotal"] = round(sum(li["amount"] for li in lines), 2)
        out.append(chk("info", "Subtotal not printed", f"Computed from line items: {money(inv['subtotal'])}"))
    if lines and inv.get("subtotal") is not None:
        s = round(sum((li["amount"] or 0) for li in lines), 2)
        if abs(s - inv["subtotal"]) > 0.01:
            out.append(chk("warn", "Line items don't sum to subtotal",
                           f"Lines {money(s)} vs subtotal {money(inv['subtotal'])}"))
    if None not in (inv.get("subtotal"), inv.get("tax"), inv.get("total")):
        if abs(inv["subtotal"] + inv["tax"] - inv["total"]) > 0.01:
            out.append(chk("warn", "Subtotal + tax != total",
                           f"{money(inv['subtotal'])} + {money(inv['tax'])} != {money(inv['total'])}"))
        else:
            out.append(chk("pass", "Arithmetic checks out",
                           f"{money(inv['subtotal'])} + tax {money(inv['tax'])} = {money(inv['total'])}"))

    try:
        d = date.fromisoformat(inv.get("invoice_date") or "")
        age = (date.today() - d).days
        if age < 0:
            out.append(chk("warn", "Invoice is future-dated", f"Dated {d}"))
        elif age > r["max_invoice_age_days"]:
            out.append(chk("warn", "Stale invoice", f"{age} days old"))
    except ValueError:
        if inv.get("invoice_date"):
            out.append(chk("warn", "Unrecognised date", str(inv.get("invoice_date"))))

    low = [f for f, v in (inv.get("confidence") or {}).items()
           if inv.get(f) not in (None, "") and v < r["min_extraction_confidence"]]
    if low:
        out.append(chk("warn", "Low-confidence fields",
                       f"{', '.join(low)} below {r['min_extraction_confidence']:.0%}. A human should verify them."))
    worst = max((LEVELS[x["level"]] for x in out), default=0)
    return out, "Looks consistent" if worst < 2 else "Issues found", {}, False


def step_vendor(c: Ctx):
    inv, out = c.inv, []
    vs = store.vendors()
    by_tax = next((v for v in vs if inv.get("vendor_tax_id") and v["tax_id"] == inv["vendor_tax_id"]), None)
    names = {n: v for v in vs for n in [v["legal_name"], *v["aliases"]]}
    best = process.extractOne(inv.get("vendor_name") or "", list(names), scorer=fuzz.token_sort_ratio)
    by_name = names[best[0]] if best and best[1] >= c.rules["vendor_name_match_threshold"] else None
    score = round(best[1]) if best else 0

    if by_tax and by_name and by_tax is not by_name:
        out.append(chk("warn", "Tax ID and name point to different vendors",
                       f"Tax ID matches {by_tax['legal_name']}, name matches {by_name['legal_name']}."))
    v = by_tax or by_name
    if not v:
        return [chk("fail", "Unknown vendor",
                    f"'{inv.get('vendor_name')}' is not in the vendor master (best name match {score}%). "
                    "Vendor must be onboarded before any payment.")], "Not found", {}, True
    c.vendor = v
    how = "tax ID + name" if by_tax and by_name else ("tax ID" if by_tax else f"name ({score}% match)")
    out.append(chk("pass", f"Matched {v['legal_name']} ({v['vendor_id']})", f"Matched on {how}."))
    if v["status"] != "approved":
        out.append(chk("fail", "Vendor is blocked", f"Status in vendor master: {v['status']}."))
        return out, "Blocked vendor", {"vendor": v}, True

    acct = inv.get("bank_account")
    if not acct:
        out.append(chk("info", "No bank details on invoice", "Payment will go to the account on file."))
    elif acct != v["bank_account"] or (inv.get("routing_number") and inv["routing_number"] != v["routing_number"]):
        note = inv.get("notes") or ""
        extra = " The invoice itself asks to pay a new account." if "bank" in note.lower() else ""
        out.append(chk("hold", "Bank account differs from vendor master",
                       f"Invoice says {mask(acct)}, master says {mask(v['bank_account'])}.{extra} "
                       "Do NOT pay. Verify by calling the vendor on the number in the vendor master, "
                       "not any number on the invoice."))
    else:
        out.append(chk("pass", "Bank details match master", mask(acct)))
    if inv.get("currency") and inv["currency"] != v["currency"]:
        out.append(chk("warn", "Currency differs from vendor default", f"{inv['currency']} vs {v['currency']}"))
    return out, v["legal_name"], {"vendor": v}, False


def _desc_score(inv_lines: list[dict], po_lines: list[dict]) -> float:
    if not inv_lines or not po_lines:
        return 0.0
    names = [p["description"] for p in po_lines]
    return sum(process.extractOne(li["description"], names, scorer=fuzz.token_set_ratio)[1]
               for li in inv_lines) / (100 * len(inv_lines))


def step_po(c: Ctx):
    inv, out = c.inv, []
    pos = store.purchase_orders()
    raw = (inv.get("po_number") or "").upper().replace(" ", "")
    ref = ("PO-" + raw[2:].lstrip("-")) if raw.startswith("PO") else raw
    po = pos.get(ref)
    if po:
        c.po = po
        out.append(chk("pass", f"Found {ref}", f"{po['description']}, {money(po['total_amount'])}, {po['status']}"))
        if po["vendor_id"] != c.vendor["vendor_id"]:
            out.append(chk("warn", "PO belongs to another vendor",
                           f"{ref} was issued to {po['vendor_id']}, invoice is from {c.vendor['vendor_id']}."))
        if po["status"] != "open":
            out.append(chk("warn", "PO is closed", f"{ref} is {po['status']}; no further billing expected."))
        return out, ref, {"po": po}, False

    if ref:
        out.append(chk("warn", "PO not found", f"Invoice quotes {ref}, which doesn't exist."))
    # Infer: rank this vendor's open POs by line similarity and how well the amount fits.
    sub = inv.get("subtotal") or 0
    cands = []
    for p in pos.values():
        if p["vendor_id"] != c.vendor["vendor_id"] or p["status"] != "open":
            continue
        remaining = p["total_amount"] - store.po_billed(p["po_number"])[0]
        amt = min(sub, remaining) / max(sub, remaining) if sub > 0 and remaining > 0 else 0
        desc = _desc_score(inv.get("line_items") or [], p["lines"])
        cands.append((round(0.6 * desc + 0.4 * amt, 2), desc, amt, p))
    cands.sort(key=lambda x: -x[0])
    ranked = [{"po": x[3]["po_number"], "score": x[0], "lines": round(x[1], 2), "amount_fit": round(x[2], 2)}
              for x in cands]
    if cands and cands[0][0] >= 0.6:
        score, desc, amt, p = cands[0]
        c.po, c.po_inferred = p, True
        out.append(chk("warn", f"No PO on invoice: suggested {p['po_number']} ({score:.0%} match)",
                       f"Line items match {desc:.0%}, amount fit {amt:.0%}. "
                       "Inferred POs are never auto-approved: a reviewer must confirm."))
        return out, f"Suggested {p['po_number']}", {"candidates": ranked}, False
    out.append(chk("warn", "No matching PO", "No open PO for this vendor fits. Ask the requester for the PO."))
    return out, "No PO", {"candidates": ranked}, False


def step_checks(c: Ctx):
    inv, r, v, out = c.inv, c.rules, c.vendor, []
    num = store.norm_invoice_number(inv.get("invoice_number"))
    dup = store.find_same_number(v["vendor_id"], num) if num else None
    if dup:
        same = "" if dup["invoice_number"] == inv.get("invoice_number") else \
            f" ('{inv.get('invoice_number')}' and '{dup['invoice_number']}' are the same once punctuation is ignored)"
        out.append(chk("fail", "Duplicate invoice number",
                       f"Already processed in run #{dup['id']} on {dup['created_at']} "
                       f"({dup['decision']}, {money(dup['total'])}){same}. Likely a resubmission."))
    else:
        out.append(chk("pass", "Not a duplicate", f"No earlier invoice {num} from this vendor."))
        if inv.get("total"):
            for s in store.find_same_amount(v["vendor_id"], inv["total"], num):
                try:
                    gap = abs((date.fromisoformat(inv["invoice_date"]) - date.fromisoformat(s["invoice_date"])).days)
                except (TypeError, ValueError):
                    continue
                if gap <= r["duplicate_window_days"]:
                    out.append(chk("warn", "Possible duplicate",
                                   f"Same vendor and amount ({money(inv['total'])}) as {s['invoice_number']} "
                                   f"(run #{s['id']}), {gap} days apart, different invoice number."))
                    break

    po = c.po
    if po:
        names = [p["description"] for p in po["lines"]]
        for li in inv.get("line_items") or []:
            m = process.extractOne(li["description"], names, scorer=fuzz.token_set_ratio)
            if not m or m[1] < r["line_description_match_threshold"]:
                amt = li["amount"] or 0
                lvl = "info" if amt <= c.tolerance(po["total_amount"]) else "warn"
                out.append(chk(lvl, "Line not on PO",
                               f"'{li['description']}' ({money(amt)})" +
                               (": small, within tolerance." if lvl == "info" else ": needs approval.")))
                continue
            pl = po["lines"][names.index(m[0])]
            if li["quantity"] and li["quantity"] > pl["qty"]:
                out.append(chk("warn", "Quantity exceeds PO",
                               f"'{pl['description']}': billed {li['quantity']:g}, PO allows {pl['qty']:g}."))
            if li["unit_price"] and li["unit_price"] - pl["unit_price"] > c.tolerance(pl["unit_price"]):
                out.append(chk("warn", "Unit price above PO",
                               f"'{pl['description']}': {money(li['unit_price'])} vs PO {money(pl['unit_price'])}."))

        billed, n = store.po_billed(po["po_number"])
        remaining = round(po["total_amount"] - billed, 2)
        sub = inv.get("subtotal") or 0
        over = round(sub - remaining, 2)
        tol = c.tolerance(po["total_amount"])
        ctx = f"PO {money(po['total_amount'])}, already billed {money(billed)} across {n} invoice(s), " \
              f"remaining {money(remaining)}."
        if over <= 0:
            out.append(chk("pass", "Within PO balance", f"Invoice {money(sub)} (pre-tax). {ctx}"))
        elif over <= tol:
            out.append(chk("info", f"Over PO balance by {money(over)}, within tolerance",
                           f"Tolerance is {money(tol)} (min of {r['amount_tolerance_pct']}% / "
                           f"${r['amount_tolerance_abs']}). {ctx}"))
        else:
            out.append(chk("warn", f"Exceeds remaining PO balance by {money(over)}",
                           f"Invoice {money(sub)} (pre-tax). {ctx} Needs a PO amendment or a credit note."))

    if (inv.get("total") or 0) > r["auto_approve_max_amount"]:
        out.append(chk("warn", "Above auto-approval limit",
                       f"{money(inv['total'])} > {money(r['auto_approve_max_amount'])}"))
    worst = max((LEVELS[x["level"]] for x in out), default=0)
    return out, ["All clear", "All clear", "Issues found", "Hold", "Failed"][worst], {}, False


# ---------------- decision ----------------

NEXT = {
    "APPROVED": "Schedule payment of {total} to {vendor} (account on file, {terms}).",
    "NEEDS_REVIEW": "Route to AP reviewer with the flagged items below.",
    "ON_HOLD": "Do not pay. Verify with the vendor via the contact on file; escalate to the finance controller.",
    "REJECTED": "Do not pay. Notify the vendor / requester with the reason below.",
}


def decide(c: Ctx, halted_at: str | None) -> dict:
    worst = max((LEVELS[x["level"]] for x in c.checks), default=0)
    decision = DECISION_FOR[worst]
    flagged = sorted([x for x in c.checks if LEVELS[x["level"]] >= 1], key=lambda x: -LEVELS[x["level"]])
    top = [x for x in flagged if LEVELS[x["level"]] == worst]
    if decision == "APPROVED":
        headline = "All checks passed" + (" (with notes)" if flagged else "")
    else:
        headline = top[0]["title"] + (f" (+{len(top) - 1} more)" if len(top) > 1 else "")
    v = c.vendor or {}
    nxt = NEXT[decision].format(total=money(c.inv.get("total")), vendor=v.get("legal_name", "vendor"),
                                terms=v.get("payment_terms", ""))
    return {"decision": decision, "headline": headline, "next_action": nxt,
            "reasons": flagged, "halted_at": halted_at}


# ---------------- runner ----------------

FUNCS = {"intake": step_intake, "extract": step_extract, "validate": step_validate,
         "vendor": step_vendor, "po": step_po, "checks": step_checks}


def run_pipeline(pdf_bytes: bytes, filename: str) -> Iterator[dict]:
    c = Ctx(pdf_bytes, filename)
    steps_log, halted = [], None
    for key, title in STEPS[:-1]:
        if halted:
            ev = {"type": "done", "step": key, "title": title, "status": "skipped",
                  "summary": f"Skipped: stopped at {halted}", "checks": [], "data": {}, "ms": 0}
            steps_log.append(ev)
            yield ev
            continue
        yield {"type": "start", "step": key, "title": title}
        t0 = time.time()
        checks, summary, data, halt = FUNCS[key](c)
        c.checks += checks
        worst = max((LEVELS[x["level"]] for x in checks), default=0)
        ev = {"type": "done", "step": key, "title": title, "status": list(LEVELS)[worst],
              "summary": summary, "checks": checks, "data": data, "ms": int((time.time() - t0) * 1000)}
        steps_log.append(ev)
        yield ev
        if halt:
            halted = title

    yield {"type": "start", "step": "decision", "title": "Decision"}
    d = decide(c, halted)
    inv = c.inv
    rec = {
        "created_at": store.now(), "filename": filename, "file_hash": c.hash,
        "extractor": (c.meta.get("model") if c.meta.get("method") == "gemini" else c.meta.get("method")),
        "vendor_id": (c.vendor or {}).get("vendor_id"),
        "vendor_name": (c.vendor or {}).get("legal_name") or inv.get("vendor_name"),
        "invoice_number": inv.get("invoice_number"),
        "invoice_number_norm": store.norm_invoice_number(inv.get("invoice_number")),
        "invoice_date": inv.get("invoice_date"),
        "po_number": (c.po or {}).get("po_number"), "po_inferred": int(c.po_inferred),
        "currency": inv.get("currency"), "subtotal": inv.get("subtotal"), "tax": inv.get("tax"),
        "total": inv.get("total"), "decision": d["decision"], "headline": d["headline"],
        "next_action": d["next_action"], "reviewed_by": None, "review_note": None,
        "extraction_json": store.to_json({"fields": inv, "extractor": c.meta}),
        "steps_json": store.to_json(steps_log + [{"step": "decision", **d}]),
    }
    run_id = store.save_run(rec)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / f"{run_id}.pdf").write_bytes(pdf_bytes)
    yield {"type": "decision", "run_id": run_id, **d, "record": rec}
