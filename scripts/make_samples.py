"""Generate a synthetic month of purchase invoices with realistic, deliberately planted problems.

Writes, under data/:
  invoices/invNN.png + invNN.json   rendered invoice and its ground-truth extraction
  gstr2b.csv                        what suppliers "filed" on the GST portal
  expected_issues.json              which issue codes each invoice should raise (used by tests)

All companies and GSTINs are fictional (valid checksums, made-up PANs).
Run from the repo root: python scripts/make_samples.py   (needs Pillow)
"""
import csv
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gst_agent.gstin import STATES, make_gstin  # noqa: E402

OUT = Path("data")
OUR = {"name": "Nilgiri Home Goods Pvt Ltd", "gstin": make_gstin("29", "AABCN4821K")}
SISTER = {"name": "Nilgiri Home Goods (Pune)", "gstin": make_gstin("27", "AABCN4821K", "2")}

SUPPLIERS = {
    "kavery": ("Kavery Packaging Industries", make_gstin("29", "AAFCK3310P")),
    "deccan": ("Deccan Steel Fixtures LLP", make_gstin("27", "AAKFD7720Q")),
    "coromandel": ("Coromandel Textiles", make_gstin("33", "ABZPC5512M")),
    "malnad": ("Malnad Office Supplies", make_gstin("29", "AQLPM9043R")),
    "yamuna": ("Yamuna Electricals Pvt Ltd", make_gstin("07", "AACCY2287H")),
    "sabarmati": ("Sabarmati Ceramics", make_gstin("24", "AAGFS6105D")),
    "hooghly": ("Hooghly Jute Mills", make_gstin("19", "AABCH8841E")),
    "konkan": ("Konkan Logistics", make_gstin("27", "AAJFK1290L")),
}


def make(supplier, number, day, lines, buyer=OUR):
    name, gstin = SUPPLIERS[supplier]
    items, taxable, tax = [], 0.0, 0.0
    for desc, hsn, qty, price, rate in lines:
        value = round(qty * price, 2)
        items.append({"description": desc, "hsn": hsn, "quantity": qty, "unit_price": price,
                      "taxable_value": value, "gst_rate": rate})
        taxable += value
        tax += value * rate / 100
    tax = round(tax, 2)
    intra = gstin[:2] == buyer["gstin"][:2]
    cgst = sgst = round(tax / 2, 2) if intra else 0.0
    igst = 0.0 if intra else tax
    exact = taxable + cgst + sgst + igst
    total = round(exact)
    return {
        "supplier_name": name, "supplier_gstin": gstin, "buyer_name": buyer["name"],
        "buyer_gstin": buyer["gstin"], "invoice_number": number, "invoice_date": f"2026-10-{day:02d}",
        "place_of_supply_code": buyer["gstin"][:2], "line_items": items,
        "totals": {"taxable_value": round(taxable, 2), "cgst": cgst, "sgst": sgst, "igst": igst,
                   "round_off": round(total - exact, 2), "total": float(total)},
    }


def build():
    inv = {}
    inv["inv01"] = make("kavery", "KPI/2026/0412", 3, [("Corrugated boxes 5-ply", "4819", 400, 38.5, 18)])
    inv["inv02"] = make("deccan", "DSF-1187", 5, [("Steel shelving rack 6ft", "9403", 12, 6450, 18)])
    inv["inv03"] = make("coromandel", "CT/26-27/0931", 7, [("Cotton bedsheet double", "6304", 150, 640, 5)])
    inv["inv04"] = make("malnad", "MOS-2290", 9, [("A4 paper ream 75gsm", "4802", 60, 285, 18),
                                                  ("Printer toner cartridge", "8443", 4, 3200, 18)])
    # Supplier GSTIN typed with one wrong character: checksum fails, and it won't match GSTR-2B.
    inv["inv05"] = make("yamuna", "YE/0098", 11, [("LED panel light 18W", "9405", 80, 520, 18)])
    g = inv["inv05"]["supplier_gstin"]
    inv["inv05"]["supplier_gstin"] = g[:9] + ("8" if g[9] != "8" else "6") + g[10:]
    # Intra-state supplier wrongly charged IGST.
    inv["inv06"] = make("kavery", "KPI/2026/0451", 13, [("Bubble wrap roll 1m x 100m", "3920", 25, 1150, 18)])
    t = inv["inv06"]["totals"]
    t["igst"], t["cgst"], t["sgst"] = round(t["cgst"] + t["sgst"], 2), 0.0, 0.0
    # 18% item but tax worked out at 12%.
    inv["inv07"] = make("sabarmati", "SC-26-3341", 15, [("Ceramic dinner set 24pc", "6912", 40, 2150, 18)])
    t = inv["inv07"]["totals"]
    t["igst"] = round(t["taxable_value"] * 0.12, 2)
    t["total"] = float(round(t["taxable_value"] + t["igst"]))
    t["round_off"] = round(t["total"] - t["taxable_value"] - t["igst"], 2)
    # Grand total typed wrong.
    inv["inv08"] = make("hooghly", "HJM/7765", 17, [("Jute shopping bags", "6305", 1000, 42, 5)])
    inv["inv08"]["totals"]["total"] += 500
    # Same invoice as inv02, scanned again.
    inv["inv09"] = json.loads(json.dumps(inv["inv02"]))
    # Billed to the Pune branch's GSTIN, so Karnataka entity can't claim it.
    inv["inv10"] = make("konkan", "KL-55120", 21, [("Freight Bengaluru-Pune", "9965", 1, 18500, 12)], buyer=SISTER)
    inv["inv11"] = make("yamuna", "YE/0112", 23, [("Ceiling fan 1200mm", "8414", 30, 2350, 18),
                                                  ("Jute doormat", "5702", 100, 180, 5)])
    # Invoice number longer than Rule 46 allows (warning only).
    inv["inv12"] = make("sabarmati", "SC-26-27-EXPORT-3398", 25, [("Ceramic mugs set of 6", "6912", 120, 540, 18)])

    expected = {
        "inv01": [], "inv02": [], "inv03": [], "inv04": [],
        "inv05": ["INVALID_SUPPLIER_GSTIN"], "inv06": ["WRONG_TAX_TYPE"], "inv07": ["TAX_MISMATCH"],
        "inv08": ["TOTAL_MISMATCH"], "inv09": ["DUPLICATE"], "inv10": ["WRONG_BUYER"],
        "inv11": [], "inv12": ["INVOICE_NUMBER_FORMAT"],
    }

    # What suppliers filed. inv03 not filed; inv04 filed with a lower amount; inv05 filed under the
    # correct GSTIN; inv10 appears in the Pune branch's 2B, not ours; one extra invoice we never recorded.
    portal = []
    for k in ["inv01", "inv02", "inv04", "inv05", "inv06", "inv07", "inv08", "inv11", "inv12"]:
        v = inv[k]
        t = dict(v["totals"])
        gstin = SUPPLIERS["yamuna"][1] if k == "inv05" else v["supplier_gstin"]
        if k == "inv04":
            t = {"taxable_value": 15000.0, "cgst": 1350.0, "sgst": 1350.0, "igst": 0.0}
        portal.append([gstin, v["invoice_number"], v["invoice_date"], t["taxable_value"], t["igst"], t["cgst"], t["sgst"]])
    portal.append([SUPPLIERS["deccan"][1], "DSF-1203", "2026-10-28", 22000.0, 3960.0, 0.0, 0.0])
    return inv, expected, portal


