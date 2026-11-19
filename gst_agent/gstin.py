"""GSTIN format and checksum validation.

A GSTIN is 15 characters: 2-digit state code, 10-character PAN, entity number,
the letter Z by default, and a mod-36 check character.
"""
import re

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][A-Z0-9][0-9A-Z]$")

STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory",
}


def check_char(first14: str) -> str:
    total = 0
    for i, c in enumerate(first14):
        product = CHARS.index(c) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return CHARS[(36 - total % 36) % 36]


def gstin_error(gstin: str | None) -> str | None:
    """Return why a GSTIN is invalid, or None if it is valid."""
    if not gstin:
        return "missing"
    g = gstin.strip().upper()
    if not PATTERN.match(g):
        return "wrong format"
    if g[:2] not in STATES:
        return f"unknown state code {g[:2]}"
    if check_char(g[:14]) != g[14]:
        return f"checksum fails (expected {check_char(g[:14])}, got {g[14]})"
    return None


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    """Build a valid GSTIN, used to generate sample data."""
    first14 = f"{state}{pan}{entity}Z"
    return first14 + check_char(first14)
