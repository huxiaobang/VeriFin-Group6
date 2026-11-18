# Financial tools and source adapters

`ToolRegistry(store, data_dir, allow_network=False, user_agent=None, cache_dir=None, retrieval_vectors=None, market_data=None, market_adapter=None)` exposes eight
tools through `list_tools()` and `execute(name, arguments, session_id, scope)`.
The result has `ok`, `summary`, `evidence`, `calculations`, `data`, and optionally
`error`. The controller must count **every invocation**, including failures, and
persist returned evidence before calculations. `data.network_requests` counts
HTTP attempts separately. A tool never invokes a model.

## Capabilities and calls

| Tool | Main arguments | Behavior |
|---|---|---|
| `discover_company` | `query`, optional `source` | Resolve replay name/ticker/CIK candidates; opt-in live discovery uses SEC submissions for a CIK or the SEC ticker directory for a name/ticker. |
| `financial_facts` | `entity`, `metric`, `period_start`, `period_end`, optional `unit`, `cutoff`, `source` | Read native SEC company-facts records for exact dates. Incomplete dates return period choices without silently selecting a duration. |
| `discover_filings` | `entity`, optional `forms`, `report_date`, `cutoff`, `source`, `limit`, `offset` | Return recent SEC filing metadata and exact primary HTML URLs; one optional submissions HTTP request. |
| `read_filing_history` | `entity`, `filename`, optional `forms`, `report_date`, `cutoff`, `source`, `limit`, `offset` | Read one explicitly discovered historical submissions JSON page; one optional request. |
| `read_filing` | `entity`, `accession`, `primary_document`, optional `query` or `offset`, `limit_chars`, `cutoff`, `source` | Read an identified SEC Archives HTML filing and return an exact extracted-text passage with raw/text hashes. |
| `search_documents` | `entity`, `query`, optional `period_end`, `cutoff`, `limit`, `forms`, `document_ids`, `retrieval_mode`, `max_context_chars` | Real BM25 over verified document chunks; optional genuine frozen dense embeddings and RRF hybrid. See [retrieval](RETRIEVAL.md). |
| `calculate` | `operation`, `operand_ids` | Validate two saved operands from the same session; compute with `Decimal`, retain formula and operand versions. No arbitrary code or expressions. |
| `market_prices` | `entity`, `start_date`, `end_date`, optional `cutoff`, `source`, `price_type` | Validated generic local imports, opt-in Alpha Vantage adapter, or legacy replay. Explicit listing/currency/timezone/adjustment provenance. See [market contract](MARKET.md). |

`source` is `auto`, `replay`, or `live`. For SEC facts/submissions, `auto` selects
the more recently observed verified bundled or persistent cached snapshot; it
requires explicitly enabled network access only when neither exists. `replay`
uses the bundled snapshot when available, otherwise a verified persistent cache,
and never requests the network. Request `live` to refresh: it always makes an
explicit request instead of silently accepting a cache. Cached data is labelled
`cached_live` with its original observation time, not presented as fresh online
data. A missing
entity defaults only when the scope contains exactly one entity. Financial date
and cutoff arguments fall back to the active scope.
An explicit tool cutoff may narrow the active cutoff, but cannot extend it.
Changing the information date requires an explicit visible scope update first.

Example financial call:

```python
result = registry.execute("financial_facts", {
    "entity": "MSFT", "metric": "revenue",
    "period_start": "2024-10-01", "period_end": "2024-12-31",
    "cutoff": "2025-01-31", "source": "replay",
}, session_id, scope)
for record in result["evidence"]:
    store.put_evidence(session_id, record)
```

Metric aliases now cover 25 explicitly mapped concepts/categories: revenue,
operating/net income, gross profit, operating/investing/financing cash flow,
capital expenditures, cost of revenue, R&D, operating expenses, income tax,
pretax income, basic/diluted EPS and weighted shares, total/current assets and
liabilities, cash, parent stockholders' equity, inventory and current receivables.
The exact map is in `verifin/financial.py`; explicit `us-gaap:Concept` and
`ifrs-full:Concept` remain supported without claiming all tags are equivalent.
Returned evidence includes the SEC concept label/definition. Aliases are discovery
aids, not an accounting-equivalence proof; custom/segment/non-GAAP concepts are
not silently substituted. Whole-entity SEC facts are labelled consolidated.
`period_length_class` describes duration only; `fy`, `fp`, and `frame` remain
visible filing context rather than period selectors. Per-share currencies are
preserved without treating EPS or weighted shares as additive cash flows.

