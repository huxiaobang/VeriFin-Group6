# E1 frozen-evidence evaluation scaffold

This module implements a paired **detection protocol and common candidate revision
executor**, not reported experimental results. Its tests use explicit fake model replies and supplied annotation fixtures.
No real evaluation, effectiveness gain, or live model call is claimed.

## Conditions and fairness

`evaluate_frozen_case(case, model)` returns four conditions:

| Condition | Rule findings | Review call | Corrective actions during detection |
|---|---|---|---|
| `no_verification` | None | None | None |
| `rules_only` | Deterministic checks | None | None |
| `generic_review` | Same deterministic checks | Substantive open-ended review | None |
| `structured_critic` | Same deterministic checks | Per-item/category Structured Critic | None |

Both review calls receive byte-equivalent serialized task context: request,
conversation, scope, required-answer checklist, original evidence, calculations,
candidate claims, available tool descriptions and rule findings. They use the
same client, model snapshot, output limit, context cap, timeout and optional
thinking setting. The run records context, prompt, settings and output hashes.
It refuses settings changes between the paired calls. Neither condition receives
gold issues, expected answers or human judgments.

Generic review is deliberately substantive. It asks about correctness, support,
completeness, financial context, calculations, explanations, gaps and conflicting
evidence, and invites useful corrective actions. Its response is two free-form
strings, `review` and `suggested_actions`, inside the common JSON transport
envelope. It does not require item/category records or reviewed-ID lists. The
Structured Critic uses the proposal's per-item/category checks and issue fields.
This evaluates the **whole structured verification scheme**. It cannot isolate
an effect of JSON formatting, a particular prompt, or the checking procedure.

Each model condition makes at most one call with no retry. A failed/malformed
review is recorded as `unavailable`, with its attempted call and available usage,
not as an empty successful review. Missing usage is unknown rather than zero
cost. `rule_seconds` is measured once on the frozen case and shared by the three
rule-based conditions; `model_seconds` measures the review call and response
validation. These are scaffold diagnostics, not an end-to-end latency benchmark.
The optional revision phase records interpretation/action attempts and elapsed
time separately. Revised-answer human assessment now has an offline annotation,
adjudication and scoring protocol; the actual human work is not completed or timed.

`review_order` can be either permutation of the two model conditions. A future
study should predeclare and counterbalance order, freeze model/prompts/budgets,
keep failed calls, and use repeated trials where appropriate. Do not select
favorable outputs after observing the scores.

## Create and run a frozen case

```python
from verifin.evaluation import FrozenCase, evaluate_frozen_case
from verifin.model import ModelClient

# context contains the same candidate answer and original sources for all arms.
# Human gold and expected answers must be stored separately.
case = FrozenCase.from_context("heldout-001", context)
run = evaluate_frozen_case(case, ModelClient.from_env())  # makes real calls
```

The factory makes a detached JSON snapshot, removes candidate status/confidence
badges, and keeps task/source fields. It rejects annotation fields in context.
Freeze immediately after candidate synthesis and before its first semantic
review. The factory rejects an episode explicitly marked reviewed, reviewer-role
messages, explicit prior-review result fields, and contradictory scope/checklist
copies. Prior reports and episode diagnostics are excluded; the original episode
request is retained when it is not a top-level field. Natural-language feedback
hidden inside ordinary user/assistant prose cannot be recognized reliably by a
schema check. Case curators must therefore inspect conversation history and
exclude feedback about the candidate being tested; do not construct held-out
cases by copying a post-review chat transcript.
The caller should save `case.context()` plus `case.context_sha256`, then save the
returned run JSON. The scorer does not treat a model's `Supported` label or a
rule pass as ground truth. Free-text document instructions remain untrusted data.

The example is usage documentation, not a command run during this implementation.
See [MODEL.md](MODEL.md) for credential and provider configuration.

## Independent annotation and scoring

`score_detection(case, condition_result, gold, judgments)` calculates counts from
separate human records. At least two distinct annotator IDs, an adjudication
flag, a guideline version and the frozen-case hash are required for both records.
These are protocol checks; the software cannot prove that named humans actually
performed independent annotation. The team must retain the annotation evidence.

Gold format:

```json
{
  "case_id": "heldout-001",
  "context_sha256": "ACTUAL_FROZEN_CASE_HASH",
  "source": "independent_human",
  "annotators": ["reviewer-a", "reviewer-b"],
  "adjudicated": true,
  "guideline_version": "e1-guidelines-v1",
  "issues": [
    {"id": "g1", "target_id": "claim-or-requirement-id", "description": "Independently established error or omission."}
  ]
}
```

For each condition, human assessors segment its **complete feedback**, including
rule findings, into distinct issue detections. They use the same guidelines for
both prose and structured output; format alone earns no credit. When possible,
blind assessors to condition labels. Each finding maps to one gold issue or null.
Judgments have the same case/source/annotator/adjudication/guideline metadata plus:

