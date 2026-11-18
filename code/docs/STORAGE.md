# Session and evidence persistence

`verifin.storage.SessionStore` uses Python's standard-library SQLite driver. The
database path is exposed as `store.path`. `:memory:` is supported for disposable
tests; a file path is needed to reopen a session after process exit.

## Read and write behavior

- `create_session`, `get_session`, `list_sessions` and `update_scope` return
  JSON-compatible dictionaries. `update_scope` replaces the complete resolved
  scope: the controller merges a model's partial scope update beforehand.
- `append_message` preserves every turn with a per-session sequence. `add_event`
  stores the supplied object directly, adding `id`, `created_at` and
  `event_sequence`. There is no `event` wrapper, so an `episode_checkpoint` can
  be read directly from `events()` after reopening the database.
- `latest_event(session_id, event_type)` uses a database index to return the
  newest event of that exact type, or `None`. It returns the same unwrapped
  dictionary as `events()` and avoids loading the entire log for a checkpoint.
  Schema version 2 adds the index and backfills version 1 databases in a single
  transaction without changing their event payloads or sequence numbers.
- `events(session_id, limit=30, exclude_types=["episode_checkpoint"])` reads
  the last 30 matching events in chronological order, filtering before limiting.
  Both arguments are optional; existing `events(session_id)` callers still get
  the full log. Untyped events remain included unless the caller filters them.
- `put_evidence`, `put_calculation` and `put_claim` return the persisted record.
  IDs are local to a session. Evidence and calculation IDs cannot collide because
  calculation operands may refer to either kind.
- `get_context` includes the latest evidence, calculations and claims, including
  stale records. Consumers must check staleness and scope; it is intentionally
  not a filter that hides inconvenient research history.
- `get_context(session_id, report_mode="full", report_limit=None)` preserves
  the existing complete-history behavior by default. Set `report_limit=3` to
  load only the last three full reports, `report_mode="summaries"` to read small
  metadata rows, or `report_mode="none"` to omit reports entirely. Returned
  `report_count` always gives the session's total saved-report count, so callers
  can accurately state how much historical material is omitted from a view.
- `save_report` creates an immutable, session-wide report version. It snapshots
  scope and exact record version references as well as the supplied report data.
  `list_reports` returns all versions in creation order.
- `list_report_summaries(session_id, limit=None)` returns report metadata in
  chronological order, optionally only the last N reports. It includes ID,
  version, creation time, request, status, stop reason, scope, summary, resource
  usage and counts of claims/unanswered items when those fields exist. A
  `supported_claim_excerpt` summary copies a whole reviewed answer sentence
  (or one supported claim for reports without question mappings) and includes
  its `summary_claim_id`. `see_report_findings` points to the full report when
  there is no suitable excerpt or an older compact row held only a draft. It does
  not include claims, evidence or calculation copies. The query reads a separate
  persisted summary column, rather than decoding the full report and trimming it.
- `get_report(session_id, report_id)` loads one complete historical report on
  demand. A string identifies an exact report ID; a positive integer identifies
  the session-wide version. If the same ID was deliberately reused, its string
  lookup returns the newest version; integer version lookup remains precise.
  An unavailable report raises `KeyError`, while an invalid selector raises
  `ValueError`. Every lookup is scoped to the supplied session.
- Cancellation is a short durable write. `request_cancel`, `clear_cancel` and
  `is_cancelled` do not acquire the controller's long-running research lock.

Missing sessions raise `KeyError`. Invalid JSON, record shapes, periods or
dependency versions raise `ValueError`. Methods return detached data: changing
an input dictionary or returned record cannot mutate a stored value.

## Corrections and reuse

Changed records append a new version. `history(session_id, kind, record_id)`
returns retained versions in order, where `kind` is `evidence`, `calculation`, or
`claim` (or `document_source` for opt-in source observations). Payload rows are
never updated. Separate invalidation records add a stale flag and its reasons
to views without rewriting original payloads.

