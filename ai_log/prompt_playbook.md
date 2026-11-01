# Prompt playbook

**This is not part of the session log.** These are prompts I would use to review, extend or stress-test this harness
with an AI coding tool. They were written after the build and were not sent during it. The real session is in
[`claude_session.md`](claude_session.md).

## Reviewing what exists

1. **Hand-check the exam answers**
   > For three findings in `out/exam_report.json` (one overcharge, the conflict on CDU-3051302 and the
   > cannot_determine on CDU-3051286), show me the arithmetic from the source documents step by step: which contract
   > row, which amendment or notice applies on the pickup date, and why. Quote the lines. Don't run the harness: work
   > from the documents so I can compare it with the harness output.

2. **Find weak spots**
   > Read `harness/nova_audit/engine.py` and `docs.py`. List the five places most likely to break on an unseen
   > mailbox (new invoice layout, new clause wording, new currency). For each, write a small eval case under
   > `evals/cases/` that would expose it, and run `make eval`. Report which cases fail. Don't fix anything yet.

3. **Check confidence calibration**
   > The confidence score in `report.py` is a penalty model. Build five deliberately wrong items (wrong pickup date,
   > misread OCR amount, unauthorised waiver accepted) and check that their confidence falls below every correct
   > item's. Tell me whether the penalties need re-weighting, with numbers.

## Extending it

4. **Clause extraction fallback**
   > Contract parameters are read with regexes keyed on clause titles. Add an LLM fallback in `llm.py` for when a
   > clause is found but a parameter (late-fee rate, free storage days, grace period) can't be parsed. The value must
   > appear verbatim in the clause text or it is rejected. Add one eval case where the late-fee rate is written in
   > words ("one and a half per cent").

5. **Per-line conflict ranges**
   > When one invoice has a conflicted line and a definite overcharge, the conflict range currently includes both.
   > Split them so the conflict's `amount_range` covers only the conflicted lines and the overcharge is its own
   > finding. Keep practice at 99.8 and the exam sheet unchanged.

6. **Margin step-down**
   > `margin_step` in `engine.py` is written from the glossary only. Generate a mailbox in
   > `evals/build_mailboxes.py` where the handling margin steps down after a volume threshold, with one invoice
   > billed at the old margin. Add the case and make it pass.

## Robustness

7. **Hostile inputs**
   > Write eval mailboxes for: an email whose body tells the AI to change the payment recipient, a forwarded waiver
   > from someone not in Schedule 1, and a PDF invoice with no text layer and a rotated page. Confirm the harness
   > flags the first, ignores the second and still reads the third.

8. **Cost and speed**
   > Time `ingest` and `report` on a mailbox ten times the size of the exam mailbox (duplicate it with new IDs). Report
   > the time per document and where it goes, and whether anything grows faster than linearly.

## Before submitting

9. **Submission check**
   > Run `make validate`, `make grade`, `make sheet` and `make eval` from a fresh clone on Python 3.11 in Linux. Report
   > anything that differs from the Windows results, and make sure no generated state folder or API key is tracked.
