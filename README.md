# Group 6 · VeriFin

**COMP7608B Final Project**  
**Members** HU Xiaobang, Liang Da Wen, Yang Yisheng, Liao Sijia  
**Submitted source** VeriFin 0.5.0, Git revision `503c149808aeaf225f4b35be89c89dd50a827fbc`

VeriFin is a local research assistant for analysts who need to answer and revisit company-finance questions with dated sources, calculation operands and review status. Its Planner selects tools and decides when to ask for clarification, draft, repair or stop. A Researcher builds claims and a Structured Critic checks them. The Python controller enforces budgets and records evidence and report versions in SQLite.

## Demo language

On the team-runtime branch, newly generated research checklists and review notes default to English. Questions and follow-ups may be written in Chinese. Answers default to English unless the user explicitly requests another language, for example “请用简体中文回答”. Original quotes, user request text and evidence IDs retain their source form. The submitted revision and its archived prompt record above describe the original course snapshot.

## Experimental request recovery

The web client saves the request ID before submitting a turn. Reconnect and retry the pending turn to recover its saved work or original report. A new question gets a new ID, even if its text is identical. A call dispatched before a crash with no committed receipt ends with a partial report and an unknown outcome.

From `code/`, the CLI equivalents are `python3 -m verifin --db sessions.db ask SESSION --request-id ID -- "Question"`, `python3 -m verifin --db sessions.db request-status SESSION ID`, and `python3 -m verifin --db sessions.db resume SESSION ID`. Reuse the database and the original model/source settings for a pending run. Old reports remain readable.

## Run locally

Use Python **3.11+** on macOS or Linux with a writable working directory. The core uses the standard library, including SQLite, and POSIX file locking. The application and checks can run directly from this source tree without a notebook, Python package installation, GPU or provider key. For a repeatable run, create fresh output directories or choose new names on each run. Start from the extracted ZIP root.

```sh
python3 evaluation/check_evidence.py
cd code
python3 -m scripts.demo_replay --output runs/scripted-demo
python3 -m verifin --db runs/scripted-demo/sessions.sqlite3 serve --offline
```

Open `http://127.0.0.1:8765` and select the scripted MSFT session. Inspect its saved report versions, evidence and calculations. `Ctrl-C` stops the localhost server. The replay uses real frozen financial sources and arithmetic, with explicitly scripted Planner, Researcher and Critic responses. The first turn gives USD 69.632 billion in revenue and about 45.4575% operating margin for October–December 2024. The next turn reopens the database and explains the saved calculation with zero new tool calls. Output files include `runs/scripted-demo/result.json` and `runs/scripted-demo/report/report.md`. Scripted review labels describe fixture behavior. Interactive offline questions can run supported local research steps and stop as partial before model synthesis or semantic verification.

## Sample requests and offline checks

The sample cases under `evaluation/experiments/e2-cases/` contain messages, publication cutoffs and structured TaskSpecs. The source-side example [planning_case.example.json](code/docs/planning_case.example.json) includes a growth follow-up.

| Case | Sample request or fault | Expected observation |
| --- | --- | --- |
| Ordinary | Show Microsoft's FY2025 Q2 revenue and operating margin, using information available by 31 January 2025. | The fixture calculates the margin from two same-quarter operands and saves their sources. The fixed planning preview retrieves and calculates the same inputs before stopping for synthesis. |
| Changed request | Correction. Use FY2025 Q1, July through September 2024, using information available by 1 November 2024. | The correction fixture retrieves USD 65.585 billion in quarterly revenue, invalidates dependent claims and keeps the old report available. The issuer-switch regression reopens the session with an NVIDIA request. |
| Failure | Repeat an ordinary request when `financial_facts` returns a source failure, or supply the unsupported period starting 2 October 2024. | The controller records the failed tool action and can search source documents. It keeps the requested period unresolved when the required fact is unavailable. A separate budget fixture reaches the call limit before review and retains the candidate as pending verification. |

Run these commands from `code/`. The full suite expects a working loopback socket and the HTML and disclosure parser extras listed below.

```sh
python3 -m verifin planning preview \
  --task examples/offline-checks/task.json --output runs/preview
python3 -m unittest -v \
  tests.test_sessions_e2e.SessionEndToEndTests.test_scope_correction_preserves_report_invalidates_old_results_and_reviews_new_quarter \
  tests.test_company_name_switch.CompanyNameSwitchTests.test_reopening_a_microsoft_session_uses_nvda_before_planning_and_completes \
  tests.test_planning.FixedWorkflowTests.test_source_failure_counts_and_no_annual_substitute \
  tests.test_sessions_e2e.SessionEndToEndTests.test_corrected_scope_without_remaining_critic_budget_stays_partial
python3 -m unittest discover -s tests -v
```

