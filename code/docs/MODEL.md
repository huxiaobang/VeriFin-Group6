# Model transport and evidence verification

This implementation supports OpenAI Responses and compatible HTTPS Chat Completions.
It has no automatic mock fallback. The test suite injects fake transports and fake
model replies explicitly; those tests do **not** establish live model quality or
provider access. No live model call was made during this implementation because
credentials were not configured.

## Configuration

### OpenAI Responses

```sh
export VERIFIN_PROVIDER='openai'
export OPENAI_API_KEY='your-local-key'
export VERIFIN_MODEL='YOUR_AVAILABLE_MODEL_ID'
export VERIFIN_REASONING_EFFORT='medium'
python3 -m verifin config
```

An exact model is required; this does not choose one or make a paid call. The
OpenAI provider defaults to `api_mode=responses`, fixes the endpoint to
`https://api.openai.com/v1`, and sends `service_tier=default` (Standard). Clear old
`VERIFIN_API_KEY`, `VERIFIN_BASE_URL`, and `VERIFIN_ENABLE_THINKING` exports before
switching providers: the explicit VeriFin key takes precedence. Inherited third-party
endpoints are rejected rather than receiving an OpenAI key. The `.env.openai.example`
file is documentation only; no `.env` file is auto-loaded.

Responses requests use `store=false`, explicit local conversation context, JSON-object
mode via `text.format`, and `max_output_tokens`. That output limit includes reasoning
tokens; a truncated response is a failed call, not a completed answer. The parser
requires one completed assistant text answer and rejects refusal, tool/action items,
incomplete output and invalid role JSON. Reasoning items are not treated as answer text.
No provider conversation or `previous_response_id` is required for local multi-turn
sessions. `store=false` is not a claim of zero provider retention.

For GPT-6.1 Sol on Responses, requests with registered tool definitions use
explicit 30-minute prompt caching by default. In a research session the order
is shared tool definitions, ordered original evidence, selected historical
reports and conversation messages, the current task, then the role and changing
work. The Planner receives the full evidence. Revision requests include the
open review findings, the prior drafting instruction, and observed tool activity.
New passages can reuse an existing evidence prefix. A change to a passage,
its metadata or the task can shorten the matching prefix. The API usage
records report actual reads and writes. Set `VERIFIN_CACHE_SHARED_TOOLS=0`
to disable explicit caching.

`VERIFIN_REASONING_EFFORT` is optional and model-dependent. The transport recognizes
`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`; not every model accepts
every value. For example, current GPT-6.1 Sol does not support `none`. Verify the
actual model page and account. No hidden model fallback, sampling parameter change,
or extra retry is introduced by a provider switch.

### Compatible Chat Completions

Set these environment variables in your shell; do not commit credentials:

```sh
export DASHSCOPE_API_KEY='your-key' # alternatively VERIFIN_API_KEY
export VERIFIN_PROVIDER='compatible'
export VERIFIN_API_MODE='chat_completions'
export VERIFIN_BASE_URL='https://YOUR_WORKSPACE_ID.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1'
export VERIFIN_MODEL='YOUR_AVAILABLE_MODEL_SNAPSHOT'
export VERIFIN_TIMEOUT='60'
export VERIFIN_MAX_OUTPUT_TOKENS='4096'
# Qwen3.7 Plus defaults to 8192 for Researcher and Critic when the global limit is unset.
# Each role override takes precedence over the global value and includes reasoning tokens.
# export VERIFIN_QWEN_RESEARCHER_MAX_OUTPUT_TOKENS='8192'
# export VERIFIN_QWEN_CRITIC_MAX_OUTPUT_TOKENS='8192'
export VERIFIN_MAX_CONTEXT_CHARS='200000'
# For Qwen models whose JSON mode requires non-thinking operation:
export VERIFIN_ENABLE_THINKING='false'
```

Replace the placeholders with your actual workspace endpoint and available model.
The example hostname is Hong Kong; select the region that owns your API key.
`VERIFIN_API_KEY` takes precedence over `DASHSCOPE_API_KEY`. No endpoint or model is
silently selected. The client appends `/chat/completions` to the base URL, requires
HTTPS, refuses redirects, and uses Python's standard-library HTTP stack. It does
not require an OpenAI account, package or SDK. Other compatible providers can use
their own base URL and leave `VERIFIN_ENABLE_THINKING` unset.

On OpenRouter Chat Completions, the Qwen3.7 Plus Critic requests low reasoning
effort by default. Explicit reasoning effort or thinking settings take precedence.
The provider controls actual reasoning usage. If it reports an output length stop,
the report keeps the draft and cited evidence and marks verification incomplete.

The request uses JSON-object mode, non-streaming output, and the configured output
limit. JSON schema and reference validation run locally. This first implementation
uses a planner action object rather than provider-native function-call messages;
the controller dispatches the selected tool against the tool registry's schema.
This choice avoids mixing provider tool-calling differences with the shared agent
contract. Provider-native tool calling and strict server-side schemas remain
possible later improvements.

Serialized context above `VERIFIN_MAX_CONTEXT_CHARS` is refused before network I/O;
the client never silently truncates original evidence to fit. This character cap
is a safety bound, not an estimate of the provider's token context window.

