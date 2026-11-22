import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from urllib.request import Request

from verifin.context import planner_view, shared_evidence_sources, shared_receipt_data
from verifin.model import ModelClient, ModelError, SpendLimitReached
from verifin.spend_guard import SpendGuard
from verifin.tools import TOOL_SPECS


class PlannerViewTests(unittest.TestCase):
    def setUp(self):
        self.body = "A"*400 + "MARK-IN-THE-MIDDLE" + "B"*500
        self.span = {"id":"p1","document_id":"D","kind":"document","start":12,
                     "end":12+len(self.body),"text":self.body}
        self.cell = {"id":"cell","kind":"table_cell","text":self.body,"value":3.1,"column_headers":["Q4"]}
        self.ctx = {"tools":TOOL_SPECS,"evidence":[self.span,self.cell],"episode":{"request":"compare"}}

    def test_full_window_restored_on_read_and_cells_unchanged(self):
        view = planner_view(self.ctx)
        self.assertNotIn("text",view["evidence"][0])
        self.assertEqual(view["evidence"][0]["read_document"],{"document_id":"D","offset":12,"limit_chars":len(self.body)})
        self.assertEqual(view["evidence"][1],self.cell)
        self.ctx["episode"]["recent_tool_results"]=[{"tool":"read_document","evidence_ids":["p1"]}]
        self.assertEqual(planner_view(self.ctx)["evidence"][0], self.span)
        self.assertEqual(self.ctx["evidence"][0]["text"],self.body)
        self.assertIs(planner_view({**self.ctx,"tools":[]})["evidence"],self.ctx["evidence"])

    def test_critic_wire_is_byte_identical(self):
        c = ModelClient("unit","gpt-6.1-sol","https://api.openai.com/v1",api_mode="responses",cache_shared_tools=True)
        c._transport=Mock(return_value=b'{}')
        for mode in ("full","compact"):
            c.planner_view=mode
            with self.assertRaises(ModelError): c.complete("critic",self.ctx)
        self.assertEqual(*[call.args[0].data for call in c._transport.call_args_list])

    def test_search_hands_off_every_latest_original_and_preserves_conflicting_receipt(self):
        earlier = {**self.span, "id": "old", "snapshot_path": "earlier.txt"}
        recent = [{**self.span, "id": key} for key in ("p1", "p2")]
        different = {**recent[0], "text": "different receipt"}
        receipt = {"tool": "search_documents", "ok": True, "data": {},
                   "evidence_ids": ["p1", "p2"], "calculation_ids": []}
        raw = {"ok": True, "data": {}, "evidence": [different, recent[1]], "calculations": []}
        ctx = dict(self.ctx, evidence=[earlier, *recent],
                   episode={"last_tool_result": raw, "recent_tool_results": [receipt]})
        view = planner_view(ctx)
        self.assertEqual(view["evidence"][1:], recent)
        self.assertNotIn("text", view["evidence"][0])
        self.assertEqual(view["evidence"][0]["snapshot_path"], "earlier.txt")
        self.assertEqual(view["episode"]["last_tool_result"]["evidence"][0], different)

    def test_full_role_requests_pack_before_the_limit_and_preserve_every_record(self):
        from tests.test_responses import response
        rows = [{**self.span, "id": f"p{i}", "text": self.body + str(i), "end": self.span["end"] + 1,
                 "snapshot_hash": str(i // 3) * 64, "source_url": "https://example.test/" + "x" * 900,
                 "published_at": f"2025-01-0{i+1}", "locator": f"row {i}"} for i in range(6)]
        ctx = {**self.ctx, "evidence": rows, "tools": [TOOL_SPECS[0]]}
        size = lambda v: len(json.dumps(v, ensure_ascii=False))
        limit = size(shared_evidence_sources(ctx))
        self.assertLess(limit, size(ctx))
        replies = {"planner": {"action": "research"},
                   "researcher": {"claims": [], "answered_requirement_ids": [], "unanswered_requirement_ids": [], "summary": ""},
                   "critic": {"reviewed_claim_ids": [], "reviewed_requirement_ids": [], "issues": []}}
        for role, reply in replies.items():
            for cache in (False, True):
                send = Mock(return_value=response(reply))
                client = ModelClient("fake", "test", "https://example.test/v1", api_mode="responses",
                                     max_context_chars=limit + 100000, cache_shared_tools=cache, transport=send)
                self.assertEqual(client.complete(role, ctx), reply)
                messages = json.loads(send.call_args.args[0].data)["input"]
                if cache:
                    packed = [json.loads(block["text"].split("\n", 1)[1]) for block in messages[1]["content"]]
                else:
                    packed = json.loads(messages[1]["content"].split("\n", 1)[1])["evidence"]
                self.assertTrue(any("source_fields_from" in row for row in packed))
                self.assertEqual(self.restore(packed), rows)
        collision = {**ctx, "evidence": [{**rows[0], "source_fields_from": "original"}, *rows[1:]]}
        self.assertIs(shared_evidence_sources(collision), collision)

    @staticmethod
    def restore(rows):
        restored = {}
        for packed in rows:
            row = dict(packed)
            ref = row.pop("source_fields_from", None)
            if ref:
                row.update({key:restored[ref["id"]][key] for key in ref["fields"]})
            if "text_parts" in row:
                chunks = []
                for part in row.pop("text_parts"):
                    if isinstance(part, str):
                        chunks.append(part)
                    else:
                        earlier = restored[part["evidence_id"]]
                        offset = earlier["start"]
                        chunks.append(earlier["text"][part["start"]-offset:part["end"]-offset])
                row["text"] = "".join(chunks)
            restored[row["id"]] = row
        return list(restored.values())

    def test_overlaps_across_windows_preserve_prefix_and_revised_sources(self):
        text = "".join(chr(0x5000 + i) for i in range(2000))
        def span(key, start, end, **patch):
            return {**self.span, "id":key, "start":start, "end":end, "text":text[start:end],
                    "entity":"issuer", "snapshot_hash":"hash", "text_snapshot_hash":"text-hash", **patch}
        rows = [span("a",100,600), span("b",650,1000), span("c",300,1100), span("d",90,1200),
                span("old-period",300,1100,snapshot_hash="another-snapshot"),
                span("disagrees",100,600,text="x"*500)]
        packed = shared_evidence_sources({"evidence":rows})["evidence"]
        for n in range(1,len(rows)):
            self.assertEqual(packed[:n], shared_evidence_sources({"evidence":rows[:n]})["evidence"])
        self.assertEqual("".join(x for x in packed[2]["text_parts"] if isinstance(x,str)), text[600:650]+text[1000:1100])
        self.assertNotIn("text_parts", packed[4])
        self.assertNotIn("text_parts", packed[5])
        self.assertEqual(self.restore(packed), rows)


class SharedProvenanceTests(unittest.TestCase):
    def test_shared_table_provenance_keeps_exact_cell_headers_and_values(self):
        first = {
            "id": "first", "kind": "financial_fact", "document_id": "release", "snapshot_hash": "a" * 64,
            "table_id": "quarterly-income", "column_headers": ["Three Months Ended", "2025"],
            "column_header_locators": ["/same/heading/" + "x" * 80],
            "currency_reference": {"source": "disclosure/" + "x" * 200, "published_at": "2025-07-30"},
            "printed_unit": "In millions except per share amounts", "printed_value": "76,441", "value": 76_441_000_000,
            "locator": "row 3, column 4", "period_end": "2025-06-30",
        }
        second = {**first, "id": "second", "printed_value": "20,500", "value": 20_500_000_000,
                  "locator": "row 4, column 4"}
        changed_header = {**second, "id": "changed", "column_headers": ["Nine Months Ended", "2025"]}
        different_source = {**second, "id": "alternate", "snapshot_hash": "b" * 64}
        stale = {**second, "id": "stale", "stale": True}
        rows = [first, second, changed_header, different_source, stale]
        legacy = shared_evidence_sources({"evidence": rows})["evidence"]
        extended = shared_evidence_sources({"evidence": rows}, extended_sources=True)["evidence"]

        self.assertEqual(legacy[1]["currency_reference"], first["currency_reference"])
        self.assertEqual(extended[1]["source_fields_from"]["id"], first["id"])
        self.assertIn("currency_reference", extended[1]["source_fields_from"]["fields"])
        self.assertIn("column_header_locators", extended[1]["source_fields_from"]["fields"])
        self.assertEqual(extended[1]["value"], second["value"])
        self.assertEqual(extended[1]["locator"], second["locator"])
        self.assertEqual(extended[2]["column_headers"], changed_header["column_headers"])
        self.assertEqual(extended[3:], rows[3:])
        self.assertEqual(PlannerViewTests.restore(extended), rows)
        self.assertLess(len(json.dumps(extended)), len(json.dumps(legacy)))

    def test_taxonomy_metadata_requires_same_source_and_concept(self):
        first = {
            "id": "first", "kind": "financial_fact", "entity": "MSFT", "snapshot_hash": "a" * 64,
            "source_url": "https://example.test/companyfacts", "concept": "revenue",
            "concept_description": "Official taxonomy release note. " * 20,
            "period_end": "2025-03-31", "value": 700,
        }
        second = {**first, "id": "second", "period_end": "2025-06-30", "value": 900}
        other_concept = {**second, "id": "third", "concept": "gross_profit"}
        other_source = {**second, "id": "fourth", "source_url": "https://example.test/other"}
        context = {"evidence": [first, second, other_concept, other_source]}

        packed = shared_evidence_sources(context, extended_sources=True)["evidence"]
        self.assertIs(shared_evidence_sources(context), context)
        self.assertIn("concept_description", packed[1]["source_fields_from"]["fields"])
        self.assertEqual(packed[1]["period_end"], second["period_end"])
        self.assertEqual(packed[2:], context["evidence"][2:])
        self.assertEqual(PlannerViewTests.restore(packed), context["evidence"])

    def test_latest_tool_data_points_only_to_an_exact_visible_duplicate(self):
        observation = {
            "ok": True, "summary": "Results", "data": {"search_term": "截至期末" * 40, "value": 1},
            "evidence": [{"id": "c1", "value": 76_441_000_000}], "calculations": [],
        }
        recent = {"tool": "search_documents", "ok": True, "summary": "Results", "data": dict(observation["data"]),
                  "evidence_ids": ["c1"], "calculation_ids": []}
        context = {"episode": {"last_tool_result": observation, "recent_tool_results": [recent, dict(recent)]}}
        packed = shared_receipt_data(context)["episode"]
        latest = dict(packed["last_tool_result"])
        self.assertEqual(latest.pop("data_from_recent_tool_result"), 1)
        latest["data"] = packed["recent_tool_results"][1]["data"]
        self.assertEqual(latest, observation)
        self.assertEqual(packed["recent_tool_results"], context["episode"]["recent_tool_results"])
        self.assertEqual(context["episode"]["last_tool_result"], observation)

        different = {**recent, "data": {**recent["data"], "value": True}}
        contexts_without_safe_duplicate = [
            {**context["episode"], "recent_tool_results": [recent, different]},
            {**context["episode"], "recent_tool_results": [recent, {**recent, "summary": "Other operation"}]},
            {**context["episode"], "recent_tool_results": []},
            {"last_tool_result": observation},
        ]
        for episode in contexts_without_safe_duplicate:
            with self.subTest(episode=episode):
                other = {"episode": episode}
                self.assertIs(shared_receipt_data(other), other)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/"ledger.sqlite")
        self.catalog=json.loads((Path(__file__).resolve().parents[1]/"data/pricing/openai-standard-2026-11-06.json").read_text())
        self.model="gpt-6.1-sol"
        self.req=Request("https://api.openai.com/v1/responses",data=json.dumps({
            "input":[{"role":"user","content":"hello"}],"service_tier":"default","model":self.model,
            "prompt_cache_options":{"mode":"explicit","ttl":"30m"},"max_output_tokens":10}).encode())
    def guard(self,limit):
        return SpendGuard(self.path,limit,self.catalog,self.model,"smoke")
    def rows(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("select hold,actual from attempts").fetchall()
    def test_concurrent_unknown_receipts_hold_one_reservation(self):
        guard=self.guard("0.019")
        def run(_):
            try:
                guard.attempt("planner",self.req,1,lambda *_: (_ for _ in ()).throw(TimeoutError()))
            except TimeoutError: return "sent"
            except ModelError: return "denied"
        with ThreadPoolExecutor(max_workers=4) as workers: results=list(workers.map(run,range(4)))
        self.assertCountEqual(results,["denied"]*3+["sent"])
        self.assertIsNone(self.rows()[0][1])
        with self.assertRaises(ModelError): self.guard("1.00")
    def test_failed_completion_still_settles_from_provider_usage(self):
        g=self.guard("0.10")
        g.attempt("critic",self.req,1,lambda *_: json.dumps({"status":"incomplete","model":self.model,
            "service_tier":"default","usage":{"input_tokens":100,"output_tokens":10,
            "input_tokens_details":{"cached_tokens":20,"cache_write_tokens":0}}}).encode())
        self.assertEqual(self.rows()[0][1],262000)
        self.assertGreater(self.rows()[0][0],self.rows()[0][1])
        g.attempt("critic",self.req,1,lambda *_: b'{"error":{}}')
        self.assertIsNone(self.rows()[1][1])

    def test_unexpected_provider_overage_freezes_all_future_dispatch(self):
        g=self.guard("0.9")
        with self.assertRaisesRegex(ModelError,"exceeded reservation"):
            g.attempt("planner",self.req,1,lambda *_: json.dumps({"model":self.model,"service_tier":"default",
                "usage":{"input_tokens":80000,"output_tokens":5,
                "input_tokens_details":{"cached_tokens":0,"cache_write_tokens":0}}}).encode())
        transport=Mock()
        with self.assertRaisesRegex(ModelError,"no more requests"):
            g.attempt("planner",self.req,1,transport)
        transport.assert_not_called()

    def test_only_complete_matching_messages_with_a_settled_receipt_reduce_the_hold(self):
        def request(first, second, *, breakpoint=False, effort="medium"):
            body = json.loads(self.req.data)
            block = {"type":"input_text","text":first}
            if breakpoint:
                block["prompt_cache_breakpoint"] = {"mode":"explicit"}
            body.update(input=[{"role":"developer","content":[block]}, {"role":"user","content":second}],
                        reasoning={"effort":effort})
            return Request(self.req.full_url, data=json.dumps(body).encode())

        guard = self.guard("1")
        prefix = '财报 "text"\n' * 4000
        original = request(prefix, "old", breakpoint=True)
        changed = request(prefix, "新的问题")
        transport = Mock()
        def receipt(status):
            return json.dumps({"model":self.model, "service_tier":"default", "status":status,
                               "usage":{"input_tokens":9000,"output_tokens":10,
                                        "input_tokens_details":{"cached_tokens":0,"cache_write_tokens":0}}}).encode()
        full = guard.reservation(changed)
        other_effort = request(prefix, "新的问题", effort="high")
        original_other_effort_hold = guard.reservation(other_effort)
        guard.attempt("planner", original, 1, lambda *_: receipt("incomplete"))
        self.assertEqual(guard.reservation(changed), full)
        guard.attempt("researcher", original, 1, lambda *_: receipt("completed"))
        restarted = self.guard("1")
        self.assertLess(restarted.reservation(changed), full)
        self.assertGreaterEqual(restarted.reservation(request(prefix + "!", "新的问题")), full)
        self.assertEqual(restarted.reservation(other_effort), original_other_effort_hold)
        # Matching output limits and all other settings cannot make an arbitrarily large new suffix free.
        with self.assertRaises(SpendLimitReached):
            restarted.attempt("critic",request(prefix, "extra"*100_000),1,transport)
        transport.assert_not_called()
        with sqlite3.connect(self.path) as db:
            self.assertTrue(all(len(digest)==32 and ceiling==9000 for digest,ceiling in
                                db.execute("select prefix,input_ceiling from input_prefixes")))

    def test_existing_ledger_keeps_its_unsettled_hold_on_upgrade(self):
        self.guard("0.019")
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE input_prefixes")  # Schema from before receipt-based reservations.
            db.execute("INSERT INTO attempts(id,label,role,hold) VALUES('old','previous','planner',18000000)")
        transport = Mock()
        with self.assertRaises(SpendLimitReached):
            self.guard("0.019").attempt("critic",self.req,1,transport)
        transport.assert_not_called()
        self.assertEqual(self.rows(), [(18000000,None)])

    def test_append_whole_evidence_blocks_keeps_only_the_matched_prefix_bound(self):
        guard = self.guard("1")
        def request(parts, role="user"):
            body = json.loads(self.req.data)
            body["input"] = [{"role":role, "content":[{"type":"input_text","text":p} for p in parts]}]
            return Request(self.req.full_url,data=json.dumps(body).encode())
        old_parts = ["public material " * 1200, "quarter table " * 1300]
        appended = request([*old_parts,"new row"])
        before = guard.reservation(appended)
        guard.attempt("planner",request(old_parts),1,lambda *_:json.dumps({
            "model":self.model,"service_tier":"default","status":"completed",
            "usage":{"input_tokens":9000,"output_tokens":5,
                     "input_tokens_details":{"cached_tokens":0,"cache_write_tokens":0}}
        }).encode())
        self.assertLess(guard.reservation(appended),before)
        self.assertGreater(guard.reservation(request(old_parts[::-1]+["new row"])),guard.reservation(appended))
        self.assertGreater(guard.reservation(request([*old_parts,"new row"],"developer")),guard.reservation(appended))
        with self.assertRaises(SpendLimitReached):
            guard.attempt("planner", request(old_parts+["extra"*200000]),1, self.fail)
