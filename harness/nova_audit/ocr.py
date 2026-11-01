"""Text for scanned PDF pages (no text layer): local OCR (rapidocr-onnxruntime) first, Claude vision second.

rapidocr returns boxes with positions and drops most spaces inside a box; boxes are grouped into lines by their
vertical centre and read left to right, so each line looks like a row of the printed table. The parsers compare
labels with spaces removed, so 'Fuelsurcharge' and '274kg' still read."""
from __future__ import annotations

import sys

from . import llm

_engine = None
_engine_failed = False


def _page_png(doc, page) -> bytes:
    """The page's largest embedded image (the scan itself), else a 200 dpi render."""
    best = None
    for img in page.get_images(full=True):
        try:
            x = doc.extract_image(img[0])
        except Exception:  # noqa: BLE001 - an unreadable image just falls back to a render
            continue
        if best is None or x["width"] * x["height"] > best["width"] * best["height"]:
            best = x
    if best is not None and best.get("ext") == "png":
        return best["image"]
    if best is not None:
        import pymupdf
        try:
            return pymupdf.Pixmap(best["image"]).tobytes("png")
        except Exception:  # noqa: BLE001 - an exotic image format: render the page instead
            pass
    return page.get_pixmap(dpi=200).tobytes("png")


def _rapid(png: bytes) -> list[str] | None:
    global _engine, _engine_failed
    if _engine_failed:
        return None
    try:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        result, _ = _engine(png)
    except Exception as e:  # noqa: BLE001 - missing package or system library: try the next reader
        print(f"ocr: local OCR unavailable ({e.__class__.__name__}: {str(e)[:120]})", file=sys.stderr)
        _engine_failed = True
        return None
    if not result:
        return []
    boxes = []
    for box, txt, _conf in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), txt))
    boxes.sort()
    hs = sorted(b[1] for b in boxes)
    tol = max(4.0, hs[len(hs) // 2] * 0.5)
    lines: list[list] = []
    for cy, h, x, txt in boxes:
        if lines and abs(lines[-1][0] - cy) <= tol:
            lines[-1][1].append((x, txt))
            lines[-1][0] = (lines[-1][0] + cy) / 2
        else:
            lines.append([cy, [(x, txt)]])
    return [" ".join(t for _, t in sorted(ws)) for _, ws in lines]


VISION_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"], "additionalProperties": False,
}
VISION_TASK = ("Transcribe this scanned page exactly, one printed line per array entry, top to bottom. For table rows "
               "put every cell of the row on one line, left to right, separated by single spaces. Copy numbers, ids "
               "and dates exactly as printed.")


def ocr_pdf(data: bytes, state_dir: str | None = None) -> tuple[list[str], str]:
    """-> (text per page, engine used: 'rapidocr' | 'vision' | 'none')."""
    import pymupdf
    pages, engine = [], "none"
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            png = _page_png(doc, page)
            lines = _rapid(png)
            if lines is not None:
                engine = "rapidocr"
            else:
                out = llm.call_json(VISION_TASK, "(scanned page attached as an image)", VISION_SCHEMA, state_dir,
                                    image_png=png, effort="medium")
                lines = list(out.get("lines") or []) if out else []
                engine = "vision" if out else engine
            pages.append("\n".join(lines))
    return pages, engine
