"""The grader's own text layer (grader/extract.py), loaded by path so quotes we cite are exactly what it checks."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("nova_grader_extract", _ROOT / "grader" / "extract.py")
X = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X)

SEP = X.SEP
parse_eml = X.parse_eml
email_lines = X.email_lines
attachments = X.attachments
pdf_pages = X.pdf_pages
pdf_is_image_only = X.pdf_is_image_only
xlsx_pages = X.xlsx_pages
norm = X.norm
