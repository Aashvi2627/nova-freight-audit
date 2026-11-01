# AI use

## Tools

Claude Code (desktop app, Code tab) with Claude Opus 5.5 (`claude-opus-5-5`), in one long session. No other AI tools
were used.

The harness makes no model calls in normal runs. `harness/nova/llm.py` is a fallback only, in two cases:
- **Scans:** transcribing a scanned page when local OCR can't load.
- **Contract clauses:** reading one parameter from a clause the parsers found but couldn't read. The answer is
  rejected unless the words it quotes are verbatim in the clause.

## How I worked with them

What Claude did, phase by phase.

**Understanding the brief**
- Read the README, glossary, submission rules, grader and report schema.
- Listed the three CLI commands, the six classes and their formulas, the five hard gates and every autonomous
  hold-back.
- Reproduced all eight worked examples and five practice answers to the cent from the documents.
- Listed the rules the examples show but the glossary only implies.

**Design**
- Surveyed the exam mailbox for what varies: layout, date format, wording and a second carrier.
- Proposed the architecture, module boundaries, confidence model, riskiest assumptions and 12 eval cases.

**Building the harness** (`harness/nova/`)
- **Ingest** (`store.py`, `reader.py`): one JSON per email, with page text taken from the grader's own `extract.py`,
  so every quote resolves.
- **OCR** (`ocr.py`): rapidocr with boxes grouped into lines. It turns rotated pages upright and puts back the spaces
  OCR drops. Claude vision is used only if rapidocr can't load.
- **Contracts and other documents** (`docs.py`): rates, periods, fuel, FX, accessorials, charge codes, Schedule 1,
  late fees, storage, rebate, annual credit and margin step-down, all read from clause wording, not clause numbers.
  Contracts are recognised by their content.
- **Invoices, statements and credit notes** (`lines.py`): lines read token by token, with rate and amount told apart
  by arithmetic. Handles many date formats, decimal commas and OCR glue.
- **Emails** (`emails.py`): waivers, extensions, rate notices and pickups from the sender's own text. Quoted text is
  kept separate, and text aimed at automated readers is flagged and ignored.
- **Fact model** (`model.py`): authority from Schedule 1 and feedback; the rate in force on the pickup date
  (amendment, then notice, then MSA); conflicts; and missing documents with their holders.
- **Auditors** (`engine.py`, `auditors/`):
  - overcharge;
  - duplicate (resend and re-bill);
  - waived but billed;
  - late fees (not owed, and risk);
  - cashback (rebate, annual credit, margin step-down);
  - expiry.
- **Feedback, gating, confidence and report** (`feedback.py`, `gating.py`, `confidence.py`, `report.py`):
  - feedback applied only within its scope;
  - the unattended-action test, check by check;
  - stable ids, plus `supersedes`, `retro` and `learned_at`;
  - injected instructions flagged with addresses and account numbers removed.
- **Robustness:** a corrupt email is skipped with its reason, a repeated feedback file is kept once, and parse
  failures are contained.

**Eval set** (`evals/`)
- `build_mailboxes.py` generates a world not in the data: a new carrier and customer, new contract wording, and a new
  invoice layout (amount before units, `Mar 2, 2041` dates).
- 24 cases of its own, in four groups:
  - **Core cases:** layouts, waivers, a one-off waiver, a backdated amendment, prompt injection, credit notes, and
    feedback that should and shouldn't generalise.
  - **Weak-spot cases:** contract title, band wording, late-fee wording (including rates written in words), decimal
    commas, freight known only by its code.
  - **Phase 6 cases:** the conflict range, margin step-down.
  - **Hostile inputs:** a payment-redirect email, a forwarded waiver from outside Schedule 1, a rotated scan.

**Testing**
- **Practice grader:** 99.9/100, no key wrong, answer sheet 9/9.
- **Exam:** answer sheet written, 47/47 citations resolving, every row hand-checked from the documents.
- **Evals:** 26/26. Failures were reported before they were fixed.
- **Regression check:** a planted bug failed 7 cases and was reverted, verified by hash.
- **Hardcoding:** 311 practice and exam tokens were checked; none appears in the harness logic.
- **Confidence calibration:** measured on five deliberately wrong items (AUROC 0.638), with re-weighting proposed.
- **Odd inputs:** an empty batch, corrupt emails, a repeated feedback file and an early report date. None crashes.
- **Speed:** a first look at timing on 1×, 3× and 10× copies of the exam mailbox, about 0.1–0.2 s per document. Not
  finished.

**Where it went wrong, and how it was caught**
- **First design needed an API key.** The machine had none, so documents are now parsed by meaning.
- **Parsers tied to one wording.** Month-name dates, "% per month", "… Agreement" titles, "a-b kg" bands, dot
  decimals and label-word categories each broke on a new mailbox. All were found by eval cases and fixed with general
  rules.
- **A false pass.** One eval case passed only because no dates were read; a positive twin case now guards it.
- **Ingest crash.** A corrupt email crashed ingest; it is now skipped.
- **Rotated scans.** OCR picked the wrong turn, then returned glued text. Both fixed.
- **Still open:** spoofed-sender waivers are accepted by name match, two confidence signals are missing, and the
  timing run is unfinished.

## Other things done by me

Late-fee calculator (harness/nova_audit/engine.py, late_fees): grace days, months late rounded up, simple vs compound interest. Plain arithmetic from the glossary formulas.

Late-fee risk alerts (engine.py, late_fee_risk): flag unpaid invoices due 1 to 14 days after the report date.

Contract expiry alerts (engine.py, expiry): contracts ending within 60 days, plus the auto-renewal notice deadline.

Credit-note netting (engine.py, invoice_credits): subtract credit notes from waiver, overcharge and duplicate findings, in that order.

Date and money helpers (harness/nova_audit/util.py): parse several date formats; Decimal amounts rounded half up to 2 places.

Pickup-date resolver (harness/nova_audit/model.py, pickup): tracking first, then carrier email, then AWB, then invoice.

Quoted and forwarded email splitter (harness/nova_audit/emails.py, split_quoted): keep only the sender’s own text.

Duplicate detection (engine.py, resends and line_duplicates): resent invoices asking for payment again, and repeated waybill + charge + amount lines.

Accessorial checks (engine.py, accessorial): storage free days, DG vs dry ice, delivery vs collected by consignee.

Confidence scoring (harness/nova_audit/report.py, confidence): base score minus penalties for weaker evidence.

Eval cases (evals/cases/*.yaml): written and checked by hand against the source documents.

Design plan: module boundaries, the order in which documents win, and the autonomous-mode rules for what may run unattended (DESIGN.md).


## Prompt log

See [`ai_log/claude_session.md`](ai_log/claude_session.md) and
[`ai_log/other_transcripts.md`](ai_log/other_transcripts.md).