An evidence update invalidates calculations whose recorded operand versions no
longer match, then downstream calculations and dependent claims. A calculation
must use existing, fresh operands; supplied `operand_versions` must match the
database. Self-reference and longer calculation cycles are rejected. A claim's
incorrect reference is retained and marked stale so verification can explain it.
Recomputing a calculation does not automatically revalidate a prior conclusion.

Identical evidence writes are no-ops, including after the active session scope
changes. Raw source evidence belongs to its own entity and period, so it does not
inherit the session's scope version. Evidence remains available for later reuse;
cutoff and answer-scope eligibility are additionally checked by verification.
Changes to retrieval score or fetch time alone do not create a new evidence
version; original retrieval metadata is retained. Changes to source content,
snapshot hash, source location or publication metadata do create a new version.

Scope edits increment `scope_version` and conservatively invalidate affected
calculations and claims while retaining prior reports. Adding a comparison
company preserves valid calculations for a retained entity but invalidates the
old narrative for reconsideration. Period or cutoff corrections invalidate
affected derived results. Display-only changes do not invalidate results.
Updating the status of an already stale record preserves its staleness; newly
regenerated records are checked against the current dependency versions.

## Opt-in document source revisions

`observe_document_source(session_id, observation)` records a trusted adapter's
successful acquisition of a whole document, independently of whether a lexical
query found a passage. The controller calls it for
`tool_result.data.source_observation`, including successful acquisition followed
by an `ok: false` query result. It returns `changed` and
`invalidated_evidence_ids`; a revision change also clears the controller's review
state before another result can be reported as verified.

The observation and participating document spans carry `source_document_id`,
`source_revision`, `source_observed_at`, `source_revision_authoritative`, and
`source_url`. The revision is the raw HTML hash and must equal a span's
`snapshot_hash`; the observation time is the HTTP acquisition timestamp with an
explicit timezone. Only explicitly authoritative document records with that
same source ID and URL participate. A later changed hash invalidates *all* old
participating spans and their dependent conclusions, even when the next query
returns a different span or no span. The old report versions remain intact.

Older cached observations cannot downgrade the current source. Their unmatched
spans are marked stale if subsequently inserted. An identical revision fetched
later advances observation time without invalidating evidence; identical time
with conflicting hashes is rejected as ambiguous. Explicit non-authoritative
historical replay, ordinary replay documents without opt-in metadata, other
documents, and all numerical financial facts remain unaffected. This mechanism
therefore does not treat a changed companyfacts JSON file as a reason to discard
every financial period.

Source observation history uses an additional record kind in the existing
append-only table and survives schema upgrades without a history reset.
Legacy document spans without opt-in lineage remain outside this source
refresh mechanism until retrieved through the updated adapter. Observation
timestamps order acquisitions, not historical publication versions; reconstructing
past web content from current downloads remains outside this mechanism.

## Concurrency and current limits

Schema version 3 adds the persisted report-summary column and report-ID index.
Opening a version 1 or 2 database migrates it in one transaction. Existing report
payload bytes, IDs and version numbers remain unchanged; only the new metadata
columns are backfilled. This initial migration necessarily parses each old
report once. Subsequent summary queries do not read its complete payload. Tests
enforce this boundary by denying SQLite access to `reports.payload` while
summary and report-free context queries run.

Each write uses a short `BEGIN IMMEDIATE` transaction; a reentrant lock protects
a shared Python connection, and SQLite coordinates separate processes. WAL mode,
foreign keys, a busy timeout and full synchronous writes are enabled. Context
reads use a consistent database transaction. The root controller owns the
per-session run lock so cancellation remains available during execution.

This first implementation retains history without automatic pruning and assumes
one local user. It does not encrypt the SQLite file or manage remote accounts.
Consumers should close the store, or use it as a context manager. For a portable
backup, close the application before copying the database and keep the code's
source snapshot files alongside it.

Validation is covered by `python -m unittest tests.test_storage -v`, including
reopening, multi-turn retention, scope correction, transitive invalidation,
report preservation and on-demand lookup, isolated report histories, bounded
summary reads, schema migrations, duplicate replay, malformed values, and
concurrent writes.
