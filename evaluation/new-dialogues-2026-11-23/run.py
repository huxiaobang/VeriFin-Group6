"""Run new multi-turn questions through the release model adapter with a shared USD cap."""
import argparse
from argparse import Namespace
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import time


def run(args):
    release = args.release.resolve()
    revision = subprocess.check_output(["/usr/bin/git", "-C", release, "rev-parse", "HEAD"], text=True).strip()
    if revision != "618cf9253862867fc3c7878b89f16a6dc640012a":
        raise ValueError("Use the verified team release for this comparison.")
    here = Path(__file__).resolve().parent
    sys.path[:0] = [str(release / "code"), str(here.parent / "development18")]
    from matched_gpt import MODEL, make_model_factory
    from verifin.engine import Budget, ResearchEngine
    from verifin.research_methods import configure_methods
    from verifin.reporting import render_markdown
    from verifin.storage import SessionStore
    from verifin.tools import ToolRegistry

    questions = json.loads((here / "questions.json").read_bytes())[args.group]
    out = args.output.resolve()
    dest = out / args.group
    dest.mkdir(parents=True, exist_ok=True)
    factory = make_model_factory(Namespace(output=out, spend_limit_usd=args.total_usd,
        case_limit_usd=args.total_usd, price_catalog=None, decision_output_tokens=16384))
    budget = Budget(max_model_calls=96, max_tool_calls=96, max_planner_steps=192, max_seconds=7200)
    with SessionStore(dest / "session.sqlite3") as store:
        sid_file = dest / "session-id"
        if sid_file.exists():
            sid = sid_file.read_text()
        else:
            sid = store.create_session()["id"]
            sid_file.write_text(sid)
        for n, question in enumerate(questions["turns"], 1):
            folder = dest / f"turn-{n}"
            folder.mkdir(exist_ok=True)
            result_path = folder / "result.json"
            if result_path.exists():
                existing = json.loads(result_path.read_bytes())
                if existing["stop"] == "spend_budget_exhausted" or existing["no_provider_answer"]:
                    raise RuntimeError(f"Previously stopped at {folder}; inspect the saved attempt before continuing.")
                continue
            client = factory(Namespace(question_id=f"{args.group}-{n}"), folder)
            client.timeout = 180
            client.researcher_max_output_tokens = client.critic_max_output_tokens = 16384
            configure_methods(client, None)
            tools = ToolRegistry(store, args.corpus.resolve(), allow_network=False, cache_dir=dest / "cache")
            engine = ResearchEngine(store, client, tools)
            started = time.monotonic()
            result = engine.ask(sid, question, request_id=f"new-{args.group}-{n}",
                                as_of=questions["as_of"] if n==1 else None,
                                budget=budget, max_context_chars=850000)
            report = result.get("report")
            answer = render_markdown(report) if report else result.get("question","")
            (folder / "answer.md").write_text(answer)
            ep = result.get("episode") or {}
            with sqlite3.connect(out / "openai-cost-estimates.sqlite3") as ledger:
                attempts, settled, estimate, reserved = ledger.execute(
                    "SELECT count(*),count(actual),coalesce(sum(actual),0),"
                    "coalesce(sum(CASE WHEN actual IS NULL THEN hold ELSE 0 END),0) "
                    "FROM attempts WHERE label=?", (f"{args.group}-{n}",)).fetchone()
            status = dict(group=args.group,turn=n,session=sid,question=question,model=MODEL,
                          product_status=result.get("status"),stop=ep.get("stop_reason"),version=report and report.get("version"),
                          tools=ep.get("tool_calls"),models=ep.get("model_calls"),attempts=attempts,
                          receipts=settled,estimated_usd=estimate/1e9,outstanding_usd=reserved/1e9,
                          no_provider_answer=bool(ep.get("model_calls") and not settled),
                          seconds=round(time.monotonic()-started,2))
            result_path.write_text(json.dumps(status,ensure_ascii=False,indent=2))
            print(json.dumps(status,ensure_ascii=False),flush=True)
            if status["no_provider_answer"] or "spend" in (status["stop"] or ""):
                break


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("release","corpus","output"):
        parser.add_argument(name,type=Path)
    parser.add_argument("group",choices=("retail","disney","airline"))
    parser.add_argument("--total-usd",required=True,type=str)
    run(parser.parse_args())
