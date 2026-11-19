"""End-to-end: the sample batch, Gemini extraction (faked), and the web API."""
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as web
from gst_agent.extract import SCHEMA, extract
from gst_agent.pipeline import invoice_files, run

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXPECTED = json.loads((DATA / "expected_issues.json").read_text(encoding="utf-8"))


def sample_report():
    return run(invoice_files(DATA / "invoices"), EXPECTED["our_gstin"],
               (DATA / "gstr2b.csv").read_text(encoding="utf-8"), offline=True, today=date(2026, 11, 19))


def test_sample_batch_catches_exactly_the_planted_problems():
    report = sample_report()
    found = {inv["source"].removesuffix(".png"): sorted(i["code"] for i in inv["issues"]) for inv in report["invoices"]}
    assert found == EXPECTED["issues"]


def test_sample_batch_reconciliation():
    s = sample_report()["reconciliation"]["summary"]
    assert (s["matched"], s["amount_mismatch"], s["missing_in_2b"], s["missing_in_books"]) == (7, 1, 3, 2)
    assert s["itc_at_risk"] == 17190.0


def test_extract_sends_file_and_schema(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG fake")
    truth = json.loads((DATA / "invoices" / "inv01.json").read_text(encoding="utf-8"))
    sent = {}

    def post(url, body, key):
        sent.update(url=url, body=body, key=key)
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(truth)}]}}]}

    assert extract(f, api_key="k", model="m", post=post) == truth
    parts = sent["body"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert sent["body"]["generationConfig"]["responseSchema"] is SCHEMA
    assert "m:generateContent" in sent["url"] and sent["key"] == "k"


def test_extract_rejects_unsupported_type(tmp_path):
    f = tmp_path / "a.docx"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unsupported"):
        extract(f, api_key="k")


client = TestClient(web.app)


def test_api_sample():
    r = client.post("/api/sample")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["read"] == 12
    assert body["exports"]["reconciliation_csv"].startswith("status,")


def test_api_process_needs_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = client.post("/api/process", files={"files": ("a.png", b"x", "image/png")})
    assert r.status_code == 503


def test_api_process_with_fake_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    truth = json.loads((DATA / "invoices" / "inv02.json").read_text(encoding="utf-8"))
    monkeypatch.setattr("gst_agent.pipeline.extract", lambda path: dict(truth))
    files = [("files", ("../../evil name.png", b"x", "image/png")), ("files", ("copy.png", b"y", "image/png"))]
    r = client.post("/api/process", files=files, data={"our_gstin": EXPECTED["our_gstin"]})
    assert r.status_code == 200
    invs = r.json()["invoices"]
    assert [i["source"] for i in invs] == ["evil name.png", "copy.png"]  # path stripped, name kept
    assert [i["code"] for i in invs[1]["issues"]] == ["DUPLICATE"]
    assert "evil name.png" in invs[1]["issues"][0]["message"]


def test_api_process_rejects_bad_type(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    r = client.post("/api/process", files={"files": ("a.exe", b"x", "application/octet-stream")})
    assert r.status_code == 415


def test_csv_exports_neutralise_formulas():
    from gst_agent.pipeline import issues_csv, reconciliation_csv
    report = sample_report()
    report["invoices"][0]["supplier_name"] = '=HYPERLINK("http://evil","x")'
    report["reconciliation"]["rows"][0]["invoice_number"] = "@SUM(A1)"
    assert "'=HYPERLINK" in issues_csv(report)
    assert "'@SUM(A1)" in reconciliation_csv(report)
    assert ",18172.0," in issues_csv(report)  # numbers untouched
