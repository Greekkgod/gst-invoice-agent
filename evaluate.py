"""Measure Gemini extraction accuracy on the sample invoices, field by field and end to end.

    GEMINI_API_KEY=... python evaluate.py

"End to end" means: after extraction, did validation raise exactly the issues planted in that
invoice? That is the number that matters to an accountant. Writes eval_results.json.
"""
import json
import time
from datetime import date
from pathlib import Path

from gst_agent.extract import extract
from gst_agent.pipeline import invoice_files
from gst_agent.validate import check_batch

DATA = Path("data")
FIELDS = ["supplier_gstin", "buyer_gstin", "invoice_number", "invoice_date"]
AMOUNTS = ["taxable_value", "cgst", "sgst", "igst", "total"]


def main() -> None:
    expected = json.loads((DATA / "expected_issues.json").read_text(encoding="utf-8"))
    files = invoice_files(DATA / "invoices")
    hits = {k: 0 for k in FIELDS + AMOUNTS + ["line_count"]}
    extracted, rows, seconds = [], [], []

    for f in files:
        truth = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
        start = time.perf_counter()
        try:
            got = extract(f)
        except Exception as e:
            print(f"{f.name}: FAILED {e}")
            got = {}
        seconds.append(time.perf_counter() - start)
        got["source"] = f.name
        extracted.append(got)

        wrong = []
        for k in FIELDS:
            ok = (got.get(k) or "").strip().upper() == truth[k].upper()
            hits[k] += ok
            wrong += [] if ok else [k]
        for k in AMOUNTS:
            ok = abs(((got.get("totals") or {}).get(k) or 0) - truth["totals"][k]) <= 1
            hits[k] += ok
            wrong += [] if ok else [k]
        ok = len(got.get("line_items") or []) == len(truth["line_items"])
        hits["line_count"] += ok
        wrong += [] if ok else ["line_count"]
        rows.append({"file": f.name, "wrong_fields": wrong})
        print(f"{f.name}: {'all fields correct' if not wrong else 'wrong: ' + ', '.join(wrong)}")

    found = check_batch(extracted, expected["our_gstin"], date(2026, 11, 19))
    detection = sum(sorted(i["code"] for i in issues) == expected["issues"][f.stem] for f, issues in zip(files, found))
    for row, issues in zip(rows, found):
        row["issues_found"] = sorted(i["code"] for i in issues)

    n = len(files)
    field_acc = sum(hits.values()) / (n * len(hits))
    print("\n| Metric | Value |\n|---|---|")
    print(f"| Invoices | {n} |")
    print(f"| Field-level extraction accuracy | {field_acc:.1%} |")
    print(f"| Invoices with every planted issue caught, no false alarms | {detection}/{n} |")
    print(f"| Avg seconds per invoice | {sum(seconds) / n:.1f} |")
    for k, v in hits.items():
        print(f"| `{k}` | {v}/{n} |")

    Path("eval_results.json").write_text(json.dumps({
        "field_accuracy": field_acc, "end_to_end_correct": detection, "invoices": n,
        "avg_seconds": sum(seconds) / n, "per_field": hits, "rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
