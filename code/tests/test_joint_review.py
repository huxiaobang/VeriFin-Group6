import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path

from tests.test_engine import ScriptedModel, FixtureTools, full_script
from verifin.engine import ResearchEngine, Budget
from verifin.request_review import grounded_review_rows, report_coverage
from verifin.storage import SessionStore


class JointReviewTests(unittest.TestCase):
    def setUp(self):
        self.parts = {"part-1":"请注明财季起止日期，给出申报链接。"}
        self.claims = [{"id":"date","status":"Supported","text":"Quarter dates are 2025-01-01 to 2025-03-31."},
                       {"id":"link","status":"Supported","text":"The filing link is https://example.test/X."}]
        self.row = {"part":"part-1","status":"answered","claim_id":"date, link","supporting_claim_ids":["date","link"],
                    "answer_sentence":"Quarter dates are 2025-01-01 to 2025-03-31, and The filing link is https://example.test/X."}
        self.verdicts = {c["id"]:{"status":"Supported"} for c in self.claims}

    def mapped(self):
        return grounded_review_rows(self.parts,[self.row],self.claims,self.verdicts,"")

    def test_joint_quotes_use_only_current_reviewed_claims(self):
        rows = self.mapped()
        self.assertEqual(rows[0]["answer_sentence"],self.claims[0]["text"])
        self.assertEqual(report_coverage(self.parts,rows,self.claims,reviewed=True)[0]["status"],"answered")
        self.claims[1]["stale"] = True
        self.assertEqual(report_coverage(self.parts,rows,self.claims,reviewed=True)[0]["status"],"gap")
        self.assertNotIn("joint_claim_ids",self.mapped()[0])

    def test_altered_quote_or_unreviewed_link_is_not_credited(self):
        self.row["answer_sentence"]=self.row["answer_sentence"].replace("/X","/x")
        self.assertNotIn("joint_claim_ids",self.mapped()[0])
        self.row["answer_sentence"]=self.row["answer_sentence"].replace("/x","/X")
        self.verdicts["link"]["status"]="Unverifiable"
        self.assertNotIn("joint_claim_ids",self.mapped()[0])

    def test_last_seconds_preserve_only_the_current_question_review(self):
        time=[0.]
        times=iter([1.,2.,30.,65.,94.])
        def advance(*args):
            time[0]=next(times)
        script=full_script()
        script[3]=("critic",{**script[3][1],"issues":[{"target_id":"req:revenue","issue_type":"omission",
                    "evidence_refs":[],"missing_support":"A requested breakdown is absent.",
                    "proposed_action":"Check the breakdown."}]})
        script[-1]=("planner",{"action":"research"})
        model=ScriptedModel(script,callback=advance)
        with TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/"s.db")
            try:
                sid=store.create_session("Timing")["id"]
                engine=ResearchEngine(store,model,FixtureTools(),clock=lambda:time[0])
                result=engine.ask(sid,"Revenue?",budget=Budget(max_seconds=100))
                self.assertEqual([r for r,_ in model.calls].count("researcher"),1)
                self.assertTrue(result["report"]["verification_available"])
                self.assertEqual(result["report"]["stop_reason"],"insufficient_time_for_reviewed_revision")
                self.assertEqual(store.latest_event(sid,"revision_skipped")["last_critic_seconds"],35.)
                for field,value in (("id","another-turn"),("request","A different question?"),("summary","Later draft")):
                    ep=deepcopy(result["episode"])
                    ep[field]=value
                    self.assertFalse(engine._report(sid,ep)["verification_available"])
            finally:
                store.close()
