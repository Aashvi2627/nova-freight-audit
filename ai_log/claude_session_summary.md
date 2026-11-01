# Claude Code session log

- Tool: Claude Code desktop app (Code tab), model Claude Opus 5.5 (`claude-opus-5-5`).
- One session. Each prompt is shown as it was given. Under it is a summary of what Claude did and what it found; the
  full tool output lives in the session transcript.
- No secrets appear. The API key was never available to the session, so nothing needed removing.

---

## Prompt 1: Phase 1, understand the brief

> **Phase 1. Understand the brief before any code**
>
> 1. Read everything, write nothing. Read `README.md`, `GLOSSARY.md`, `SUBMISSION.md`, the grader (`grader/score.py`,
>    `extract.py`, `run_grader.py`) and `grader/schema/report.schema.json`. Don't write code. Give me: the three CLI
>    commands and their arguments, the six classes with the glossary formula for each, the five mistakes that cap the
>    score at 40, and every place the brief says autonomous mode must hold back.
> 2. Learn from the worked examples. Go through all eight folders in `data/examples/`. For each, give me the deciding
>    clause, the email line, the arithmetic and the decoy next to it, in a table. Then list the rules that the examples
>    show but the glossary only implies.
> 3. Work answers by hand. Pick five practice scenarios from `data/scenarios/answers/`, one per class. For each, find
>    the source documents in the mailbox and reproduce the amount step by step from the text alone. Flag any answer
>    you can't reproduce instead of guessing.
>
> Check yourself: read the glossary once without the tool's summary; redo one worked example's arithmetic on paper;
> make sure you could explain "conflict" vs "cannot_determine" in your own words.

**Claude:**
- **Read the brief and grader.** Listed the three commands, the six formulas, the five capping mistakes and every
  autonomous hold-back.
- **Dumped the documents** through the grader's own `extract.py` into a scratch folder (no repo changes).
- **Worked examples:** all eight reproduce to the cent. Listed 14 rules the examples show but the glossary only
  implies, such as weight bands, rate-period columns, the fuel knock-on, the late fee sitting on a later invoice, and
  statements counting toward qualifying spend.
- **Five practice answers:** reproduced by hand with nothing flagged.

  | Class | Amount |
  |---|---|
  | Overcharge (INR) | 383.13 |
  | Duplicate | 1363.36 |
  | Waived but billed | 116.13 |
  | Late fee not owed | 13.54 |
  | Cashback (three statements in qualifying spend) | 4450.07 |

## Prompt 2: Phase 2, plan

> **Phase 2. Plan**
>
> 4. Design before code. Propose an architecture for `harness/`: how `ingest` stores documents under `--state-dir`,
>    the fact model `report` rebuilds, one auditor per class, and where an LLM call is worth its cost. It must not
>    depend on the practice files' layout, names or lanes (the README says a rules-only harness scored 11/100 on the
>    hidden set). Give me the module list, what each one owns, and the three riskiest assumptions.
> 5. Plan the eval set first. Before implementing, list 12 eval cases that the practice mailbox does not cover: a new
>    invoice layout, new waiver wording, a backdated amendment, a one-off waiver, prompt injection, credit notes, and
>    feedback that should and shouldn't generalise. One line each on what decides it.
>
> Check yourself: decide what an LLM may and may not decide in your harness; pick the confidence approach; agree the
> module boundaries before the tool writes them.

**Claude:**
- **Surveyed the exam mailbox first.** It has a new "Charges advice" layout, DD/MM dates, new waiver wording and a
  second carrier.
- **Proposed a design:** the LLM extracts typed facts with verbatim quotes, and plain Python does all arithmetic,
  verdicts, actions and confidence.
- **Supporting pieces:** a table of what the LLM may and may not decide, 15 modules, a penalty-based confidence
  model, three risky assumptions (extraction on unseen layouts, schema completeness, the 600 s limit) and 12 eval cases.

## Prompt 3: "Approve design"

