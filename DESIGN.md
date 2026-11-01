# Design

Deterministic auditors over a fact model read by meaning; Claude only where reading runs out (scans without local
OCR, wordings the rules don't recognise), and every Claude answer is checked against the source text.

## Fact model

`ingest` stores each email under `--state-dir/docs/` with its text layer (the grader's own `extract.py`, so every
quote resolves), attachment pages, and OCR output for scans. `report` rebuilds the model from every record received
by `as_of`: contracts (Annex A rows per lane, band and period, fuel, accessorials, Annex E label→code map, FX,
Schedule 1, clause parameters such as free days, grace, late-fee rate, compounding and rebate tiers, read from clause
wording, not clause numbers), amendments and referenced annexes, rate notices, waivers and extensions (sender's own
text only), invoices and credit notes, the AP ledger, tracking events, AWBs and DG declarations. Invoice lines are
tokenised by what each piece *is* (waybill, lane, date, quantity+unit, percentage) and rate/amount are told apart by
arithmetic, so a new column order or date format still reads. Every fact keeps its `(doc, page, line)`, which
becomes the evidence quote. Keys follow the glossary; ids are `class:key`.

## Conflicts

Rates resolve per shipment on the pickup date (tracking → carrier "picked up" email → AWB → invoice): MSA period
rate, overridden by the latest signed amendment in force (or by an annex it references), then by authorised rate
notices. Notice vs. amendment that differ is a conflict only when the MSA says neither prevails; otherwise the
ranking decides. An unauthorised or quoted notice never competes. A referenced annex we don't hold, or a missing
Annex F month, is `cannot_determine` with the name as cited and the Schedule 1 holder.

## Confidence

A transparent score: 0.93 for a finding, 0.85 conflict, 0.82 cannot_determine, minus penalties for a pickup date not
from tracking (−0.12), OCR evidence (−0.08), an invoice whose lines don't reconcile to its total (−0.25), level-2
or LLM-extracted authority (−0.02 / −0.15), and line-level duplicates (−0.08). Checked on the practice card (Brier
0.008) and the eval set; AUROC couldn't be measured because no item was wrong.

## People in the loop

Supervised: internal actions run; disputes, credit claims and document requests are `draft:*` with a draft addressed
from Schedule 1 only. $5,000+ per invoice (summed over outbound findings) or per cashback key → `human_review` in
both modes. Autonomous executes only when evidence is complete (pickup from tracking/email, invoice reconciles), no
conflict on that contract/lane/period, authority is level 1–2 and a Schedule 1 recipient exists; otherwise
`human_review`. Conflicts always `escalate`. Text aimed at AI or automated readers is flagged and never extracted.

## Feedback

Stored verbatim with `decided_at` and scope. Key decisions (`close`, `reject`, `override_amount`,
`one_off_approval`) touch exactly that key. Standing facts become rules filtered by every `applies_to` field and the
validity dates: `sender_authority` adds a person+category for one contract; `rate_accepted` authorises the named
notice for one lane; `precedence` resolves that lane's conflicts (a later signed amendment still wins). Each report
runs the auditors with and without feedback; items whose answer differs carry `learned_at` naming the item used.

## New documents

Nothing is cached as a verdict: each report re-derives every key from all documents held, so an amendment, notice,
credit note or ledger update changes the answer immediately, and expired alerts vanish. History per mode gives
stable ids; a changed answer gets a new id with `supersedes`, and a key that appears only because of a backdated
document (effective before it arrived) gets `retro: true`. A new layout needs no code: the token reader and label map
cover it, and the LLM fallback covers unrecognised email wording.

## Cost

Measured locally: ingest ≈0.1 s per text document, ≈7 s per scanned page (CPU OCR); report under 1 s for 160
documents. Claude is called only for scans when local OCR is missing (~1.6k input / 0.6k output tokens per page,
≈$0.02 on Opus 5.5) and for carrier emails with decision words the rules missed (~0.6k/0.2k, ≈$0.006). With ~1%
scans and ~5% such emails, about **$0.50 and 2 minutes per 1,000 documents**; zero calls on the practice and exam
mailboxes.

## What I'd do next

The handling-margin step-down auditor is written from the glossary only, since no mailbox shows its wording: it is
the least-tested path. I'd add LLM clause extraction cross-checked against regex results for unfamiliar contract
wording, per-line conflict ranges when one invoice mixes a conflict with a definite overcharge, an eval case with a
real hidden-style layout scan, and calibration of confidence on a larger set with deliberately wrong items.
