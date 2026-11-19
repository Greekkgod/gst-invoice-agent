"""Web UI:  uvicorn app:app --reload   then open http://127.0.0.1:8000"""
import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gst_agent.extract import MAX_BYTES, MIME
from gst_agent.pipeline import invoice_files, issues_csv, reconciliation_csv, run

ROOT = Path(__file__).parent
SAMPLES = ROOT / "data" / "invoices"
MAX_FILES = 25

app = FastAPI(title="GST Invoice Agent")
app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")


def with_exports(report: dict) -> dict:
    report["exports"] = {"invoices_csv": issues_csv(report), "reconciliation_csv": reconciliation_csv(report)}
    return report


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    return {"llm_ready": bool(os.environ.get("GEMINI_API_KEY"))}


@app.post("/api/sample")
def sample():
    expected = json.loads((ROOT / "data" / "expected_issues.json").read_text(encoding="utf-8"))
    report = run(invoice_files(SAMPLES), expected["our_gstin"],
                 (ROOT / "data" / "gstr2b.csv").read_text(encoding="utf-8"), offline=True)
    report["our_gstin"] = expected["our_gstin"]
    report["sample"] = True
    return with_exports(report)


@app.post("/api/process")
async def process(files: list[UploadFile] = File(...), gstr2b: UploadFile | None = File(None),
                  our_gstin: str = Form("")):
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(503, "Server has no GEMINI_API_KEY set. Try the sample batch instead.")
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"Upload at most {MAX_FILES} invoices at a time")

    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, f in enumerate(files):
            suffix = Path(f.filename or "").suffix.lower()
            if suffix not in MIME:
                raise HTTPException(415, f"{f.filename}: only PDF, PNG, JPG and WEBP are supported")
            data = await f.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise HTTPException(413, f"{f.filename} is larger than 15 MB")
            # Keep the user's filename for messages, but sanitised and in its own folder so it can't escape tmp.
            safe = re.sub(r"[^\w.\- ]", "_", Path(f.filename or f"invoice{suffix}").name)[-100:]
            p = Path(tmp) / f"{i:03d}" / safe
            p.parent.mkdir()
            p.write_bytes(data)
            paths.append(p)

        gstr2b_text = None
        if gstr2b is not None and gstr2b.filename:
            gstr2b_text = (await gstr2b.read(5 * 1024 * 1024)).decode("utf-8-sig", errors="replace")
        try:
            report = run(paths, our_gstin.strip() or None, gstr2b_text)
        except ValueError as e:  # bad GSTR-2B CSV
            raise HTTPException(400, str(e)) from e

    report["our_gstin"] = our_gstin.strip() or None
    return with_exports(report)
