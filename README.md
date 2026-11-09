# Invoice Decision Engine (Zamp ASA case study, PS-1)

Takes a vendor invoice PDF and produces a clear, reasoned decision (**APPROVED / NEEDS REVIEW / ON HOLD / REJECTED**) with every step visible.

**Design principle:** AI *reads*, rules *decide*. Gemini is used only to extract fields from the PDF (text or scanned). Every decision is a deterministic, auditable rule in `pipeline/engine.py`, configured in `rules.yaml`.

## Pipeline

| # | Step | What it does |
|---|------|--------------|
| 1 | Intake | Fingerprints the file (SHA-256) and rejects exact re-uploads |
| 2 | Extract (AI) | Gemini reads the PDF and returns JSON with per-field confidence. Model chain: `flash-lite` → `flash` → rule-based fallback |
| 3 | Validate | Required fields, line maths, subtotal + tax = total, date sanity, low-confidence fields |
| 4 | Vendor match | Tax ID + fuzzy name vs vendor master; blocked vendors; **bank details vs master** |
| 5 | PO match | Explicit PO lookup, or **infers** the best open PO (line similarity + amount fit) for review |
| 6 | Business checks | Duplicate invoice number (normalised), same-amount duplicates, qty / price vs PO lines, **remaining PO balance across invoices**, tolerance, auto-approve limit |
| 7 | Decision | Worst check wins: info → approve with note, warn → review, hold → on hold, fail → reject |

## Demo scenarios (`samples/`)

| # | Scenario | Expected |
|---|----------|----------|
| 1 | H1 Happy path | APPROVED |
| 2-4 | E1 Split PO: 3 invoices against one $10k PO; the 3rd over-bills (qty 30 vs 20, $500 over remaining) | APPROVED, APPROVED, NEEDS REVIEW |
| 5-6 | E2 Duplicate: `NCS-1042` resubmitted as `NCS1042` with a new date | APPROVED, REJECTED |
| 7 | E3 Scanned image, no PO number: AI vision reads it, PO-1004 suggested | NEEDS REVIEW |
| 8 | E4 Bank account changed (invoice asks to pay a new account) | ON HOLD |
| 9 | T1 $40 freight not on PO, within $50 tolerance | APPROVED with note |

Run them in order; E1 and E2 depend on earlier runs. Use **Reset demo data** in the sidebar to start over.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# put GEMINI_API_KEY=... in .env
.venv\Scripts\streamlit run app.py
```

`python run_cli.py` runs all 9 scenarios in the terminal and checks expected outcomes.
`python generate_invoices.py` regenerates the sample PDFs.

## Assumptions

- Invoices arrive one at a time by upload; email intake would be a production add-on.
- The procurement system is mocked with CSVs in `data/`; the vendor master is the source of truth for bank details.
- PO balance is tracked pre-tax; only APPROVED invoices consume PO balance.
- Tolerance = the smaller of 2% and $50 (`rules.yaml`).
- Inferred POs and bank-detail changes always go to a human.
