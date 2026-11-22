"""Verify the archived outputs, call counts, SEC filing association and reported arithmetic."""
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))

def require(condition, message):
    if not condition:
        raise ValueError(message)

def main():
    trace = read("followup-traces.json")
    reference = read("sec-reference.json")
    filing = reference["primary_filing"]
    by_tag = {item["tag"]: item for item in reference["facts"]}
    for item in by_tag.values():
        row = item["row"]
        require(item["unit"] == "USD" and row["form"] == filing["form"] == "10-Q", "Unexpected unit or form")
        require(row["accn"] == filing["accessionNumber"] and row["end"] == filing["reportDate"],
                "Financial facts do not match the indexed filing")
        require(row["start"] == "2025-01-01" and row["fy"] == 2025 and row["fp"] == "Q3",
                "Unexpected fiscal window")
    income = Decimal(by_tag["OperatingIncomeLoss"]["row"]["val"])
    revenue = Decimal(by_tag["RevenueFromContractWithCustomerExcludingAssessedTax"]["row"]["val"])
    margin = (100 * income / revenue).quantize(Decimal("0.01"))
    for name, case in trace["cases"].items():
        answer = (ROOT / (name + "-answer.md")).read_bytes()
        require(hashlib.sha256(answer).hexdigest() == case["answer_sha256"], "Answer hash mismatch")
        roles = case["actual_role_sequence"]
        require(dict(Counter(step["role"] for step in roles)) == case["role_calls"], "Role count mismatch")
        require(sum("tool_outcome" in step for step in roles) == case["local_tool_calls"], "Tool count mismatch")
        require(reference["indexed_primary_document_url"] in answer.decode("utf-8"), "Missing filing link")
        if name == "formula":
            require(f"{margin}%" in answer.decode("utf-8"), "Recalculated margin missing from answer")
    print(f"Matched both original answer hashes, call counts and SEC index link. Recomputed margin: {margin}%.")
    print("This check uses the included source extracts. Human assessment of support and completeness is separate.")

if __name__ == "__main__":
    main()
