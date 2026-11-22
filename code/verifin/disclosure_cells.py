"""Attach source-addressed numeric records to text observations on supported replay documents."""
from io import BytesIO
import re

from .data import DataError


def _statement_cells(data, raw, doc, info, document_id, source_text, diagnostics):
    from lxml.etree import LxmlError
    from .statement_amount_cells import filed_boundaries, prior_currency_evidence, statement_cells
    try:
        prior_units = prior_currency_evidence(data, doc)
    except (ValueError, OSError, KeyError) as exc:
        # Optional corroboration is independent of the verified original.
        diagnostics["currency_reference_unavailable"] = str(exc)
        prior_units = {}
    try:
        boundaries = filed_boundaries(data, doc)
    except (ValueError, OSError, KeyError) as exc:
        diagnostics["period_reference_unavailable"] = str(exc)
        boundaries = {}
    try:
        return statement_cells(raw, doc, info, document_id, source_text, prior_units, boundaries,
                               companies=data.manifest["companies"])
    except LxmlError as exc:
        raise ValueError("Unsupported native income statement layout.") from exc


def candidates(data, cache, passage, doc, info, diagnostics):
    path = doc["source"]
    document_id = passage["document_id"]
    key = (document_id, info["sha256"], doc["published_at"], doc["entity"], doc.get("period_end"))
    text = passage["text"]
    from .statement_amount_cells import EXPENSE_NOUN
    if path.lower().endswith((".html", ".htm")) and (EXPENSE_NOUN.search(text) or re.search(
            r"\b(?:net sales|revenues?|operating (?:income|profit)|income from operations|gross (?:profit|margin))\b", text, re.I)):
        text_hash = data.info(doc["text"])["sha256"]
        statement_key = (*key, text_hash, "income_statement")
        if statement_key not in cache:
            rows = _statement_cells(data, data.raw(path), doc, info, document_id,
                                    data.raw(doc["text"]).decode("utf-8"), diagnostics)
            cells = [dict(row, text_snapshot_hash=text_hash) for row in rows]
            if "currency_reference_unavailable" not in diagnostics and "period_reference_unavailable" not in diagnostics:
                cache[statement_key] = cells
        else:
            cells = cache[statement_key]
        for cell in cells:
            if (passage["text_snapshot_hash"] == text_hash
                    and passage["start"] <= cell.get("source_context_start", cell["source_row_start"])
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
            markers = [(m.start(), int(m[1] or m[2])) for m in re.finditer(
                r"^(?:\[PDF page ([1-9]\d*)\]|=== page ([1-9]\d*) ===)\s*$", source_text, re.M)]
            cache[text_key] = source_text, markers
        source_text, markers = cache[text_key]
        inventory = re.search(r"\binventor(?:y|ies)\b|\bQ[1-4]\s*['’‘]\d{2}\b", text, re.I)
        from .platform_revenue import TITLE
        pages = {}
        for index, (start, page) in enumerate(markers):
            end = markers[index+1][0] if index+1 < len(markers) else len(source_text)
            if start < passage["end"] and end > passage["start"]:
                platforms = TITLE in source_text[start:end]
                kinds = (["inventory"] if inventory else []) + (["platform_revenue"] if platforms else [])
                for kind in kinds:
                    pages[(page, kind)] = start, end
        if not pages:
            return
        import pdfplumber
        from pdfminer.psexceptions import PSException
        from pdfplumber.utils.exceptions import PdfminerException
        from .chart_cells import framed_inventory_cells
        from .platform_revenue import cells as platform_cells
        from .statement_amount_cells import prior_currency_evidence
        missing = [(n, kind) for n, kind in pages if (*key, n, kind) not in cache]
        if missing:
            raw = data.raw(path)
            try:
                with pdfplumber.open(BytesIO(raw)) as pdf:
                    for n, kind in missing:
                        if n > len(pdf.pages):
                            raise ValueError("Text snapshot has an invalid physical page number.")
                        if kind == "inventory":
                            rows = framed_inventory_cells(pdf, n, doc, info, document_id)
                        else:
                            try:
                                witnesses = prior_currency_evidence(data, doc, ("revenue",))
                            except (ValueError, OSError, KeyError) as exc:
                                diagnostics["currency_reference_unavailable"] = str(exc)
                                continue
                            start, end = pages[(n, kind)]
                            rows = platform_cells(pdf.pages[n-1], n, doc, info, document_id,
                                                  source_text, start, end, witnesses)
                            rows = [dict(row, text_snapshot_hash=text_key[-2]) for row in rows]
                        cache[(*key, n, kind)] = rows
            except (PSException, PdfminerException) as exc:
                raise ValueError("Unsupported native PDF chart layout.") from exc
        for n, kind in pages:
            for cell in cache.get((*key, n, kind), []):
                if kind == "platform_revenue":
                    if (passage["text_snapshot_hash"] == cell["text_snapshot_hash"]
                            and passage["start"] <= cell["source_row_start"] <= cell["source_row_end"] <= passage["end"]):
                        yield cell
                else:
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
            for cell in candidates(data, cache, row, doc, data.info(doc["source"]), result["data"]):
                cells[cell["id"]] = cell
                passages.setdefault(cell["id"], []).append(row["id"])
        except ImportError as exc:
            result["data"]["numeric_extraction_unavailable"] = f"Missing optional parser: {exc.name}"
        except DataError:
            raise  # Snapshot integrity errors apply to the entire observation.
        except ValueError as exc:
            result["data"]["numeric_extraction_unavailable"] = str(exc)
    _attach_cells(result, cells, passages)


def attach_filing(result, data, cache, raw, source_text, info, passage):
    diagnostics = result["data"]
    document_id = passage["source_document_id"]
    key = (document_id, info["sha256"], passage["published_at"], passage["entity"], "filing_statement")
    doc = {"source": info["snapshot"], "text": passage["text_snapshot_path"], "entity": passage["entity"],
           "published_at": passage["published_at"], "form": passage["form"]}
    try:
        cells = cache.get(key)
        if cells is None:
            cells = _statement_cells(data, raw, doc, info, document_id, source_text, diagnostics)
            if "currency_reference_unavailable" not in diagnostics and "period_reference_unavailable" not in diagnostics:
                cache[key] = cells
    except ImportError as exc:
        diagnostics["numeric_extraction_unavailable"] = f"Missing optional parser: {exc.name}"
        return
    except ValueError as exc:
        diagnostics["numeric_extraction_unavailable"] = str(exc)
        return
    observed = {}
    for cell in cells:
        if diagnostics["start"] <= cell["source_row_start"] and cell["source_row_end"] <= diagnostics["end"]:
            observed[cell["id"]] = dict(cell, text_snapshot_hash=passage["text_snapshot_hash"],
                                       accession=passage["accession"], retrieved_at=passage["retrieved_at"])
    _attach_cells(result, observed, {key: [passage["id"]] for key in observed})


def _attach_cells(result, cells, passages):
    if cells:
        result["evidence"].extend(cells.values())
        result["data"]["numeric_operand_ids"] = list(cells)
        result["data"]["numeric_operand_passages"] = passages
        result["data"]["numeric_operand_status"] = "attached"
        result["summary"] += f" Attached {len(cells)} numeric cell IDs. Select by measure and period for calculate."