The fixed preview produces `runs/preview/preview.json` with `synthesis_required`, evidence, calculations and zero model calls. The test suite uses local fixtures and loopback HTTP. Tests for optional components may skip if their libraries are absent.

## Interactive model-backed research

This mode needs an account with access to a compatible hosted model and may incur model charges. The two real follow-ups in this submission used OpenAI Responses, `gpt-6.1-sol`, `medium` reasoning, the Standard tier, 16,384 output tokens per role call and a 180,000-character context limit. Availability and pricing depend on the account and provider. Use an available model ID if the recorded ID is unavailable. Its output may differ from the recorded runs. Configure access through your shell. The example `.env` files document settings and are not loaded automatically.

From `code/`, set `OPENAI_API_KEY` privately in your environment and run these commands.

```sh
unset VERIFIN_API_KEY VERIFIN_BASE_URL VERIFIN_ENABLE_THINKING
export VERIFIN_PROVIDER=openai
export VERIFIN_API_MODE=responses
export VERIFIN_MODEL=gpt-6.1-sol
export VERIFIN_REASONING_EFFORT=medium
export VERIFIN_MAX_OUTPUT_TOKENS=16384
export VERIFIN_MAX_CONTEXT_CHARS=180000
export VERIFIN_TIMEOUT=60
python3 -m verifin config
python3 -m verifin --db runs/interactive.sqlite3 serve
```

`config` validates local settings and hides the credential. The browser provides session creation and follow-ups. For CLI use, run `python3 -m verifin --db runs/interactive.sqlite3 new --title 'Quarter research'`, then substitute the returned `id` for `SESSION_ID` in the following commands.

```sh
python3 -m verifin --db runs/interactive.sqlite3 ask SESSION_ID \
  'Show MSFT FY2025 Q2 revenue and operating margin, with sources.' \
  --as-of 2025-01-31 --model-calls 16 --tool-calls 16 --seconds 360
python3 -m verifin --db runs/interactive.sqlite3 ask SESSION_ID \
  'Correction. Use FY2025 Q1 and information published by 1 November 2024.'
python3 -m verifin --db runs/interactive.sqlite3 trace SESSION_ID
python3 -m verifin --db runs/interactive.sqlite3 export SESSION_ID runs/export
```

Replay sources are the default. Opt in to SEC network tools by supplying `SEC_USER_AGENT` with your application name and appropriate contact details and appending `--live` to `ask` or `serve`. To use an OpenAI-compatible Chat Completions provider, start from `code/.env.example` and provide your own HTTPS base URL, model ID and provider key. [Model configuration](code/docs/MODEL.md)

## Evaluation and reproduction

- **Release evidence.** Two actual hosted-model diagnostic runs used the same short follow-up after different requests in one historical MSFT dialogue. The original user-visible answers, call and tool traces, source extracts and blank independent review sheet are in [evaluation/README.md](evaluation/README.md). The included `evaluation/check_evidence.py` checks the archive, counts and arithmetic offline. The human sheet remains blank. These are development cases.
- **Earlier 18-case pilot.** [Outcomes and settings](evaluation/pilot18-results.json) and [compressed user-visible answers](evaluation/pilot18-answers.jsonl.gz) record two actual hosted-model runs on the earlier source revision `0d7d9f3`. The answer archive contains 36 records and the study's grades were AI-assisted. These results describe development outcomes on the earlier revision.
- **E1, verification and revision.** [Archived case review](evaluation/experiments/e1-case-review.tsv) and [serialized-request checks](evaluation/experiments/e1-payloads.json) are from the earlier experiment record `d7433fb`. Initial detection feedback and a user's final delivered answer measure different outcomes. Complete paired final answers and independent adjudications were unavailable. From a configured model session, add `--capture-case runs/e1-case.json` to `ask`, then run `python3 -m verifin evaluate --case runs/e1-case.json --output runs/e1 --revise`. This makes additional paid calls. The offline `study` subcommands consume stored records and actual human judgments. [Evaluation protocol](code/docs/EVALUATION.md)
- **E2, fixed versus adaptive planning.** The archive includes [per-turn outputs](evaluation/experiments/e2-turns.json), [SEC-source grading](evaluation/experiments/e2-source-grades.json) and a [release readiness check](evaluation/experiments/e2-503c149-readiness.json). The archived comparison used scripted responses with both arms and injected a controlled source outage through a fixture tool registry. For a new model-backed pair with the default tool registry, run `python3 -m verifin planning run --case docs/planning_case.example.json --output runs/e2` from `code/` after configuring your model. This makes paid calls. The ordinary demo, preview, scope correction and failure fixture commands above are offline checks.
- **E3, evidence retrieval.** The [earlier development sweep](evaluation/experiments/e3-pairs.json) and [query-level rows](evaluation/experiments/e3-queries.json) use 600-character chunks with 80-character overlap. The [release tool check](evaluation/experiments/e3-503c149-retrieval.json) exercises the released default and an isolated 600/80 diagnostic. The release default uses 1,800/250 and rejects those mismatched frozen vectors. Development spans cover ten underlying questions and are distinct from independent labels. The following offline commands check the released default BM25 path and rescore the older fixed-vector configuration.

