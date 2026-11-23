"""Replay recorded last Critic replies from the corresponding pre-review SQLite state."""
import argparse
from contextlib import closing
import gzip
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory


def recorded_reviews(chain):
    reviews = {}
    for file in sorted(chain.glob("call-[0-9]*.json.gz")):
        with gzip.open(file, "rt", encoding="utf-8") as stream:
            entry = json.load(stream)
        reply = json.loads(entry["reply"])
        if "claim_verdicts" in reply:
            payload = entry["messages"][-1]["content"].split("\n", 1)[1]
            reviews[entry["turn"]] = (json.loads(payload), reply)
    return reviews


def restore(chain, turn, wire, path):
    source = (chain / "session.sqlite3").resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(source, uri=True)) as archived, closing(sqlite3.connect(path)) as db:
        archived.backup(db)
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events ORDER BY sequence")]
        review = next(ev for ev in reversed(events) if ev["type"] == "model_call"
                      and ev["role"] == "critic" and ev["episode_id"] == wire["episode"]["id"])
        sequence, time = review["event_sequence"], review["created_at"]
        episode = next(ev["episode"] for ev in reversed(events)
                       if ev["event_sequence"] < sequence and ev["type"] == "episode_checkpoint"
                       and ev["episode"]["id"] == review["episode_id"])
        db.execute("DELETE FROM events WHERE sequence >= ?", (sequence,))
        db.execute("DELETE FROM invalidations WHERE created_at >= ?", (time,))
        db.execute("DELETE FROM records WHERE created_at >= ?", (time,))
        db.execute("DELETE FROM messages WHERE json_extract(payload,'$.created_at') >= ?", (time,))
        db.execute("DELETE FROM reports WHERE version >= ?", (turn,))
        db.execute("DELETE FROM research_submissions WHERE request_id != ?", (episode["request_id"],))
        db.execute("UPDATE research_submissions SET result_event=NULL, report_version=NULL")
        session = wire["session"]
        db.execute("UPDATE sessions SET scope=?, scope_version=?, updated_at=?, cancelled=0 WHERE id=?",
                   (json.dumps(session["scope"]), session["scope_version"], session["updated_at"], session["id"]))
        db.commit()
    return episode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("release", "runs", "corpus", "output"):
        parser.add_argument(field, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.release.resolve() / "code"))
    from verifin.engine import ResearchEngine
    from verifin.model import validate_response
    from verifin.reporting import render_markdown
    from verifin.storage import SessionStore
    from verifin.tools import ToolRegistry

    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    for group in "MNA":
        chain = args.runs / group
        for turn, (wire, reply) in recorded_reviews(chain).items():
            key = f"{group}{turn}"
            final = json.loads((chain / f"turn-{turn}.json").read_bytes())["result"]
            with TemporaryDirectory(dir=args.output) as folder:
                path = Path(folder) / "replay.db"
                ep = restore(chain, turn, wire, path)
                sid = wire["session"]["id"]
                with SessionStore(path) as store:
                    tools = ToolRegistry(store, args.corpus.resolve(), allow_network=False, cache_dir=Path(folder)/"cache")
                    engine = ResearchEngine(store, None, tools)
                    ctx = engine._context(sid, ep)
                    assert {c["id"]: c["version"] for c in ctx["claims"]} == {
                        c["id"]: c["version"] for c in wire["claims"]}, key
                    ep["issues"] = ctx["rule_findings"] = engine._check_rules(ctx)
                    instruction = wire.get("draft_instruction", "")
                    engine._commit_review(sid, ep, ctx, instruction, validate_response("critic", reply, ctx))
                    # A4 attempted a live refresh after its last review. Replay those factual
                    # receipts for delivery while retaining the newly computed review.
                    for field in ("last_tool_result", "recent_tool_results", "market_window_history"):
                        if field in final["episode"]:
                            ep[field] = final["episode"][field]
                    ep["status"] = "complete" if engine._complete(sid, ep) else "partial"
                    ep["stop_reason"] = "completed" if ep["status"] == "complete" else "unresolved_items"
                    report = engine._report(sid, ep)
                    (args.output/f"{key}.md").write_text(render_markdown(report), encoding="utf-8")
                    results[key] = {
                        "status": report["status"], "coverage": report["request_coverage"],
                        "issues": report["issues"], "tool_fault": report.get("tool_fault"),
                        "archived_status": final["report"]["status"],
                        "matches_archived_coverage": report["request_coverage"] == final["report"]["request_coverage"],
                        "matches_archived_tool_fault": report.get("tool_fault") == final["report"].get("tool_fault"),
                        "claims": {c["id"]: c["status"] for c in report["claims"]},
                    }
    assert len(results) == 18
    (args.output/"results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print([(key, row["status"], row["matches_archived_coverage"]) for key, row in results.items()])


if __name__ == "__main__":
    main()
