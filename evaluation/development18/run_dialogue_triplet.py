"""Three six-turn development conversations on a pinned release and frozen public sources."""
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


FOLLOWUPS = {
    "M": [
        "那这一季的 GAAP 营业利润率和去年同期比呢？给两期比率和有符号的百分点变化。",
        "这次披露里，管理层怎么解释 Cloud/AI 的增长？请区分原文归因和你自己的解释。",
        "把刚才的利润率比较、官方解释放一起，再算 7 月 30 日收盘到 31 日收盘的原始收益。列出来源和还没证实的解释。",
        "那这季收入的 as-reported 和 constant-currency 同比各是多少？说说区别，后者能叫 GAAP 增速吗？",
        "所以 Cloud/AI 增长就是刚才股价变动的唯一原因吗？用同一次公告和那个价格窗口，给出能核实的业绩与价格变化。",
    ],
    "N": [
        "那这一季的 GAAP 营业利润率和去年同期比呢？给两期比率和有符号的百分点变化。",
        "管理层怎么解释这次的 Data Center 增长？每项指到原句或原段，说明它跟全公司利润率有什么关系。",
        "把刚才的两期利润率和公司解释放一起，再算 2 月 26 日收盘到 27 日收盘的原始收益。哪些是解释，哪些只是市场观察？",
        "那这一季的收入比上一季公告给出的本季收入指引中点高或低百分之几？分别引用历史指引和实际值。",
        "光凭这次公告和刚才那个收盘窗口，能确认其中一项管理层解释导致了全部股价变化吗？给可支持和不可支持的结论。",
    ],
    "A": [
        "那这一季的 GAAP 营业利润率和去年同期比呢？给两期比率和有符号的百分点变化。",
        "这次披露对 Data Center 和 Client 两个分部的表现分别怎么解释？标出位置，说清这些解释适用于哪一层。",
        "把刚才的两期利润率和官方解释放一起，再算 2 月 4 日收盘到 5 日收盘的原始收益。列出处和缺口。",
        "那份公告里，全年 Client 收入正文的舍入数和表格精确值有什么关系？它和下季度公司合并收入指引是同一指标吗？请分别引用。",
        "所以能说利润率变化导致刚才窗口里的全部股价变化吗？用同一份公告和窗口价格，仍然回答可核查的部分。",
    ],
}


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args):
    release = args.release.resolve()
    revision = subprocess.check_output(["/usr/bin/git", "-C", release, "rev-parse", "HEAD"], text=True).strip()
    if not revision.startswith("c9cbcd2"):
        raise ValueError("Expected team release c9cbcd2")
    sys.path.insert(0, str(release / "code"))
    from verifin.engine import ResearchEngine, Budget
    from verifin.model import ModelClient
    from verifin.reporting import render_markdown
    from verifin.research_methods import configure_methods
    from verifin.storage import SessionStore
    from verifin.tools import ToolRegistry

    out = args.output.resolve() / args.company
    out.mkdir(parents=True, exist_ok=True)
    data = Path(__file__).with_name("pilot_agent_inputs.jsonl")
    first = next(row for row in map(json.loads, data.read_text().splitlines())
                 if row["question_id"] == args.company + "1")
    questions = [first["query"], *FOLLOWUPS[args.company]]
    cutoff = first["public_context"]["information_cutoff"]
    limits = Budget(max_model_calls=96, max_tool_calls=96, max_seconds=7200, max_planner_steps=192)
    save(out / "protocol.json", dict(revision=revision, questions=questions, cutoff=cutoff,
         model=args.model, budget=vars(limits), context_chars=850000, source_network=False))
    env = {k: v for k,v in os.environ.items() if not any(s in k for s in ("API_KEY","ACCESS_TOKEN","AUTH_TOKEN"))}
    call_index = len(list(out.glob("call-*.json.gz")))
    def transport(request, timeout):
        nonlocal call_index
        call_index += 1
        messages = json.loads(request.data)["messages"]
        record = dict(messages=messages, turn=turn)
        prompt = ("Return only the JSON object required by these VeriFin role-tagged messages. "
                  "Use only the supplied content. Financial tool actions belong in the JSON response.\n"
                  + json.dumps(messages, ensure_ascii=False))
        cmd = ["/opt/homebrew/bin/codex","exec","--ignore-user-config","--ephemeral",
               "--skip-git-repo-check","-s","read-only","--disable","shell_tool","-m",args.model,
               "-c",'model_reasoning_effort="low"',"--json","-"]
        try:
            with tempfile.TemporaryDirectory() as cwd:
                p = subprocess.run(cmd, input=prompt, text=True, capture_output=True, cwd=cwd,
                                   env=env, timeout=timeout)
            events = [json.loads(s) for s in p.stdout.splitlines() if s.startswith("{")]
            if p.returncode:
                record["errors"] = [e.get("message") or e.get("error") for e in events
                                    if e.get("type") in ("error","turn.failed")]
                raise RuntimeError("Model transport unavailable")
            items = [e["item"] for e in events if e.get("type")=="item.completed"]
            if any(i.get("type") not in ("agent_message","reasoning","error") for i in items):
                raise RuntimeError("Unexpected external tool")
            reply = next(i["text"] for i in reversed(items) if i["type"]=="agent_message")
            record.update(reply=reply, usage=[e.get("usage") for e in events if e.get("type")=="turn.completed"])
            return json.dumps({"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":reply}}]}).encode()
        finally:
            with gzip.open(out/f"call-{call_index:04}.json.gz","wt",encoding="utf-8") as f:
                json.dump(record,f,ensure_ascii=False)

    model = ModelClient("unused", args.model, "https://offline.invalid/v1", timeout=600,
                        transport=transport, max_context_chars=850000, max_output_tokens=32768)
    model.researcher_max_output_tokens = model.critic_max_output_tokens = 32768
    configure_methods(model, None)
    with SessionStore(out/"session.sqlite3") as store:
        sid_file = out/"session-id.txt"
        if sid_file.exists():
            sid = sid_file.read_text()
        else:
            sid = store.create_session()["id"]
            sid_file.write_text(sid)
        registry = ToolRegistry(store, args.corpus.resolve(), allow_network=False, cache_dir=out/"cache")
        engine = ResearchEngine(store, model, registry)
        for turn, question in enumerate(questions, 1):
            dest = out/f"turn-{turn}.json"
            if dest.exists():
                continue
            started = time.monotonic()
            r = engine.ask(sid, question, as_of=cutoff if turn==1 else None, budget=limits,
                           max_context_chars=850000, request_id=f"dialogue-{args.company}-{turn}")
            answer = render_markdown(r["report"]) if r.get("report") else r.get("question","")
            (out/f"answer-{turn}.md").write_text(answer)
            ep = r.get("episode") or {}
            save(dest, dict(result=r, elapsed=round(time.monotonic()-started,2)))
            print(args.company, turn, r["status"], ep.get("stop_reason"), ep.get("model_calls"),
                  ep.get("tool_calls"), flush=True)
            if ep.get("model_calls") and not any("reply" in json.load(gzip.open(f, "rt"))
                                                for f in out.glob("call-*.json.gz")):
                raise RuntimeError("No model reply received; stop before later questions")
        save(out/"events.json", store.events(sid))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("company", choices=FOLLOWUPS)
    parser.add_argument("--model", default="gpt-6.2-sol")
    run(parser.parse_args())
