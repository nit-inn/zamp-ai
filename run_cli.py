"""Run every sample invoice through the pipeline in demo order and print results.

Usage:  python run_cli.py            (resets history first)
        python run_cli.py file.pdf   (one file, keeps history)
"""
import json
import sys

from pipeline import store
from pipeline.config import SAMPLES
from pipeline.engine import run_pipeline

ICON = {"pass": "OK ", "info": "i  ", "warn": "!  ", "hold": "HLD", "fail": "X  ", "skipped": "-  "}


def run(path):
    print(f"\n=== {path.name}")
    for ev in run_pipeline(path.read_bytes(), path.name):
        if ev["type"] == "done":
            print(f"  [{ICON[ev['status']]}] {ev['title']:<22} {ev['summary']}  ({ev['ms']} ms)")
            for c in ev["checks"]:
                if c["level"] != "pass":
                    print(f"         - {c['level']}: {c['title']}: {c['detail']}")
        elif ev["type"] == "decision":
            print(f"  ==> {ev['decision']}: {ev['headline']}")
            if ev["decision"] == "PENDING_APPROVAL":  # stand in for the user pressing Approve
                store.set_review(ev["run_id"], "APPROVED", "Approved after all checks passed")
                print("  ==> APPROVED by user")
                return "APPROVED"
            return ev["decision"]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        from pathlib import Path
        run(Path(sys.argv[1]))
        sys.exit()
    store.reset()
    manifest = sorted(json.loads((SAMPLES / "manifest.json").read_text()), key=lambda m: m["order"])
    results = []
    for m in manifest:
        got = run(SAMPLES / m["file"])
        results.append((m["scenario"], m["expect"], got))
    print("\n=== SUMMARY")
    for s, exp, got in results:
        print(f"  {'PASS' if exp == got else 'FAIL'}  {s:<40} expected {exp:<13} got {got}")
