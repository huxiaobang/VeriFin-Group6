# VeriFin: Evidence-Grounded Financial Research with Structured Verification

COMP7608B Final Project · Group 6 · 24 November 2026

HU Xiaobang · Liang Da Wen · Yang Yisheng · Liao Sijia

## Research question and delivered system

Can structured evidence verification improve the correctness and completeness of multi-source financial research at acceptable latency and cost? We built VeriFin for students and junior analysts who start with a company question and then change the period, request the underlying table or challenge an explanation. A user can inspect a source or formula, continue the same investigation and see which parts of an answer survived review. Stock forecasts and trading recommendations are outside scope.

A working example illustrates the difference between finding a number and answering the question. We restored one historical Microsoft conversation at two different points and supplied the same short follow-up, “What about the previous quarter?” In the first branch the user had been discussing an operating-margin calculation. VeriFin returned 32,000 / 70,066 = 45.67% for the quarter ended 31 March 2025. In the other branch the user had requested the original table. VeriFin found the Form 10-Q link for that quarter. The frozen source set lacked the filing body, so it could not establish the table location. It also incorrectly marked the verifiable link as insufficient. Both the actual reports and the evidence needed to challenge them are in the submission. [1]

## What changed since the proposal

| Proposal commitment | Delivered evidence and remaining work |
| --- | --- |
| Continuous research on covered issuers | A local CLI and browser app share durable sessions, dated evidence, calculations and versioned reports. English and Chinese inputs have been exercised. Coverage depends on source access, dates, table structure and metric definitions. |
| Adaptive planning and structured verification | Three model roles act on tool observations and individual review issues. The Python controller enforces limits and report eligibility. A separate LangGraph experiment tested interrupted work. |
| Evaluate verification, planning and retrieval | We evaluated verification, planning and retrieval, and tested 12 issuer events over 20 turns. Two independent AI reference passes preceded the product runs. The proposed independent human annotation and course human check remain open. [13] |

The packaged application is the team release at 4538c9c. It includes the issuer-name evidence fix. Historical comparisons below retain their own versions. In development, both generic and structured reviewers found the same six targeted issues. The evidence supports claim-level inspection and recovery, but does not establish a general answer-quality advantage over generic review. [2–5]

---

# From a user question to a reviewed answer

## Who makes the decisions?

The Planner receives the question, active company and dates, required-answer items, available tool descriptions, relevant stored evidence and remaining resources. It can seek clarification, fetch a filing, search a disclosure, obtain financial facts or prices, request a calculation, or ask the Researcher to draft. After a tool returns, VeriFin saves the observation, projects eligible evidence into the next role input and calls the Planner again. A successful search alone does not trigger drafting or establish that a question is answered. [2, 6]

The Researcher links claims to source and calculation IDs. Selected financial Skills supply methods such as reconciling adjustments or evaluating incremental margins. The Structured Critic sees the current draft, individual claims, required items and source evidence. It checks support, source or time mismatches, numerical inconsistencies, overstated causes, omissions and material conflicts. Each issue identifies a target, references, missing support and a proposed action. The Planner chooses a feasible response such as narrowing a conclusion or obtaining another period. The engine validates IDs and dates, records accepted rows against the active draft, counts attempts and stops for completion, cancellation, no progress or exhausted budget. A user can receive a reviewed partial answer. The report can distinguish supported, partially supported, insufficient-evidence and conflicting-evidence claims, with unreviewed claims shown separately. [2, 6]

| Point in the Fastenal trajectory | Actual decision and effect |
| --- | --- |
| After the sales tool returned | The Planner saw Q3 facts and a saved calculation and proposed drafting. The engine dispatched the Researcher. The report identified the extra selling day and mixed customer demand. |
| After the Critic observed a missing quantity | In the controlled revision test the Planner proposed a local repair. The Researcher supplied the 1.62 percentage-point difference. A crash before review made the last accepted draft the appropriate recovery point. |
| At the execution limit | The controller closed the episode and showed the reviewed findings and the remaining gap. A Critic suggestion cannot grant a larger budget or make an unreviewed draft eligible. |

## Implementation and boundaries