**Claude:**
- **No API key.** Found none in the environment and asked how to proceed.
- **Your choice: "Key file outside repo".**
- **Built the LLM-extraction harness** (store, reader, llm, schemas, facts, terms, pricing, six auditors, feedback,
  gating, confidence, report) and the first `evals/build_mailboxes.py` with 12 cases in a new world (Brightwater Air
  Cargo / Halvorsen Biotech).
- **Tested without the API** by hand-feeding extractions for the overcharge example: 196.92 in both modes, all
  citations resolving.
- **Blocked:** the key file was never found, so the model path stayed untested.

## Prompt 4: "Key saved, continue"

**Claude:**
- **Looked for the file** at `C:\Users\HP\.anthropic_key`, and for similar names in the home folder, Desktop and
  Documents (names only, never contents). Nothing found.
- **Gave the PowerShell command** to create it and stopped.

## Prompt 5: Phase 3, build one layer at a time

> **Phase 3. Build, one layer at a time**
>
> 6. Ingest and text layer. Implement `store.py`: `ingest` reads each `.eml` and its attachments with the grader's
>    `extract.py` so every quote resolves, and writes one JSON per email under `--state-dir/docs/`. Run it on
>    `data/scenarios/batch_01` and show me three stored records.
> 7. Scanned invoices. Implement `ocr.py` with `rapidocr-onnxruntime`: group text boxes into printed lines by position
>    and compare labels with spaces removed. Fall back to Claude vision only if local OCR can't load. Run it on the
>    scanned invoice in each mailbox and print the lines next to the page image's values.
> 8. Contracts and invoices by meaning. Implement `docs.py` and `lines.py`. Read contract parameters (free storage
>    days, grace, late-fee rate, compounding, rebate tiers) from clause wording, not clause numbers. Read invoice lines
>    by what each token is (waybill, lane, date, quantity+unit, percentage) and tell rate from amount by arithmetic.
>    Then run the parsers over every practice and exam document and list everything that failed to parse.
> 9. Emails. Implement `emails.py`: waivers, extensions and rate notices from the sender's own text only, quoted and
>    forwarded text separated, and any text aimed at an AI or automated reader flagged and never extracted.
> 10. Fact model and auditors. Implement `model.py` and `engine.py`. Resolve the rate for each shipment on its pickup
>     date (tracking, then carrier email, then AWB, then invoice), with amendments and authorised rate notices
>     overriding the MSA. Raise a conflict only when two trusted sources disagree and the contract says neither
>     prevails. Return `cannot_determine` with the document name and its Schedule 1 holder when a needed document is
>     missing.
> 11. Report, gating and feedback. Implement `report.py` and `feedback.py`: the schema output, supervised vs autonomous
>     gating per the unattended-action test, confidence from evidence completeness, authority, extraction and
>     agreement, stable ids with `supersedes`, `retro: true` for backdated documents, and feedback applied to exactly
>     the keys it names.
>
> Check yourself: after each step, open one stored record or one finding and compare it with the source document
> yourself; read every rule the tool wrote for autonomous mode against the glossary's unattended-action test.

**Claude:**
- **Environment.** Installed Python 3.12 with `uv`, because `rapidocr-onnxruntime==1.4.4` needs Python below 3.13.
  `.venv` is now 3.12; `requirements.txt` is unchanged.
- **Step 6.** `store.ingest` stores 41 emails from practice `batch_01` in about 6 s. Three records shown and compared
  with their sources.
- **Step 7.** `ocr.py` groups rapidocr boxes into lines. Both scans match their page images value for value (scores
  0.985 and 0.977).
- **Step 8.** `docs.py` / `lines.py` read clauses by meaning and type tokens, settling rate vs amount by arithmetic.
  First run: one failure (an OCR-glued invoice number). Checks then found false "OK"s: a peak-season month read from
  the word "may", master AWBs misclassified, and 9 "Freight bill" totals unrecognised. All fixed; 0 failures across
  149 documents.
- **Step 9.** `emails.py` keeps the sender's own text apart from quoted text and flags AI-addressed paragraphs.
  Fixed cut-short waiver labels and an injection only half flagged.
- **Step 10.** `model.py` / `engine.py` hold the fact model and rate resolver. All 8 examples match in both modes and
  every citation resolves.