For a fully specified period the tool selects the latest publicly filed fact at
the cutoff. Duplicate values from the same filing are deduplicated. Distinct
values/units at that selected publication are returned with a conflict flag.
If same-day publication order is unknown, conflicting values for the same
concept/unit/period cause a visible refusal with candidate metadata, not an
arbitrary selected fact. Equal duplicates are deduplicated. Distinct alias tags
or units remain separate records. Older filings and later restatements are not
averaged. Fiscal `fy`/`fp` contexts
are not used as quarter selectors: they describe filing context and may also
appear on comparative prior-year values.

## Arithmetic and comparability

| Operation | Operand order | Unit / restrictions |
|---|---|---|
| `ratio` | numerator, denominator | `ratio`; same issuer, exact period, unit, accounting basis and business scope; denominator nonzero. |
| `margin` | income/profit, revenue | `%`; supported income/profit alias, positive revenue, same issuer and period. |
| `growth` | current, prior | `%`; same issuer and concept, positive prior base, chronological non-overlapping periods with durations within 7 days. |
| `difference` | current, prior or company A, B | Original unit; comparable periods or identical periods across companies; no currency conversion. |
| `percentage_point_change` | current margin, prior margin or company A, B | `percentage_points`; operands must already be `%` with the same metric definition. |
| `derive_quarter` | later cumulative flow, previous cumulative flow | H1−Q1, nine-month−H1, or FY−nine-month; same start/concept/issuer/basis/scope and 70–105 day residual. Only known additive flow tags; EPS, weighted shares, balances, ratios and unknown concepts are refused. |
| `price_return` | end price, start price | `%`; same issuer and later endpoint; positive base price; descriptive price change, not total return or event causality. |

Financial operands must use base-unit scale 1. Missing or incompatible dimensions,
stale records, non-finite values, booleans and numeric strings are refused.
Percent growth with a negative base is deliberately not assigned a potentially
misleading sign; the caller can request a difference. A ratio is not automatically
an economically meaningful comparison: semantic verification is still required.
The 7-day duration rule is a conservative MVP check, not proof of accounting
comparability. Financial-calendar resolution, business changes and restatements
still need review.

Calculations can reference saved calculations, enabling margin changes without
re-entering values. Results retain `operand_versions`, result period, metric,
scope version, latest operand publication time and warnings. A full-year minus
YTD quarter remains visibly derived and may differ from an explicit quarterly
table by rounding; no hardcoded NVIDIA adjustment exists.
Currency cancels in dimensionless results; their source currencies remain in
`operand_currencies`. This permits percentage-point comparisons between
otherwise comparable margins without pretending to convert currencies.

## Provenance and cutoffs

`data/replay/manifest.json` records 14 copied source/derived snapshots, their
original URLs, retrieval times, SHA-256 values, and document coverage. Source
files are copied without alteration from the prior handoff; no gold answers,
prepared conclusions, or scenario-specific orchestration are loaded. The two
replay companies are examples, not an issuer allowlist. The same code accepts
other SEC CIKs; a mocked third issuer regression test verifies that path, but is
not evidence of a successful live research run.

Every source read verifies its hash. Financial evidence includes the exact
concept/unit-array locator, accession and native period. Document evidence
contains an unmodified character span with zero-based Unicode offsets, source
hash, derived-text hash and extraction method in the manifest. Search uses
paragraph/sentence/word-aware chunks up to 1,800 characters with 250-character
overlap and real BM25 term saturation/length normalization. Cutoff/issuer/report
metadata filters apply before corpus statistics. Optional dense and RRF hybrid
require actual frozen encoder output bound to this exact corpus and query. Rank
does not establish support, comprehensive research or causality. Boilerplate,
table extraction, semantic relevance and long-range context remain limitations.