Source adapters retain issuer, period, publication date and source location. SQLite ties observations, calculations, claims and issue receipts to the session and report version. Corrections invalidate dependent results and preserve historical versions. Current release entry points are in code/verifin/cli.py and webapp.py. The research loop is in engine.py, with context.py, model.py, tools.py, verification.py, storage.py and request_continuation.py supplying its inputs, checks and persistence. The release uses a local web interface and configurable model adapters. The proposal named Streamlit, LangGraph and Qwen provisionally. SQL persistence and domain checks were reusable across both orchestrators. [2, 6]

---

# When tools, reviews or sessions fail

## Same question, same evidence, different restart behavior

We compared the Python and LangGraph backends at experiment revision 5cbe065. Five conditions started from empty sessions with the same Fastenal turns, frozen filings, offline model replies and budgets. Real tools and SQLite writes ran. Two legacy reviews required compatibility receipts. Malformed responses and process exits were injected. A second restart control fixed all remaining replies. These are controlled software tests, separate from fresh live-model samples. Counts below are model/tool dispatches. [6]

| Condition | Python | LangGraph |
| --- | --- | --- |
| Normal conversation | 14/5. Advanced from Q4 to Q3 and retained both sales-calendar and customer-demand questions. | 14/5. Same partial answer and coverage. |
| One Critic row cited an unknown ID | 14/5. Kept five valid rows. The sixth remained pending and the demand question became a gap. | 14/5. Same five valid rows and same user-visible gap. |
| Exit after saving an unreviewed second draft | On restart 1/0. Archived reviewed Q3 v3, then treated the repeated message as a new Q2 turn and returned v4. | On restart 1/0. Resumed Q3 and returned reviewed v3 after the next Critic receipt failed. |
| Exit immediately after committing a tool receipt | On observation-dependent replay 2/1 after restart. A repeated tool dispatch failed the new period check. | 3/0 after restart. Reused the receipt and original calculation, reviewed and returned Q3. |
| Same tool fault, fixed remaining replies | 3/0 after restart. Returned Q2 with gaps. | 3/0 after restart. Returned the original Q3 answer. |

The graph improved which episode resumed and which answer reached the user. Both backends already retained valid Critic rows and resolved the unbroken follow-up. The Q3 filing showed total sales up 3.5% versus daily sales up 1.9%, with 64 versus 63 selling days. The answer explained the extra day and mixed customer results but omitted the computed gap. In one three-turn offline run, total time was 1.04 s for Python and 2.53 s for LangGraph. Their SQLite footprints were about 1.1 and 5.6 MB. These measurements include startup and persistence. They do not estimate online inference latency or long-run storage. [6]

## What we kept in the team release

The Python release gives each submission a request ID shared by the web and CLI. Retrying the same ID retrieves or resumes the original work. A deliberate new question receives a new ID even if the text is identical. If an external call may have been sent but has no committed outcome, the system can stop with a partial result and record the uncertainty. This avoids silently paying for a second request. Independent external billing and local transactions cannot guarantee exactly-once execution. We retained Python for delivery and used the graph experiment to identify a concrete recovery requirement. In a separate three-turn test, an Agents SDK loop also answered NVIDIA’s 61.11% margin and the 0.49 percentage-point decline. It returned one unresolvable calculation ID. Python used 15 model / 6 tool calls and the SDK 14/11. The pilot compared whole configurations with different review protocols. [2, 6]

---

# Matched comparisons from the proposal

E1 reuses 52 final texts from release 503c149, with fresh two-stage blind subscription scoring. E2 and E3 were rerun on the packaged release 4538c9c. E1 grading and E2 research requested GPT-6.1 Sol through the logged-in Codex subscription. The transport supplied neither a served-model identifier nor a dollar bill. [12, 13]

