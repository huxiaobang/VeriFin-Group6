"""Find attributable management commentary for a quarter's material earnings changes."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from .cashflow_skill import DATE_PART, MONTH_NAMES
from .data import available_by
from .sales_comparison import _labels, _source


def targets(bridge, calculations):
    """Use the signed, reconciled component changes and reported subtotals of this quarter."""
    if not bridge or not bridge.get("complete"):
        return []
    records = calculations.values() if isinstance(calculations, dict) else calculations
    amounts = {r["id"]: Decimal(str(r["value"])) for r in records
               if not r.get("stale") and isinstance(r.get("value"), (int, float, Decimal))}
    rows = bridge.get("rows", [])
    if any(r.get("contribution_id") not in amounts for r in rows):
        return []
    components = [(r, amounts[r["contribution_id"]]) for r in rows if r["role"] in {"operating", "component"}]
    total = amounts.get(bridge.get("total_id"))
    if total is None or not components or sum(v for _, v in components) != total:
        return []
    selected = []

    def largest(items):
        if items:
            row, _ = max(items, key=lambda item: abs(item[1]))
            if row not in selected:
                selected.append(row)

    # A subtotal reversal has an observable meaning independent of the order in
    # which individual expenses are printed within a block.
    previous = None
    block = []
    for row in rows:
        value = amounts[row["contribution_id"]]
        if row["role"] == "component":
            block.append((row, value))
        else:
            if previous is not None and value * previous < 0:
                largest([item for item in block if item[1] * value > 0])
            previous, block = value, []
    largest([item for item in components if item[1] * total > 0])
    if not selected or len(selected) == 1 and selected[0]["role"] == "operating":
        largest([item for item in components if item[1] * total < 0] if total else components)
    return [{key: row[key] for key in ("metric", "label", "display_label", "role", "contribution_id")}
            for row in selected]


_TERMS = {
    "income_tax": r"(?:effective (?:income )?tax rate|income tax(?:es| (?:rate|expense|benefit|provision))?|provision for (?:income )?taxes|tax expense|tax provision)",
    "interest_expense": r"(?:interest (?:expense|costs?)|net interest expense)",
    "interest_income": r"interest income",
    "operating_income": r"(?:operating (?:income|profit|loss|earnings)|(?:income|loss|earnings) from operations)",
    "equity_income": r"(?:equity in (?:earnings|loss)|(?:income|earnings|loss) from (?:equity|unconsolidated))",
}
_QUARTER = re.compile(r"\b(?:three[ -]months?|13[ -]weeks?|(?:first|second|third|fourth|fiscal|this|current)[ -](?:fiscal )?quarter|quarter ended|Q[1-4])\b", re.I)
_LONGER = re.compile(r"\b(?:six|nine|twelve|6|9|12)[ -]months?|\b(?:full[ -](?:fiscal[ -])?year|year[ -]to[ -]date|first (?:half|six|nine)|(?:fiscal )?year ended|annual results?|(?:26|39|52|53) weeks?)\b", re.I)
_FORECAST = re.compile(r"\b(?:outlook|guidance|forecast|anticipat\w*|projected|expects?|expectations?|may|might|could|will|would)\b", re.I)
_SEGMENT = re.compile(r"\b(?:results by segment|segment results?|segment performance|segment highlights|highlights by segment)\b", re.I)
_CHANGE = re.compile(r"\b(?:increas\w*|decreas\w*|declin\w*|reduc\w*|ris\w*|rose|fell|compar\w*|chang\w*|higher|lower|improv\w*|growth|grew|from|shrank|jump\w*)\b", re.I)
_CAUSE = re.compile(r"\b(?:due (?:mainly |primarily )?to|attribut(?:able|ed) (?:primarily |mainly )?to|(?:driven|impacted|affected|caused|influenced) (?:primarily |mainly )?by|because(?: of)?|as a result of|reflect(?:ed|ing|s)?|result(?:ed|ing) from|related to|owing to)\b", re.I)
_PRIOR = re.compile(r"\b(?:the )?(?:prior|previous) (?:year|period)|last year", re.I)
_PERIOD_PAIR = re.compile(r"\bcompar(?:ed|ison)|\b(?:versus|vs\.?|year[ -]over[ -]year)\b", re.I)
_OTHER_SUBJECT = re.compile(r"\b(?:operating (?:income|margin|expenses?)|(?:net |other |interest )income|interest expense|effective tax rate|income tax expense|net sales|gross (?:profit|margin))\b",re.I)
_ANAPHORA = re.compile(r"^(?:(?:this|that|the|such) (?:increase|decrease|decline|growth|improvement|change|difference)|(?:the )?(?:prior|previous) year (?:included|benefited)|(?:this|that|it) (?:was|reflect))\b", re.I)
_ORDINAL = re.compile(r"\b(first|second|third|fourth)[ -](?:fiscal )?quarter\b", re.I)
_FULL_YEAR_HEADING = re.compile(r"^\W*(?:fiscal year|annual)\b.{0,45}\b(?:summary|results|highlights|compared|discussion)\b", re.I)
_PROJECTION_HEADING = re.compile(r"^\W*(?:[\w -]{0,45}\s)?(?:outlook|guidance|forecast)(?:\s+(?:for |FY|fiscal |year |\d|Q[1-4]|\s)*)?[:.]?$", re.I)
_COMPONENT_TERMS = (
    ("gross margin", "毛利率", r"gross (?:profit )?margin"),
    ("SG&A", "销售及管理费用", r"(?:selling,? general(?:,? and| &) administrative (?:costs?|expenses?)|SG&A(?: expenses?)?)"),
    ("cost of sales", "销售成本", r"cost of (?:goods sold|sales|revenues?)"),
    ("selling costs", "销售费用", r"selling expenses?|advertising and sales promotion expenses?"),
)


def _forecast(text):
    return _FORECAST.search(DATE_PART.sub(lambda m: "" if m[1].lower() in MONTH_NAMES else m[0], text))


def _term(target):
    pattern = _TERMS.get(target["metric"])
    if pattern is None:
        # Keep the specific noun phrase, removing display-only suffixes.
        label = re.split(r",|\(|\bnet of\b", target["label"].lower(), maxsplit=1)[0].strip()
        if len(label) < 4 or label in {"other", "net", "expense", "income"}:
            return None
        pattern = re.escape(label)
    return re.compile(r"\b(?:" + pattern + r")\b", re.I)


@dataclass(frozen=True)
class Anchor:
    text: str
    start: int
    end: int
    strength: int


def _dated(text):
    for m in DATE_PART.finditer(text):
        month = MONTH_NAMES.get(m[1].lower())
        if month and m[3]:
            yield f"{m[3]}-{month:02d}-{int(m[2]):02d}"
    yield from re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)


def _period_anchor(sentence, start, current, prior, fiscal_label, ordinal):
    """Return None to inherit, False to reset, or a usable period witness."""
    if (_LONGER.search(sentence) or _SEGMENT.search(sentence) or _FULL_YEAR_HEADING.search(sentence)
            or _PROJECTION_HEADING.fullmatch(sentence)):
        return False
    if _forecast(sentence):
        # A forward-looking remark is ineligible. Within a retrospective
        # paragraph it does not establish a different historical quarter.
        return None
    if not _QUARTER.search(sentence):
        return None
    dates = list(_dated(sentence))
    if dates:
        return Anchor(sentence, start, start+len(sentence), 3) if dates[0] == current else False
    named = _ORDINAL.search(sentence)
    if ordinal and named and named[1].lower() != ordinal:
        return False
    labels = _labels(sentence)
    if labels and fiscal_label and labels[0] != fiscal_label:
        # A current-quarter heading may name only the prior fiscal year in parentheses.
        if not (labels[0] == prior and _PERIOD_PAIR.search(sentence)):
            return False
    if re.search(r"\b(?:prior|previous|last) quarter\b", sentence, re.I):
        return False
    if (ordinal and named and re.search(r'^[\W]*(?:our|the company.s)\s+', sentence, re.I)
            and re.search(r"\b(?:results|performance)\s+(?:reflect|include|were|was)", sentence, re.I)):
        return Anchor(sentence,start,start+len(sentence),2)
    # A quarter-specific results/summary/comparison heading, not a reference
    # to some quarter in running prose, can open a new current-period section.
    if (len(sentence) <= 220 and not sentence.rstrip().endswith((".", "?", "!"))
            and re.search(r"results?|summary|compar|highlights|discussion", sentence, re.I)):
        return Anchor(sentence, start, start+len(sentence), 2)
    return None


def _fiscal_label(text):
    # Earnings release title or introductory reporting sentence anchors labels in this document.
    for line in text[:1300].splitlines():
        if re.search(r"report(?:s|ed)?|announc\w*|results|summary",line,re.I):
            labels = _labels(line)
            if labels:
                return labels[0]
    return None


def _sentences(text):
    """SEC visible-text preserves source lines; retain exact offsets in those lines."""
    for line in re.finditer(r"[^\r\n]+", text):
        # A bullet may also occur mid-line in exhibits without paragraph breaks.
        for sentence in re.finditer(r"[^•]+?(?:[.!?](?=\s+[A-Z“\"•]|\s*$)|(?=•)|$)", line[0]):
            raw = sentence[0]
            phrase = raw.strip()
            if phrase:
                start = line.start()+sentence.start()+len(raw)-len(raw.lstrip())
                yield phrase, start, start+len(phrase), line.start()


def _direct(sentence, term):
    subject = term.search(sentence)
    cause = _CAUSE.search(sentence)
    if not subject or not cause or cause.start() < subject.end() or not _CHANGE.search(sentence):
        return False
    if re.search(r"\badjusted\b",sentence[:subject.end()],re.I):
        return False
    # A causal connector about a second metric is not evidence about this row.
    between = sentence[subject.end():cause.start()]
    if _OTHER_SUBJECT.search(between):
        return False
    if re.search(r"\b(?:not|never)\s+(?:primarily\s+)?$", sentence[:cause.start()], re.I):
        return False
    body=sentence[cause.end():].strip(" .,:;")
    return len(body.split()) >= 2 and not re.match(r"^(?:an? |the )?(?:increase|decrease|change) in (?:that|this) (?:item|amount)$",body,re.I)


def _scoped_sentences(record, current):
    text = record["text"]
    label = _fiscal_label(text) if not record.get("start") else None
    prior_label = str(int(label[:4])-1)+label[4:] if label else None
    title_ords = {match[1].lower() for line in text[:1600].splitlines()
                  if re.search(r"reports?|announces?", line, re.I)
                  for match in _ORDINAL.finditer(line)} if not record.get("start") else set()
    ordinal = next(iter(title_ords)) if len(title_ords)==1 else None
    anchor = None
    for sentence, start, end, line in _sentences(text):
        marker = _period_anchor(sentence,start,current,prior_label,label,ordinal)
        if marker is not None:
            anchor = marker or None
        if (not anchor or start-anchor.end>3500 or _forecast(sentence)
                or _LONGER.search(sentence) or _SEGMENT.search(sentence)
                or re.search(r"\b(?:sequential(?:ly)?|preceding quarter)\b",sentence,re.I)):
            yield sentence,start,end,line,None
        else:
            yield sentence,start,end,line,anchor


def _candidates(record, target, current, prior_end):
    text = record["text"]
    term = _term(target)
    if not term:
        return
    previous = None
    for sentence, start, end, line, anchor in _scoped_sentences(record,current):
        if anchor is None:
            previous = None
            continue
        linked = (previous and (line==previous[3] or start-previous[2] <= 2)
                  and previous[4]==anchor and term.search(previous[0]) and _CHANGE.search(previous[0]))
        direct = _direct(sentence,term)
        anaphoric = (linked and _ANAPHORA.search(sentence) and _CAUSE.search(sentence)
                     and not any(not term.fullmatch(m[0]) for m in _OTHER_SUBJECT.finditer(sentence[:_CAUSE.search(sentence).start()])))
        if direct or anaphoric:
            # Reattach a directly preceding metric sentence only inside one paragraph.
            begin = previous[1] if linked and (anaphoric or line==previous[3]) else start
            excerpt = text[begin:end]
            has_comparison = _PERIOD_PAIR.search(anchor.text) or _PRIOR.search(excerpt) or _PERIOD_PAIR.search(excerpt)
            named_comparison = list(_dated(excerpt))
            # A 'from A to B' with two figures in a quarter's results heading is a comparison.
            if not has_comparison and term.search(excerpt) and re.search(r"\bfrom\b",excerpt,re.I):
                has_comparison = len(re.findall(r"\d+(?:\.\d+)?",excerpt))>=2
            if has_comparison and (not named_comparison or all(d in {current,prior_end} for d in named_comparison)) and len(excerpt)<=750:
                yield (anchor.strength, int(bool(re.search(r"\d",excerpt))), int(direct), -len(excerpt)), begin, end, anchor
        previous = (sentence,start,end,line,anchor)


def _component_candidates(record, subterm, current):
    """A causal sentence about an operating input supports that input only."""
    term = re.compile(r"\b(?:"+subterm+r")\b",re.I)
    consequence = re.compile(r"\b(?:pressured|reduced|lowered|raised|hurt|benefited|lifted|boosted|improved|increased)\s+(?:(?:our|the|its)\s+)?"+term.pattern,re.I)
    for sentence, start, end, line, anchor in _scoped_sentences(record,current):
        if not anchor or len(sentence)>600 or not term.search(sentence):
            continue
        effect = consequence.search(sentence)
        if not _direct(sentence,term) and not (effect and len(sentence[:effect.start()].split())>=2):
            continue
        yield (anchor.strength,int(bool(_PERIOD_PAIR.search(anchor.text))),0,-len(sentence)),start,end,anchor


def _eligible(record, entity, cutoff, period):
    return (record.get("entity")==entity and record.get("document_report_date")==period
            and record.get("published_at") and available_by(record["published_at"],cutoff)
            and (record.get("form") or "").startswith(("10-Q","10-K","8-K"))
            and not record.get("stale") and record.get("snapshot_hash") and record.get("text_snapshot_hash")
            and record.get("text_snapshot_path") and record.get("text"))


def inspect(corpus, entity, cutoff, bridge, calculations, *, existing_evidence=(), search=True):
    selected = targets(bridge, calculations)
    current, prior = bridge.get("current_period",{}), bridge.get("prior_period",{})
    end = current.get("end")
    existing = [r for r in existing_evidence if r.get("kind")=="document"
                and _eligible(r,entity,cutoff,end) and r.get("start") is not None]
    documents = []
    found, context, new_sources = {}, {}, {}

    def collect(records, remaining, *, saved, subcomponent=None):
        for target in remaining:
            matches = []
            for record in records:
                stream = (_component_candidates(record,subcomponent[2],end) if subcomponent
                          else _candidates(record,target,end,prior.get("end")))
                for score, lo, hi, anchor in stream:
                    matches.append((score, record,lo,hi,anchor))
            if not matches:
                continue
            _, record, lo, hi, anchor = max(matches,key=lambda r:r[0])
            offset = record["start"] if saved else 0
            evidence = record if saved else _source(record,lo,hi)
            if not saved:
                new_sources[evidence["id"]]=evidence
            anchor_path = record["text_snapshot_path"]
            finding = {**target,"evidence_id":evidence["id"],
                "quote":record["text"][lo:hi], "quote_start":lo+offset, "quote_end":hi+offset,
                "period_anchor":anchor.text,
                "period_anchor_locator":f"{anchor_path}:characters[{offset+anchor.start}:{offset+anchor.end}]",
                "period_anchor_start":offset+anchor.start,"period_anchor_end":offset+anchor.end,
                "allocation":"qualitative_only","support_kind":"direct"}
            if subcomponent:
                finding.update(support_kind="component_context", subject=subcomponent[0], subject_label=subcomponent[1])
                context[subcomponent[0]] = finding
            else:
                found[target["metric"]] = finding
    collect(existing,selected,saved=True)
    missing = [t for t in selected if t["metric"] not in found]
    if search and missing:
        documents = [doc for doc in corpus.documents if _eligible(doc, entity, cutoff, end)]
        collect(documents, missing, saved=False)
    unresolved = [t for t in selected if t["metric"] not in found]
    operating = [t for t in unresolved if t["metric"]=="operating_income"]
    if operating:
        for part in _COMPONENT_TERMS:
            collect(existing,operating,saved=True,subcomponent=part)
            if part[0] not in context:
                collect(documents,operating,saved=False,subcomponent=part)
            if len(context)==2:
                break
    findings = [*found.values(),*context.values()]
    result = {"targets":selected,"findings":findings,
              "gaps":[{**t,"gap_kind":"direct_cause_missing"} for t in unresolved],
              "current_period":current,"prior_period":prior,"partial_allocation":False,
              "documents_examined":len(documents),"existing_spans_examined":len(existing),
              "matched_passages":len({(f["evidence_id"],f["quote_start"],f["quote_end"]) for f in findings}),
              "new_passages":len(new_sources)}
    return {"ok":True,"summary":"已检查本季变动对应的管理层说明。","evidence":list(new_sources.values()),
            "calculations":[],"data":{"income_explanation":result}}
