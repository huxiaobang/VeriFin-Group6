"""Attach source-addressed numeric records to text observations on supported replay documents."""
from io import BytesIO
import re

from .data import DataError


def candidates(data, cache, passage, doc, info):
    path = doc["source"]
    document_id = passage["document_id"]
    key = (document_id, info["sha256"], doc["published_at"], doc["entity"], doc.get("period_end"))
    text = passage["text"]
    from .statement_amount_cells import EXPENSE_NOUN
    if path.lower().endswith((".html", ".htm")) and (EXPENSE_NOUN.search(text) or re.search(
            r"\b(?:net sales|revenues?|operating (?:income|profit)|income from operations|gross (?:profit|margin))\b", text, re.I)):
        from lxml.etree import LxmlError
        from .statement_amount_cells import prior_currency_evidence, statement_cells
        text_hash = data.info(doc["text"])["sha256"]
        statement_key = (*key, text_hash, "income_statement")
        if statement_key not in cache:
            try:
                rows = statement_cells(data.raw(path), doc, info, document_id,
                                       data.raw(doc["text"]).decode("utf-8"), prior_currency_evidence(data, doc))
            except LxmlError as exc:
                raise ValueError("Unsupported native income statement layout.") from exc
            cache[statement_key] = [dict(row, text_snapshot_hash=text_hash) for row in rows]
        for cell in cache[statement_key]:
            if (passage["text_snapshot_hash"] == text_hash and passage["start"] <= cell["source_row_start"]
                    and cell["source_row_end"] <= passage["end"]):
                yield cell
    if (path.lower().endswith((".html", ".htm")) and doc.get("form") in ("10-K", "10-Q")
            and isinstance(doc.get("period_end"), str) and "|" in text):
        from lxml.etree import LxmlError
        from .revenue_bridge import bridge_cells
        if key not in cache:
            raw = data.raw(path)
            try:
                cache[key] = bridge_cells(raw, doc, info, document_id)
            except LxmlError as exc:
                raise ValueError("Unsupported native HTML table layout.") from exc
        lines = [{ " ".join(field.casefold().split()) for field in line.split("|") }
                 for line in text.splitlines() if "|" in line]
        for cell in cache[key]:
            label = cell["row_label"].casefold()
            if any(label in line and cell["printed_value"].casefold() in line for line in lines):
                yield cell
    elif path.lower().endswith(".pdf"):
        # Page numbers come from the verified text snapshot; a chunk may begin mid-page.
        text_key = (*key, data.info(doc["text"])["sha256"], "pages")
        if text_key not in cache:
            source_text = data.raw(doc["text"]).decode("utf-8")
            cache[text_key] = [(m.start(), int(m[1])) for m in re.finditer(
                r"^\[PDF page ([1-9]\d*)\]\s*$", source_text, re.M)]
        markers = cache[text_key]
        pages = [page for i, (start, page) in enumerate(markers) if start < passage["end"]
                 and (i+1 == len(markers) or markers[i+1][0] > passage["start"])]
        if not pages or not re.search(r"\binventor(?:y|ies)\b|\bQ[1-4]\s*['’‘]\d{2}\b", text, re.I):
            return
        import pdfplumber
        from pdfminer.psexceptions import PSException
        from pdfplumber.utils.exceptions import PdfminerException
        from .chart_cells import framed_inventory_cells
        missing = [n for n in pages if (*key, n) not in cache]
        if missing:
            raw = data.raw(path)
            try:
                with pdfplumber.open(BytesIO(raw)) as pdf:
                    for n in missing:
                        if n > len(pdf.pages):
                            raise ValueError("Text snapshot has an invalid physical page number.")
                        cache[(*key, n)] = framed_inventory_cells(pdf, n, doc, info, document_id)
            except (PSException, PdfminerException) as exc:
                raise ValueError("Unsupported native PDF chart layout.") from exc
        for n in pages:
            for cell in cache[(*key, n)]:
                number = r"(?<![\d.,])" + re.escape(cell["printed_value"]) + r"(?![\d.,])"
                if cell["row_label"].casefold() in text.casefold() or (
                        cell["printed_period"] in text and re.search(number, text)):
                    yield cell


def attach(result, data, cache):
    docs = {d["source"]: d for d in data.manifest["documents"]}
    cells = {}
    passages = {}
    for row in result["evidence"]:
        doc = docs.get(row.get("snapshot_path"))
        if not doc or row.get("kind") != "document" or row.get("mode") != "replay":
            continue
        try:
            for cell in candidates(data, cache, row, doc, data.info(doc["source"])):
                cells[cell["id"]] = cell
                passages.setdefault(cell["id"], []).append(row["id"])
        except ImportError as exc:
            result["data"]["numeric_extraction_unavailable"] = f"Missing optional parser: {exc.name}"
        except DataError:
            raise  # Snapshot integrity errors apply to the entire observation.
        except ValueError as exc:
            result["data"]["numeric_extraction_unavailable"] = str(exc)
    if cells:
        result["evidence"].extend(cells.values())
        result["data"]["numeric_operand_ids"] = list(cells)
        result["data"]["numeric_operand_passages"] = passages
        result["summary"] += f" Attached {len(cells)} numeric cell IDs. Select by measure and period for calculate."