Fact IDs identify the logical observation (CIK, taxonomy/concept, unit and exact
period), not the downloaded-file hash. A changed filing/value/snapshot therefore
updates the same evidence record and invalidates dependent results in storage.
Prior versions retain their source provenance. Prices use provider/issuer/day
identity. Frozen issuer-document spans use source identity, source/text hashes, chunker
version and exact offsets; explicitly tracked SEC filing revisions follow the source lifecycle
described below. A new company-facts JSON hash never invalidates every other
financial period merely because the aggregate file changed.

Replay SEC facts use accession acceptance timestamps where the preserved
submissions metadata provides them; otherwise filing dates are labelled
date-only. A date-only cutoff includes that whole UTC date. A date-only
publication is conservatively unavailable for an intraday cutoff on that same
date. Issuer document dates come from the earnings release event metadata;
intraday publication time is not asserted. Legacy Yahoo price timestamps identify bars rather than verified publication
instants and retain their labelled date-only assumption. New imports require
explicit timezone-aware availability; the optional live adapter conservatively
uses actual response observation time and adjusted-data vintage.

These snapshots were downloaded on 8 October 2026, not contemporaneously with
the 2025 events. Publication metadata filters exclude later filings, but the
frozen website copies do not establish what every page byte looked like at the
historical cutoff. This limitation must remain visible in historical evaluation.
Live issuer-website/news discovery remains unimplemented. The optional market
adapter is implemented and mock-tested; no live account/entitlement was exercised.

## SEC filing discovery and reading

`discover_filings` uses the saved SEC submissions for replay, or one submissions
request for an arbitrary resolved CIK. It returns original accession, form,
`reportDate`, `filingDate`, acceptance time and primary-document filename. It
constructs an Archives URL only for a safe primary `.htm`/`.html` filename.
The output also provides `primary_document` as an exact alias of SEC's
`primaryDocument`, ready to copy into the `read_filing` argument of that name.
The default forms cover 10-K, 10-Q, their amendments, 8-K, 8-K/A, 20-F, 40-F and
6-K. A caller can supply up to ten form names and an exact report-date filter.
Publication cutoffs apply before results are shown. The metadata's older-history
file references are returned in `history_files` (plus the compatibility field
`history_files_not_fetched`). `read_filing_history` accepts only one filename
listed for that issuer, reads cached data or makes one explicit history request,
and can paginate eligible rows with `offset`/`limit`. Reopening restores cached
history and its exact acceptance timestamps. No hidden HTTP requests expand coverage.
No fiscal-year or quarter label is inferred from `reportDate`.

`read_filing` requires an accession/primary-document pair present in prior live
discovery or verified replay submissions. It never fetches missing identity
metadata behind the caller's back. The URL must be a constructed HTTPS
`www.sec.gov/Archives/edgar/data/<CIK>/<accession>/<primary HTML>` path. Redirects,
arbitrary URLs, traversal, non-HTML responses, known SEC access-denial pages and
responses exceeding 8 MB are refused. Previously captured SEC failure bodies
are not in the replay manifest and cannot count as successful filing content.
The supplied replay corpus contains filing metadata but no successful SEC filing
HTML, so a first read needs explicitly enabled live access.

HTML parsing performs no network requests, script execution or financial
interpretation. It removes script/style/noscript/`ix:hidden` text and normalizes
whitespace. Raw HTML and derived text are independently hashed and saved.
`query` returns one lexical matching span; otherwise `offset` and `limit_chars`
page through extracted text. Exact character locations remain inspectable.
Subsequent reads of the cached filing use no HTTP request unless `source=live`
explicitly refreshes it. The persistent URL-to-snapshot index survives new
ToolRegistry instances and process restarts. Cached SEC ticker/submissions JSON
reconstructs issuer and filing identities without a hidden metadata request;
cached HTML can be searched again after reopening a session. Original hashes,
cutoffs and observation times remain in force after restart.

Each parsed SEC filing also returns `data.source_observation` with
`source_document_id` (canonical URL identity), `source_revision` (raw HTML hash),
`source_observed_at` (original HTTP observation time),
`source_revision_authoritative` and `source_url`. The controller must send this
to `store.observe_document_source` even when a lexical query has no match or an
offset is beyond the document. This records an observed source change without
pretending an empty query result is textual evidence.

