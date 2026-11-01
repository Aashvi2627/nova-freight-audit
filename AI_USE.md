# AI use

## Tools

Claude Code (desktop app) running Claude Opus 5.5 (`claude-opus-5-5`) wrote the harness, the eval mailbox generator,
the eval cases and these write-ups, and ran every check. At run time the harness can call Claude Opus 5.5 through the
Anthropic API for vision OCR and unrecognised email wording; neither path is needed on the practice or exam mailboxes.

A separate general-purpose AI chat assistant was used once at the end: it read a zip of the finished repo and wrote a
checklist of what was still missing for submission (this file's transcript and "Written without AI" section). That
checklist was pasted into the Claude Code session as the last prompt and is in the log.

## How you worked with them

One brief to Claude Code (below): read the whole repository first, implement everything, don't train models, test
honestly, document. Three short follow-ups came later: "are all modifications in the codebase", "check very
carefully that everything is implemented properly", and the submission checklist. The agent worked in this order:

1. Read the brief, glossary, grader (`score.py`, `extract.py`, `run_grader.py`), the 8 worked examples, all 30
   practice answers, and every document in both mailboxes as text (including rendering both scans to images).
2. Worked every practice answer and the expected exam answers by hand from the documents before writing code, so the
   rules were checked against the arithmetic (e.g. the India contract's 3 free storage days explain TXI/35-36/0030).
3. Wrote parsers, then ran them over all practice and exam documents. That exposed a table-of-contents bug (annex
   sections matched the contents page) and OCR quirks (`TXI` read as `TX1`, `INR108485.85`), which were fixed.
4. Ran the practice grader (99.8/100), the exam sheet, and all 8 worked examples. Then built a fresh eval world with
   a different carrier, wording and layout. Its first run caught a real late-fee parsing bug (see SOLUTION.md).
5. Verified by hand what the runners can't see: no injected address in reports, `retro: true` on a backdated
   amendment, and that the Claude fallback rejects hallucinated sentences and waybills (mocked response).
6. On the follow-up review: checked Python 3.11 syntax, odd inputs (empty or corrupt mailboxes, repeated feedback),
   added per-document crash guards, and found that credit notes were not netted off overcharge findings; fixed, with
   two new eval cases.
7. Exported this log, completed this file, added `.gitignore` entries and made the git commit.

Where it got things wrong: a cache attribute that shadowed a method (caught by the eval suite), a regex edit that a
shell heredoc mangled (caught on re-read), the TOC/annex bug above, and the credit-note gap, which it missed until the second review. It stated where testing was not possible: no
API key was available, so the Claude paths were tested only with mocks.

## Written without AI

No code, eval case, generated mailbox or document in this repo was written or edited by hand: Claude Code wrote all
of it, as the log shows. What I did myself:

- Wrote the prompts that directed the work (the opening brief and the follow-ups in the log), including the decision
  that no model training should happen in the session.
- Read the results Claude reported, asked for a second careful review before submitting (which found and fixed the
  credit-note gap described in SOLUTION.md), and decided what to submit.
- Asked for the submission clean-up (Claude exported this log and made the git commit at my request), and sent the
  submission.

## Prompt log

The full Claude Code session is in [`ai_log/claude_session.md`](ai_log/claude_session.md), exported from the
tool's own session file: every prompt, reply, tool call and tool result in order, trimmed only for length and secrets
(see [`ai_log/README.md`](ai_log/README.md)). The opening prompt, abbreviated:

```
I have provided you with the initial repository of this project. The repository contains the complete project
description, requirements, implementation details, and other relevant information, primarily documented in the
README files. Your task is to thoroughly understand, analyze, and implement the entire project as described in the
repository.
Phase 1: Complete Repository Analysis ... Phase 2: Implementation Planning ... Phase 3: Complete Implementation ...
Phase 4: Training and Computational Tasks (IMPORTANT: Do NOT perform any model training yourself. I have access to
dedicated Ada GPU servers ...) ... Phase 5: Testing and Validation ... Phase 6: Final Documentation and Handover ...
The ultimate objective is to deliver a complete, properly implemented, tested, and documented project that
satisfies every requirement in the initial repository, with all training-related components ready for me to
execute independently on my Ada GPU servers.
```