| Study | Matched result | Interpretation |
| --- | --- | --- |
| E1 · verification | On 11 development drafts with 64 required items, original and rules-only each delivered 35 items and one full answer. Generic review delivered 48 items and eight full answers. Structured Critic delivered 50 items and eight full answers. Two same-event diagnostic drafts were scored separately. | Both reviews gained 17 previously missing items. Generic lost four previously correct items and Structured Critic lost two. The small difference and the paired drafts limit conclusions about relative performance. |
| E2 · planning | Five paired scenarios gave each policy seven financial turns and one date clarification. Adaptive delivered five fully supported financial answers and stopped twice before drafting. Fixed delivered two full answers and five correct amounts with insufficient support for “latest.” | Adaptive clarified an ambiguous date. Fixed asked about its internal schema. Both recovered from a failed fact lookup using the original disclosure. Across both policies there were 54 model calls and 36 tool actions. |
| E3 · retrieval | On 20 answerable English queries from ten questions, plus four negative controls, BM25 retrieved the complete reference for 15, dense for 16 and hybrid for 16. All methods had a five-passage, 3,000-character limit. Three metadata controls excluded all results. | Dense and hybrid each returned five irrelevant passages for the unrelated query. The tested vectors use 600-character passages with 80-character overlap. The default 1,800/250 split lacks matching archived vectors. |

## What the failures tell us

The NVIDIA operating-income test contains a real discrepancy. The later CFO commentary directly reports USD 13,615 million for fiscal Q4 2024. Annual operating income less nine-month operating income gives USD 13,614 million. Both reviewers' revised answers distinguished the two figures and left the cause unresolved. In the same-event revenue control, direct disclosure and subtraction agreed at USD 22,103 million. Both model arms retained the original text.

Two adaptive follow-ups selected the intended company or quarter and paraphrased a locked requirement. The controller stopped both turns. A repair preserved the original requirement text under its identifier. A separate fixed-policy repair reused eligible evidence on the repeated query, reducing its five then four tool attempts to three then two. A Critic transport timeout still occurred in candidate testing. The submission release and repair trials retain separate scores. [13]

---

# A twelve-event test on the submitted release

We froze questions, disclosure cutoffs and source snapshots before product inference. Twelve issuer events drew on 13 original documents. Eight events had two turns in the same session. Two isolated AI reference passes and adjudication against the originals preceded the first product call. Searches of 1,009 prior project records found no known overlap for these events. These checks give a bounded account of independence. [13]

| Measure | Result on release 4538c9c |
| --- | --- |
| AI grades against the frozen reference checklist | 15 pass, two partial and three fail across 20 turns. Eight of the 12 events passed every turn. Product status labelled 13 turns complete and seven partial. |
| Research resources | 113 subscription calls across Planner (69), Researcher (23) and Critic (21), and 28 financial tool actions. The parallel batch took 29.8 minutes. Median turn duration was 176.9 seconds. |
| Calculation traceability | No calculation IDs were saved in these 20 reports. Several answers reported correct disclosed values. For derived figures, a reader could often reconstruct the arithmetic, while the application offered no stored formula to replay. |

## What a failed answer looked like

Autodesk’s first answer omitted USD 451 million although its Researcher had produced that value. A unit validator treated two equivalent descriptions of USD millions as inconsistent. Replaying the same response after repair retained the amount and still rejected a changed currency. Intuitive Surgical’s follow-up stopped when a valid Planner response paraphrased a locked requirement. UPS’s segment comparison never reached the user because a list was supplied where the claim contract expected a single business scope.

A separate fresh cross-company trial on repaired code delivered Meta’s 43.02% and Alphabet’s 32.43% operating margins, their 10.59 percentage-point spread, two SEC filing links and three saved calculations. It had filing-linked facts and could not verify the statement tables inside the filing bodies. The release candidate passed 997 software tests. These case-specific trials measure behavior on known failures. The packaged source and the 12-event scores describe the original submitted release. [13]

## Does a longer conversation lose the research context?

In a separate eight-turn Costco session on the 997-test candidate, the user changed from a 16-week quarter to the full year, then asked to revisit the earlier comparison. A fresh process reopened the session after turn four. All four earlier report IDs, versions and content hashes still matched after turn eight. The first answer gave the numerator, denominator and source but withheld the requested margin. Seven subsequent answers met the source-checked content requirements, including a change of denominator and a refusal to infer causation from arithmetic alone. The application recorded three complete and five partial reports, 50 model calls, nine tool actions and zero saved calculations. This is one development conversation. In a later UPS trial a repaired candidate delivered 15.0% − 6.5% = 8.5 percentage points in the primary answer and saved a calculation tied to both original segment cells. An independent EPS guidance question still displayed a redundant gap despite giving the signed adjustment. [14]

