"""Invoice Decision Engine: Streamlit UI (live run view + dashboard).

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import html
import json

import pandas as pd
import streamlit as st

from pipeline import store
from pipeline.config import ROOT, SAMPLES, UPLOADS, gemini_key, load_rules, save_rules
from pipeline.engine import STEPS, money, run_pipeline

st.set_page_config(page_title="Invoice Decision Engine", page_icon=":material/receipt_long:", layout="wide",
                   initial_sidebar_state="expanded")

STEP_KEYS = [k for k, _ in STEPS]
DEC_STATUS = {"PENDING_APPROVAL": "pass", "APPROVED": "pass", "NEEDS_REVIEW": "warn", "ON_HOLD": "hold", "REJECTED": "fail"}

# status -> (tint background, strong colour, pill label)
PAL = {
    "pending": ("#F3F4F6", "#9CA3AF", "Pending"),
    "running": ("#EEF2FF", "#4F46E5", "Running"),
    "pass": ("#ECFDF5", "#047857", "Passed"),
    "info": ("#EEF2FF", "#4338CA", "Passed · note"),
    "warn": ("#FFFBEB", "#B45309", "Review"),
    "hold": ("#FEF2F2", "#B91C1C", "Hold"),
    "fail": ("#FEF2F2", "#B91C1C", "Failed"),
    "skipped": ("#F3F4F6", "#9CA3AF", "Skipped"),
}
GLYPH = {"pass": "✓", "info": "✓", "warn": "!", "hold": "‖", "fail": "✕", "skipped": "–"}
DOT = {"pass": "#10B981", "info": "#6366F1", "warn": "#F59E0B", "hold": "#EF4444", "fail": "#DC2626"}
# decision -> (label, tint, strong, border)
DEC = {
    "PENDING_APPROVAL": ("Awaiting approval", "#EEF2FF", "#4338CA", "#C7D2FE"),
    "APPROVED": ("Approved", "#ECFDF5", "#047857", "#A7F3D0"),
    "NEEDS_REVIEW": ("Needs review", "#FFFBEB", "#B45309", "#FDE68A"),
    "ON_HOLD": ("On hold", "#FEF2F2", "#B91C1C", "#FECACA"),
    "REJECTED": ("Rejected", "#FEF2F2", "#991B1B", "#FECACA"),
}

CSS = """<style>
[data-testid="stSidebarCollapseButton"],[data-testid="stSidebarCollapsedControl"],[data-testid="stExpandSidebarButton"]{display:none !important}
[data-testid="stSidebarHeader"] img{height:2.4rem !important;max-width:none;margin-top:1.25rem !important}
[data-testid="stSidebarHeader"]{height:auto !important;min-height:4.5rem}
.zs-runhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 6px}
.zs-eyebrow{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#6B7280}
.zs-fname{font-size:18px;font-weight:600;color:#111827;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;word-break:break-all}
.zs-step{display:flex;gap:14px;align-items:flex-start;padding:14px 18px;border:1px solid #E6E8F0;border-radius:12px;background:#FFFFFF}
.zs-step.s-pending{background:#FAFBFC}
.zs-step.s-pending .zs-title{color:#9CA3AF}
.zs-step.s-running{border-color:#C7D2FE;background:#FAFAFF}
.zs-ic{position:relative;flex:none;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700}
.s-running .zs-ic::after{content:"";position:absolute;inset:-3px;border-radius:50%;border:2px solid transparent;border-top-color:#4F46E5;animation:zsspin .8s linear infinite}
@keyframes zsspin{to{transform:rotate(360deg)}}
.zs-body{flex:1;min-width:0}
.zs-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-height:28px}
.zs-title{font-weight:600;color:#111827;font-size:15px}
.zs-sum{color:#4B5563;font-size:14px}
.zs-right{margin-left:auto;display:flex;gap:10px;align-items:center}
.zs-ms{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#9CA3AF}
.zs-pill{font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px;letter-spacing:.02em;white-space:nowrap}
.zs-checks{margin-top:8px;display:flex;flex-direction:column;gap:5px}
.zs-ck{display:flex;gap:10px;align-items:baseline;font-size:13.5px;color:#374151;line-height:1.5}
.zs-dot{flex:none;width:7px;height:7px;border-radius:50%;transform:translateY(-1px)}
.zs-ck b{font-weight:600;color:#111827}
.zs-ck .d{color:#6B7280}
.zs-dec{border:1px solid;border-radius:14px;overflow:hidden;background:#FFFFFF;margin-top:6px}
.zs-dec-top{display:flex;align-items:center;gap:12px;padding:12px 20px}
.zs-dec-badge{font-size:13px;font-weight:700;padding:4px 12px;border-radius:999px;color:#FFFFFF;letter-spacing:.02em}
.zs-dec-body{padding:16px 20px 18px}
.zs-dec-head{font-size:17px;font-weight:600;color:#111827;line-height:1.4}
.zs-next{margin-top:14px;padding:12px 14px;background:#F8F9FC;border:1px solid #E6E8F0;border-radius:10px;font-size:14px;color:#1F2937}
.zs-next .zs-eyebrow{margin-bottom:4px}
.zs-rev{margin-top:14px;display:grid;grid-template-columns:150px 1fr;border:1px solid #E6E8F0;border-radius:10px;overflow:hidden;font-size:14px}
.zs-rev>div{padding:10px 14px;border-top:1px solid #EEF0F5;color:#1F2937}
.zs-rev>div:nth-child(-n+2){border-top:none}
.zs-rev>div.k{background:#F8F9FC;color:#6B7280;font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;display:flex;align-items:center}
.zs-stop{margin-top:8px;font-size:13.5px;color:#4B5563}
.zs-issue{margin-top:10px;padding:12px 14px;border:1px solid #E6E8F0;border-left:4px solid;border-radius:6px;background:#FFFFFF}
.zs-issue-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.zs-issue-t{font-size:15px;font-weight:600;color:#111827;margin-top:4px}
.zs-issue-d{font-size:13.5px;color:#4B5563;margin-top:2px;line-height:1.5}
.zs-fix{margin-top:10px;padding:8px 12px;background:#F5F6FF;border-radius:8px;font-size:13.5px;color:#1F2937}
.zs-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:10px}
.zs-tile{padding:10px 14px;background:#F8F9FC;border:1px solid #E6E8F0;border-radius:10px}
.zs-tile-l{font-size:12px;color:#6B7280}
.zs-tile-v{font-size:18px;font-weight:600;color:#111827;margin-top:2px}
.zs-tbl{width:100%;border-collapse:separate;border-spacing:0;border:1px solid #E6E8F0;border-radius:10px;overflow:hidden;font-size:13.5px;background:#FFFFFF}
.zs-tbl th{background:#F8F9FC;color:#6B7280;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;text-align:left;padding:8px 12px;border-bottom:1px solid #E6E8F0}
.zs-tbl th.grp{color:#4338CA;padding-bottom:2px;border-bottom:none}
.zs-tbl td{padding:9px 12px;border-bottom:1px solid #F0F1F5;color:#1F2937;vertical-align:top}
.zs-tbl tr:last-child td{border-bottom:none}
.zs-tbl td.f{font-weight:600;color:#111827}
.zs-tbl td.s{color:#6B7280}
.zs-tbl .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.zs-tbl .sep{border-left:1px solid #E6E8F0}
.zs-st{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap}
.zs-st.ok{background:#ECFDF5;color:#047857}
.zs-st.bad{background:#FEF2F2;color:#B91C1C}
.zs-st.na{background:#F3F4F6;color:#6B7280}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)


def esc(s) -> str:
    """HTML-escape, and neutralise characters Streamlit markdown would treat as LaTeX/emphasis."""
    return html.escape(str(s)).replace("$", "&#36;").replace("_", "&#95;").replace("*", "&#42;")


def manifest() -> list[dict]:
    p = SAMPLES / "manifest.json"
    return sorted(json.loads(p.read_text()), key=lambda m: m["order"]) if p.exists() else []


# ---------------- rendering helpers ----------------

def checks_html(checks: list[dict], brief: bool = False) -> str:
    """brief=True shows only each check's heading; reasons for problems appear in the decision's issue cards."""
    rows = []
    for c in checks:
        show = c.get("detail") and not brief
        detail = f" <span class='d'>{esc(c['detail'])}</span>" if show else ""
        rows.append(f"<div class='zs-ck'><span class='zs-dot' style='background:{DOT[c['level']]}'></span>"
                    f"<div><b>{esc(c['title'])}</b>{detail}</div></div>")
    return f"<div class='zs-checks'>{''.join(rows)}</div>" if rows else ""


def render_step(ph, ev: dict, state: str | None = None) -> None:
    status = state or ev.get("status", "pending")
    tint, strong, label = PAL[status]
    num = STEP_KEYS.index(ev["step"]) + 1 if ev["step"] in STEP_KEYS else ""
    glyph = GLYPH.get(status, num)
    summary = "Running…" if status == "running" else ev.get("summary") or ""
    if ev["step"] == "decision" and summary and status not in ("pending", "running"):
        label, summary = summary.capitalize(), "Final outcome of all checks"
    card = (
        f"<div class='zs-step s-{status}'>"
        f"<div class='zs-ic' style='background:{tint};color:{strong}'>{glyph}</div>"
        f"<div class='zs-body'><div class='zs-head'>"
        f"<span class='zs-title'>{esc(ev['title'])}</span>"
        f"<span class='zs-sum'>{esc(summary)}</span>"
        f"<span class='zs-right'><span class='zs-pill' style='background:{tint};color:{strong}'>{label}</span></span>"
        f"</div>{checks_html(ev.get('checks', []), brief=True)}</div></div>"
    )
    with ph.container():
        st.markdown(card, unsafe_allow_html=True)
        data = ev.get("data") or {}
        if ev["step"] == "po" and data.get("candidates"):
            with st.expander("PO candidates considered", icon=":material/rule:"):
                st.dataframe(pd.DataFrame(data["candidates"]), hide_index=True, width="stretch")


FIELD_LABEL = {"vendor_name": "Vendor", "vendor_tax_id": "Tax ID", "invoice_number": "Invoice number",
               "invoice_date": "Invoice date", "po_number": "PO number", "bank_account": "Bank account",
               "routing_number": "Routing number"}
MONEY_FIELDS = {"subtotal", "tax", "total"}


def render_fields(f: dict, heading: bool = True) -> None:
    """Extracted invoice data as a clean table, plus its line items."""
    if not f:
        return
    keys = list((f.get("confidence") or {}).keys()) or \
        [k for k, v in f.items() if k not in ("line_items", "notes", "confidence") and not isinstance(v, (dict, list))]

    def val(k):
        v = f.get(k)
        if v in (None, ""):
            return "—"
        return money(v) if k in MONEY_FIELDS and isinstance(v, (int, float)) else str(v)

    body = "".join(f"<tr><td class='f'>{esc(FIELD_LABEL.get(k, k.replace('_', ' ').capitalize()))}</td>"
                   f"<td>{esc(val(k))}</td></tr>" for k in keys)
    out = ("<div class='zs-eyebrow' style='margin:22px 0 6px'>Extracted fields</div>" if heading else "") + \
        f"<table class='zs-tbl'><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>{body}</tbody></table>"
    if f.get("line_items"):
        def n(x):
            return "—" if x is None else f"{x:,.10g}"

        def m(x):
            return "—" if x is None else money(x)

        lb = "".join(f"<tr><td class='f'>{esc(li.get('description'))}</td><td class='n'>{n(li.get('quantity'))}</td>"
                     f"<td class='n'>{esc(m(li.get('unit_price')))}</td><td class='n'>{esc(m(li.get('amount')))}</td></tr>"
                     for li in f["line_items"])
        out += ("<table class='zs-tbl' style='margin-top:12px'><thead><tr><th>Line item</th><th class='n'>Qty</th>"
                f"<th class='n'>Unit price</th><th class='n'>Amount</th></tr></thead><tbody>{lb}</tbody></table>")
    if f.get("notes"):
        out += f"<div class='zs-stop' style='margin-top:8px'>Note printed on invoice: <i>{esc(f['notes'])}</i></div>"
    st.markdown(out, unsafe_allow_html=True)


CLEAN = ("PENDING_APPROVAL", "APPROVED")  # all checks passed (before / after the user approved)


def current_decision(dec: dict, run: dict) -> dict:
    """The system's decision as stored in the run log, updated with what a person decided since."""
    d = {**dec, "decision": run["decision"], "run_id": run["id"]}
    if not run["reviewed_by"]:
        return d
    approved = run["decision"] == "APPROVED"
    d["headline"] = "Approved by reviewer" if approved else "Rejected by reviewer"
    d["next_action"] = (f"Schedule payment of {money(run['total'])} to {run['vendor_name'] or 'the vendor'}."
                        if approved else "Do not pay. Notify the vendor / requester.")
    d["review"] = {"by": run["reviewed_by"], "note": run["review_note"] or "",
                   "system": dec["decision"], "system_reason": dec["headline"]}
    if dec["decision"] == "PENDING_APPROVAL":
        d["reasons"] = []  # nothing was flagged; the notes are not what the reviewer decided on
    return d


def approval_actions(run_id: int, key: str) -> None:
    """Approve / Reject buttons for an invoice that passed every check."""
    st.markdown("<div class='zs-eyebrow' style='margin:14px 0 6px'>All checks passed. Approve this invoice?</div>",
                unsafe_allow_html=True)
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Approve", key=f"ap{key}{run_id}", type="primary", icon=":material/check:", width="stretch"):
        store.set_review(run_id, "APPROVED", "")
        st.rerun()
    if b2.button("Reject", key=f"rj{key}{run_id}", icon=":material/close:", width="stretch"):
        store.set_review(run_id, "REJECTED", "")
        st.rerun()


AUTO_NOTES = {"Approved after all checks passed", "Rejected by user"}  # written by older versions


def review_html(d: dict) -> str:
    """Who decided, what the system had said, and the reviewer's note, as a small key/value grid."""
    rv = d.get("review")
    if not rv:
        return ""
    rows = [("Reviewed by", esc(rv["by"])), ("System verdict", esc(rv["system_reason"]))]
    if rv["note"] and rv["note"] not in AUTO_NOTES:
        rows.append(("Reviewer note", esc(rv["note"])))
    return "<div class='zs-rev'>" + "".join(f"<div class='k'>{k}</div><div>{v}</div>" for k, v in rows) + "</div>"


def render_decision(d: dict, actions=None) -> None:
    """Invoice-vs-records comparison first, then the decision card, then optional `actions`
    (approve / reject buttons), so the user decides after seeing everything."""
    m = d.get("match") or {}
    if m.get("rows"):
        st.markdown("<div class='zs-eyebrow' style='margin:22px 0 6px'>Invoice vs records</div>",
                    unsafe_allow_html=True)
        render_match(m)
    label, tint, strong, border = DEC[d["decision"]]
    run = f"<span class='zs-ms' style='margin-left:auto'>Run #{d['run_id']}</span>" if d.get("run_id") else ""
    reasons = ""
    if d.get("reasons") and d["decision"] in CLEAN:
        reasons = f"<div class='zs-eyebrow' style='margin-top:16px'>Notes</div>{checks_html(d['reasons'])}"
    elif d.get("reasons"):
        stop = (f"<div class='zs-stop'>Processing stopped at <b>{esc(d['halted_at'])}</b>; "
                f"later steps were skipped.</div>") if d.get("halted_at") else ""
        n = len(d["reasons"])
        reasons = (f"<div class='zs-eyebrow' style='margin-top:18px'>{n} issue{'s' if n > 1 else ''} found</div>"
                   f"{stop}{''.join(issue_html(x) for x in d['reasons'])}")
    st.markdown(
        f"<div class='zs-dec' style='border-color:{border};margin-top:22px'>"
        f"<div class='zs-dec-top' style='background:{tint}'>"
        f"<span class='zs-eyebrow' style='color:{strong}'>Decision</span>"
        f"<span class='zs-dec-badge' style='background:{strong}'>{label}</span>{run}</div>"
        f"<div class='zs-dec-body'><div class='zs-dec-head'>{esc(d['headline'])}</div>{review_html(d)}"
        f"<div class='zs-next'><div class='zs-eyebrow'>Next action</div>{esc(d['next_action'])}</div>"
        f"{reasons}</div></div>",
        unsafe_allow_html=True)
    if actions:
        actions()


SEV = {"info": "Note", "warn": "Needs review", "hold": "On hold", "fail": "Rejects invoice"}


def issue_html(x: dict) -> str:
    tint, strong, _ = PAL[x["level"]]
    where = esc(x.get("step") or "")
    fix = (f"<div class='zs-fix'><span class='zs-eyebrow' style='color:#4338CA'>How to resolve</span>"
           f"<div>{esc(x['fix'])}</div></div>") if x.get("fix") else ""
    detail = f"<div class='zs-issue-d'>{esc(x['detail'])}</div>" if x.get("detail") else ""
    return (f"<div class='zs-issue' style='border-left-color:{DOT[x['level']]}'>"
            f"<div class='zs-issue-top'><span class='zs-eyebrow'>{where}</span>"
            f"<span class='zs-pill' style='background:{tint};color:{strong}'>{SEV[x['level']]}</span></div>"
            f"<div class='zs-issue-t'>{esc(x['title'])}</div>{detail}{fix}</div>")


def render_match(m: dict) -> None:
    po = m.get("po")
    bal_ok = next((r["ok"] for r in m["rows"] if r["field"] == "Subtotal vs PO balance"), True)
    if po:
        left = po["remaining_after"]
        col = "color:#047857" if left >= 0 else ("color:#B45309" if bal_ok else "color:#B91C1C")
        tiles = [("PO " + po["number"] + (" (suggested)" if po["inferred"] else ""), money(po["total"]), ""),
                 (f"Billed before ({po['invoices_before']} inv.)", money(po["billed_before"]), ""),
                 ("This invoice (pre-tax)", money(po["this_invoice"]), ""),
                 ("Remaining if paid", money(left), col)]
        st.markdown("<div class='zs-tiles'>" + "".join(
            f"<div class='zs-tile'><div class='zs-tile-l'>{esc(a)}</div><div class='zs-tile-v' style='{s}'>"
            f"{esc(b)}</div></div>" for a, b, s in tiles) + "</div>", unsafe_allow_html=True)

    def badge(ok):
        if ok is True:
            return "<span class='zs-st ok'>✓ Match</span>"
        if ok is False:
            return "<span class='zs-st bad'>✕ Mismatch</span>"
        return "<span class='zs-st na'>Not on invoice</span>"

    body = "".join(f"<tr><td class='f'>{esc(r['field'])}</td><td>{esc(r['invoice'])}</td>"
                   f"<td>{esc(r['record'])}</td><td class='s'>{esc(r['source'])}</td><td>{badge(r['ok'])}</td></tr>"
                   for r in m["rows"])
    html_out = ("<table class='zs-tbl'><thead><tr><th>Field</th><th>On invoice</th><th>On record</th>"
                f"<th>Source</th><th>Status</th></tr></thead><tbody>{body}</tbody></table>")
    if m.get("lines"):
        def num(x):
            return "—" if x is None else f"{x:,.10g}"

        def cur(x):
            return "—" if x is None else money(x)

        def lbadge(s):
            return f"<span class='zs-st {'ok' if s == 'Match' else 'bad'}'>{'✓ ' if s == 'Match' else '✕ '}{esc(s)}</span>"

        lb = "".join(
            f"<tr><td class='f'>{esc(li['inv_item'])}</td><td class='n'>{num(li['inv_qty'])}</td>"
            f"<td class='n'>{esc(cur(li['inv_price']))}</td><td class='n'>{esc(cur(li['inv_amount']))}</td>"
            f"<td class='sep'>{esc(li['po_item'] or '—')}</td><td class='n'>{num(li['po_qty'])}</td>"
            f"<td class='n'>{esc(cur(li['po_price']))}</td><td>{lbadge(li['status'])}</td></tr>"
            for li in m["lines"])
        html_out += ("<table class='zs-tbl' style='margin-top:12px'><thead>"
                     "<tr><th colspan='4' class='grp'>On invoice</th><th colspan='3' class='grp sep'>On PO</th><th></th></tr>"
                     "<tr><th>Item</th><th class='n'>Qty</th><th class='n'>Unit price</th><th class='n'>Amount</th>"
                     "<th class='sep'>Item</th><th class='n'>Qty</th><th class='n'>Unit price</th><th>Status</th></tr>"
                     f"</thead><tbody>{lb}</tbody></table>")
    st.markdown(html_out, unsafe_allow_html=True)


def run_header(eyebrow: str, name: str) -> None:
    st.markdown(
        f"<div class='zs-runhead'><div><div class='zs-eyebrow'>{eyebrow}</div>"
        f"<div class='zs-fname'>{esc(name)}</div></div>"
        f"<span class='zs-pill' style='background:#EEF2FF;color:#4338CA'>7-step pipeline</span></div>",
        unsafe_allow_html=True)


def decision_step(d: dict) -> dict:
    return {"step": "decision", "title": "Decision", "status": DEC_STATUS[d["decision"]],
            "summary": d["decision"].replace("_", " "), "checks": []}


def run_live(pdf_bytes: bytes, name: str, below) -> None:
    """Steps stream into the current column; fields and the decision go full-width into `below`."""
    run_header("Processing invoice", name)
    phs = {k: st.empty() for k in STEP_KEYS}
    for k, t in STEPS:
        render_step(phs[k], {"step": k, "title": t}, "pending")
    events = []
    for ev in run_pipeline(pdf_bytes, name):
        if ev["type"] == "start":
            render_step(phs[ev["step"]], ev, "running")
        elif ev["type"] == "done":
            events.append(ev)
            render_step(phs[ev["step"]], ev)
        else:
            render_step(phs["decision"], decision_step(ev))
            st.session_state["last_run"] = {"name": name, "events": events, "decision": ev}
            with below:
                render_fields(extracted(events))
                render_decision(ev, actions=pending_actions(ev, "live"))


def extracted(events: list[dict]) -> dict:
    return next(((e.get("data") or {}).get("fields") or {} for e in events if e["step"] == "extract"), {})


def replay(run: dict, below) -> None:
    run_header("Last run", run["name"])
    for ev in run["events"]:
        render_step(st.empty(), ev)
    d = run["decision"]
    saved = store.get_run(d["run_id"]) if d.get("run_id") else None
    if saved:  # pick up an approval / rejection made since the run
        d = current_decision(d, saved)
    render_step(st.empty(), decision_step(d))
    with below:
        render_fields(extracted(run["events"]))
        render_decision(d, actions=pending_actions(d, "live"))


def reason_text(r: dict) -> str:
    """Every issue that drove the decision, spelled out ('Quantity exceeds PO; Over PO balance'),
    worst first; 'All checks passed' when there were none."""
    try:
        reasons = json.loads(r["steps_json"])[-1].get("reasons") or []
    except (TypeError, ValueError, IndexError):
        return r["headline"]
    titles = [x["title"] for x in reasons if x["level"] in ("warn", "hold", "fail")]
    return "; ".join(dict.fromkeys(titles)) or "All checks passed"


def pending_actions(d: dict, key: str, review: bool = True):
    """Buttons for a person, only while the invoice is waiting for one: Approve / Reject for a clean
    invoice, and (when `review`) the reviewer's note + Approve / Reject for one that needs review or is on hold."""
    if not d.get("run_id"):
        return None
    if d["decision"] == "PENDING_APPROVAL":
        return lambda: approval_actions(d["run_id"], key)
    if review and d["decision"] in ("NEEDS_REVIEW", "ON_HOLD"):
        return lambda: reviewer_actions(d["run_id"], key)
    return None


def reviewer_actions(run_id: int, key: str) -> None:
    """Human in the loop for flagged invoices: approve or reject, with an optional note."""
    st.markdown("<div class='zs-eyebrow' style='margin:14px 0 6px'>Reviewer action</div>", unsafe_allow_html=True)
    note = st.text_input("Note (optional)", placeholder="e.g. Confirmed PO-1004 with requester",
                         key=f"n{key}{run_id}").strip()
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Approve", key=f"ra{key}{run_id}", icon=":material/check:", width="stretch"):
        store.set_review(run_id, "APPROVED", note)
        st.rerun()
    if b2.button("Reject", key=f"rr{key}{run_id}", icon=":material/close:", width="stretch"):
        store.set_review(run_id, "REJECTED", note)
        st.rerun()


# ---------------- sidebar ----------------

st.logo(str(ROOT / "assets" / "zamp_logo.svg"), size="large")

with st.sidebar:
    st.title("Invoice Decision Engine")
    st.caption("PDF in, reasoned decision out. AI reads; rules decide.")
    if not gemini_key():
        st.warning("No Gemini key: using rule-based fallback (scanned invoices can't be read).",
                   icon=":material/warning:")
    rules = load_rules()
    with st.form("rules_form", border=False):
        limit = st.number_input(
            "Auto-approve limit ($)", min_value=0, step=1000, value=int(rules["auto_approve_max_amount"]),
            help="Invoices with a total above this go to Needs review instead of the Approve prompt, "
                 "even if every check passes.")
        window = st.number_input(
            "Duplicate window (days)", min_value=0, max_value=3650, step=15,
            value=int(rules["duplicate_window_days"]),
            help="Same vendor and same total but a different invoice number, with invoice dates this many "
                 "days apart or less, is flagged as a possible duplicate. 0 turns this check off. "
                 "Exact re-uploads and repeated invoice numbers are always caught.")
        if st.form_submit_button("Save rules", icon=":material/save:", width="stretch"):
            save_rules({"auto_approve_max_amount": int(limit), "duplicate_window_days": int(window)})
            st.toast("Rules saved. They apply to the next invoice you process.", icon=":material/check:")
    st.divider()
    if st.button("Reset demo data", icon=":material/restart_alt:", width="stretch",
                 help="Clears run history and PO balances so the demo can be replayed."):
        store.reset()
        st.session_state.pop("last_run", None)
        st.toast("History cleared")
        st.rerun()

tab_run, tab_dash, tab_rec, tab_data = st.tabs([":material/play_circle: Run an invoice",
                                                ":material/monitoring: Dashboard", ":material/receipt_long: Records",
                                                ":material/database: Master data"])

# ---------------- run tab ----------------

with tab_run:
    left, right = st.columns([1, 2], gap="large")
    with left:
        st.subheader("Input")
        src = st.radio("Source", ["Sample invoice", "Upload PDF"], horizontal=True, label_visibility="collapsed")
        pdf_bytes = name = None
        if src == "Sample invoice":
            mf = manifest()
            if not mf:
                st.error("No samples found. Run `python generate_invoices.py`.")
            else:
                pick = st.selectbox("Scenario", mf, format_func=lambda m: f"{m['order']}. {m['scenario']}")
                pdf_bytes, name = (SAMPLES / pick["file"]).read_bytes(), pick["file"]
                st.download_button("View this PDF", pdf_bytes, file_name=name, mime="application/pdf",
                                   icon=":material/picture_as_pdf:")
        else:
            up = st.file_uploader("Invoice PDF", type=["pdf"])
            if up:
                pdf_bytes, name = up.getvalue(), up.name
        go = st.button("Process invoice", type="primary", icon=":material/play_arrow:",
                       disabled=pdf_bytes is None, width="stretch")
    below = st.container()  # full width, under both columns: extracted fields + decision
    with right:
        if go and pdf_bytes:
            run_live(pdf_bytes, name, below)
        elif st.session_state.get("last_run"):
            replay(st.session_state["last_run"], below)
        else:
            st.info("Pick a sample (or upload a PDF) and press **Process invoice** to watch each stage run.",
                    icon=":material/info:")

# ---------------- dashboard tab ----------------

with tab_dash:
    runs = store.list_runs()
    counts = {k: sum(r["decision"] == k for r in runs) for k in DEC}
    c = st.columns(7)
    c[0].metric("Invoices processed", len(runs))
    c[1].metric("Awaiting approval", counts["PENDING_APPROVAL"])
    c[2].metric("Approved", counts["APPROVED"])
    c[3].metric("Needs review", counts["NEEDS_REVIEW"])
    c[4].metric("On hold", counts["ON_HOLD"])
    c[5].metric("Rejected", counts["REJECTED"])
    c[6].metric("Approved value", f"${sum(r['total'] or 0 for r in runs if r['decision'] == 'APPROVED'):,.0f}")

    if not runs:
        st.info("No runs yet.", icon=":material/info:")
    else:
        st.subheader("Run details")
        rid = st.selectbox("Open run", [r["id"] for r in runs],
                           format_func=lambda i: next(f"#{r['id']} · {r['invoice_number']} · {r['decision']}"
                                                      for r in runs if r["id"] == i))
        run = store.get_run(rid)
        steps = json.loads(run["steps_json"])
        d1, d2 = st.columns([3, 2], gap="large")
        full = st.container()  # comparison, decision and approval span both columns
        with d1:
            for ev in steps[:-1]:
                render_step(st.empty(), ev)
        with full:
            dec_now = current_decision(steps[-1], run)
            render_decision(dec_now, actions=pending_actions(dec_now, "dash", review=False))
        with d2:
            st.markdown("**Extracted fields**")
            render_fields(json.loads(run["extraction_json"])["fields"], heading=False)
            pdf_path = UPLOADS / f"{rid}.pdf"
            if pdf_path.exists():
                st.download_button("Download original PDF", pdf_path.read_bytes(), icon=":material/download:",
                                   file_name=run["filename"], mime="application/pdf", key=f"dl{rid}")

    st.subheader("PO balances")
    vend = {v["vendor_id"]: v["legal_name"] for v in store.vendors()}
    mf = manifest()
    samples = {m["file"] for m in mf}
    rows = []
    for po in store.purchase_orders().values():
        total_inv = sum(m.get("po") == po["po_number"] for m in mf) + \
            store.uploaded_invoice_files(po["po_number"], samples)
        billed, n = store.po_billed(po["po_number"], approved_only=True)
        rows.append({"PO": po["po_number"], "Vendor": vend.get(po["vendor_id"]), "Status": po["status"],
                     "PO total": po["total_amount"], "Approved so far": billed, "Invoices processed": n,
                     "Remaining": po["total_amount"] - billed,
                     "Total invoices": total_inv})
    money_col = st.column_config.NumberColumn(format="$%.2f")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"PO total": money_col, "Approved so far": money_col, "Remaining": money_col})

# ---------------- master data tab ----------------

# ---------------- records tab ----------------

with tab_rec:
    runs = store.list_runs()
    if not runs:
        st.info("No invoices processed yet.", icon=":material/info:")
    else:
        f1, f2, f3 = st.columns([2, 2, 3])
        dec_labels = {k: DEC[k][0] for k in DEC}
        pick_dec = f1.multiselect("Decision", list(DEC), format_func=dec_labels.get, placeholder="All decisions")
        pick_ven = f2.multiselect("Vendor", sorted({r["vendor_name"] or "Unknown" for r in runs}),
                                  placeholder="All vendors")
        q = f3.text_input("Search", placeholder="Invoice #, PO, reason or file name").strip().lower()
        shown = []
        for r in runs:
            row = {"Run": r["id"], "Time": r["created_at"], "Vendor": r["vendor_name"] or "Unknown",
                   "Invoice #": r["invoice_number"],
                   "PO": (r["po_number"] or "") + (" (suggested)" if r["po_inferred"] else ""),
                   "Total": r["total"], "Decision": dec_labels[r["decision"]], "Reason": reason_text(r),
                   "File": r["filename"]}
            if pick_dec and r["decision"] not in pick_dec or pick_ven and row["Vendor"] not in pick_ven:
                continue
            if q and q not in " ".join(str(v) for v in row.values()).lower():
                continue
            shown.append(row)
        st.caption(f"Showing {len(shown)} of {len(runs)} invoices. Open one on the Dashboard tab for its full details.")
        st.dataframe(pd.DataFrame(shown, columns=["Run", "Time", "Vendor", "Invoice #", "PO", "Total", "Decision",
                                                  "Reason", "File"]),
                     hide_index=True, width="stretch",
                     column_config={"Total": st.column_config.NumberColumn(format="$%.2f")})

# ---------------- master data tab ----------------

with tab_data:
    st.caption("A mock procurement system. In production these would come from the ERP.")
    st.markdown("**Vendor master**")
    st.dataframe(pd.read_csv(ROOT / "data" / "vendors.csv", dtype=str), hide_index=True, width="stretch")
    st.markdown("**Purchase orders**")
    st.dataframe(pd.read_csv(ROOT / "data" / "purchase_orders.csv"), hide_index=True, width="stretch")
    st.markdown("**PO lines**")
    st.dataframe(pd.read_csv(ROOT / "data" / "po_lines.csv"), hide_index=True, width="stretch")
