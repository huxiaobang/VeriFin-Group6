# E2 planning protocol and fixed workflow

The implementation runs a task-type fixed workflow and an adaptive Planner through the **same `ResearchEngine`**, storage, tools, Researcher, rules and Structured Critic. It produces inspectable experiment artifacts. No E2 answer-quality or planning-effectiveness result has been established: the automated tests inject explicitly labelled scripted models and validate execution, accounting and isolation only.

## Public task specification

`TaskSpec.from_dict(value)` validates an immutable public description of what the user wants. `spec.to_dict()`, `spec.sha256` and `spec.scope()` expose a copy, stable hash and common initial scope. Both conditions receive this same specification in `scope.planning_task`, the original request and the conversation.

The format is:

```json
{
  "version": 1,
  "cutoff": "2025-01-31",
  "source": "replay",
  "items": [
    {
      "id": "revenue",
      "text": "Revenue for the requested quarter",
      "kind": "fact",
      "entities": ["MSFT"],
      "metric": "revenue",
      "periods": [{"start": "2024-10-01", "end": "2024-12-31"}]
    }
  ]
}
```

[`planning_case.example.json`](planning_case.example.json) contains a development conversation: obtain revenue/operating margin, then compare revenue with the previous quarter. Its figures are fetched from registered source snapshots; the file contains no expected answer or tool sequence. It is development material, not a held-out evaluation case.

| Task kind | Public fields and fixed acquisition |
|---|---|
| `fact` | One issuer, metric and exact period; retrieve the requested fact. |
| `compare_periods` | One issuer, metric and two periods; retrieve both and request their difference. The calculation tool rejects incompatible periods. |
| `compare_companies` | At least two issuers, metric and one period each; retrieve the observations for side-by-side comparison. Distinct fiscal dates stay explicit; this task does not silently calculate a difference across unmatched periods. |
| `margin` | One issuer and `operating_income`, `net_income` or `gross_profit`; retrieve numerator and revenue, calculate each requested margin. With two periods, also request percentage-point change. |
| `growth` | One issuer, metric and two periods; retrieve both, order current/prior by their actual end dates, then calculate growth. Zero/negative bases and incompatible definitions remain tool failures. |
| `price` | One issuer and `window: {start, end}`; retrieve exact endpoint closes and descriptive price return. No weekend substitution, prediction or causal attribution. |
| `explanation` | One issuer, one period (end alone allowed), and `query`; retrieve original passages and use a bounded original-filing fallback if needed. |

`periods_by_entity` can replace `periods`, mapping every issuer query to its own dates. Supported metric aliases come from the shared tool adapter; explicit `us-gaap:Concept` / `ifrs-full:Concept` values are also accepted. Instant aliases use end only. An explicit taxonomy concept may supply end only or start/end; the financial adapter checks its actual temporal definition. Optional `unit` filters fact retrieval. More complex requests use multiple required-answer items.

Relative periods can use `{"relative":"latest","kind":"quarter","offset":0}`; kind may also be `annual` or `instant`. The workflow obtains `available_periods` already filtered by the information cutoff, distinguishes durations from cumulative values, and selects the requested distinct end date. Quarter and annual are duration classes of 70–100 and 330–380 days. Multiple matching starts at the selected end date require clarification. Offset means prior *available distinct periods*, not a silently inferred “same fiscal quarter last year.” Exact fiscal-year/quarter labels require upstream resolution to dates.

The specification is **not a natural-language parser**. The fixed baseline does not pretend to understand arbitrary dialogue through ticker keywords. An unresolved/null specification produces a clarification. A shared upstream resolver or user-confirmed structured scope can supply the specification; its creation cost and accuracy are outside the present runner and must be reported separately. A clarification must update the public specification before the fixed workflow continues. Consequently this protocol evaluates planning after shared task resolution; it does not establish end-to-end natural-language scope-resolution quality.

Unknown fields, non-finite JSON, private annotations, expected answers and gold tool sequences are rejected. The runner also prevents an adaptive plan from dropping/rewriting the public required-answer items, changing the information cutoff, or replacing `planning_task`. Issuer canonicalization and source-based date resolution remain available. These shape checks cannot detect a private answer disguised as prose: case authors still need independent input review and split control.

## Fixed policy

`FixedWorkflowPlanner(max_revision_rounds=1).decide(context)` returns the existing planner action schema. It performs no tool or model calls itself. The controller executes every returned action and applies cancellation, no-progress and finite budgets.

1. Resolve issuer queries through `discover_company`, or reuse an exact issuer already represented by eligible saved evidence. Multiple candidates require clarification. There is no company allowlist.
2. Follow the selected task-type acquisition. Reuse exact, fresh, cutoff-eligible facts and calculations with matching operand versions. A failed financial-fact lookup triggers a bounded disclosure lookup; it does not substitute annual/cumulative figures. A lexical document miss can lead to discovered filings and one bounded preview after a readable filing has no query match. A preview does not establish complete document coverage.
3. Use the common model for synthesis and the common rules/Critic for verification. Missing observations remain required-answer gaps.
4. Apply at most the configured number of fixed repair rounds. Retrieval-related issues trigger reacquisition for affected requirements; wording-only issues trigger rewriting. Every revision is synthesized and checked by the same model roles. The common controller decides complete versus partial.

This is a predetermined task-type policy with bounded failure/repair branches, rather than a gold sequence or a universal query script. It does not generate open-ended new searches or infer unsupported financial transformations. The adaptive arm can make other legitimate choices using the same tools and budgets. This difference is part of E2's whole planning-policy comparison.

