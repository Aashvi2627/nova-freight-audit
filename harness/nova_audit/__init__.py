"""Nova freight-invoice audit harness.

Pipeline: ingest (read every .eml, its PDF/XLSX attachments, OCR scans, store page text and a layout-independent
parse per document under --state-dir) -> report (rebuild the fact model from every document received by as_of,
run the deterministic auditors, apply feedback, gate actions by mode, attach evidence and confidence) -> feedback
(store a person's decisions with their scope so later reports apply them).
"""
