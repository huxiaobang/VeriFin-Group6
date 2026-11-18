"""Fetch/audit a pinned public claim benchmark, export blinded inputs, run BM25.

No LLM is called. --download is the only network operation; it downloads nine
fixed report groups from official testmini, never the entire report archive.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verifin.claim_benchmarks import FINDVER_REVISION, load_findver, retrieval_diagnostic, score_claim_predictions

SEED = "verifin-findver-dev-2026-11-06"
ANNOTATION_HASHES = {"testmini": "09ffb848fec6cc38c7df153500f63388fa5fb7d9bc78300690ddbb83fa4e403a",
                     "test": "7cfef6889d0355a0d07c06e66ab46e6fcd4ebf06080c0f908409c7be4c7aa3b7"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def selection(rows):
    reports = set()
    for subset in sorted({r["subset"] for r in rows}):
        candidates = {r["report"] for r in rows if r["subset"] == subset}
        reports.update(sorted(candidates, key=lambda name: hashlib.sha256((SEED + ":" + name).encode()).hexdigest())[:3])
    chosen = [r for r in rows if r["report"] in reports]
    return {"protocol": "findver-testmini-stratified-reports-v2", "seed": SEED,
            "selection": "For each published subset: first 3 report names by SHA256(seed:report); union reports; preserve all testmini claims for chosen reports. No performance-dependent selection.",
            "revision": FINDVER_REVISION, "reports": sorted(reports),
            "case_ids": [r["example_id"] for r in chosen], "case_count": len(chosen),
            "subset_counts": dict(Counter(r["subset"] for r in chosen))}


def fetch(base, name):
    path = base / name
    if path.is_file():
        return
    url = f"https://raw.githubusercontent.com/yilunzhao/FinDVer/{FINDVER_REVISION}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": "VeriFin-academic-benchmark-audit"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(15_000_001)
    if len(raw) > 15_000_000:
        raise ValueError("Unexpectedly large benchmark file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    if args.download:
        for name in ("README.md", "LICENSE", "data/testmini.json", "data/test.json"):
            fetch(args.data_dir, name)
    data = {}
    for split, expected in ANNOTATION_HASHES.items():
        raw = (args.data_dir / "data" / (split + ".json")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Annotation hash differs from pinned revision: " + split)
        data[split] = json.loads(raw)
    frozen = selection(data["testmini"])
    write_json(args.output_dir / "selection.json", frozen)
    if args.download:
        for name in frozen["reports"]:
            fetch(args.data_dir, "financial_reports/" + name)
    pinned = json.loads((ROOT / "data/evaluation/findver_report_manifest.json").read_text())
    if pinned["revision"] != FINDVER_REVISION:
        raise ValueError("Pinned report manifest revision mismatch")
    report_hashes = {r["report"]: r["sha256"] for r in pinned["reports"]}
    cases = load_findver(args.data_dir / "data/testmini.json", args.data_dir / "financial_reports",
                         split="testmini", selected_ids=frozen["case_ids"], expected_report_hashes=report_hashes)
    files = [(args.data_dir / "data" / (s + ".json")) for s in data]
    files += [args.data_dir / "financial_reports" / name for name in frozen["reports"]]
    file_manifest = [{"path": str(p.relative_to(args.data_dir)), "bytes": p.stat().st_size,
                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                      "url": f"https://raw.githubusercontent.com/yilunzhao/FinDVer/{FINDVER_REVISION}/{p.relative_to(args.data_dir)}"}
                     for p in files]
    inputs, references = args.output_dir / "inputs.jsonl", args.output_dir / "references.jsonl"
    # IDs AND source order can encode FinDVer labels. Only opaque content-derived
    # IDs, with a fixed hash order unrelated to official numbering, reach models.
    input_order = sorted(cases, key=lambda c: c.model_case_id)
    inputs.write_text("".join(json.dumps(c.input_record(), ensure_ascii=False) + "\n" for c in input_order))
    references.write_text("".join(json.dumps(c.reference_record(), ensure_ascii=False) + "\n" for c in input_order))
    report_sets = {s: {r["report"] for r in rows} for s, rows in data.items()}
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "revision": FINDVER_REVISION,
              "protocol": frozen, "published_split_audit": {
                  s: {"cases": len(rows), "reports": len(report_sets[s]),
                      "subsets": dict(Counter(r["subset"] for r in rows)),
                      "boolean_labels": sum(type(r.get("entailment_label")) is bool for r in rows)} for s, rows in data.items()},
              "testmini_test_report_overlap": len(report_sets["testmini"] & report_sets["test"]),
              "pinned_report_bytes_verified": True,
              "files": file_manifest, "retrieval": retrieval_diagnostic(cases),
              "source_isolation": {"model_input_file": "inputs.jsonl", "scorer_only_file": "references.jsonl",
                                   "labels_or_gold_evidence_in_input": False,
                                   "official_ids_in_input": False,
                                   "ordering": "opaque content-derived model ID, not official ID or source order",
                                   "inputs_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest(),
                                   "references_sha256": hashlib.sha256(references.read_bytes()).hexdigest()},
              "llm_calls": 0, "claim_verification_result": None,
              "limits": ["Fixed public testmini development probe; not untouched test performance.",
                         "Full report contexts are provided by the dataset, so source discovery is not measured.",
                         "Published labels reduce fresh annotation for binary verification; they do not label omissions, tool planning or arbitrary new system claims.",
                         "Public data may overlap pretraining; no contamination-free claim.",
                         "Repository MIT does not relicense underlying issuer reports."]}
    if args.predictions:
        result["claim_verification_result"] = score_claim_predictions(cases, json.loads(args.predictions.read_text()))
    write_json(args.output_dir / "audit.json", result)
    print(json.dumps({"case_count": len(cases), "reports": len(frozen["reports"]),
                      "summary": result["retrieval"]["summary"], "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