---

# What users actually received

## Three kinds of development evidence

| Design | User-visible result | Resources and interpretation |
| --- | --- | --- |
| Eighteen independent questions per model, 0d7d9f3 | GPT-6.1 Sol had 12 pass, 6 partial, 0 fail. Qwen3.7-Plus had 8 pass, 9 partial, 1 fail. Each question had a fresh session. | GPT 187 model / 91 tool calls, USD 5.0821 estimated. Qwen 126/59, USD 0.8351 reported with USD 0.0879 separately reserved. Shared call, time and context ceilings. Dollar admission caps differed. AI graded known development questions. |
| Three six-turn conversations using the earlier question set | All 15 follow-ups retained the expected context. Product state was complete for 15/18 turns. These questions and issuers were already known. | Historical GPT-6.2 Sol via a logged-in Codex account, 130 roles and 43 tools. This experiment carries no measured per-run dollar price. |
| Three new English dialogues, 618cf925 | Across 12 turns at HD/LOW, Disney and Delta, 9/9 follow-ups retained the intended referent. Answer review found 10 full and 2 partial. Product labels were 8 complete and 4 partial. | GPT-6.1 Sol via the native OpenAI adapter, 94 actual model receipts and 20 local tool calls. Usage estimate USD 1.8902. One run per conversation. |

The new English set exposed a discrepancy that a context-only score would miss. Delta’s two adjusted-revenue figures and growth percentage appeared in the user answer. Their USD 415 million difference appeared only in review comments. A retail follow-up gave the 0.1 percentage-point comparison, but a duplicate checklist item remained open. In a restoration of the prior publication cutoff, the answer also left a source-availability explanation incomplete. A grader must read what the user received and check the requested elements separately from the runtime badge. [1, 7]

## Finding a tool-chain error

In two further three-turn dialogues, searches for “Disney” and “Home Depot” succeeded. The next model still saw no useable records. The search tool saved records under DIS and HD, while the engine’s visibility filter compared them with the long issuer names. On the old release, six turns consumed 134 model and 130 tool calls and delivered zero full answers. With the issuer binding repaired, six turns consumed 30 model and seven tool calls and all six answers covered their requests. Both arms used real GPT-6.1 Sol with the same frozen sources and questions. Replaying the identical first Planner decisions then isolated the code change: both versions saved the same six and five evidence IDs respectively, but text visible to the next role changed from zero to three and from zero to four records. [8]

The fix is in 4538c9c. Two Skills absent from the 48 earlier turns we inspected reached the Researcher and Critic in the repaired traces. Home Depot’s incremental GAAP operating profit was about 1.00 cent per extra dollar of sales, versus 3.33 cents on an adjusted basis. Three correct numerical answers across the two dialogues nevertheless lacked saved calculation IDs. The Researcher instructions require a saved calculation for derived numbers. These three correct answers violated that provenance requirement. [8]

---

# Stress-testing financial reasoning across follow-ups

We wrote six English three-turn dialogues in two batches. Each chain starts with a blank SQLite session and keeps its own context across three turns. Questions target statement reconciliation, business-perimeter changes, cash-flow measures and unsupported causal explanations. Tests supply public questions and frozen issuer documents. The live GPT-6.1 Sol roles choose their tools, method and answer. Reference figures stay in separate reviewer files. The host uses the authenticated Codex CLI for these experiments. On 4538c9c, 18 turns produced 13 complete and five partial product reports using 125 research-role calls and 31 financial-tool attempts. These totals exclude candidate reruns. The packaged app uses its configured API provider for live inference. [9]

## Cash-flow cases on the packaged release

Disney's first question asks for the quarter's free-cash-flow bridge. The next asks for six months and the implied first quarter. The third challenges whether the improvement is sustainable. The answer correctly identified tax-payment timing as a limitation and avoided inventing an adjusted cash amount. At Home Depot, the system calculated operating cash flow less capital expenditures of 7,245 and 9,340 million for the two half-years, a decline of 2,095 million. It distinguished a 70 million increase in right-of-use assets from actual cash lease payments and reconciled the 1,145 million increase in cash without treating the increase in net property and equipment as cash capex. [9a]

