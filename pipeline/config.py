"""Paths, secrets and business rules."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLES = ROOT / "samples"
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "invoices.db"

load_dotenv(ROOT / ".env")


def get_secret(name: str, default: str | None = None) -> str | None:
    """Read from env/.env locally, or from Streamlit secrets when deployed."""
    val = os.getenv(name)
    if val:
        return val
    try:
        import streamlit as st

        return st.secrets.get(name, default)  # type: ignore[attr-defined]
    except Exception:
        return default


def gemini_key() -> str | None:
    key = get_secret("GEMINI_API_KEY")
    if not key or key.startswith("paste-"):
        return None
    return key


DEFAULT_MODELS = "gemini-3.5-flash-lite,gemini-3.5-flash"


def gemini_models() -> list[str]:
    """Ordered list: if the first model is busy/unavailable, the next one is tried."""
    raw = get_secret("GEMINI_MODEL", DEFAULT_MODELS) or DEFAULT_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


def gemini_model() -> str:
    return gemini_models()[0]


def load_rules() -> dict:
    """Re-read on every run so rules.yaml can be edited live during a demo."""
    with open(ROOT / "rules.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
