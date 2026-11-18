"""Real financial snapshots + SCRIPTED model fixture, never a live Agent demo.

Run from the source root: python -m scripts.demo_replay --output .verifin/demo
The predetermined choices exercise integration; they do not measure planning or
semantic verification quality. No API key or network is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from verifin.engine import ResearchEngine
from verifin.model import validate_response
from verifin.reporting import export_report
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class ReplayFixtureModel:
    mode = "scripted_fixture"
    timeout = 30
    last_usage = {}

    def __init__(self, *, reuse=False):
        self.step = 3 if reuse else 0
        self.reuse = reuse

    def complete(self, role, context):
        scope = {"entities": ["MSFT"], "period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-01-31",
                 "requirements": [{"id": "req:revenue", "text": "Quarterly revenue"}, {"id": "req:margin", "text": "Quarterly operating margin with source operands"}]}
        if role == "planner":
            step = self.step
            self.step += 1
            if step < 2:
                result = {"action": "tool", "scope": scope, "tool": "financial_facts", "arguments": {"entity": "MSFT", "metric": ["revenue", "operating_income"][step]}}
            elif step == 2:
                facts = {e["metric"]: e for e in context["evidence"]}
                result = {"action": "tool", "tool": "calculate", "arguments": {"operation": "margin", "operand_ids": [facts["operating_income"]["id"], facts["revenue"]["id"]]}}
            else:
                result = {"action": "research" if step == 3 else "finish"}
                if step == 3 and self.reuse:
                    result["requirements"] = [{"id":"req:formula","text":context["episode"]["request"]}]
        elif role == "researcher":
            revenue = next(e for e in context["evidence"] if e.get("metric") == "revenue")
            margin = next(c for c in context["calculations"] if c["operation"] == "margin")
            dimensions = lambda r: {k: r[k] for k in ("entity", "metric", "period_start", "period_end", "unit")}
            claims = [
                {"id": "claim:revenue", "text": f"MSFT revenue for 2024-10-01 through 2024-12-31 was USD {revenue['value']:,}.", "evidence_ids": [revenue["id"]], "calculation_ids": [], "requirement_ids": ["req:revenue"], "constraints": dimensions(revenue), "asserted_value": revenue["value"], "asserted_unit": "USD"},
                {"id": "claim:margin", "text": f"Operating margin for the same quarter was {margin['value']:.4f}%, calculated from saved operating income and revenue.", "evidence_ids": [], "calculation_ids": [margin["id"]], "requirement_ids": ["req:margin"], "constraints": dimensions(margin), "asserted_value": margin["value"], "asserted_unit": "%"},
            ]
            result = {"claims": claims, "answered_requirement_ids": ["req:revenue", "req:margin"], "unanswered_requirement_ids": [], "summary": "SCRIPTED FIXTURE: source and arithmetic integration only."}
            if self.reuse:
                calc = dict(claims[1], requirement_ids=["req:formula"],
                            text=f"Operating income divided by quarterly revenue, multiplied by 100, gives {margin['value']:.4f}%.")
                result.update(claims=[calc],answered_requirement_ids=["req:formula"])
            else:
                result["claims"].append({"id":"claim:summary", "text":" ".join(c["text"] for c in claims),
                    "evidence_ids":[revenue["id"]], "calculation_ids":[margin["id"]],
                    "requirement_ids":result["answered_requirement_ids"], "constraints":{}})
        elif role == "critic":
            result = {"issues": [], "reviewed_claim_ids": [c["id"] for c in context["claims"]], "reviewed_requirement_ids": [r["id"] for r in context["requirements"]]}
            result["claim_verdicts"] = [{"claim_id":c["id"], "status":"Supported",
                "evidence_refs":c["evidence_ids"] + c["calculation_ids"],
                "basis":"Scripted fixture checks the saved quarterly revenue or its operating margin calculation."}
                for c in context["claims"]]
            claim = next(c for c in context["claims"] if self.reuse or c["id"] == "claim:summary")
            result["request_coverage"] = [{"part":"part-1", "status":"answered", "claim_id":claim["id"],
                                           "answer_sentence":claim["text"]}]
        else:
            raise ValueError("Unexpected fixture role")
        return validate_response(role, result, context)


def run_demo(output, data_dir=None):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    data = data_dir or Path(__file__).resolve().parents[1] / "data" / "replay"
    database = folder / "sessions.sqlite3"
    store = SessionStore(database)
    sid = store.create_session("SCRIPTED FIXTURE — real MSFT snapshots")["id"]
    try:
        first = ResearchEngine(store, ReplayFixtureModel(), ToolRegistry(store, data)).ask(sid, "Show revenue and operating margin for MSFT FY2025 Q2.")
    finally:
        store.close()
    # Reopen from disk: the follow-up must need no fresh tool calls.
    store = SessionStore(database)
    try:
        second = ResearchEngine(store, ReplayFixtureModel(reuse=True), ToolRegistry(store, data)).ask(sid, "Explain that calculation using the saved sources.")
        if first["status"] != "complete" or second["status"] != "complete" or second["episode"]["tool_calls"] != 0:
            raise RuntimeError("Scripted integration did not complete: " + json.dumps([first["episode"], second["episode"]]))
        paths = export_report(second["report"], folder / "report")
        record = {"mode": "scripted_fixture", "live_model_calls": 0, "network_requests": 0, "session_id": sid, "first_episode_model_fixture_calls": first["episode"]["model_calls"], "first_episode_tool_calls": first["episode"]["tool_calls"], "followup_tool_calls": second["episode"]["tool_calls"], "saved_report_versions": len(store.list_reports(sid)), "source_values": {e["metric"]: e["value"] for e in second["report"]["evidence"]}, "operating_margin_percent": second["report"]["calculations"][0]["value"], "exports": paths}
        (folder / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        return record
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".verifin/demo")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output), indent=2))
