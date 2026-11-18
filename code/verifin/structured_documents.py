"""Offline HTML structure adapter backed by lxml and pandas, not an LLM.

Optional dependencies are imported only on use. Raw bytes stay authoritative:
locators bind the source SHA256 and original DOM XPath, not invented byte spans.
Table parsing preserves strings and expands rowspan/colspan through pandas.
No extracted table is certified as a financial fact or semantically comparable.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import copy
import re
import warnings

from .data import DataError, stable_id


PARSER_VERSION = "lxml-pandas-structure-v1"
_BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
_HIDDEN_TAGS = {"script", "style", "noscript", "ix:hidden", "head"}


def _text(element):
    return " ".join(" ".join(element.itertext()).split())


def _hidden(element):
    tag = str(element.tag).lower()
    style = re.sub(r"\s+", "", element.get("style", "")).lower()
    return (tag in _HIDDEN_TAGS or "hidden" in element.attrib
            or element.get("aria-hidden", "").lower() == "true"
            or re.search(r"(?:^|;)(?:display:none|visibility:hidden)(?:;|!important|$)", style))


def _render_table(table):
    """Tab-separated evidence text; commas, brackets, percentages stay strings."""
    if table["status"] != "parsed":
        return "[Unresolved table: " + table["failure_reason"] + "]\n" + table.get("text_preview", "")
    lines = []
    if table.get("caption"):
        lines.append(table["caption"])
    if table["column_headers"]:
        lines.append("\t".join(" / ".join(dict.fromkeys(v for v in parts if v))
                                for parts in table["column_headers"]))
    lines.extend("\t".join(row) for row in table["rows"])
    return "\n".join(lines)


def parse_structured_html(raw, *, source_url, snapshot_path=None, document_id=None,
                          max_tables=500, max_rows=2000, max_columns=200,
                          max_cells=50_000, max_bytes=8_000_000):
    """Parse supplied bytes; no downloads, OCR, scripts, CSS or network requests.

    Nested/oversized/malformed tables remain explicit unresolved records. CSS
    class rules are not evaluated, so this is not browser-rendered visibility.
    Values remain source strings. Publication/issuer/period metadata must come
    from the existing filing adapter, not from table heuristics in this module.
    """
    if not isinstance(raw, (bytes, str)) or not raw:
        raise DataError("Structured HTML requires nonempty bytes or text")
    if not isinstance(source_url, str) or not source_url.strip():
        raise DataError("Structured HTML requires its source URL")
    for value in (max_tables, max_rows, max_columns, max_cells, max_bytes):
        if type(value) is not int or value < 1:
            raise DataError("Structured HTML limits must be positive integers")
    original = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(original) > max_bytes:
        raise DataError("Structured HTML exceeds the explicit byte limit")
    try:
        from lxml import etree, html
        import pandas as pd
    except ImportError as exc:
        raise DataError("Structured HTML requires optional lxml and pandas dependencies") from exc
    parser = html.HTMLParser(no_network=True, recover=True, remove_comments=True,
                             encoding="utf-8" if isinstance(raw, str) else None)
    try:
        root = html.document_fromstring(original, parser=parser)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
        raise DataError("HTML could not be parsed into a document") from exc
    if any("excessive" in entry.message.lower() for entry in parser.error_log):
        raise DataError("HTML parser resource limit reached; document was not silently truncated")
    tree = root.getroottree()
    paths = {element: tree.getpath(element) for element in root.iter()}
    sha = hashlib.sha256(original).hexdigest()
    document_id = document_id or stable_id("structured_doc_", [source_url, sha])
    original_fragments = {element: hashlib.sha256(etree.tostring(element, encoding="utf-8")).hexdigest()
                          for element in root.iter("table")}
    for element in list(root.iter()):
        if element is not root and _hidden(element) and element.getparent() is not None:
            # A tail belongs to the visible parent, not to the hidden element.
            element.drop_tree()
    table_nodes = list(root.iter("table"))
    if len(table_nodes) > max_tables:
        raise DataError("HTML table count exceeds the explicit limit")

    def locator(element):
        return {"source_url": source_url, "snapshot_hash": sha,
                "snapshot_path": str(snapshot_path) if snapshot_path is not None else None,
                "dom_xpath": paths[element], "source_line": element.sourceline,
                "locator_kind": "original_html_dom_xpath", "parser_version": PARSER_VERSION}

    tables, lookup = [], {}
    for ordinal, element in enumerate(table_nodes):
        identity = stable_id("table_", [document_id, sha, paths[element], PARSER_VERSION])
        record = {"id": identity, "kind": "table", "ordinal": ordinal,
                  "locator": locator(element), "html_fragment_sha256": original_fragments[element],
                  "fragment_hash_kind": "lxml_serialized_original_DOM",
                  "caption": " ".join(_text(x) for x in element.xpath("./caption")),
                  "status": "unresolved", "failure_reason": None,
                  "column_headers": [], "header_source_rows": [], "rows": [], "source_cells": [],
                  "financial_semantics_validated": False, "warnings": []}
        tables.append(record)
        lookup[element] = record
        try:
            if element.xpath(".//table"):
                raise DataError("nested_table_requires_separate_interpretation")
            rows = element.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
            if not rows or len(rows) > max_rows:
                raise DataError("missing_rows_or_row_limit_exceeded")
            cells = []
            for row_index, row in enumerate(rows):
                width = 0
                for cell_index, cell in enumerate(row.xpath("./th | ./td")):
                    spans = {}
                    for field in ("rowspan", "colspan"):
                        value = cell.get(field, "1")
                        if not re.fullmatch(r"[0-9]+", value):
                            raise DataError("invalid_" + field)
                        spans[field] = int(value)
                        if not 1 <= spans[field] <= (max_rows if field == "rowspan" else max_columns):
                            raise DataError("unsupported_or_excessive_" + field)
                    if row_index + spans["rowspan"] > len(rows):
                        raise DataError("rowspan_extends_past_table")
                    width += spans["colspan"]
                    cells.append({"source_row": row_index, "source_cell": cell_index,
                                  "tag": cell.tag, "text": _text(cell), **spans,
                                  "dom_xpath": paths[cell], "source_line": cell.sourceline})
                if width > max_columns:
                    raise DataError("column_limit_exceeded")
            record["source_cells"] = cells
            if not any(c["text"] for c in cells):
                raise DataError("empty_layout_table")
            # Bound expanded grid before pandas allocates it, including spans.
            if sum(c["rowspan"] * c["colspan"] for c in cells) > max_cells:
                raise DataError("expanded_cell_limit_exceeded")
            header_rows = []
            leading_headers = True
            for row_index, row in enumerate(rows):
                row_cells = row.xpath("./th | ./td")
                declared = str(row.getparent().tag).lower() == "thead"
                all_th = bool(row_cells) and all(str(c.tag).lower() == "th" for c in row_cells)
                if declared or (leading_headers and all_th):
                    header_rows.append(row_index)
                elif row_cells:
                    leading_headers = False
            # Let pandas expand the geometry, but read every source header as
            # data first. Otherwise pandas invents suffixes for duplicate
            # headers (e.g. FY2025.1), which are not source evidence.
            parse_table = copy.deepcopy(element)
            for node in parse_table.iter():
                if node.tag == "th":
                    node.tag = "td"
                elif node.tag in {"thead", "tfoot"}:
                    node.tag = "tbody"
            fragment = etree.tostring(parse_table, encoding="unicode", method="html")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                # read_html has no dtype=str option. First inspect the expanded
                # shape, then convert every actual column to strings. Giving
                # converters for nonexistent columns raises IndexError in
                # pandas; inferring values from the first frame loses zeros.
                options = dict(flavor="lxml", header=None, keep_default_na=False,
                               thousands=None, displayed_only=True)
                frames = pd.read_html(io.StringIO(fragment), **options)
                if len(frames) != 1:
                    raise DataError("ambiguous_table_count")
                shape = frames[0].shape
                if shape[0] > max_rows or shape[1] > max_columns or frames[0].size > max_cells:
                    raise DataError("expanded_grid_limit_exceeded")
                frames = pd.read_html(io.StringIO(fragment), **options,
                                      converters={i: str for i in range(shape[1])})
            if len(frames) != 1:
                raise DataError("ambiguous_table_count")
            frame = frames[0]
            if frame.shape[0] > max_rows or frame.shape[1] > max_columns or frame.size > max_cells:
                raise DataError("expanded_grid_limit_exceeded")
            grid = [[str(value) for value in row] for row in frame.itertuples(index=False, name=None)]
            if len(grid) != len(rows):
                raise DataError("source_row_alignment_changed")
            record.update(status="parsed", failure_reason=None,
                          header_source_rows=header_rows,
                          column_headers=[[grid[r][c] for r in header_rows] for c in range(frame.shape[1])] if header_rows else [],
                          rows=[row for i, row in enumerate(grid) if i not in header_rows])
            record["warnings"] = sorted({type(w.message).__name__ for w in caught})
        except (ValueError, TypeError, IndexError, KeyError, OverflowError) as exc:
            record["failure_reason"] = str(exc) if isinstance(exc, DataError) else "parser_" + type(exc).__name__
            record["text_preview"] = _text(element)[:2000]
        record["text"] = _render_table(record)

    blocks = []

    def append(element, text, kind="paragraph", suffix=""):
        if text.strip():
            blocks.append({"id": stable_id("block_", [document_id, sha, paths[element], suffix, PARSER_VERSION]),
                           "kind": kind, "text": text, "locator": {**locator(element), "text_node": suffix or None}})

    def walk(element):
        tag = str(element.tag).lower()
        if tag == "table":
            table = lookup[element]
            blocks.append({"id": table["id"], "kind": "table", "table_id": table["id"],
                           "text": table["text"], "status": table["status"], "locator": table["locator"]})
            return
        if (tag in _BLOCK_TAGS and not element.xpath(".//table")) or (tag == "div" and not element.xpath(".//div | .//table | .//p | .//li | .//h1 | .//h2 | .//h3")):
            append(element, _text(element), "heading" if tag.startswith("h") else "paragraph")
            return
        if element.text and element.text.strip():
            append(element, " ".join(element.text.split()), suffix="text()[1]")
        for child in element:
            walk(child)
            if child.tail and child.tail.strip():
                append(child, " ".join(child.tail.split()), suffix="following-sibling::text()[1]")

    walk(root)
    extracted_text = "\n\n".join(block["text"] for block in blocks)
    text_digest = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
    cursor = 0
    for block in blocks:
        block["text_start"] = cursor
        block["text_end"] = cursor + len(block["text"])
        block["text_snapshot_hash"] = text_digest
        cursor = block["text_end"] + 2
    return {"schema_version": 1, "document_id": document_id, "source_url": source_url,
            "snapshot_hash": sha, "parser_version": PARSER_VERSION,
            "text_snapshot_hash": text_digest,
            "text_offset_unit": "unicode_codepoints_in_structured_text",
            "libraries": {name: importlib.metadata.version(name) for name in ("lxml", "pandas")},
            "blocks": blocks, "tables": tables,
            "text": extracted_text,
            "status": "partial" if any(t["status"] != "parsed" for t in tables) else "parsed",
            "warnings": ["No CSS class evaluation or browser rendering; no financial semantics inferred"],
            "network_requests": 0}


def table_chunks(document, *, max_rows=12):
    """Structure-preserving evidence chunks with repeated source headers.

    Row-count target is explicit, not a tokenizer bound. A table without source
    header markup is kept whole rather than guessing financial period headers.
    No long row is truncated;
    the existing encoder must reject overlong input or a later token-aware stage
    must split it with source mapping. This intentionally does not modify Corpus.
    """
    if type(max_rows) is not int or max_rows < 1:
        raise DataError("Table chunk row count must be a positive integer")
    chunks = []
    for table in document["tables"]:
        if table["status"] != "parsed":
            continue
        # SEC tables often encode their header in <td>. Splitting these without
        # a verified header boundary would detach values from their periods.
        step = max_rows if table["column_headers"] else max(1, len(table["rows"]))
        for start in range(0, len(table["rows"]), step):
            stop = min(len(table["rows"]), start + step)
            text = _render_table({**table, "rows": table["rows"][start:stop]})
            chunk = {"id": stable_id("table_chunk_", [table["id"], start, stop]),
                     "table_id": table["id"], "row_start": start, "row_end": stop,
                     "column_headers": table["column_headers"], "locator": table["locator"],
                     "chunking_status": "header_repeated" if table["column_headers"] else "whole_table_no_declared_headers",
                     "row_count_exceeds_target": stop - start > max_rows,
                     "text_is_exact_document_slice": any(b["id"] == table["id"] and b["text"] == text for b in document["blocks"]),
                     "text": text}
            chunks.append(chunk)
    return chunks


def main(argv=None):
    """Inspectable offline CLI; new extraction files never replace old spans."""
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Extract traceable HTML blocks and tables without network or model calls")
    parser.add_argument("input", type=Path, help="Existing original HTML snapshot")
    parser.add_argument("--source-url", required=True, help="Original source URL; recorded, not fetched")
    parser.add_argument("--output-dir", type=Path, required=True, help="New output directory")
    parser.add_argument("--rows-per-chunk", type=int, default=12)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("Use a new output directory; existing evidence is not overwritten")
    try:
        result = parse_structured_html(args.input.read_bytes(), source_url=args.source_url,
                                       snapshot_path=args.input.resolve())
        chunks = table_chunks(result, max_rows=args.rows_per_chunk)
        args.output_dir.mkdir(parents=True)
        (args.output_dir / "document.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (args.output_dir / "table-chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # No appended newline: bytes must match the document's text_snapshot_hash.
        (args.output_dir / "text.txt").write_text(result["text"], encoding="utf-8")
        print(json.dumps({"status": result["status"], "blocks": len(result["blocks"]),
                          "tables": len(result["tables"]), "parsed_tables": sum(t["status"] == "parsed" for t in result["tables"]),
                          "text_snapshot_hash": result["text_snapshot_hash"], "output_dir": str(args.output_dir)}))
        return 0
    except (OSError, DataError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
