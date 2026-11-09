"""Invoice Decision Engine: Streamlit UI (live run view + dashboard).

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from pipeline import store
from pipeline.config import ROOT, SAMPLES, UPLOADS, gemini_key, gemini_model, load_rules
from pipeline.engine import STEPS, money, run_pipeline

st.set_page_config(page_title="Invoice Decision Engine", page_icon="🧾", layout="wide")

ICON = {"pending": "⏳", "running": "🔄", "pass": "✅", "info": "✅", "warn": "⚠️", "hold": "🛑",
        "fail": "❌", "skipped": "⏭️"}
CHECK_ICON = {"pass": "✅", "info": "ℹ️", "warn": "⚠️", "hold": "🛑", "fail": "❌"}
DECISION_STYLE = {
    "APPROVED": ("✅ APPROVED", st.success),
    "NEEDS_REVIEW": ("⚠️ NEEDS REVIEW", st.warning),
    "ON_HOLD": ("🛑 ON HOLD", st.error),
    "REJECTED": ("❌ REJECTED", st.error),
}
DEC_STATUS = {"APPROVED": "pass", "NEEDS_REVIEW": "warn", "ON_HOLD": "hold", "REJECTED": "fail"}


def manifest() -> list[dict]:
    p = SAMPLES / "manifest.json"
    return sorted(json.loads(p.read_text()), key=lambda m: m["order"]) if p.exists() else []


# ---------------- rendering helpers ----------------

def render_step(ph, ev: dict, state: str | None = None) -> None:
    status = state or ev.get("status", "pending")
    with ph.container(border=True):
        head = f"{ICON[status]} **{ev['title']}**"
        if status == "running":
            head += "  · _running…_"
        elif ev.get("summary"):
            head += f"  ·  {ev['summary']}"
        if ev.get("ms"):
            head += f"  <span style='color:grey;font-size:0.8em'>({ev['ms']} ms)</span>"
        st.markdown(head, unsafe_allow_html=True)
        for c in ev.get("checks", []):
            line = f"{CHECK_ICON[c['level']]} {c['title']}"
            if c["detail"]:
                line += f": <span style='color:grey'>{c['detail']}</span>"
            st.markdown(f"<div style='margin-left:1.6em;font-size:0.9em'>{line}</div>", unsafe_allow_html=True)
        data = ev.get("data") or {}
        if ev["step"] == "extract" and data.get("fields"):
            with st.expander("Extracted fields"):
                render_fields(data["fields"])
        if ev["step"] == "po" and data.get("candidates"):
            with st.expander("PO candidates considered"):
                st.dataframe(pd.DataFrame(data["candidates"]), hide_index=True, width="stretch")


def render_fields(f: dict) -> None:
    conf = f.get("confidence") or {}
    rows = [{"field": k, "value": "" if f.get(k) is None else str(f.get(k)),
             "confidence": conf.get(k)} for k in conf]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"confidence": st.column_config.ProgressColumn(min_value=0, max_value=1,
                                                                              format="%.2f")})
    if f.get("line_items"):
        st.caption("Line items")
        st.dataframe(pd.DataFrame(f["line_items"]), hide_index=True, width="stretch")
    if f.get("notes"):
        st.caption(f"Notes on invoice: _{f['notes']}_")


def render_decision(d: dict) -> None:
    label, box = DECISION_STYLE[d["decision"]]
    body = f"### {label}\n**{d['headline']}**\n\n**Next action:** {d['next_action']}"
    if d.get("run_id"):
        body += f"\n\n_Saved as run #{d['run_id']}_"
    box(body)
    if d.get("reasons"):
        st.markdown("**Notes**" if d["decision"] == "APPROVED" else "**Why**")
        for r in d["reasons"]:
            st.markdown(f"- {CHECK_ICON[r['level']]} **{r['title']}**: {r['detail']}")


def run_live(pdf_bytes: bytes, name: str) -> None:
    st.markdown(f"#### Running: `{name}`")
    phs = {k: st.empty() for k, _ in STEPS}
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
            dec = {"step": "decision", "title": "Decision", "status": DEC_STATUS[ev["decision"]],
                   "summary": ev["decision"].replace("_", " "), "checks": []}
            render_step(phs["decision"], dec)
            render_decision(ev)
            st.session_state["last_run"] = {"name": name, "events": events, "decision": ev}


def replay(run: dict) -> None:
    st.markdown(f"#### Last run: `{run['name']}`")
    for ev in run["events"]:
        render_step(st.empty(), ev)
    d = run["decision"]
    render_step(st.empty(), {"step": "decision", "title": "Decision", "status": DEC_STATUS[d["decision"]],
                             "summary": d["decision"].replace("_", " "), "checks": []})
    render_decision(d)


# ---------------- sidebar ----------------

with st.sidebar:
    st.title("🧾 Invoice Decision Engine")
    st.caption("PDF in → reasoned decision out. AI reads; rules decide.")
    if gemini_key():
        st.success(f"AI extractor: **{gemini_model()}**")
    else:
        st.warning("No Gemini key: using rule-based fallback (scanned invoices can't be read).")
    rules = load_rules()
    st.markdown("**Active rules** (`rules.yaml`)")
    st.markdown(
        f"- Tolerance: min of **{rules['amount_tolerance_pct']}%** / **${rules['amount_tolerance_abs']}**\n"
        f"- Min extraction confidence: **{rules['min_extraction_confidence']:.0%}**\n"
        f"- Auto-approve limit: **{money(rules['auto_approve_max_amount'])}**\n"
        f"- Duplicate window: **{rules['duplicate_window_days']} days**")
    st.divider()
    if st.button("🔄 Reset demo data", width="stretch",
                 help="Clears run history and PO balances so the demo can be replayed."):
        store.reset()
        st.session_state.pop("last_run", None)
        st.toast("History cleared")
        st.rerun()

tab_run, tab_dash, tab_data = st.tabs(["▶️ Run an invoice", "📊 Dashboard", "📁 Master data & rules"])

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
                st.caption(f"{pick['explain']}  \nExpected: **{pick['expect'].replace('_', ' ')}**")
                pdf_bytes, name = (SAMPLES / pick["file"]).read_bytes(), pick["file"]
                st.download_button("View this PDF", pdf_bytes, file_name=name, mime="application/pdf")
        else:
            up = st.file_uploader("Invoice PDF", type=["pdf"])
            if up:
                pdf_bytes, name = up.getvalue(), up.name
        go = st.button("▶ Process invoice", type="primary", disabled=pdf_bytes is None, width="stretch")
        st.divider()
        st.caption("**Pipeline:** Intake → Extract (AI) → Validate → Vendor → PO → Checks → Decision. "
                   "Only step 2 uses AI; every decision is a deterministic, auditable rule.")
    with right:
        if go and pdf_bytes:
            run_live(pdf_bytes, name)
        elif st.session_state.get("last_run"):
            replay(st.session_state["last_run"])
        else:
            st.info("Pick a sample (or upload a PDF) and press **Process invoice** to watch each stage run.")

# ---------------- dashboard tab ----------------

with tab_dash:
    runs = store.list_runs()
    counts = {k: sum(r["decision"] == k for r in runs) for k in DECISION_STYLE}
    c = st.columns(6)
    c[0].metric("Invoices processed", len(runs))
    c[1].metric("✅ Approved", counts["APPROVED"])
    c[2].metric("⚠️ Needs review", counts["NEEDS_REVIEW"])
    c[3].metric("🛑 On hold", counts["ON_HOLD"])
    c[4].metric("❌ Rejected", counts["REJECTED"])
    c[5].metric("Approved value", f"${sum(r['total'] or 0 for r in runs if r['decision'] == 'APPROVED'):,.0f}")

    if not runs:
        st.info("No runs yet.")
    else:
        df = pd.DataFrame([{
            "Run": r["id"], "Time": r["created_at"], "Vendor": r["vendor_name"], "Invoice #": r["invoice_number"],
            "PO": (r["po_number"] or "") + (" (suggested)" if r["po_inferred"] else ""),
            "Total": r["total"], "Decision": r["decision"].replace("_", " "),
            "Reason": r["headline"] + (" · reviewed" if r["reviewed_by"] else ""), "Read by": r["extractor"],
            "File": r["filename"]} for r in runs])
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Total": st.column_config.NumberColumn(format="$%.2f")})

        st.subheader("Run details")
        rid = st.selectbox("Open run", [r["id"] for r in runs],
                           format_func=lambda i: next(f"#{r['id']} · {r['invoice_number']} · {r['decision']}"
                                                      for r in runs if r["id"] == i))
        run = store.get_run(rid)
        steps = json.loads(run["steps_json"])
        d1, d2 = st.columns([3, 2], gap="large")
        with d1:
            for ev in steps[:-1]:
                render_step(st.empty(), ev)
            dec = steps[-1]
            dec_now = {**dec, "decision": run["decision"], "run_id": run["id"]}
            if run["reviewed_by"]:
                dec_now["headline"] = f"{run['review_note']} (by {run['reviewed_by']}; " \
                                      f"system said {dec['decision'].replace('_', ' ')}: {dec['headline']})"
                dec_now["next_action"] = "Decision made by human reviewer."
            render_decision(dec_now)
        with d2:
            st.markdown("**Extracted data**")
            render_fields(json.loads(run["extraction_json"])["fields"])
            pdf_path = UPLOADS / f"{rid}.pdf"
            if pdf_path.exists():
                st.download_button("Download original PDF", pdf_path.read_bytes(),
                                   file_name=run["filename"], mime="application/pdf", key=f"dl{rid}")
            if run["decision"] in ("NEEDS_REVIEW", "ON_HOLD") and not run["reviewed_by"]:
                st.markdown("**Reviewer action** (human in the loop)")
                note = st.text_input("Note", placeholder="e.g. Confirmed PO-1004 with requester", key=f"n{rid}")
                b1, b2 = st.columns(2)
                if b1.button("Approve", key=f"a{rid}", width="stretch", disabled=not note):
                    store.set_review(rid, "APPROVED", note)
                    st.rerun()
                if b2.button("Reject", key=f"r{rid}", width="stretch", disabled=not note):
                    store.set_review(rid, "REJECTED", note)
                    st.rerun()

    st.subheader("PO balances")
    vend = {v["vendor_id"]: v["legal_name"] for v in store.vendors()}
    rows = []
    for po in store.purchase_orders().values():
        billed, n = store.po_billed(po["po_number"])
        rows.append({"PO": po["po_number"], "Vendor": vend.get(po["vendor_id"]), "Status": po["status"],
                     "PO total": po["total_amount"], "Approved so far": billed, "Invoices": n,
                     "Remaining": po["total_amount"] - billed, "Used": 100 * billed / po["total_amount"]})
    money_col = st.column_config.NumberColumn(format="$%.2f")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"PO total": money_col, "Approved so far": money_col, "Remaining": money_col,
                                "Used": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                        format="%.0f%%")})

# ---------------- master data tab ----------------

with tab_data:
    st.caption("A mock procurement system. In production these would come from the ERP.")
    st.markdown("**Vendor master**")
    st.dataframe(pd.read_csv(ROOT / "data" / "vendors.csv", dtype=str), hide_index=True, width="stretch")
    a, b = st.columns(2)
    with a:
        st.markdown("**Purchase orders**")
        st.dataframe(pd.read_csv(ROOT / "data" / "purchase_orders.csv"), hide_index=True, width="stretch")
    with b:
        st.markdown("**PO lines**")
        st.dataframe(pd.read_csv(ROOT / "data" / "po_lines.csv"), hide_index=True, width="stretch")
    st.markdown("**rules.yaml**")
    st.code((ROOT / "rules.yaml").read_text(), language="yaml")