The fixed planner keeps scheduling state in its engine instance, while source evidence and reports remain persistent. Clarification on the same instance resumes without resetting the episode allowance. If a process dies, the shared controller closes the interrupted episode as partial; a fresh workflow can reuse durable evidence in a new episode. The fixed generator itself is not serialized for cross-process continuation at a clarification boundary.

## Paired runner

```python
from verifin.engine import Budget
from verifin.model import ModelClient
from verifin.planning_evaluation import FrozenPlanningCase, run_planning_pair

case = FrozenPlanningCase.from_dict(public_case_json)
run = run_planning_pair(
    case,
    ModelClient.from_env,          # explicitly configured; this invokes real models
    "data/replay",
    output_dir="runs/e2-case-001", # must be empty
    budget=Budget(max_model_calls=16, max_tool_calls=16,
                  max_planner_steps=64, max_seconds=240),
)
```

`FrozenPlanningCase.from_dict` accepts `{case_id, history, turns}`. History contains public `{role: user|assistant, content}` messages. Each turn contains `{message, task}`; `task` is a public TaskSpec dictionary or null for an unresolved request. `context()`, `to_dict()` and `context_sha256` support persistence and study-manifest linkage. Case IDs, event/issuer/dialogue group membership and split assignments belong in the separate study manifest, not model inputs.

The stable runner signature is:

```python
run_planning_pair(case, model_factory, data_dir, *, output_dir,
                  budget=None, tools_factory=None,
                  condition_order=("adaptive", "fixed"),
                  fixed_revision_rounds=1, clock=time.monotonic)
```

`model_factory()` creates two fresh clients with identical declared settings. `tools_factory(store, frozen_data_dir, cache_dir)` can configure both tool registries identically, including a documented source-failure fixture in `evaluation_config`. Neither factory receives a condition label. The runner checks model IDs/settings, tool schemas, provenance manifest and declared tool configuration before the first inference. Condition order is explicit and should be counterbalanced in a study.

Both arms use an integrity-checked copy of registered original source files, network disabled, separate SQLite databases and separate initially empty caches. Optional frozen `retrieval_vectors` and `market_data` JSON artifacts are copied into each arm, hashed and compared. An attached live market adapter is rejected. Live observations must first become a reviewed frozen source bundle; two arms reading changing online sources would not be a matched comparison. Source-file integrity and settings are checked again during/after execution. A protocol failure leaves a `failure.json` and preserves available databases/logs instead of returning a valid comparison.

The same user turns and initial public history are replayed into each arm. Subsequent generated histories naturally differ and stay within their arm; the adaptive arm never sees the fixed answer or vice versa. Every substantive turn receives the same configured allowance. A clarification resumes the existing allowance. Aggregate counters deduplicate repeated snapshots of that same episode.

The adaptive Planner's inference counts as a model attempt; fixed deterministic decisions do not. Both count each decision against `max_planner_steps`. Researcher and Critic calls, failed calls, tools and active execution time use the common controller accounting. Equal maxima do not imply equal consumption: resource savings from avoiding planner inference are measured, not replaced by imaginary calls. Scripted fixture runs remain labelled `scripted_protocol_fixture` and cannot establish real model cost or quality.

Saved artifacts include:

- `case.json`, `protocol.json`, source snapshots and optional frozen extra inputs;
- one persistent database per arm, keeping immutable reports and evidence versions;
- per-turn `turn-NN.json` with the first candidate **before verification**, final report, events, phase counters and output hashes;
- per-arm and overall `result.json`, containing model attempts by role, tool attempts, planner steps, failures, exact repeated calls and token usage;
- `failure.json` for an interrupted/invalid protocol comparison.

When no candidate or verification is available, that phase is explicit null/unavailable. Runtime exceptions and exhausted budgets remain failures/partial outcomes. `controller_complete_episodes` is a protocol diagnostic, not truth. An exact repeated call can be a legitimate refresh, and a failed call can be a justified probe; neither is automatically a planning mistake. Independent human assessment must score content correctness, unsupported assertions, omissions and mistaken calls, accepting equivalent valid paths. Both before/after artifacts preserve required content for that assessment. No semantic quality score is synthesized from completion badges.

## Zero-model preview

```python
from verifin.planning_evaluation import preview_fixed_workflow

preview = preview_fixed_workflow(
    public_task_json, "data/replay", output_dir="runs/planning-preview",
    max_tool_calls=16, max_planner_steps=64, max_seconds=240,
)
```

This executes the fixed planner and real replay acquisition/calculation tools, then stops at `synthesis_required` or `clarification_required`, or a finite budget. It writes `preview.json`, source files and a database with no synthesized claims or reports. `status` is always `preview_only`, `model_calls` is zero and `truth_scored` is false. It checks tool wiring and source availability; it is not a model demonstration or E2 experiment result.

The preview also accepts the paired runner's optional `tools_factory(store, frozen_sources, assigned_cache)` to use a frozen local market-data or retrieval-vector artifact. It copies original artifact bytes, checks hashes and applies the same offline/cache isolation checks. A generic configured market issuer need not have a SEC identity.

Run the protocol tests with `python -m unittest tests.test_planning tests.test_planning_evaluation`. They cover exact/relative periods, margin/growth/price calculations, scope correction and evidence reuse, company comparisons, provider/source failure, public-input integrity, clarification allowance, source/settings/cache isolation, pre/post-verification capture and finite budgets.
