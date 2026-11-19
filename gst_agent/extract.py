"""Read an invoice PDF or image into structured JSON with Gemini (standard library only)."""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_BYTES = 15 * 1024 * 1024  # stay under Gemini's inline request limit

PROMPT = """Extract this Indian GST tax invoice exactly as printed. Do not correct, recompute or
infer any number: if the invoice's arithmetic is wrong, copy the wrong numbers.
Dates as YYYY-MM-DD. Amounts in rupees as numbers. GSTINs as printed, uppercase.
place_of_supply_code is the 2-digit state code printed for place of supply, or "" if absent.
Use 0 for a tax that is not charged."""

NUM, STR = {"type": "NUMBER"}, {"type": "STRING"}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "supplier_name": STR, "supplier_gstin": STR, "buyer_name": STR, "buyer_gstin": STR,
        "invoice_number": STR, "invoice_date": STR, "place_of_supply_code": STR,
        "line_items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "description": STR, "hsn": STR, "quantity": NUM, "unit_price": NUM,
            "taxable_value": NUM, "gst_rate": NUM,
        }, "required": ["description", "taxable_value", "gst_rate"]}},
        "totals": {"type": "OBJECT", "properties": {
            "taxable_value": NUM, "cgst": NUM, "sgst": NUM, "igst": NUM, "round_off": NUM, "total": NUM,
        }, "required": ["taxable_value", "cgst", "sgst", "igst", "total"]},
    },
    "required": ["supplier_name", "supplier_gstin", "buyer_gstin", "invoice_number",
                 "invoice_date", "line_items", "totals"],
}
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def build_request(data: bytes, mime: str) -> dict:
    return {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }


def _post(url: str, body: dict, api_key: str, attempts: int = 4) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # Free-tier rate limits (429) and overloads (503) are worth waiting out.
            if e.code not in (429, 503) or attempt == attempts - 1:
                raise RuntimeError(f"Gemini API {e.code}: {e.read().decode(errors='replace')[:500]}") from e
            time.sleep(2 ** attempt * 5)


def extract(path: Path, api_key: str | None = None, model: str | None = None, post=_post) -> dict:
    mime = MIME.get(path.suffix.lower())
    if not mime:
        raise ValueError(f"Unsupported file type {path.suffix!r}; use PDF, PNG, JPG or WEBP")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than 15 MB")
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY to read invoices (or use offline mode with the sample data)")
    model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    resp = post(API_URL.format(model=model), build_request(data, mime), api_key)
    try:
        return json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Gemini returned no usable JSON for {path.name}") from e