The default `VERIFIN_FOCUS_EVIDENCE=1` builds a new filing view from stored records on every
role call, before packing shared provenance and checking the character limit. For recognized
cash, tax and acquisition-risk questions, it sends full relevant windows and index entries
for deferred passages. Calculations, structured records, explicit reads in this turn, and
cited evidence stay available. Adjacent table headings are checked locally. A locator is
not admissible as a source and omission cannot establish non-disclosure. Unknown or broad
questions fall back to the original view. With `VERIFIN_FOCUS_EVIDENCE=0`, only the pre-existing
packing/view settings apply. Shared prefix reuse and provider cost are not measured by character savings.

One `complete()` call performs at most one network attempt. The root controller
reserves the model-call budget before calling it and may reduce `client.timeout`
to the episode's remaining time. There are no hidden retries. `last_usage` retains
valid token counters returned by that request, including on malformed/truncated
model output; it is cleared before the next call. If transport fails or the
provider supplies no usage, usage is unknown (an empty dictionary), not proof of
zero tokens or zero cost. Monetary cost is not calculated by this module.

Usage also preserves reported cached-input, cache-write lifetime counts and reasoning
tokens, including failure cases. Reasoning tokens are a subset of output tokens.
Call events record requested model, endpoint/API mode/tier and bounded returned
model/tier identifiers. Paired evaluation settings include API mode, reasoning effort
and service tier. The offline [cost ledger](COSTS.md) uses an explicit dated catalog;
it cannot enforce a dollar cap or invent absent usage.

Errors identify the failure class/status without copying provider error bodies,
credential-bearing request details or arbitrary network exception text. A failed
or truncated critic response is an unavailable verification, never a successful
rules-only review. Calls are synchronous; cancellation prevents future calls but
does not interrupt an in-flight socket read before its timeout.

## Role contracts

- Planner: `action` is `tool`, `research`, `clarify` or `finish`. The registry supplies
  tool schemas. A scope patch preserves unspecified fields; supplied requirements
  replace the checklist, so IDs for unchanged requirements should be stable.
- Researcher: produces candidate claims with evidence/calculation/requirement IDs,
  explicit result-context constraints, and an answered/unanswered checklist
  partition. A numeric assertion includes its exact machine value and unit;
  display rounding belongs in the prose. Derivations should cite calculation
  records. Claims cannot self-certify their status.
- Structured Critic: reviews every current claim and required-answer item against
  original evidence plus rule findings. It returns issue records with
  `target_id`, `issue_type`, `evidence_refs`, `missing_support`, `proposed_action`,
  and explicit lists of reviewed IDs. An omission can target a requirement even
  when no claim or evidence exists. Evidence references are record IDs; their
  locators identify original spans or calculation operands.

The model response parser rejects invalid or duplicate-key JSON, fenced JSON,
nonfinite numbers, incomplete output, unexpected provider roles/tool messages,
unknown source/requirement/target IDs, duplicate IDs and skipped review targets.
Untrusted source text is serialized only into a data message; fixed system role
instructions explicitly prohibit following embedded document instructions. This
is a prompt boundary, not a demonstrated guarantee against prompt injection.
Further adversarial tests with the chosen real model are required.

## Deterministic checks and limits

`check_rules()` detects dangling/stale references, changed dependency versions,
missing provenance, evidence published after the cutoff, ambiguous same-day
publication under an intraday cutoff, unmatched explicit financial constraints,
unmatched numeric assertions, and requirements without claims. It traverses
calculation dependencies and independently reproduces the supported scalar
operations without evaluating formula strings. The calculation tool checks the
financial comparability of operands. Result constraints are compared with the
referenced calculation's context, so prior-period operands in a growth calculation
are not incorrectly rejected as period mismatches. Existing valid calculations
may survive an unrelated scope extension.

For an exact number quoted only from an original document passage, an adapter may
have no structured `value` field. The rules do not falsely label this absence a
numerical contradiction or treat missing passage metadata as a known mismatch;
the Critic must assess the number against the original span. Known document
entity/time/scope mismatches still fail. This preserves uncertainty rather than
silently claiming that all textual numbers have been arithmetically verified.

The rules deliberately do not infer entailment, causal support, complete source
coverage, or semantic completeness from a matching citation/value. An empty rule
finding list is not a supported/completed decision. Review uses one subsequent
model call. A same-model critic can repeat the researcher's mistakes; neither
schema validity nor passing mocked tests establishes financial accuracy.

The substantive generic-review control and frozen-evidence E1 detection scaffold
are implemented in [EVALUATION.md](EVALUATION.md), with matched inputs/settings and
separate human annotation. The shared revision executor now applies either review
condition with the same Planner, tools and budgets in isolated workspaces. It
does not add another Critic pass or certify its own candidates. No real experiment
has been run. Human revision scoring and E2/E3 execution/assessment protocols are now implemented; actual independent annotations and real-model experiments remain project work. See ANNOTATION.md, PLANNING.md and RETRIEVAL.md.

## Official references checked

The OpenAI route follows [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
and [prompt-cache usage](https://developers.openai.com/api/docs/guides/prompt-caching),
checked on 6 November 2026. We use JSON-object mode and local shape/reference checks,
**not strict provider-side JSON Schema**. Selection rationale and current candidates
are in [MODEL_SELECTION_2026-11-06.md](MODEL_SELECTION_2026-11-06.md).

The transport follows Alibaba's [Chat compatibility documentation](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)
and [structured-output documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output),
checked on 13 October 2026. Workspace endpoints and region-bound keys must match;
model and output-mode support should be checked in the actual configured workspace.
See also [regions and endpoints](https://www.alibabacloud.com/help/en/model-studio/regions).
