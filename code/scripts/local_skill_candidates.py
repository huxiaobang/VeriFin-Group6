"""Rank packaged VeriFin methods for a question with the existing, local multilingual E5 encoder."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_embedding_candidates import CANDIDATES
from verifin.runtime_embeddings import LocalSentenceEncoder
from verifin.skill_routing import catalog


def rank_queries(model_dir, questions):
    spec = CANDIDATES["e5-small"]
    encoder = LocalSentenceEncoder(model_dir, **spec)
    cards = dict(catalog())
    ids = list(cards)
    vectors = encoder.encode(list(cards.values()), "passage")
    unique = list(dict.fromkeys(questions))
    ranks = {}
    for question, query in zip(unique, encoder.encode(unique, "query")):
        scores = [sum(x*y for x, y in zip(query, row)) for row in vectors]
        ranks[question] = sorted(zip(ids, scores), key=lambda item: -item[1])
    return ranks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    args_group = parser.add_mutually_exclusive_group(required=True)
    args_group.add_argument("--question", help="Current research question, with any references resolved.")
    args_group.add_argument("--compare", type=Path, help="Compare original and automatically focused questions in the offline probe log.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(2)
    if args.question:
        payload = {"question": args.question, "candidates": rank_queries(args.model_dir, [args.question])[args.question][:5]}
    else:
        rows = json.loads(args.compare.read_text())["turns"]
        inputs = [row["question"] for row in rows] + [row["focus"]["query"] for row in rows if row["focus"]["query"]]
        ranks = rank_queries(args.model_dir, inputs)
        stats, turns = {}, []
        for group in ("existing", "controls"):
            positive = [row for row in rows if row["group"] == group and row["reference"]]
            stats[group] = {"positive_turns": len(positive)}
            for source in ("question", "focused"):
                totals = {}
                for limit in (1, 3, 5):
                    def covered(row):
                        query = row["question"] if source == "question" else row["focus"]["query"]
                        ids = {card for card, _ in ranks.get(query, [])[:limit]}
                        return set(row["reference"]).issubset(ids)
                    totals[f"top_{limit}"] = sum(covered(row) for row in positive)
                stats[group][source] = totals
        for row in rows:
            turns.append({"id": row["id"], "question": ranks[row["question"]][:5],
                          "focused": ranks.get(row["focus"]["query"], [])[:5]})
        payload = {"encoder": CANDIDATES["e5-small"], "summary": stats, "turns": turns}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
