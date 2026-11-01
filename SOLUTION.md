# My solution

## How to run it

Python 3.11+ and `make setup` (installs `harness/requirements.txt`; nothing else is needed). The three grader
commands are unchanged:

```
python harness/harness.py ingest <batch_dir> --state-dir <dir>
python harness/harness.py report --as-of YYYY-MM-DD --mode supervised|autonomous --state-dir <dir>
python harness/harness.py feedback <feedback.json> --state-dir <dir>
```

Then `make grade`, `make sheet`, `make eval`, `make validate` as in the brief. `python evals/build_mailboxes.py`
regenerates the eval mailboxes in `evals/mailboxes/` and feedback files in `evals/feedback/`.

- No model training is involved anywhere: there are no weights, datasets to train on or GPU steps. The harness is
  deterministic reading plus a few targeted Claude calls at run time.
- Scanned invoices are read with the local OCR package from the requirements (`rapidocr-onnxruntime`). If it can't
  load, the harness falls back to Claude vision through `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`. With neither,
  scanned pages are skipped and everything else still runs.
- Windows: the Makefile assumes `.venv/bin/python`; run `make PY=.venv/Scripts/python grade` (or the commands in the
  Makefile directly) and set `PYTHONIOENCODING=utf-8` so the report card's check marks print.

Code: `harness/harness.py` (CLI) and `harness/nova_audit/`:

| Module | Job |
|---|---|
| `store.py` | ingest: email + attachments to text pages (grader's `extract.py`), OCR for scans, one JSON per email |
| `ocr.py` | rapidocr boxes grouped into printed lines; Claude vision fallback |
| `docs.py`, `lines.py` | contracts and amendments clause by clause; invoices in any layout (typed tokens + arithmetic) |
| `emails.py` | waivers, extensions, rate notices, status and customs emails, quoted/forwarded text, injection |
| `model.py` | the fact model as of a date, with authority lookups |
| `engine.py` | the auditors: rates, accessorials, waivers, duplicates, late fees, risk, rebates, credits, expiry |
| `feedback.py`, `report.py` | decisions and their scope; learned_at, gating per mode, confidence, ids |
| `llm.py` | cached, schema-constrained Claude calls (`claude-opus-5-5`, server-side refusal fallback) |

## What works

- Practice card (`make grade`, both modes): **99.8 / 100**, every hard gate clear. Detection F1 1.00 (22 hits, 0
  false, 0 missed), amounts 100%, adaptation 11/11, learning 3/3, conflict/abstention 2/2, actions 24/24 in each
  mode, clean controls 16/16, Brier 0.008. The 0.2 lost is the AUROC half of confidence, which the card can't
  measure when no item is wrong.
- Exam sheet (`make sheet`): total savings $8,165.33 (overpayment $3,847.70, cashback $4,307.34, late fees avoided
  $10.29), 1 conflict, 1 cannot_determine. Every number was also worked by hand from the documents, including the
  scanned invoice `CDI-3031-0306` (OCR: $85.46 overcharge). Schema and all 54 citations validate.
- All 8 worked examples reproduce `expected_finding.json`, decoys included, with no extra keys.
- Own eval set: **29 / 29** (27 of mine), on a new world (carrier, contract wording, invoice layout with
  amount-before-units and `Mar 2, 2036` dates) covering prompt injection, unauthorised / quoted / conditional
  waivers, new waiver wording, a backdated amendment, consignee collection, storage, DG vs dry ice, credit notes
  against duplicates and against overcharges (full and partial), re-billed lines, simple vs compound late fees, unauthorised extensions, auto-renewal notice
  deadlines, rebates, and four feedback kinds with their "don't over-generalise" siblings.
- Also checked by hand: the injection mailbox never mentions the outside address, and the backdated amendment yields
  `retro: true` across consecutive reports.

## What doesn't

- The handling-margin step-down (`margin_step_down`) and Annex B (other freight services) are implemented from the
  glossary only: no mailbox shows their wording, so they are the least tested paths.
- Contract parameters come from clause wording with regexes keyed on clause titles. An unusual phrasing (say, a late
  fee written in words) would be missed; the LLM fallback covers emails but not yet clauses.
- When one invoice mixes a conflicted line with a definite overcharge, the conflict range includes both.
- A due date paid exactly on time but listed with status `scheduled` is treated as paid for late-fee risk.
- The Claude paths (vision OCR fallback, email fallback) were tested only with a mocked response and an unreachable
  endpoint: no API key was available while building. They are off unless a key is set, and every answer is
  validated against the source text before use.

## The eval case that caught a bug

`evals/cases/simple_interest_late_fee.yaml`. The carrier billed a month-2 late fee of 6.94 (compounded) where the
contract's simple interest gives 6.84. The case failed with "key missing": when a late-fee line's amount doesn't
match the expected fee (the very line we want to catch), the line reader fell back to "the last number on the
line", which in that layout was the re-printed rate `1.50`. It read the billed fee as 1.50, below the correct 6.84, so
no finding. Fix in `harness/nova_audit/lines.py`: the amount of a late-fee line is never the principal or the fee
rate. The case then passed with 0.10, and practice and exam results stayed unchanged. A final review also found
that credit notes were not netted off overcharge and waiver findings; two cases (`credit_cancels_overcharge`,
`credit_partial_overcharge`) now cover it. The suite also caught a
regression later: a cache attribute named `_waivers` shadowed the `_waivers()` method and every report crashed
(0/27); renaming the cache fixed it.