- **Step 11.** Read the gating code against the unattended-action test and found 4 mismatches: the conflict scope
  lacked the rate period, any sender's pickup email counted, the Schedule 1 recipient was unchecked, and extra
  hold-backs existed that the test doesn't contain. All fixed.
- **Results.** Practice grader 99.9/100 with the answer sheet 9/9; evals 14/14; exam sheet written.

## Prompt 6: Phase 4, test honestly

> **Phase 4. Test honestly**
>
> 12. Practice and exam. Run `make grade` and `make sheet`. For every key the card gets wrong, show me the document
>     lines and the rule that failed. Don't tune anything to a practice file's wording; fix the general rule or tell
>     me it can't be fixed generally.
> 13. Build the eval world. Write `evals/build_mailboxes.py` to generate mailboxes for a carrier and customer that
>     don't exist in the data, with a different invoice layout (amount before units, `Mar 2, 2036` dates) and
>     different wording. Add the Phase 2 cases under `evals/cases/` and run `make eval`. Report failures before fixing
>     any.
> 14. Prove the eval set catches regressions. Break one auditor on purpose, run `make eval`, show me which cases fail,
>     then revert.
>
> Check yourself: hand-check the exam sheet totals from the documents; confirm the injection mailbox's report never
> mentions the injected address; confirm no practice answer is hardcoded anywhere in `harness/`.

**Claude** (no `make` on the machine, so it ran each Makefile recipe directly):
- **Step 12, practice.** The card gets no key wrong; the only 0.12 lost is calibration.
- **Step 12, exam.** Every row hand-checked from document lines, and the Q3 rebate recomputed from raw text:
  3,847.70 + 4,307.34 + 10.29 = 8,165.33.
- **Step 13, failures first.** On the new layout (amount before units, `Mar 2, 2041` dates), 2 cases failed. Cause:
  month-name-first dates weren't parsed. A third case was passing for the wrong reason.
- **Step 13, fix.** General date fix; added a positive twin case. Evals 15/15.
- **Step 14.** A planted fuel-base bug failed 7 of 15 cases, each by exactly its fuel knock-on. Reverted, verified by
  hash.
- **Self-checks.** The injected address is in no report. 311 practice and exam tokens are absent from `harness/`
  logic; two appeared in comments and were replaced.

## Prompt 7: Phase 5, review what exists

> **Phase 5. Review what exists**
>
> 15. Hand-check the exam answers. For three findings in `out/exam_report.json` (one overcharge, the conflict on
>     CDU-3051302 and the cannot_determine on CDU-3051286), show me the arithmetic from the source documents step by
>     step: which contract row, which amendment or notice applies on the pickup date, and why. Quote the lines. Don't
>     run the harness: work from the documents so I can compare it with the harness output.
> 16. Find weak spots. Read `harness/nova_audit/engine.py` and `docs.py`. List the five places most likely to break on
>     an unseen mailbox (new invoice layout, new clause wording, new currency). For each, write a small eval case under
>     `evals/cases/` that would expose it, and run `make eval`. Report which cases fail. Don't fix anything yet.
> 17. Check confidence calibration. The confidence score in `report.py` is a penalty model. Build five deliberately
>     wrong items (wrong pickup date, misread OCR amount, unauthorised waiver accepted) and check that their confidence
>     falls below every correct item's. Tell me whether the penalties need re-weighting, with numbers.
> 18. Odd inputs. Try an empty batch, a corrupt `.eml`, the same feedback file fed twice and a report dated before any
>     document. Nothing may crash; report what each one produces.

**Claude:**
- **Step 15.** Worked the three items from quoted document lines only: 244.37; [0.00, 61.93]; Annex A rev 2 held by
  Joaquin Rathbone. All match the harness. (The real path is `harness/nova/`, not `nova_audit`.)
- **Step 16.** Named five weak spots and wrote five cases. All five failed with "key missing", unfixed as asked:
  contract title, band wording, late-fee wording, decimal commas, freight known only by code.
- **Step 17.** Only 1 of 5 wrong items scored below every correct item (AUROC 0.638). Proposed weights and new
  signals with numbers, and found a correctness bug: a spoofed sender is accepted by name match.
