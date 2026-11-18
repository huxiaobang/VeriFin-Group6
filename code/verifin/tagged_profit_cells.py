"""Read issuer-tagged monetary rows with dimensionless contexts in a consolidated P&L."""
from decimal import Decimal
import re

from .data import stable_id
from .financial import METRICS as GAAP
from .statement_amount_cells import nearby, text
from .structured_documents import _hidden


INCOME_TITLE = re.compile(r"(?:condensed )?consolidated (?:statements? of (?:income|earnings|operations)|"
                          r"results of operations|income statements?)(?:\s*\(unaudited\))?", re.I)


def _locator(tree, node):
    # In HTML, inline-XBRL names contain a literal colon with no XPath namespace binding.
    return re.sub(r"(?<=/)([a-z][\w.-]*:[a-z][\w.-]*)",
                  lambda m: "*[name()='" + m[1] + "']", tree.getpath(node))


def tagged_cells(doc):
    from lxml import html
    root = html.document_fromstring(doc["raw"], parser=html.HTMLParser(encoding="utf-8", no_network=True, remove_comments=True))
    tree = root.getroottree()
    document_id = stable_id("source_doc_", doc["url"])
    tags = {"us-gaap:"+tag: metric for metric in ("revenue","gross_profit","operating_income") for tag in GAAP[metric]}
    cik_values = {text(n).lstrip("0") for n in root.iter() if n.get("name") == "dei:EntityCentralIndexKey"}
    if len(cik_values) != 1:
        return
    contexts = {}
    for ctx in root.iter("xbrli:context"):
        parts = {n.tag: n for n in ctx.iter()}
        if ("xbrli:identifier" not in parts or text(parts["xbrli:identifier"]).lstrip("0") not in cik_values or
                "xbrli:segment" in parts or "xbrli:scenario" in parts or
                not {"xbrli:startdate", "xbrli:enddate"} <= parts.keys()):
            continue
        contexts[ctx.get("id")] = (text(parts["xbrli:startdate"]), text(parts["xbrli:enddate"]), _locator(tree, ctx))
    units = {u.get("id"): text(u[0])[8:] for u in root.iter("xbrli:unit")
             if len(u) == 1 and u[0].tag == "xbrli:measure" and re.fullmatch(r"iso4217:[A-Z]{3}", text(u[0]))}
    for table in root.xpath("//table[not(.//table)]"):
        title = next((n for n in nearby(table) if INCOME_TITLE.fullmatch(text(n))), None)
        if title is None:
            continue
        table_path = tree.getpath(table)
        for node in table.iter("ix:nonfraction"):
            metric = tags.get(node.get("name"))
            dates = contexts.get(node.get("contextref"))
            currency = units.get(node.get("unitref"))
            printed = text(node)
            if not (metric and dates and currency and re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", printed)):
                continue
            if (node.get("sign", "") not in ("", "-") or not re.fullmatch(r"-?\d{1,2}", node.get("scale", "0"))
                    or node.get("format", "").split(":")[-1].lower() not in ("", "num-dot-decimal", "numdotdecimal")
                    or any(_hidden(n) for n in [node, *node.iterancestors()])):
                continue
            scale = Decimal(10) ** int(node.get("scale", "0"))
            amount = Decimal(printed.replace(",", "")) * scale * (-1 if node.get("sign") == "-" else 1)
            tr = next(node.iterancestors("tr"), None)
            if tr is None:
                continue
            label = next((text(td) for td in tr if text(td)), "")
            locator = _locator(tree, node)
            yield {"id":stable_id("cell_", [document_id, doc["snapshot_hash"], locator]),
                   "kind":"financial_fact", "cell_type":"ixbrl_profit_cell", "entity":doc["entity"],
                   "document_id":document_id,"table_id":stable_id("table_", [document_id, table_path]),
                   "table_title":text(title), "table_xpath":table_path, "table_title_locator":tree.getpath(title),
                   "locator":locator, "row_label":label, "origin_context":node.get("contextref"),
                   "context_locator":dates[2], "period_start":dates[0],"period_end":dates[1],
                   "concept":node.get("name"), "metric":metric,"unit":currency,"currency":currency,
                   "basis":"US-GAAP","business_scope":"consolidated","value":int(amount) if amount == int(amount) else float(amount),
                   "printed_value":printed,"printed_scale":float(scale),"printed_decimals":node.get("decimals"),
                   "snapshot_path":doc["source"],"source_url":doc["url"],"snapshot_hash":doc["snapshot_hash"],
                   "published_at":doc["published_at"],"form":doc["form"],"text":f"{text(title)} | {label} | {dates[0]} to {dates[1]} | {printed} (×{scale} {currency})"}