```json
{
  "condition": "generic_review",
  "output_sha256": "ACTUAL_CONDITION_OUTPUT_HASH",
  "segmentation_complete": true,
  "findings": [
    {"id": "f1", "text": "The human-segmented detection.", "gold_issue_id": "g1"},
    {"id": "f2", "text": "A finding not matched to a true issue.", "gold_issue_id": null}
  ]
}
```

The output hash prevents reusing judgments for a changed review. Gold targets may
be a claim or a required-answer item; omissions do not require an existing claim.
Each gold issue counts once. Extra duplicate detections count as false positives,
so annotations should consolidate mere repetitions under a predeclared rule.
Precision, recall and F1 use TP/FP/FN counts; a denominator of zero is reported as
`null`, not invented perfect performance. The no-verification arm emits no defect
findings. Failed review conditions are unavailable observations and cannot be
scored as successful zero-finding reviews.

This scorer measures **issue detection only**. It does not automatically evaluate
claim truth, answer quality, correct required-content coverage, causal support,
regressions, or completion. Natural outputs and injected-error diagnostics must
be kept separate, with related turns/perturbations in the same data split.

## Common candidate revision

`revision_input(case, condition_result)` returns `{context, feedback}` for every
condition. `CommonRevisionExecutor(model, tools_factory).revise(context, feedback,
*, budget)` executes the same correction loop for every feedback format. The
common `revision_planner` chooses a tool, candidate synthesis, a clarification,
or stopping. Its feedback interpretation calls count against the same finite
model allowance. The normal researcher synthesizes candidates; no extra rule
review, generic review or Structured Critic is inserted into any condition.
No-verification/rules-only controls can use the same executor and receive only
their original feedback. The executor itself receives no condition label.

`run_revisions(case, detection_run, executor, *, budget, conditions=...)` validates
the entire requested pair's case, feedback and settings hashes before execution.
It defaults to the generic and structured review arms; `conditions` also permits
the two controls and records execution order. Detection failures remain unavailable,
with no silent fallback revision. Predeclare/counterbalance order in a study.

```python
from verifin.evaluation import run_revisions
from verifin.revision import CommonRevisionExecutor
from verifin.tools import ToolRegistry

# Same model object/settings and same tool/source configuration for all arms.
# Persistent immutable source snapshots must outlive the temporary working DB.
executor = CommonRevisionExecutor(model, lambda store: ToolRegistry(
    store, data_dir, allow_network=False, cache_dir=persistent_source_cache))
revisions = run_revisions(case, run, executor, budget={
    "max_model_calls": 10, "max_tool_calls": 8,
    "max_seconds": 180, "max_no_progress": 3,
})
```

Each invocation creates a new temporary SQLite workspace, imports the same
frozen task and evidence, and discards that database at the end. It does not
change the user's session. Import assigns local storage versions and records
an original-to-working version map plus each original record's hash. Existing
stale flags, mismatched dependency versions and claim scope mismatches remain
stale. Calculations with missing/stale/cyclic operands remain visible as blocked
records, unavailable to calculation tools until repaired. Stable logical session
metadata prevents random temporary IDs and import timestamps becoming input
differences between arms. Source URLs, locators, hashes and existing snapshot
paths are retained; the caller must preserve those files. New source caches
must be outside the executor's temporary workspace. The tool interface must
match the frozen case when the case includes tool descriptions.

The original scope and checklist are immutable during this phase. Empty answers,
unanswered requirements, clarification, failed tool/model attempts, no-progress
stops and exhausted budgets remain explicit. Available token usage and elapsed
time are recorded, including failed attempts. Missing provider token usage is
unknown, not zero. Time limits shorten per-call timeouts and prevent further
scheduling; this synchronous interface cannot forcibly interrupt an already
running transport. To compare retrieval outcomes, freeze the same replay corpus
or immutable source snapshot set across arms. Identical tools and settings alone
do not make live-changing source responses equivalent; source freshness and cache
warming must be controlled separately for a defensible latency comparison.

`finish` yields only a **candidate**, never Supported or Complete. All claim
statuses remain `Pending Verification`; `verification_available` and
`truth_scored` are false. The returned coverage is syntactic linkage only, not
proof that required content is correctly answered. Independent human assessors
must judge both corrections and regressions, including whether originally
correct required content was deleted or weakened. The existing detection
scorer must not be presented as a revision-outcome scorer.

The offline outcome scorer, explicit-price resource summary, grouped study plan,
annotation worksheets and JSON/CSV aggregation are documented in
[ANNOTATION.md](ANNOTATION.md). They do not call a model or create completed
human judgments. Correct required content removed during revision blocks a safe
improvement label; missing costs and unassessable outcomes remain unknown.

Still required before the proposed evaluation: actual independently annotated held-out
cases, a frozen development protocol, actual model access checks, measured revision
outcomes and latency/cost, case-level failure analysis,
and the separate E2 planning and E3 retrieval comparisons. No numerical gains
should be reported from the current fixture tests.