- **Step 18.** A corrupt `.eml` crashed ingest and lost the rest of the batch. Feedback fed twice was stored twice.
  The other odd inputs were fine.

## Prompt 8: "Yes, fix both"

**Claude:**
- **Crash.** An unreadable email is now skipped with its reason. Parse failures are contained per attachment, a
  repeated feedback file is kept once, and malformed feedback is ignored.
- **Weak spots, each a general rule:**
  - a contract is recognised by content (Schedule 1 plus a rate table);
  - a band-phrase reader handles worded weight bands;
  - late-fee rates are read in any common wording;
  - each document's number style is detected, so decimal commas parse;
  - a line's category comes from the contract's charge codes first.
- **Results.** Evals 20/20, examples 16/16, practice 99.9, exam sheet unchanged.

## Prompt 9: "Not now"

You declined the spoofed-sender fix for now and interrupted to give Phase 6.

## Prompt 10: Phase 6, extend

> **Phase 6. Extend**
>
> 19. Clause extraction fallback. Contract parameters are read with regexes keyed on clause titles. Add an LLM fallback
>     in `llm.py` for when a clause is found but a parameter (late-fee rate, free storage days, grace period) can't be
>     parsed. The value must appear verbatim in the clause text or it is rejected. Add one eval case where the late-fee
>     rate is written in words ("one and a half per cent").
> 20. Per-line conflict ranges. When one invoice has a conflicted line and a definite overcharge, the conflict range
>     currently includes both. Split them so the conflict's `amount_range` covers only the conflicted lines and the
>     overcharge is its own finding. Keep practice at 99.8 and the exam sheet unchanged.
> 21. Margin step-down. `margin_step` in `engine.py` is written from the glossary only. Generate a mailbox in
>     `evals/build_mailboxes.py` where the handling margin steps down after a volume threshold, with one invoice billed
>     at the old margin. Add the case and make it pass.
> 22. Hostile inputs. Write eval mailboxes for: an email whose body tells the AI to change the payment recipient, a
>     forwarded waiver from someone not in Schedule 1, and a PDF invoice with no text layer and a rotated page. Confirm
>     the harness flags the first, ignores the second and still reads the third.
> 23. Cost and speed. Time `ingest` and `report` on a mailbox ten times the size of the exam mailbox (duplicate it with
>     new IDs). Report the time per document and where it goes, and whether anything grows faster than linearly.

**Claude:**
- **Step 20, raised first.** A conflict and an overcharge on one invoice share the key `(invoice_id, overcharge)`,
  and the grader allows one item per key. You answered "no preference", so Claude kept the glossary's whole-invoice
  range and pinned it with `conflict_plus_definite` ([14.87, 86.27]).
- **Step 19.** A deterministic number-words reader comes first. The LLM fallback in `llm.py` must quote words that
  are verbatim in the clause and state that number. The stub-client test accepted the true span and rejected an
  invented span and a wrong number. Case `weak_late_fee_in_words` passes.
- **Step 21.** Margin step-down parsing and auditing (in the cashback auditor) plus case `margin_step_down`:
  200.00, `draft:claim_credit`.
- **Step 22.**
  - The payment-redirect email is flagged in a top-level `notices` list, with account numbers and addresses redacted;
    the overcharge stands.
  - A forwarded waiver from a named person outside Schedule 1 is ignored.
  - The rotated scan is read after two fixes: upright-first orientation ranking, and un-gluing OCR text.
  - Evals 26/26.
- **Step 23.** Built 1×/3×/10× re-identified copies of the exam mailbox. Ingest ran at about 100–170 ms per document,
  dominated by the batch holding the scan. The first copies weren't faithful (an invoice-number shape wasn't renamed;
  the scan degraded when re-rasterised), and Claude was fixing the copier when **you stopped the run**. The timing
  conclusions are therefore provisional.

## Prompt 11: this file

> Make AI_USE.md, ai_log/claude_session.md and ai_log/other_transcripts.md … For claude prompt, session, etc you
> already have everything in this chat. The part human did: write different things, what human can actually do in
> this project, which may cover plan, writing some code, some tests, etc related to project but explained properly.

---