An independent comparison with the source figures found six substantively correct user answers. The application returned three complete and three partial. Across 44 role calls and nine tool calls, the application saved zero calculation records. The Home Depot first turn alone took 537 s. A candidate parser then attached dated cash-flow cells to the source-tool observation. In a fresh run on the same question, the model chose the calculator and left five saved calculations. It still returned partial. The verifier compared a signed change of −1,938 to the wording “1,938 million to the year-over-year decline” and raised a numeric inconsistency. Tool repairs improved auditability and exposed a separate sign-versus-magnitude error. The candidate is outside the submission release. [9a]

## Challenging an interpretation

A Disney question asks whether a 37% stake means an entire joint venture's revenue stayed in consolidated results. The preceding turns establish Q2 and derive Q1; the third asks about the last period without restating it. The answer retained Q1, explained the change from consolidation to equity-method accounting and did not infer the venture's revenue from its ownership share. The Q1 bridge from segment operating income to pretax income was 5,060 − 460 − 33 − 143 − 367 − 397 = 3,660 million. [9b]

Delta's premium-ticket question tested a different failure mode. The release reports cabin categories, not the same customers' movements between them. VeriFin preserved this distinction when asked whether passengers switched cabins. Earlier it had supplied a correct 235 million subtotal without a saved calculation and labelled a dollar symbol as USD more confidently than the frozen table warranted. All three Delta turns carried complete badges. In a second chain Home Depot disclosed comparable sales and overall transaction measures for different populations. The app correctly kept a gap where the release did not reconcile the populations, and declined to apportion growth to online orders. At Disney, a rubric also penalized omission of an inferable Q1 network total (1,098 million) although the user explicitly asked for three unavailable components. This is a grading ambiguity. [9c]

---

# Do industry research methods improve the answer?

Our 22 method cards give role-specific research guidance. We expected industry knowledge to change which disclosure an analyst seeks. For example, weaker manufacturer shipments may reflect dealer destocking while retail sales rise. The catalogue also includes general methods such as cash-flow reconciliation. This development study supplements E1–E3. [15]

## What changed when roles received a card?

On local candidate 38, each card had one synthetic question, a fixed source packet and four prespecified scoring criteria. The two arms used identical inputs and fresh subscription inference. Only the treatment supplied the card text to the researcher and reviewer. An independent grading call scored the final answers with arm labels concealed. [15]

| Condition | With card | Without card |
| --- | --- | --- |
| All 22 suitable questions | 88 / 88 | 87 / 88 |
| Subset of 14 cards with no confirmed prior body exposure in the audited records | 56 / 56 | 56 / 56 |
| Same-question repeat for the one prespecified-score difference | 4 / 4 | 3 / 4 |

Both arms correctly reconstructed subscription revenue from average paying accounts. Only the card-assisted answer consistently explained why multiplying back a unit rate derived from that same revenue provides a consistency check. A second blinded review also identified an optional percentage-point detail on a different question. On two cards tested for discovery, the ordinary Planner selected the intended method on four suitable prompts and selected none on four similar lookup prompts. This small routing check establishes that the Planner can recognize an appropriate method in these cases.

## What remains open

Three full-engine pairs covered a real Disney tax disclosure, a synthetic project contract and a historical UPS cost-per-piece follow-up. Across these pairs, enabling a card added no correct, reproducible financial conclusion to the delivered report. Both Disney reports were marked complete with no saved calculation. Both UPS arms saved the cost reconstruction and left its difference from segment expense outside the verified claims. Each condition ran once per product case. [15]

The evidence packets and sometimes the questions supplied the reasoning path. With only one point of headroom, this trial tells us little about independent discovery. We would share unit and period checks across roles and test an industry card on a neutral question such as “Does the drop in shipments mean demand is falling?” The test would measure whether the Planner independently seeks retail sales and channel inventory.

---

# Cost, workload and conditions of use

## What was controlled

