"""Unit tests for GSTIN validation, GST rules and GSTR-2B reconciliation."""
import copy
from datetime import date

import pytest

from gst_agent.gstin import gstin_error, make_gstin
from gst_agent.reconcile import read_gstr2b, reconcile
from gst_agent.validate import check_batch, check_invoice, norm_invoice_no

TODAY = date(2026, 11, 19)
BUYER = make_gstin("29", "AABCN4821K")
INTRA = make_gstin("29", "AAFCK3310P")  # same state as buyer
INTER = make_gstin("27", "AAKFD7720Q")


def codes(issues):
    return sorted(i["code"] for i in issues)


def invoice(supplier=INTRA, **totals):
    """Rs 10,000 at 18%: intra-state 900 + 900, inter-state 1800 IGST."""
    intra = supplier[:2] == BUYER[:2]
    t = {"taxable_value": 10000.0, "cgst": 900.0 if intra else 0.0, "sgst": 900.0 if intra else 0.0,
         "igst": 0.0 if intra else 1800.0, "round_off": 0.0, "total": 11800.0}
    t.update(totals)
    return {"supplier_name": "S", "supplier_gstin": supplier, "buyer_gstin": BUYER,
            "invoice_number": "INV/001", "invoice_date": "2026-10-01",
            "line_items": [{"description": "x", "quantity": 10, "unit_price": 1000, "taxable_value": 10000, "gst_rate": 18}],
            "totals": t}


# --- GSTIN ---------------------------------------------------------------

@pytest.mark.parametrize("g", ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "33AAACH7409R1Z8", BUYER])
def test_real_and_generated_gstins_pass(g):
    assert gstin_error(g) is None


@pytest.mark.parametrize("g, why", [
    ("27AAPFU0939F1ZW", "checksum"),      # last char wrong
    ("27AAPFU0939G1ZV", "checksum"),      # one PAN letter mistyped
    ("27AAPFU0939F1Z", "format"),         # too short
    ("99AAPFU0939F1ZV", "state code"),    # no such state
    ("", "missing"),
])
def test_bad_gstins_fail(g, why):
    assert why in gstin_error(g)


# --- invoice rules -------------------------------------------------------

def test_clean_invoices_have_no_issues():
    assert check_invoice(invoice(INTRA), BUYER, TODAY) == []
    assert check_invoice(invoice(INTER), BUYER, TODAY) == []


def test_igst_on_intra_state_supply():
    inv = invoice(INTRA, cgst=0.0, sgst=0.0, igst=1800.0)
    assert codes(check_invoice(inv, BUYER, TODAY)) == ["WRONG_TAX_TYPE"]


def test_cgst_sgst_on_inter_state_supply():
    inv = invoice(INTER, cgst=900.0, sgst=900.0, igst=0.0)
    assert codes(check_invoice(inv, BUYER, TODAY)) == ["WRONG_TAX_TYPE"]


def test_place_of_supply_overrides_buyer_state():
    inv = invoice(INTER)
    inv["place_of_supply_code"] = "27"  # goods delivered in the supplier's own state
    assert "WRONG_TAX_TYPE" in codes(check_invoice(inv, BUYER, TODAY))


def test_tax_at_wrong_rate():
    inv = invoice(INTER, igst=1200.0, total=11200.0)
    assert codes(check_invoice(inv, BUYER, TODAY)) == ["TAX_MISMATCH"]


def test_total_typo_but_rounding_allowed():
    assert codes(check_invoice(invoice(INTRA, total=12300.0), BUYER, TODAY)) == ["TOTAL_MISMATCH"]
    assert check_invoice(invoice(INTRA, total=11800.4), BUYER, TODAY) == []


def test_unequal_cgst_sgst():
    inv = invoice(INTRA, cgst=1000.0, sgst=800.0)
    assert codes(check_invoice(inv, BUYER, TODAY)) == ["CGST_SGST_UNEQUAL"]


def test_invalid_rate_and_line_math():
    inv = invoice(INTER)
    inv["line_items"][0].update(gst_rate=15, quantity=9)
    found = codes(check_invoice(inv, BUYER, TODAY))
    assert "INVALID_RATE" in found and "LINE_MATH" in found


def test_wrong_buyer_future_date_and_long_number():
    inv = invoice(INTRA)
    inv.update(invoice_date="2027-01-01", invoice_number="A" * 17)
    other = make_gstin("29", "ZZZZZ9999Z")
    assert codes(check_invoice(inv, other, TODAY)) == ["FUTURE_DATE", "INVOICE_NUMBER_FORMAT", "WRONG_BUYER"]


def test_duplicates_ignore_formatting():
    a, b = invoice(INTRA), invoice(INTRA)
    b["invoice_number"] = "inv-0001"
    assert norm_invoice_no("INV/001") == norm_invoice_no("inv-0001") == norm_invoice_no("INV 1")
    assert norm_invoice_no("INV/10") != norm_invoice_no("INV/1")
    assert norm_invoice_no("KPI/2026/0412") == "KPI-2026-412"
    assert norm_invoice_no("1/12") != norm_invoice_no("11/2")
    results = check_batch([a, b], BUYER, TODAY)
    assert results[0] == [] and codes(results[1]) == ["DUPLICATE"]


# --- reconciliation ------------------------------------------------------

CSV_HEAD = "supplier_gstin,invoice_number,invoice_date,taxable_value,igst,cgst,sgst\n"


def test_reconcile_all_statuses():
    matched, missing, short = invoice(INTRA), invoice(INTER), invoice(INTRA)
    missing["invoice_number"], short["invoice_number"] = "INV/002", "INV/003"
    portal = read_gstr2b(CSV_HEAD +
                         f"{INTRA},inv-001,2026-10-01,10000,0,900,900\n"     # formatting differs, still matches
                         f"{INTRA},INV/003,2026-10-01,5000,0,450,450\n"      # filed for less
                         f"{INTER},INV/999,2026-10-02,2000,360,0,0\n")       # not in books
    r = reconcile([matched, missing, short], portal)
    status = {row["invoice_number"]: row["status"] for row in r["rows"]}
    assert status == {"INV/001": "MATCHED", "INV/002": "MISSING_IN_2B",
                      "INV/003": "AMOUNT_MISMATCH", "INV/999": "MISSING_IN_BOOKS"}
    assert r["summary"]["itc_at_risk"] == 1800 + 900
    assert r["summary"]["unclaimed_itc_in_2b"] == 360


def test_reconcile_counts_duplicate_once():
    a = invoice(INTRA)
    r = reconcile([a, copy.deepcopy(a)], [])
    assert len(r["rows"]) == 1 and r["summary"]["itc_at_risk"] == 1800


def test_gstr2b_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        read_gstr2b("supplier_gstin,invoice_number\nX,1\n")


def test_no_line_items_fails_closed():
    inv = invoice(INTRA)
    inv["line_items"] = []
    assert codes(check_invoice(inv, BUYER, TODAY)) == ["NO_LINE_ITEMS"]
