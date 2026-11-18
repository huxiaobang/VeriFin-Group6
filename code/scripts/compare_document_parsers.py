#!/usr/bin/env python3
"""Offline source-fidelity probe: existing flat text, lxml/pandas, EdgarTools.

No downloads or LLM calls. The real SEC snapshots are separately recorded in a
manifest; built-in checks are development examples, not held-out financial gold.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import socket
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verifin.structured_documents import parse_structured_html, table_chunks
from verifin.tools import _FilingText


COMPLEX = """<!doctype html><html><head><meta charset="UTF-8"></head><body>
<h1>Quarterly financials</h1><p>Visible <span hidden>HIDDEN</span> continuation.</p>
<div style="display: none">SECRET</div><ix:hidden>HIDDEN_XBRL</ix:hidden>
<table id="financials"><caption>USD millions; actual versus outlook</caption>
<thead><tr><th rowspan="2">Metric</th><th colspan="2">FY2025</th></tr>
<tr><th>Actual Q1</th><th>Outlook Q2</th></tr></thead><tbody>
<tr><td rowspan="2">Revenue</td><td>001,203</td><td>N/A</td></tr>
<tr><td>(52.3)</td><td>10.5%</td></tr>
<tr><td>Gross margin</td><td>70.6%</td><td>71.0%</td></tr>
<tr><td>Non-numeric placeholder</td><td>—</td><td>0</td></tr>
</tbody></table><p>This constructed fixture tests geometry and strings only.</p></body></html>"""
R2_SHA = "13cad7f06f77cf15858b13ed3dc4dc468265e9fdfe0cf2c9d895b392e9ff50f1"
PRIMARY_SHA = "487848fb1b223cb23fe4f359fce0795902800a81f386d372fc2507d36569a003"
EXPECTED_NET_SALES = ["Net sales", "$ 95,359", "$ 90,753", "$ 219,659", "$ 210,328"]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def frame_json(frame):
    # Diagnostic export only. Keep conversion differences visible as JSON types.
    return {"columns": [[str(x) for x in c] if isinstance(c, tuple) else str(c) for c in frame.columns],
            "index": [str(x) for x in frame.index],
            "data": json.loads(frame.to_json(orient="values"))}


def edgar_parse(text):
    from edgar.documents import parse_html
    from edgar.documents.table_nodes import Cell
    started = time.perf_counter()
    document = parse_html(text)
    tables = []
    for table in document.tables or []:
        rows = [[cell.text() for cell in row.cells] for row in table.rows]
        record = {"headers": [[cell.text() for cell in row] for row in table.headers],
                  "rows": rows, "caption": table.caption,
                  "metadata": table.metadata,
                  "cells": [{"text": cell.text(), "rowspan": cell.rowspan, "colspan": cell.colspan}
                            for row in table.rows for cell in row.cells]}
        # DataFrame behavior is tested for target financial/fixture tables only.
        if any(row and row[0] in {"Net sales", "Total net sales", "Revenue"} for row in rows):
            try:
                record["dataframe"] = frame_json(table.to_dataframe())
            except Exception as exc:
                record["dataframe_error"] = type(exc).__name__ + ": " + str(exc)
        tables.append(record)
    return {"version": importlib.metadata.version("edgartools"),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "tables": tables,
            "numeric_value_api_probe": {value: Cell(content=value).numeric_value
                                        for value in ["001,203", "N/A", "—", "(52.3)", "10.5%"]},
            "original_snapshot_xpath_provided": False}


def compare(path, output_dir, source, with_edgar):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    started = time.perf_counter()
    flat = _FilingText()
    flat.feed(text)
    flat_text = flat.text()
    baseline = {"latency_ms": (time.perf_counter() - started) * 1000,
                "characters": len(flat_text), "line_count": len(flat_text.splitlines()),
                "row_column_structure_available": False,
                "hidden_marker_leaked": any(v in flat_text for v in ["HIDDEN", "SECRET"])}
    (output_dir / (path.name + ".flat.txt")).write_text(flat_text, encoding="utf-8")
    started = time.perf_counter()
    structured = parse_structured_html(raw, source_url=source["download_url"], snapshot_path=path.resolve())
    latency = (time.perf_counter() - started) * 1000
    chunks = table_chunks(structured, max_rows=5)
    dump(output_dir / (path.name + ".structured.json"), structured)
    dump(output_dir / (path.name + ".table_chunks.json"), chunks)
    summary = {"latency_ms": latency, "visible_tables": len(structured["tables"]),
               "table_status_counts": dict(Counter(t["status"] for t in structured["tables"])),
               "failures": dict(Counter(t["failure_reason"] for t in structured["tables"] if t["failure_reason"])),
               "structured_cells": sum(len(t["source_cells"]) for t in structured["tables"]),
               "table_chunks": len(chunks),
               "whole_table_chunks_without_declared_headers": sum(c["chunking_status"] == "whole_table_no_declared_headers" for c in chunks),
               "hidden_marker_leaked": any(v in structured["text"] for v in ["HIDDEN", "SECRET"]),
               "financial_semantics_validated": False,
               "source_bound_locators": all(t["locator"]["snapshot_hash"] == digest and t["locator"]["dom_xpath"] for t in structured["tables"])}
    checks = {}
    if digest == R2_SHA:
        target = structured["tables"][0]
        checks = {"kind": "selected_real_source_row_and_header_fidelity",
                  "expected_net_sales": EXPECTED_NET_SALES,
                  "net_sales_row_preserved": EXPECTED_NET_SALES in target["rows"],
                  "period_header_preserved": target["column_headers"][1:] == [
                      ["3 Months Ended", "Mar. 29, 2025"], ["3 Months Ended", "Mar. 30, 2024"],
                      ["6 Months Ended", "Mar. 29, 2025"], ["6 Months Ended", "Mar. 30, 2024"]],
                  "flat_text_contains_same_numbers": all(v in flat_text for v in EXPECTED_NET_SALES),
                  "not_a_financial_semantic_accuracy_test": True}
    elif digest == PRIMARY_SHA:
        matching = [t for t in structured["tables"] if any(r and r[0] == "Total net sales" for r in t["rows"])]
        checks = {"kind": "primary_filing_numeric_string_crosscheck_against_R2_attachment",
                  "target_tables": [t["ordinal"] for t in matching],
                  "all_four_net_sales_strings_present_in_same_row": any(
                      all(v in row for v in ["95,359", "90,753", "219,659", "210,328"])
                      for t in matching for row in t["rows"] if row[0] == "Total net sales"),
                  "not_a_financial_semantic_accuracy_test": True}
    elif raw == COMPLEX.encode():
        target = structured["tables"][0]
        checks = {"kind": "synthetic_geometry_and_string_fidelity",
                  "expected_rows_preserved": target["rows"] == [
                      ["Revenue", "001,203", "N/A"], ["Revenue", "(52.3)", "10.5%"],
                      ["Gross margin", "70.6%", "71.0%"], ["Non-numeric placeholder", "—", "0"]],
                  "actual_vs_outlook_headers_separate": target["column_headers"][1:] == [["FY2025", "Actual Q1"], ["FY2025", "Outlook Q2"]],
                  "not_a_financial_semantic_accuracy_test": True}
    result = {"input": str(path.resolve()), "sha256": digest, "bytes": len(raw), "source": source,
              "baseline_FilingText": baseline, "lxml_pandas_adapter": summary, "checks": checks}
    if with_edgar:
        try:
            edgar = edgar_parse(text)
            dump(output_dir / (path.name + ".edgartools.json"), edgar)
            result["edgartools"] = {key: value for key, value in edgar.items() if key != "tables"}
            result["edgartools"]["table_count_including_hidden"] = len(edgar["tables"])
            if digest == R2_SHA:
                result["edgartools"]["net_sales_raw_row_preserved"] = any(EXPECTED_NET_SALES in t["rows"] for t in edgar["tables"])
        except Exception as exc:
            result["edgartools"] = {"status": "failed", "error": type(exc).__name__ + ": " + str(exc)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--complex-fixture", action="store_true")
    parser.add_argument("--include-edgartools", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = {r["name"]: r for r in json.loads(args.source_manifest.read_text())} if args.source_manifest else {}
    if args.complex_fixture:
        fixture = args.output_dir / "parser_probe_complex.html"
        fixture.write_text(COMPLEX, encoding="utf-8")
        args.input.append(fixture)
    if not args.input:
        parser.error("provide at least one --input or --complex-fixture")
    # EdgarTools initializes a cache on import even for local parsing. Keep the
    # entire comparison in its output area, not the user's home directory.
    os.environ["EDGAR_LOCAL_DATA_DIR"] = str((args.output_dir / "edgar_data").resolve())
    os.environ["EDGAR_CACHE_DIR"] = str((args.output_dir / "edgar_cache").resolve())
    with patch.object(socket.socket, "connect", side_effect=AssertionError("offline parser probe attempted network")) as connect:
        records = [compare(path, args.output_dir,
                           sources.get(path.name, {"download_url": "local-fixture://" + path.name,
                                                    "independent_test_gold": False}), args.include_edgartools)
                   for path in args.input]
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "offline_development_source_fidelity_probe",
              "independent_test_gold": False, "LLM_calls": 0, "network_connection_attempts": connect.call_count,
              "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
                              "libraries": {name: importlib.metadata.version(name) for name in ("lxml", "pandas")},
                              "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)},
              "timing_note": "one run per source; imports/OS caches affect timings; not a throughput benchmark",
              "records": records}
    dump(args.output_dir / "parser_probe_results.json", report)
    print(json.dumps({"output": str(args.output_dir.resolve()), "inputs": len(records),
                      "network_connection_attempts": connect.call_count,
                      "checks": [{"source": Path(r["input"]).name, **r["checks"]} for r in records]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
