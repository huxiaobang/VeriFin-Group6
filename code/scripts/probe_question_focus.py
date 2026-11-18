"""Compare dialogue versus current research question for local Skill recommendations."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from probe_planner_skills import INSTRUCTION, NoTools, score_routes
from verifin.engine import Budget, ResearchEngine
from verifin.model import validate_response
from verifin.reranking import LocalQwenReranker
from verifin.request_review import request_parts
from verifin.reporting import question_answers
from verifin.storage import SessionStore

# Discourse markers only. No company, financial term, card ID or answer appears in resolution.
ORDINAL = re.compile(r"前面那个|后面那个|前者|后者|(?:前|后|第[一二三四])(?:个|一个|一项|项|条|点|部分)|\b(?:former|latter|first|second|third|fourth) (?:one|item|point|part)\b", re.I)
NEGATED = re.compile(
    r"^(?:(?:这次|这回|这一轮|本轮|请|先|暂时|就|我们|那个|这个|前者|后者|前一个|后一个)\s*)*"
    r"(?:(?:不用|不必|不再|别|先不|暂不)(?:再)?(?:重算|看|谈|聊|解释|讨论|展开|回答|分析|追问)|跳过)"
    r"|(?:先|暂时)?(?:放一边|放在一边|放着|搁置|不谈了|不用重算)[吧了。！.!]*$",
    re.I,
)
DIALOGUE_PROMPT = "判断用户本轮话语与对话上下文的关系。为最符合这一句话的意图描述打高分。"
MODES = {
    "current":"本轮已经说明想了解的内容，单看本轮就能确定研究方向。允许沿用公司和期间。",
    "reference":"用户依赖前一问或刚才回答中的具体事项，单看本轮无法确定要研究什么，需要找回它指的是哪一项。",
    "ambiguous":"用户明确表示有两种尚未确定的解释或口径，必须先问清选择才能开展这项研究。"
}


def active_text(text):
    clauses = [s.strip() for s in re.split(r"[。；;，,](?!\d)", text) if s.strip()]
    # Keep wording (including punctuation and numeric syntax) intact if nothing was withdrawn.
    active = [s for s in clauses if not NEGATED.search(s)]
    return text.strip() if len(active)==len(clauses) else "，".join(active)


def last_exchange(context):
    prev = []
    for m in reversed(context["messages"][:-1]):
        prev.insert(0, m)
        if m["role"]=="user":
            break
    if not prev:
        return "", ""
    user = next((m["content"] for m in prev if m["role"]=="user"), "")
    answer = next((m["content"] for m in reversed(prev) if m["role"]=="assistant"), "")
    # The web app saves a delivery receipt in chat; substantive claims live in the archived report.
    if answer.startswith("Research ") and "See the report's findings" in answer:
        reports = [r for r in context["reports"] if r.get("request")==user]
        if not reports or not (reports[-1].get("claims") or reports[-1].get("request_coverage")):
            return user, ""
        # Refer to the order the interface actually presented, even when an earlier item was a gap.
        rows = question_answers(reports[-1])
        answer = "；".join(
            row["question"] + (" " + row["answer_sentence"] if row.get("answer_sentence") else "")
            for row in rows
        )
    return user, answer


def parts(text):
    chunks=[s.strip() for s in re.split(r"[；;\n]+",text) if s.strip()]
    if len(chunks)>1:
        return chunks
    return list(request_parts(text).values()) or chunks


def indexed_part(text, token):
    items=parts(text)
    if len(items)<2:
        return ""
    lower=token.lower()
    if lower.startswith(("前","后","former","latter")):
        if len(items)!=2:
            return ""
        return items[int(lower.startswith(("后","latter")))]
    for i,(cn,en) in enumerate(zip("一二三四",("first","second","third","fourth"))):
        if cn in token or lower.startswith(en):
            return items[i] if i<len(items) else ""
    return ""


def resolve(question, context, model):
    current=active_text(question)
    if not current:
        return {"state":"clarify","query":"","source":"","reason":"no_active_clause"}
    prior, answer=last_exchange(context)
    ordinal=ORDINAL.search(current)
    refers_to_answer=bool(re.search(r"你.{0,5}(?:列|说|提|指出)|your (?:answer|list)|you (?:said|listed)",current,re.I))
    if ordinal:
        source=answer if refers_to_answer else (answer or active_text(prior))
        item=indexed_part(source,ordinal[0])
        return {"state":"referenced_item" if item else "clarify", "query":item+"\n"+current if item else "",
                "source":item, "reason":"ordered_reference_in_previous_exchange"}
    # One local model supplies a discourse classification and relevance scores for different instructions.
    modes=model.score_for(DIALOGUE_PROMPT, current, list(MODES.values()))
    mode=max(zip(MODES,modes),key=lambda pair:pair[1])[0]
    if mode=="ambiguous":
        return {"state":"clarify","query":"","source":"","relation_scores":dict(zip(MODES,modes))}
    if mode=="reference":
        source=answer or active_text(prior)
        return {"state":"continue" if source else "clarify", "query":source+"\n"+current if source else "",
                "source":source, "relation_scores":dict(zip(MODES,modes))}
    return {"state":"current","query":current,"source":"","relation_scores":dict(zip(MODES,modes))}


class Comparison:
    mode="offline_focus_probe"
    model="Qwen3-Reranker-0.6B (offline routing)"
    def __init__(self,model):
        self.scorer=model
        self.cache={}

    def score_for(self,instruction,query,documents):
        key=(instruction,query,tuple(documents))
        if key not in self.cache:
            # This local, serial probe owns the scorer for its lifetime.
            self.scorer.instruction=instruction
            self.cache[key]=self.scorer.score(query,documents)
        return self.cache[key]

    def rank(self,query,catalog):
        if not query:
            return {}
        scores=self.score_for(INSTRUCTION,query,list(catalog.values()))
        return dict(sorted(zip(catalog,scores),key=lambda x:-x[1]))

    def complete(self,role,context):
        assert role=="planner"
        catalog=context["skill_catalog"]
        question=context["episode"]["request"]
        prev=[m["role"]+": "+m["content"] for m in context["messages"][:-1]]
        raw=("【前文（仅为消解指代）】\n"+"\n".join(prev)+"\n" if prev else "")+"【本轮】"+question
        before=self.rank(raw,catalog)
        focus=resolve(question,context,self)
        after=self.rank("【本轮】"+focus["query"] if focus["query"] else "",catalog)
        self.last={"question":question,"focus":focus, "raw":before, "focused":after}
        return validate_response(role,{"action":"finish","research_skills":[k for k,v in after.items() if v>=0.5]},context)


def report(rows,ref):
    out={}
    for mode in ("raw","focused"):
        tests=[{"id":r["id"],"selected":[k for k,v in r[mode].items() if v>=0.5]} for r in rows]
        totals=score_routes(tests,ref)
        for count in (3,5):
            totals[f"all_reference_in_top_{count}"]=sum(bool(ref[r["id"]]) and set(ref[r["id"]]).issubset(list(r[mode])[:count]) for r in rows)
        out[mode]=totals
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir",type=Path,required=True)
    parser.add_argument("--output", type=Path, help="Save batch results (required in batch mode).")
    parser.add_argument("--question", help="Probe an arbitrary current question and print recommendations.")
    parser.add_argument("--history-file", type=Path, help="Optional UTF-8 JSON array of prior user/assistant messages.")
    args=parser.parse_args()
    if args.history_file and not args.question:
        parser.error("--history-file requires --question")
    if not args.question and not args.output:
        parser.error("use --question for a single turn or --output for the batch comparison")
    if args.question and not args.question.strip():
        parser.error("question must contain text")
    os.environ.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(3)
    model=Comparison(LocalQwenReranker(args.model_dir,batch_size=3,instruction=INSTRUCTION))
    base=ROOT/"data/skill_routing"
    read=lambda filename:json.loads((base/filename).read_text())
    rows=[]
    with tempfile.TemporaryDirectory() as temp:
        store=SessionStore(Path(temp)/"state.sqlite3")
        try:
            engine=ResearchEngine(store,model,NoTools())
            if args.question:
                sid = store.create_session("local question")["id"]
                history = json.loads(args.history_file.read_text()) if args.history_file else []
                if not isinstance(history, list) or any(
                    not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}
                    or not isinstance(m.get("content"), str) or not m["content"].strip() for m in history
                ):
                    parser.error("history must be an array of user/assistant text messages")
                for message in history:
                    store.append_message(sid, message["role"], message["content"])
                episode = engine.ask(sid, args.question, budget=Budget(max_model_calls=1, max_seconds=240))["episode"]
                if episode.get("skill_selection_source") != "planner":
                    raise RuntimeError(f"Selection failed: {episode.get('error_type')}")
                result = {"question":args.question, "focus":model.last["focus"],
                          "recommended_by_threshold":episode["research_skills"],
                          "top_candidates":list(model.last["focused"].items())[:5],
                          "whole_dialogue_top_candidates":list(model.last["raw"].items())[:5],
                          "raw": model.last["raw"], "focused": model.last["focused"]}
                payload = json.dumps(result, ensure_ascii=False, indent=2)
                if args.output:
                    args.output.write_text(payload+"\n")
                print(payload)
                return
            for group,infile in (("existing","turns.json"),("controls","focus_controls.json")):
                for case in read(infile):
                    sid=store.create_session(case["session"])["id"]
                    for m in case.get("prior_dialogue",[]):
                        store.append_message(sid,m["role"],m["content"])
                    for i,q in enumerate(case["turns"],1):
                        episode=engine.ask(sid,q,budget=Budget(max_model_calls=1,max_seconds=240))["episode"]
                        if episode.get("skill_selection_source")!="planner":
                            raise RuntimeError(f"Selection failed: {episode.get('stop_reason')}")
                        row={"id":f"{case['session']}/{i}","group":group,**model.last}
                        rows.append(row)
                        print(row["id"],row["focus"]["state"],episode["research_skills"],flush=True)
        finally:
            store.close()
    # Keys never enter the scorer or focus resolver. Use them only after all decisions.
    old,new=read("expectations.json"),read("focus_controls_key.json")
    summary={g:report([r for r in rows if r["group"]==g],key) for g,key in (("existing",old),("controls",new))}
    key={**old,**new}
    for r in rows:
        r["reference"]=key[r["id"]]
    args.output.write_text(json.dumps({"model":model.scorer.metadata["model_id"],"revision":model.scorer.metadata["revision"],
        "threshold":0.5,"relation_descriptions":MODES,"summary":summary,"turns":rows},ensure_ascii=False,indent=2)+"\n")
    print(summary,flush=True)

if __name__=="__main__":
    main()
