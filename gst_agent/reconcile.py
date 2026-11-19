"""Reconcile purchase invoices (your books) against GSTR-2B (what suppliers filed).

ITC can only be claimed on invoices that appear in GSTR-2B, so anything in the
books but missing from 2B, or filed for less tax, is credit at risk.
"""
import csv
import io

from .validate import TOLERANCE, norm_invoice_no

# shortcut: expects a simplified CSV, not the portal's multi-sheet Excel/JSON; add a portal parser when needed.
GSTR2B_COLUMNS = ["supplier_gstin", "invoice_number", "invoice_date", "taxable_value", "igst", "cgst", "sgst"]


def read_gstr2b(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and (missing := set(GSTR2B_COLUMNS) - set(rows[0])):
        raise ValueError(f"GSTR-2B CSV is missing columns: {sorted(missing)}")
    for r in rows:
        for k in ("taxable_value", "igst", "cgst", "sgst"):
            r[k] = float(r[k] or 0)
    return rows


def _key(gstin, number):
    return (gstin or "").strip().upper(), norm_invoice_no(number)


def _tax(t: dict) -> float:
    return (t.get("igst") or 0) + (t.get("cgst") or 0) + (t.get("sgst") or 0)


def reconcile(invoices: list[dict], gstr2b: list[dict]) -> dict:
    portal = {_key(r["supplier_gstin"], r["invoice_number"]): r for r in gstr2b}
    rows, matched_keys, seen = [], set(), set()

    for inv in invoices:
        key = _key(inv.get("supplier_gstin"), inv.get("invoice_number"))
        if key in seen:
            continue  # duplicates are reported by validation; count the credit once
        seen.add(key)
        books_tax = _tax(inv.get("totals") or {})
        row = {"supplier_gstin": key[0], "invoice_number": inv.get("invoice_number"),
               "supplier_name": inv.get("supplier_name"), "books_tax": round(books_tax, 2)}
        p = portal.get(key)
        if p is None:
            row.update(status="MISSING_IN_2B", portal_tax=0.0, itc_at_risk=round(books_tax, 2),
                       action="Supplier has not filed this invoice. Ask them to file GSTR-1 before you claim ITC.")
        else:
            matched_keys.add(key)
            portal_tax = _tax(p)
            diff = books_tax - portal_tax
            if abs(diff) <= TOLERANCE and abs((inv.get("totals") or {}).get("taxable_value", 0) - p["taxable_value"]) <= TOLERANCE:
                row.update(status="MATCHED", portal_tax=round(portal_tax, 2), itc_at_risk=0.0, action="")
            else:
                row.update(status="AMOUNT_MISMATCH", portal_tax=round(portal_tax, 2),
                           itc_at_risk=round(max(diff, 0), 2),
                           action="Supplier filed different amounts. Get a credit/debit note or an amended GSTR-1.")
        rows.append(row)

    for key, p in portal.items():
        if key not in matched_keys:
            rows.append({"supplier_gstin": key[0], "invoice_number": p["invoice_number"], "supplier_name": None,
                         "books_tax": 0.0, "portal_tax": round(_tax(p), 2), "itc_at_risk": 0.0,
                         "status": "MISSING_IN_BOOKS",
                         "action": "In GSTR-2B but not in your books. Find the invoice or you lose eligible credit."})

    count = lambda s: sum(r["status"] == s for r in rows)  # noqa: E731
    return {
        "rows": rows,
        "summary": {
            "matched": count("MATCHED"),
            "amount_mismatch": count("AMOUNT_MISMATCH"),
            "missing_in_2b": count("MISSING_IN_2B"),
            "missing_in_books": count("MISSING_IN_BOOKS"),
            "itc_at_risk": round(sum(r["itc_at_risk"] for r in rows), 2),
            "unclaimed_itc_in_2b": round(sum(r["portal_tax"] for r in rows if r["status"] == "MISSING_IN_BOOKS"), 2),
        },
    }
