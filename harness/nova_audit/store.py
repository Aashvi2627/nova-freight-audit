"""What ingest keeps on disk: one JSON record per email with its text layer, attachments' pages and OCR output.

Parsing into facts happens at report time from these records (it is cheap and deterministic), so a document that
arrives before the contract it refers to is still read correctly once the contract is held."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from . import ocr
from . import textlayer as T


def docs_dir(state: str) -> Path:
    p = Path(state) / "docs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_email(path: Path, batch: str, state: str) -> dict:
    data = path.read_bytes()
    msg = T.parse_eml(data)
    rel = f"{batch}/{path.name}"
    try:
        dt = parsedate_to_datetime(str(msg["Date"])).astimezone(timezone.utc)
    except (TypeError, ValueError):
        dt = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    name, addr = parseaddr(str(msg["From"]))
    page = "\n".join(T.email_lines(msg))
    body = msg.get_body(preferencelist=("plain",))
    body_text = body.get_content().replace("\r\n", "\n") if body else ""
    rec = {"doc": rel, "batch": batch, "file": path.name, "received": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "from_name": name, "from_addr": addr.lower(), "to": str(msg["To"] or ""), "cc": str(msg["Cc"] or ""),
           "subject": str(msg["Subject"] or ""), "message_id": str(msg["Message-ID"] or ""),
           "page": page, "body": body_text, "parts": []}
    for aname, adata in T.attachments(data):
        ext = aname.rsplit(".", 1)[-1].lower() if "." in aname else ""
        part = {"doc": f"{rel}#{aname}", "name": aname, "ext": ext, "pages": [], "scan": False, "ocr": None}
        try:
            if ext == "pdf":
                if T.pdf_is_image_only(adata):
                    part["scan"] = True
                    part["pages"], part["ocr"] = ocr.ocr_pdf(adata, state)
                else:
                    part["pages"] = T.pdf_pages(adata)
            elif ext == "xlsx":
                part["pages"] = T.xlsx_pages(adata)
        except Exception as e:  # noqa: BLE001 - a broken attachment must not stop the batch
            print(f"ingest: could not read {part['doc']}: {e}", file=sys.stderr)
            part["error"] = str(e)
        rec["parts"].append(part)
    return rec


def ingest(batch_dir: str, state: str) -> int:
    bdir = Path(batch_dir)
    batch = bdir.name
    out = docs_dir(state)
    n = 0
    for f in sorted(bdir.glob("*.eml")):
        dest = out / f"{batch}__{f.name}.json"
        if dest.is_file():
            continue
        try:
            rec = read_email(f, batch, state)
        except Exception as e:  # noqa: BLE001
            print(f"ingest: could not read {f}: {e}", file=sys.stderr)
            continue
        dest.write_text(json.dumps(rec))
        n += 1
    return n


def load_all(state: str) -> list[dict]:
    recs = []
    for f in sorted(docs_dir(state).glob("*.json")):
        try:
            recs.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    recs.sort(key=lambda r: (r["received"], r["doc"]))
    return recs