Run these commands from `code/`.

```sh
python3 -m verifin retrieval corpus --output runs/release-corpus.json
python3 -m verifin retrieval evaluate --corpus runs/release-corpus.json \
  --cases data/evaluation/e3_development_queries.json --modes bm25 \
  --output runs/release-bm25.json

python3 - <<'PY'
import json
from pathlib import Path
from verifin.retrieval import Corpus, corpus_from_snapshots

corpus = Corpus(corpus_from_snapshots('data/replay').documents, max_chars=600, overlap=80)
Path('runs/corpus-600-80.json').write_text(json.dumps({
    'fingerprint': corpus.fingerprint,
    'chunking': {'max_chars': corpus.max_chars, 'overlap': corpus.overlap},
    'documents': corpus.documents,
}, ensure_ascii=False), encoding='utf-8')
PY
python3 -m verifin retrieval evaluate --corpus runs/corpus-600-80.json \
  --cases data/evaluation/embedding_selection_development_queries.json \
  --embeddings data/evaluation/embedding_selection_results/qwen3-0.6b-chars600-overlap80.embeddings.json \
  --modes bm25 dense hybrid --limit 5 --max-context-chars 3000 --repeats 1 \
  --output runs/e3-dev-600-80.json
```

The development check at five passages and 3,000 characters yields 15 BM25, 24 dense and 24 hybrid full-span hits over 30 expressions. This measures retrieved evidence coverage. The archive also preserves [503c149 output-stage and budget-stop checks](evaluation/experiments/partial-delivery-503c149.json). The three checks named `503c149` are from verification record `673633e` and identify the submitted revision within each file.

## Data, optional dependencies and operating limits

- **Included snapshots.** `code/data/replay/manifest.json` lists the original URLs, dates and SHA-256 hashes for the frozen MSFT and NVIDIA source fixtures, including SEC Company Facts, company submissions, issuer releases and narrowly scoped historical market files. Original public issuer source bytes preserve evidence locators and can include issuer-published press contacts. Use `--data-dir` before the subcommand to point at another compatible replay bundle. The bundled sample is historical and covers two issuers.
- **HTML and PDF cell extraction.** The `html` extra pins lxml `6.1.3` and pandas `2.2.3`. The `disclosures` extra uses lxml `>=5` and pdfplumber `>=0.11,<0.12`. To install both for the full suite, run `python3 -m pip install '.[html,disclosures]'` from `code/` in a virtual environment. Packaging uses setuptools `>=77`.
- **Local encoders.** The `embeddings` extra pins Sentence Transformers `6.2.0`, Transformers `5.22.1` and Torch `2.15.0`. For live local encoding, use a platform that supports those packages with matching local safetensors weights. CPU inference is supported. The recorded Qwen3-Embedding-0.6B development run used about 1.21 GB of weights and 2.39 GiB of peak process RAM. Frozen development vectors in the E3 example require no such weights or optional packages. [Encoder setup](code/docs/RUNTIME_EMBEDDINGS.md)
- **Larger public benchmarks.** The package retains source code, manifests, selection files, development vectors, aggregate results and third-party notices. Full public datasets, large optional full-benchmark vector exports and model weights require separate acquisition or regeneration. `python3 scripts/fetch_public_benchmark_data.py --dataset convfinqa --output-dir runs/public` shows the pinned download plan. Append `--download` to fetch and check the public files. See the project guides for pinned model revisions, local encoding, result rescoring and separate resource needs. [Public benchmark reproduction](code/docs/REPRODUCE_OPEN_SOURCE_STUDY.md) · [Pinned model and benchmark study](code/docs/OPEN_SOURCE_MODELS_2026-11-06.md)
- **Application limits.** VeriFin runs as a single-user local research tool. A source can lack the requested fiscal period, table or publication precision. Model review can miss omissions and conflicts. A partial report exposes pending requirements and its stop reason. A stop prevents further scheduling after any active request returns. Cloud model-backed queries send selected context to the configured provider. Use the offline mode for material that should stay local.

The archive contains the runnable source, tests, prompts, configuration and selected evaluation artifacts. Internal handoff files, private contact records, host-specific outputs, session databases and the separately submitted report PDF are outside its scope. The third-party license notices preserve their original wording. The archive's `SHA256SUMS` identifies its contents.

## Attribution

[Member contributions](attribution/MEMBER-CONTRIBUTIONS.md) · [AI-use declaration](attribution/AI-USE.md) · [Prompt record](attribution/PROMPTS.md) · [Third-party attribution](attribution/THIRD-PARTY.md)