The matched GPT/Qwen independent pilot allocated each question up to 16 model calls, 16 tool calls, 64 Planner steps and 360 active seconds. Both arms used the same frozen sources and 180,000-character context ceiling. Admission caps were USD 15 per batch / 1.75 per case for GPT and 4 / 0.45 for Qwen. The 18-turn Chinese follow-up study used higher per-turn ceilings of 96/96 calls, 192 steps and 7,200 seconds. None of its three partials stopped for budget exhaustion. For the current English dialogue study we used the same high ceilings to expose substantive failures. These are experimental limits. The core release defaults are 16/16 calls, 64 steps and 240 seconds, with a no-progress limit of three. A resource limit ends new work while preserving available results. [1, 7, 9a]

| Observable check | Evidence and operational consequence |
| --- | --- |
| Latency and workload | The matched Microsoft formula and table-link turns took 83.8 and 58.9 s, using 11/4 and 8/4 model/tool calls. In the new paid 12-turn test the three chains required 361.44, 283.83 and 304.67 s in aggregate per chain. A short user question can require several expensive role calls. |
| Paid usage | The two Microsoft continuations cost an estimated USD 0.6519 in total. The English 12-turn set settled all reservations, with 94 receipts and USD 1.8902 estimated from tokens. A subscription run has no equivalent project API invoice. Recorded usage, estimates and unresolved reservations are reported separately. |
| Input reuse | In the matched Qwen run 79/125 billed receipts had some cache hit. Of 2.20 million input tokens, 621,184 (28.22%) were cached. Uncached input and generated output still dominated cost. Long changing evidence and different roles limit reuse. |
| Model and output budget | An earlier DeepSeek V4.1 Flash 1017 pilot had 3 pass / 8 partial / 7 fail. A subsequent release with role output caps raised to 16,384 had 12/3/3, with USD 0.3513 recorded for the new batch. Both release and run conditions changed; the gain cannot be attributed to the output cap alone. |

## Data boundaries, sources and reproducibility

The app stores the research session and snapshots locally. Live inference sends role instructions, the question and selected evidence to the configured provider. External source calls require access and the SEC requires an identifying user agent. Frozen mode fixes the available record; public index metadata can establish a filing URL even when the body needed to support a specific amount or line is absent. In the new paid dialogues SEC snapshot requests returned HTTP 403 and research used issuer releases. A transport success can still contain an unusable model response. Neither an HTTP success nor a citation alone settles financial correctness. [1, 7, 10]

On a fixed issuer set the group can inspect mistakes and improve tool design. We have no service-level guarantee, representative workload distribution, or measured population accuracy. Legacy market snapshots record a provider closing price whose adjustment status can be uncertain. Causation and access outside the available disclosures must remain explicit gaps. Frozen-source tests and compatibility replays cannot establish today’s live data availability.

---

# Evaluation limits, authorship and reproducibility

## How we judged answers

Pass requires correct required content, units, period and support in the delivered answer. Partial requires at least one useful supported element and a substantive omission, contradiction or unsupported assertion. Fail means no reliable substantive answer. We inspect source spans, recompute derived amounts and record whether a claimed calculation actually exists. A legitimate limitation can satisfy a request about what the source can establish. Product status records the engine's decision. Development grades were assigned with AI assistance; grader identity and access to model labels can bias them. Multiple related turns and paraphrases share one event and cannot be counted as independent trials. [1, 3–5, 7–9]

The revised team protocol allowed two isolated AI reference passes for the 12-event study. Independent annotation by two members, as proposed, and a human check of AI judgments remain outstanding. The evidence package contains the delivered answers, source checks, AI grades and a blank human-review worksheet. Cases used to diagnose and repair the application are development evidence for subsequent releases. [1, 11, 13]

## Contributions and AI assistance

| Member | Contribution supported by the available records |
| --- | --- |
| HU Xiaobang | Integrated source and group handoffs, directed project iterations, and maintained implementation and evaluation work on claim verification, recovery and tool-chain reliability. Commits and test records identify this work. |
| Liang Da Wen | Contributed testing feedback and recovery cases. Shared team-account tasks cover Critic handling and Qwen review settings. The records associate these tasks with Liang but do not identify personal authorship of every change. |
| Yang Yisheng | Participated in member testing. The project reused the member's financial ticket to check continuations and source references. |
| Liao Sijia | Participated in member testing. A financial request exposed incomplete verification and supplied a case for follow-up diagnostics. |

