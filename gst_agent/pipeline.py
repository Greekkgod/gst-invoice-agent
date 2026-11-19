"""Extract -> validate -> reconcile, for a batch of invoice files."""
import csv
import io
import json
from datetime import date
from pathlib import Path

from .extract import MIME, extract
from .reconcile import read_gstr2b, reconcile
from .validate import check_batch


def load_invoice(path: Path, offline: bool) -> dict:
    """Offline mode reads the JSON next to the file (sample data) instead of calling Gemini."""
    if offline:
        return json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    return extract(path)


def invoice_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in MIME)


def run(files: list[Path], our_gstin: str | None = None, gstr2b_csv: str | None = None,
        offline: bool = False, today: date | None = None) -> dict:
    invoices, failures = [], []
    for f in files:
        try:
            inv = load_invoice(f, offline)
        except Exception as e:  # one unreadable invoice should not sink the batch
            failures.append({"source": f.name, "error": str(e)})
            continue
        inv["source"] = f.name
        invoices.append(inv)

    issues = check_batch(invoices, our_gstin, today)
    for inv, found in zip(invoices, issues):
        inv["issues"] = found
        inv["status"] = "error" if any(i["severity"] == "error" for i in found) else (
            "warning" if found else "ok")

    report = {
        "invoices": invoices,
        "failures": failures,
        "summary": {
            "files": len(files),
            "read": len(invoices),
            "clean": sum(i["status"] == "ok" for i in invoices),
            "with_errors": sum(i["status"] == "error" for i in invoices),
            "with_warnings": sum(i["status"] == "warning" for i in invoices),
            "total_tax_in_books": round(sum(sum((i.get("totals") or {}).get(k) or 0 for k in ("cgst", "sgst", "igst"))
                                            for i in invoices), 2),
        },
    }
    if gstr2b_csv is not None:
        report["reconciliation"] = reconcile(invoices, read_gstr2b(gstr2b_csv))
    return report


def issues_csv(report: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["file", "supplier", "supplier_gstin", "invoice_number", "invoice_date", "taxable_value",
                "cgst", "sgst", "igst", "total", "status", "issues"])
    for inv in report["invoices"]:
        t = inv.get("totals") or {}
        w.writerow([inv["source"], inv.get("supplier_name"), inv.get("supplier_gstin"), inv.get("invoice_number"),
                    inv.get("invoice_date"), t.get("taxable_value"), t.get("cgst"), t.get("sgst"), t.get("igst"),
                    t.get("total"), inv["status"], " | ".join(i["message"] for i in inv["issues"])])
    return out.getvalue()


def reconciliation_csv(report: dict) -> str:
    out = io.StringIO()
    cols = ["status", "supplier_gstin", "supplier_name", "invoice_number", "books_tax", "portal_tax",
            "itc_at_risk", "action"]
    w = csv.DictWriter(out, cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(report.get("reconciliation", {}).get("rows", []))
    return out.getvalue()