def render(v: dict, path: Path) -> None:
    img = Image.new("RGB", (1100, 1400), "white")
    d = ImageDraw.Draw(img)
    big, font, small = (ImageFont.load_default(size=s) for s in (34, 22, 19))
    y = 40
    d.text((40, y), v["supplier_name"], font=big, fill="black"); y += 50
    d.text((40, y), f"GSTIN: {v['supplier_gstin']}   State: {STATES.get(v['supplier_gstin'][:2], '?')}",
           font=font, fill="black"); y += 50
    d.text((820, 40), "TAX INVOICE", font=big, fill="#1f3a93")
    d.line((40, y, 1060, y), fill="black", width=2); y += 20
    d.text((40, y), f"Bill to: {v['buyer_name']}", font=font, fill="black")
    d.text((640, y), f"Invoice No: {v['invoice_number']}", font=font, fill="black"); y += 34
    d.text((40, y), f"GSTIN: {v['buyer_gstin']}", font=font, fill="black")
    d.text((640, y), f"Date: {v['invoice_date']}", font=font, fill="black"); y += 34
    pos = v["place_of_supply_code"]
    d.text((40, y), f"Place of supply: {pos}-{STATES.get(pos, '')}", font=font, fill="black"); y += 50

    cols = [40, 470, 580, 680, 820, 930]
    for x, h in zip(cols, ["Description", "HSN", "Qty", "Rate", "GST %", "Taxable"]):
        d.text((x, y), h, font=font, fill="black")
    y += 32
    d.line((40, y, 1060, y), fill="black", width=1); y += 12
    for li in v["line_items"]:
        for x, val in zip(cols, [li["description"], li["hsn"], f"{li['quantity']:g}", f"{li['unit_price']:,.2f}",
                                 f"{li['gst_rate']:g}%", f"{li['taxable_value']:,.2f}"]):
            d.text((x, y), val, font=small, fill="black")
        y += 32
    y += 20
    d.line((600, y, 1060, y), fill="black", width=1); y += 14
    t = v["totals"]
    for label, key in [("Taxable value", "taxable_value"), ("CGST", "cgst"), ("SGST", "sgst"),
                       ("IGST", "igst"), ("Round off", "round_off"), ("TOTAL (Rs)", "total")]:
        d.text((640, y), label, font=font, fill="black")
        d.text((900, y), f"{t[key]:,.2f}", font=font, fill="black")
        y += 34
    d.text((40, 1330), "This is a computer-generated sample invoice for testing. Not a real document.",
           font=small, fill="#888888")
    img.save(path)


def main() -> None:
    inv, expected, portal = build()
    folder = OUT / "invoices"
    folder.mkdir(parents=True, exist_ok=True)
    for k, v in inv.items():
        (folder / f"{k}.json").write_text(json.dumps(v, indent=2) + "\n", encoding="utf-8")
        render(v, folder / f"{k}.png")
    (OUT / "expected_issues.json").write_text(
        json.dumps({"our_gstin": OUR["gstin"], "issues": expected}, indent=2) + "\n", encoding="utf-8")
    with open(OUT / "gstr2b.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["supplier_gstin", "invoice_number", "invoice_date", "taxable_value", "igst", "cgst", "sgst"])
        w.writerows(portal)
    print(f"Wrote {len(inv)} invoices, gstr2b.csv and expected_issues.json to {OUT}/  (our GSTIN {OUR['gstin']})")


if __name__ == "__main__":
    main()
