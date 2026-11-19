"""CLI:  python -m gst_agent data/invoices --our-gstin 29... --gstr2b data/gstr2b.csv [--offline] [--out out/]"""
import argparse
import json
from pathlib import Path

from .pipeline import invoice_files, issues_csv, reconciliation_csv, run


def main() -> None:
    ap = argparse.ArgumentParser(prog="gst_agent", description="Read, validate and reconcile GST purchase invoices.")
    ap.add_argument("folder", type=Path, help="folder of invoice PDFs/images")
    ap.add_argument("--our-gstin", help="your company's GSTIN; flags invoices billed to someone else")
    ap.add_argument("--gstr2b", type=Path, help="GSTR-2B CSV to reconcile against")
    ap.add_argument("--offline", action="store_true", help="use the JSON next to each file instead of Gemini")
    ap.add_argument("--out", type=Path, default=Path("out"), help="where to write the CSV and JSON reports")
    a = ap.parse_args()

    files = invoice_files(a.folder)
    report = run(files, a.our_gstin, a.gstr2b.read_text(encoding="utf-8") if a.gstr2b else None, a.offline)

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (a.out / "invoices.csv").write_text(issues_csv(report), encoding="utf-8")
    if "reconciliation" in report:
        (a.out / "reconciliation.csv").write_text(reconciliation_csv(report), encoding="utf-8")

    s = report["summary"]
    print(f"Read {s['read']}/{s['files']} invoices: {s['clean']} clean, {s['with_errors']} with errors, "
          f"{s['with_warnings']} with warnings")
    for inv in report["invoices"]:
        for i in inv["issues"]:
            print(f"  [{i['severity']:7}] {inv['source']}: {i['message']}")
    for f in report["failures"]:
        print(f"  [failed ] {f['source']}: {f['error']}")
    if "reconciliation" in report:
        r = report["reconciliation"]["summary"]
        print(f"GSTR-2B: {r['matched']} matched, {r['amount_mismatch']} amount mismatch, "
              f"{r['missing_in_2b']} missing in 2B, {r['missing_in_books']} missing in books")
        print(f"ITC at risk: Rs {r['itc_at_risk']:,.2f}   Unclaimed ITC found in 2B: Rs {r['unclaimed_itc_in_2b']:,.2f}")
    print(f"Reports written to {a.out}/")


if __name__ == "__main__":
    main()
