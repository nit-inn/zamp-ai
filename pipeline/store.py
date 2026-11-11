"""Master data (CSV = mock procurement system) and run history (SQLite)."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime

from .config import DATA, DB_PATH, UPLOADS


# ---------- master data (read-only "procurement system") ----------

def _read_csv(name: str) -> list[dict]:
    with open(DATA / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def vendors() -> list[dict]:
    rows = _read_csv("vendors.csv")
    for r in rows:
        r["aliases"] = [a for a in r["aliases"].split("|") if a]
    return rows


def purchase_orders() -> dict[str, dict]:
    pos = {r["po_number"]: {**r, "total_amount": float(r["total_amount"]), "lines": []}
           for r in _read_csv("purchase_orders.csv")}
    for line in _read_csv("po_lines.csv"):
        pos[line["po_number"]]["lines"].append(
            {"line_no": int(line["line_no"]), "description": line["description"],
             "qty": float(line["qty"]), "unit_price": float(line["unit_price"])})
    return pos


def norm_invoice_number(s: str | None) -> str:
    """'INV-1042', 'inv 1042', 'INV1042' -> 'INV1042'."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# ---------- run history ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    filename TEXT,
    file_hash TEXT,
    extractor TEXT,
    vendor_id TEXT,
    vendor_name TEXT,
    invoice_number TEXT,
    invoice_number_norm TEXT,
    invoice_date TEXT,
    po_number TEXT,
    po_inferred INTEGER DEFAULT 0,
    currency TEXT,
    subtotal REAL,
    tax REAL,
    total REAL,
    decision TEXT,
    headline TEXT,
    next_action TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    extraction_json TEXT,
    steps_json TEXT
);
"""


def _conn() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def save_run(rec: dict) -> int:
    cols = [k for k in rec if k != "id"]
    with _conn() as c:
        cur = c.execute(f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [rec[k] for k in cols])
        return int(cur.lastrowid)


def list_runs() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC")]


def get_run(run_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None


def find_by_hash(file_hash: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE file_hash=? AND decision!='REJECTED' ORDER BY id LIMIT 1",
                      (file_hash,)).fetchone()
        return dict(r) if r else None


def find_same_number(vendor_id: str, number_norm: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE vendor_id=? AND invoice_number_norm=? "
                      "AND decision!='REJECTED' ORDER BY id LIMIT 1", (vendor_id, number_norm)).fetchone()
        return dict(r) if r else None


def find_same_amount(vendor_id: str, total: float, number_norm: str) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM runs WHERE vendor_id=? AND ABS(total-?)<0.01 AND invoice_number_norm!=? "
            "AND decision!='REJECTED'", (vendor_id, total, number_norm))]


def po_billed(po_number: str, approved_only: bool = False) -> tuple[float, int]:
    """Amount (pre-tax) committed against a PO, and how many invoices.

    By default this includes invoices awaiting approval, so two clean invoices can't both claim the
    same remaining balance; approved_only=True counts only those a person has approved."""
    states = ("APPROVED",) if approved_only else ("APPROVED", "PENDING_APPROVAL")
    with _conn() as c:
        r = c.execute(f"SELECT COALESCE(SUM(subtotal),0), COUNT(*) FROM runs WHERE po_number=? "
                      f"AND decision IN ({','.join('?' * len(states))})", (po_number, *states)).fetchone()
        return float(r[0]), int(r[1])


def uploaded_invoice_files(po_number: str, known: set[str]) -> int:
    """Distinct uploaded files (not in `known`, e.g. the sample set) processed against a PO."""
    with _conn() as c:
        names = {r[0] for r in c.execute("SELECT DISTINCT filename FROM runs WHERE po_number=?", (po_number,))}
    return len(names - known)


def set_review(run_id: int, decision: str, note: str) -> None:
    with _conn() as c:
        c.execute("UPDATE runs SET decision=?, reviewed_by='AP reviewer', review_note=? WHERE id=?",
                  (decision, note, run_id))


def reset() -> None:
    with _conn() as c:
        c.execute("DELETE FROM runs")
        c.execute("DELETE FROM sqlite_sequence WHERE name='runs'")
    if UPLOADS.exists():
        for f in UPLOADS.iterdir():
            f.unlink()


def to_json(x) -> str:
    return json.dumps(x, default=str)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
