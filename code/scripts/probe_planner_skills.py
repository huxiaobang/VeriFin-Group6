"""Offline selection diagnostic. Never loads project API settings or generates financial answers."""
import argparse
from pathlib import Path
import sys, os, json, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verifin.engine import ResearchEngine, Budget
from verifin.storage import SessionStore
from verifin.reranking import LocalQwenReranker
from verifin.model import validate_response
ROOT = Path(__file__).resolve().parents[1]
INSTRUCTION = ("判断这项研究方法是否直接帮助回答【本轮】要求的至少一个子问题。"
               "只用前文来消解本轮的指代；忽略用户已撤回的要求。泛问、简单查数或仍有关键歧义时不选专门方法。")


class NoTools:
    def list_tools(self):
        return []


class LocalSelectionProbe:
    mode = "offline_selection_diagnostic"
    def __init__(self, scorer):
        self.scorer = scorer
    def complete(self, role, context):
        assert role == "planner"
        cards = context["skill_catalog"]
        messages = context["messages"]
        history = [m["role"] + ": " + m["content"] for m in messages[:-1]]
        question = context["episode"]["request"]
        query = ("【前文（仅为消解指代）】\n" + "\n".join(history) + "\n" if history else "") + "【本轮】" + question
        scores = self.scorer.score(query, list(cards.values()))
        self.last_scores = dict(sorted(zip(cards, scores), key=lambda row: -row[1]))
        self.last_query = query
        chosen = [n for n,s in self.last_scores.items() if s >= 0.5]
        return validate_response(role, {"action":"finish","reason":"Selection probe ends before acquisition or drafting.",
                                        "research_skills":chosen}, context)


def score_routes(rows, key):
    """Reference agreement and omission are different diagnostics; an extra card is not a verdict."""
    assert set(key)=={r["id"] for r in rows}
    positives=hits=abstentions=empty=0
    for row in rows:
        wanted, got = set(key[row["id"]]), set(row["selected"])
        row.update(expected=key[row["id"]], exact=got==wanted,
                   missing=sorted(wanted-got), additional=sorted(got-wanted))
        if wanted:
            positives+=1
            hits+=wanted.issubset(got)
        else:
            empty+=1
            abstentions+=not got
    return {"reference_nonempty":positives, "all_reference_cards_present":hits,
            "reference_empty":empty, "empty_when_reference_empty":abstentions,
            "exact":sum(row["exact"] for row in rows),"total":len(rows)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(3)
    base=ROOT/"data/skill_routing"
    reader=lambda p:json.loads(p.read_text())
    model=LocalSelectionProbe(LocalQwenReranker(args.model_dir,batch_size=3,instruction=INSTRUCTION))
    cases=reader(base/"turns.json")
    result=[]
    with tempfile.TemporaryDirectory() as temp:
        store=SessionStore(Path(temp)/"sessions.sqlite3")
        try:
            engine=ResearchEngine(store,model,NoTools())
            for case in cases:
                sid=store.create_session(case["session"])["id"]
                for message in case.get("prior_dialogue",[]):
                    store.append_message(sid,message["role"],message["content"])
                for i, question in enumerate(case["turns"],1):
                    episode=engine.ask(sid,question,budget=Budget(max_model_calls=1,max_seconds=240))["episode"]
                    if episode.get("skill_selection_source") != "planner":
                        raise RuntimeError(f"Planner decision was not committed: {episode.get('stop_reason')}")
                    result.append(dict(id=f"{case['session']}/{i}",question=question,received=model.last_query,
                                       selected=episode["research_skills"],scores=model.last_scores))
                    print(result[-1]["id"], result[-1]["selected"],flush=True)
        finally:
            store.close()
    # Read the expected decisions only after all predictions are committed.
    key=reader(base/"expectations.json")
    scores=score_routes(result,key)
    out={"model":model.scorer.metadata["model_id"],"revision":model.scorer.metadata["revision"],
         "instruction":INSTRUCTION,"decision":"independent yes/no scores >= 0.5; no top-k floor or ceiling",
         "scope":"development routing only; local scoring adapter in ResearchEngine; no remote API or financial synthesis",
         **scores,"turns":result}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__":
    main()
