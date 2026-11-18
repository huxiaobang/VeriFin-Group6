"""Extend preexisting scripted controller replies with their fixture evidence."""
def fixture_verdicts(role, response, context):
    if role != "critic" or "claim_verdicts" in response:
        return response
    response = dict(response)
    verdicts = []
    for claim in context["claims"]:
        if claim["id"] not in response["reviewed_claim_ids"]:
            continue
        refs = claim["evidence_ids"] + claim["calculation_ids"]
        finding = next((i for i in response["issues"] if i["target_id"] == claim["id"]), None)
        verdicts.append({"claim_id":claim["id"], "evidence_refs":refs,
                         "status":"Insufficient Evidence" if finding else "Supported",
                         "basis":finding["missing_support"] if finding else "Scripted source-backed claim for this controller test."})
    response["claim_verdicts"] = verdicts
    if "request_coverage" not in response and context.get("request_parts") == {"part-1": context.get("episode",{}).get("request")}:
        if context["claims"]:
            claim = context["claims"][0]
            response["request_coverage"] = [{"part":"part-1","status":"answered","claim_id":claim["id"],"answer_sentence":claim["text"]}]
        else:
            response["request_coverage"] = [{"part":"part-1","status":"gap","missing_support":"No source in this fixture."}]
    return response