These descriptions need individual confirmation for work outside the records. Codex assisted with code, regression design, orchestration comparisons, review and report drafting. GPT, Qwen and DeepSeek supplied research-role outputs in the specified studies. AI-assisted graders and independent software replays both have different roles from human annotators. The ZIP includes model settings, prompts, preserved development instructions and a declaration of missing historical prompts. [11]

## Prior work and submitted materials

ReAct informed observation-driven planning, RARR informed evidence-based revision, and ALCE informed our distinction between citation quality and answer correctness. We credit SEC EDGAR and issuer disclosures for research evidence. Python, SQLite, document parsers and experimental retrieval dependencies are credited with notices and model provenance in the ZIP. Its README runs the application without a notebook, demonstrates a normal question, a scope change and a failure, and identifies where to supply optional credentials. [2, 10, 11]

---

# Evidence guide

Paths below are relative to the companion ZIP. Its application source is pinned to 4538c9c.

| Ref. | Material to inspect |
| --- | --- |
| [1] | evaluation/followup-traces.json, formula-answer.md, source-answer.md and sec-reference.json document Microsoft follow-ups. pilot18-results.json and pilot18-answers.jsonl.gz contain independent-question results. |
| [2] | code/verifin/ contains the application and skills/. See code/docs/MODEL.md and attribution/runtime-role-prompts.json for model settings and static prompts. |
| [3] | evaluation/experiments/e1-case-review.tsv and e1-payloads.json give the paired detection results and serialization checks. evidence/e1/ records missing historical four-arm finals and earlier revisions. The subsequent handbook collection is in [12]. |
| [4] | evaluation/experiments/e2-503c149-readiness.json gives per-turn source and final outcomes. e2-cases/ contains the shared public prompts and TaskSpecs. |
| [5] | evaluation/experiments/e3-pairs.json, e3-queries.json and e3-503c149-retrieval.json give retrieval budgets, query-level results and release-configuration checks. |
| [6] | evidence/recovery/comparison.json compares both backends, answers and calls. sdk-comparison.json covers the Agents SDK pilot. |
| [7] | evaluation/development18/dialogue-2026-11-23/ archives three six-turn conversations. evaluation/new-dialogues-2026-11-23/ records 12 fresh paid turns and issuer snapshot identities. Both include the questions and actual delivered answers. |
| [8] | evidence/issuer-binding/ contains paired search receipts, role/Skill exposure, actual reports and saved-calculation audit. Baseline and candidate live draws were separate. |
| [9] | evidence/nov23/ holds six three-turn dialogues grouped as cash [9a], basis [9b] and segment [9c]. Each has its own questions, post-run assessment and original user answers. Candidate reruns carry separate labels. |
| [10] | evidence/provider-studies.json reports the historical DeepSeek output-budget and Qwen cache diagnostics. code/data/replay/manifest.json and the individual study manifests identify available documents and source dates. |
| [11] | attribution/ has model-use declarations, third-party notices, contribution evidence and project prompts. evaluation/human-review.csv is the worksheet for the outstanding human check. |
| [12] | evidence/handbook/report-tables.md and adjacent E1, E2 and E3 records document the course-version tests. |
| [13] | evidence/handbook-v2/ contains the revised results, four-arm scores, paired planning review, retrieval rankings, turn-by-turn issuer grades and delivered answers. Release-candidate validation is recorded separately. |
| [14] | evidence/dialogue-length/ contains the eight-turn Costco answers, post-restart checks and four-turn Home Depot study. The short and long wording comparison uses independent fresh sessions. |
| [15] | evidence/skill-cards/ contains the questions, paired scores, routing records and three full-engine comparisons. |

See attribution/THIRD-PARTY.md for publication links and notices. The pinned code passed 971 Python and two browser-script tests. Later commit df7d81e reached a draft using local Codex login before an account limit stopped the Critic.