SEC filing passages carry the same source fields and include their source hash
in their IDs. A later authoritative source observation can therefore invalidate
all previous current-revision spans and their dependent results even when the
new query selects a different span. Storage keeps old report/evidence versions;
it does not delete their historical content. Observation times are retrieval
times, not asserted publisher version dates. Cached reads retain the original
HTTP time so an old cache does not acquire a newer timestamp just by being read.
Explicit `source=replay` reading is non-authoritative and uses separate historical
passage IDs, so it does not replace the current source revision. It reads the
available cached snapshot; it is not an arbitrary historical-version selector.

The persistent source index is `cache_dir/sources.sqlite3`, separate from session
storage and packaged replay data. SQLite transactions serialize short concurrent
updates. Each index entry stores checked metadata, a metadata hash, a relative
snapshot filename, original source URL and observation time; the referenced
snapshot bytes are hash-checked on use. Older observations cannot replace a
newer indexed source. Files remain addressed by content hash, so earlier source
bytes are not overwritten when the current URL index advances. No index or cache
directory is created merely by running an offline bundled-replay request.

Metadata or snapshot corruption and SQLite errors are visible tool failures.
They do not silently trigger a network fallback or relabel old data as current.
An explicit successful live request can repair a damaged metadata row for that
URL; a damaged database or damaged content-addressed file requires deliberate
cache repair/removal while retaining any files needed by saved reports. There
is no automatic eviction, garbage collection or whole-cache deletion. This
small cache is not a cross-user service or a publisher revision registry.

A filing can contain several periods and comparisons. Returned document
evidence therefore carries `filing_report_date` separately and leaves financial
`period_start`/`period_end` null. The Researcher/Critic must read the relevant
table or statement context. `search_documents` includes already cached,
identified SEC HTML and labels the report date as `document_report_date` while
leaving financial periods null. Tables, figures, PDF attachments, exhibits and
automated financial semantic parsing are not implemented. Live issuer websites and news remain separate future adapters.

## Live SEC access

Live calls require `allow_network=True` and an honest identifying `user_agent`
containing a contact email. Only constructed HTTPS SEC endpoints are supported;
arbitrary URLs and redirects are refused. Each action makes at most one HTTP
request, without retry. Failures count; 403/429 responses are visible. A local
process-wide 0.2-second spacing guard refuses a too-fast second request instead
of sleeping. Request counts and timeouts are local to each worker thread.
The root runtime should also keep overall traffic below SEC limits, especially
if multiple processes are introduced. Default timeout is 20 seconds and the
controller may reduce `registry.timeout` to the remaining episode allowance.

Live response bytes are saved atomically in `cache_dir` (default: replay root's
`live/` directory), addressed by SHA-256. Parsed replay JSON is returned as a fresh object, so one
session cannot mutate a shared payload cache. No API credentials are needed for
the SEC. Runtime discovery does
not fetch company facts or other filings behind the caller's back; use another
counted action. Persisted ticker/submissions sources restore previously discovered
issuers after process restart. If neither identity source was ever acquired, a
CIK can still be used directly or resolved with an explicit discovery action.

The adapter follows the primary SEC documentation:
[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
and [developer resources / fair access](https://www.sec.gov/about/developer-resources).
The latter currently specifies a maximum of 10 requests per second in aggregate
per user; the local guard is stricter but is not a distributed rate limiter.

## Validation

Run `python -m unittest tests.test_tools tests.test_history tests.test_market tests.test_retrieval tests.test_financial -v`. Tests cover real frozen source
values, hash/path integrity, exact periods and cutoffs, dimension/zero/stale
refusals, derived-quarter arithmetic, source-span identity, price windows,
disabled networking, single-attempt failure accounting, and a synthetic unseen
issuer through the generic SEC response parser. HTTP tests are mocked and are
not an assertion that live access was verified on this machine.
SEC HTML/discovery tests additionally cover original metadata preservation,
cutoff enforcement, unknown-identity refusal, HTML/MIME/byte limits, cache hash
checks, exact pagination, script stripping and cache isolation. No external SEC
call was made and no group member contact information was transmitted.
Persistent-cache tests include a real separate Python process reopening a
SQLite session, restoring an unseen synthetic issuer/filing and querying cached
HTML with zero HTTP attempts; concurrent writes, source ordering, explicit live
refresh, corruption and read-only replay behavior are also checked. These are
mechanism tests with mocked responses, not a live-source acceptance result.
