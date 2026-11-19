"""Deterministic GST checks on extracted invoices.

The LLM only reads the document. Every rule that decides whether input tax
credit can be claimed is checked here in code, never trusted to the model.
Invoices are plain dicts shaped like extract.SCHEMA.
"""
import re
from datetime import date

from .gstin import gstin_error

TOLERANCE = 1.0  # rupees; invoices round to the nearest rupee
GST_RATES = {0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 12, 18, 28, 40}
INVOICE_NO = re.compile(r"^[A-Za-z0-9/-]{1,16}$")  # CGST Rule 46: max 16 chars, letters, digits, - and /


def norm_invoice_no(no: str | None) -> str:
    """'INV/001', 'inv-0001' and 'INV 1' are the same invoice: drop separators and zero padding."""
    parts = re.findall(r"[A-Z]+|[0-9]+", (no or "").upper())
    return "-".join(p.lstrip("0") or "0" if p.isdigit() else p for p in parts)


def place_of_supply(inv: dict) -> str | None:
    pos = (inv.get("place_of_supply_code") or "").strip()
    if pos:
        return pos.zfill(2)
    buyer = inv.get("buyer_gstin") or ""
    return buyer[:2] if len(buyer) >= 2 else None


def _close(a: float, b: float) -> bool:
    return abs((a or 0) - (b or 0)) <= TOLERANCE


def check_invoice(inv: dict, our_gstin: str | None = None, today: date | None = None) -> list[dict]:
    """Return a list of issues: {code, severity, message}."""
    issues = []

    def add(code, severity, message):
        issues.append({"code": code, "severity": severity, "message": message})

    if err := gstin_error(inv.get("supplier_gstin")):
        add("INVALID_SUPPLIER_GSTIN", "error", f"Supplier GSTIN {inv.get('supplier_gstin')!r}: {err}")
    if err := gstin_error(inv.get("buyer_gstin")):
        add("INVALID_BUYER_GSTIN", "error", f"Buyer GSTIN {inv.get('buyer_gstin')!r}: {err}")
    elif our_gstin and inv["buyer_gstin"].upper() != our_gstin.upper():
        add("WRONG_BUYER", "error",
            f"Billed to {inv['buyer_gstin']}, not your GSTIN {our_gstin}. ITC cannot be claimed on it.")

    no = inv.get("invoice_number") or ""
    if not INVOICE_NO.match(no):
        add("INVOICE_NUMBER_FORMAT", "warning",
            f"Invoice number {no!r} breaks Rule 46 (1-16 chars, letters, digits, - or /)")

    try:
        d = date.fromisoformat(inv.get("invoice_date") or "")
        if d > (today or date.today()):
            add("FUTURE_DATE", "error", f"Invoice date {d} is in the future")
    except ValueError:
        add("BAD_DATE", "error", f"Invoice date {inv.get('invoice_date')!r} is not a valid date")

    lines = inv.get("line_items") or []
    expected_tax = 0.0
    for i, li in enumerate(lines, 1):
        rate = li.get("gst_rate")
        if rate not in GST_RATES:
            add("INVALID_RATE", "error", f"Line {i}: GST rate {rate}% is not a valid GST slab")
        qty, price, taxable = li.get("quantity"), li.get("unit_price"), li.get("taxable_value") or 0
        if qty is not None and price is not None and not _close(qty * price, taxable):
            add("LINE_MATH", "warning", f"Line {i}: {qty} x {price} = {qty * price:,.2f}, invoice says {taxable:,.2f}")
        expected_tax += taxable * (rate or 0) / 100

    t = inv.get("totals") or {}
    cgst, sgst, igst = t.get("cgst") or 0, t.get("sgst") or 0, t.get("igst") or 0
    taxable_total = t.get("taxable_value") or 0

    if lines and not _close(sum(li.get("taxable_value") or 0 for li in lines), taxable_total):
        add("TAXABLE_MISMATCH", "error", "Line items do not add up to the taxable value")

    supplier_state, pos = (inv.get("supplier_gstin") or "")[:2], place_of_supply(inv)
    if supplier_state and pos:
        if supplier_state == pos and igst > TOLERANCE:
            add("WRONG_TAX_TYPE", "error",
                "Intra-state supply charged IGST; it should be CGST + SGST. ITC will be disputed.")
        elif supplier_state != pos and (cgst + sgst) > TOLERANCE:
            add("WRONG_TAX_TYPE", "error",
                "Inter-state supply charged CGST + SGST; it should be IGST. ITC will be disputed.")
        elif supplier_state == pos and not _close(cgst, sgst):
            add("CGST_SGST_UNEQUAL", "error", f"CGST {cgst:,.2f} and SGST {sgst:,.2f} should be equal")

    if lines and not _close(cgst + sgst + igst, expected_tax):
        add("TAX_MISMATCH", "error",
            f"Tax charged {cgst + sgst + igst:,.2f} but line rates give {expected_tax:,.2f}")

    grand = taxable_total + cgst + sgst + igst + (t.get("round_off") or 0)
    if not _close(grand, t.get("total") or 0):
        add("TOTAL_MISMATCH", "error", f"Taxable + tax = {grand:,.2f}, invoice total says {t.get('total') or 0:,.2f}")

    return issues


def check_batch(invoices: list[dict], our_gstin: str | None = None, today: date | None = None) -> list[list[dict]]:
    """Check every invoice and flag duplicates (same supplier and invoice number)."""
    results = [check_invoice(inv, our_gstin, today) for inv in invoices]
    seen = {}
    for i, inv in enumerate(invoices):
        key = ((inv.get("supplier_gstin") or "").upper(), norm_invoice_no(inv.get("invoice_number")))
        if key in seen:
            results[i].append({"code": "DUPLICATE", "severity": "error",
                               "message": f"Same supplier and invoice number as {seen[key]}. Claiming twice is fraud risk."})
        else:
            seen[key] = inv.get("source") or f"invoice #{i + 1}"
    return results
