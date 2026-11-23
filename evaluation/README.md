# Review two real follow-ups

The user had been researching Microsoft's consolidated operating margin. Two copies of the saved conversation were opened at different points. In the first, the preceding question asked for the operating-margin formula and inputs. In the second, it asked which original financial table supported the figure and requested a link. The next message in both was “那上一季呢？” (“What about the previous quarter?”).

The packaged Python release, `503c149`, ran both episodes with fresh GPT-6.1 Sol responses and financial tools restricted to frozen public data. Each episode began with exactly its historical questions, records and reports. The harness supplied the user message; the system selected the subsequent actions. These are two diagnostic runs of a previously studied company. The observations come from one generation per context.

| Prior topic | User-visible answer | Calls and cost |
|---|---|---|
| Formula | [Original Markdown](formula-answer.md) | 11 role calls, 4 local tool calls, 83.8 active seconds, estimated USD 0.3307181 |
| Original financial table and link | [Original Markdown](source-answer.md) | 8 role calls, 4 local tool calls, 58.9 active seconds, estimated USD 0.3211568 |

The two Markdown files contain the original Chinese answers, including the review labels and any contradictions. The trace records the observed Planner actions, tool outcomes, researcher claim IDs, Critic issues and final stops in sequence. Financial research used local sources. Model calls went to the official API. Costs are estimates from provider token receipts. The recorded study can be inspected without an account. [Trace and settings](followup-traces.json)

## How to review

1. Read each answer in the context of its preceding question. Record whether it retained the issuer, advanced one fiscal quarter and answered the request about the formula or the original table. Assess the findings as well as the answer and unresolved-item sections.
2. Compare each answer against the two SEC Company Facts rows and the submissions-index row. Check the period, units, arithmetic or filing identity directly. The SEC index establishes the filing's primary document. This run's frozen materials did not include its HTML text, so checking a specific table or line against that HTML was outside the observed source coverage. [Frozen-source rows, original SEC URLs and snapshot hashes](sec-reference.json)
3. Make a personal copy of the review sheet and record a grade of **pass**, **partial** or **fail** with the reason and source locator. A pass requires all requested elements to be correct and supported. Partial means a substantive correct element was delivered with an omission, contradiction or unsupported element. Fail means no reliable substantive answer was delivered. A correct source link and a verified line in a financial statement are separate elements. Fill in the actual reviewer's name and date only after reading the materials. [Review sheet](human-review.csv)

The supplied sheet has no human grades. Two reviewers can fill separate copies before comparing or adjudicating. These cases are development evidence and must remain outside the planned twelve held-out cases.

Run `python3 evaluation/check_evidence.py` from the ZIP root to check the answer hashes, role and tool counts, shared SEC filing identity and rounded margin using the included source extracts. The script runs offline with the Python standard library.

## Historical 18-question comparison

The two historical reference arms ran on release `0d7d9f3`. Each of the 18 public development questions began a separate session. The same question is recorded once per model. The compressed file contains 36 original answers with their question IDs and answer hashes. The results file preserves the per-case AI judgment, rationale, grading reference used after generation, calls and cost summaries. Separately rerun cases are excluded from this comparison. Human regrading and the new-event holdout remain unfinished. [Original answers](pilot18-answers.jsonl.gz) · [Post-run results and references](pilot18-results.json)

## Team runtime dialogue results

[Three new financial dialogues on GPT-6.1 Sol](new-dialogues-2026-11-23/README.md) includes 12 user-facing answers, per-turn usage and the remaining gaps. [Three six-turn follow-up dialogues](development18/dialogue-2026-11-23/README.md) includes the earlier answers and the review replay after the release fixes.
