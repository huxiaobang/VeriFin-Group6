"""Tie a segment disclosure to a separately reported consolidated amount for the same quarter."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re

from .skill_routing import TOPICS
from .revenue_bridge import dom, layout, nearby, numeric
from .statement_amount_cells import nearby as statement_headings, text

SALES = re.compile(r"(?:total |net )?(?:sales|revenues?)", re.I)
PROFIT = re.compile(r"(?:operating (?:income|earnings|profit)(?:\s*\(loss\))?|income from operations)", re.I)
GROUP = re.compile(r"(?:condensed\s+)?consolidated statements? of (?:earnings|operations|income)", re.I)
QUARTER = re.compile(r"(?:three months(?:\s*\((?:13|thirteen) weeks\))?|thirteen weeks|13 weeks|(?:fiscal )?quarter) ended\s*(.*)", re.I)
SEGMENT = re.compile(r"\bsegment\b", re.I)
TOTAL = re.compile(r"(?:consolidated\s+)?totals?(?:\s+(?:net )?(?:sales|revenues?|operating (?:profit|income|earnings)))?", re.I)
ADJUSTMENT = re.compile(r"unallocated|corporate|eliminat|intersegment|reconcil|contingent consideration|accounting adjustment", re.I)
LABELS = {"revenue":"收入", "operating_income":"营业利润"}


@dataclass(frozen=True)
class Amount:
    name: str
    value: Decimal
    path: str


def _metric(label):
    clean = label.strip(' :')
    if SALES.fullmatch(clean):
        return 'revenue'
    if PROFIT.fullmatch(clean):
        return 'operating_income'


def _requested(question):
    sales = re.search(r'收入|营收|销售|\b(?:sales|revenue)', question, re.I)
    profit = re.search(r'利润|盈利|营业收益|\b(?:operating (?:earnings|income|profit))', question, re.I)
    return (['revenue'] if sales else []) + (['operating_income'] if profit else []) or list(LABELS)


def _date(label):
    label = ' '.join(label.replace(',', ' ').split())
    for pattern in ('%B %d %Y','%b %d %Y','%Y-%m-%d'):
        try:
            return datetime.strptime(label, pattern).date().isoformat()
        except ValueError:
            continue


def _quarter_column(rows, end):
    """Resolve the overlap of quarter and date headers, including quarter/YTD side by side."""
    for idx,row in enumerate(rows[:6]):
        for span,label,_ in row:
            match = QUARTER.fullmatch(label.strip())
            if not match or idx + 1 >= len(rows):
                continue
            prefix = match[1].strip()
            following = rows[idx+1]
            for child, heading, _ in following:
                if child.start not in span or not heading:
                    continue
                if _date(' '.join([prefix, heading])) == end:
                    yield child


def _amount(row, span, name):
    fields = [(s,t,n) for s,t,n in row if s.start < span.stop and s.stop > span.start and t and t not in ('$','USD')]
    if not fields:
        return None
    number_span,token,node = fields[0]
    if token.startswith('(') and not token.endswith(')'):
        # Some SEC HTML puts the closing parenthesis in an immediately adjacent formatting cell.
        if not any(s.start == number_span.stop and t == ')' for s,t,_ in row):
            return None
        token += ')'
        if len(fields) == 2 and fields[1][1] == ')':
            fields = fields[:1]
    if len(fields) != 1:
        return None
    # The printed dash occupies an amount cell. An empty cell is never a zero.
    val = Decimal(0) if token in ('—','–') else numeric(token.strip('$').strip())
    if val is None:
        return None
    # HTML parsers retain inline-XBRL prefixes as literal names, without registering XML namespaces.
    path = re.sub(r'/([\w-]+:[\w-]+)(?=/|\[|$)',r"/*[name()='\1']",node.getroottree().getpath(node))
    return Amount(name,val,path)


def _title_unit(table,rows,doc):
    parts = [w for _,w in nearby(table)] + [text(n) for n in statement_headings(table)]
    title = ' '.join(dict.fromkeys(parts))
    scales = set(re.findall(r'\bin (thousands|millions|billions)\b', title.lower()))
    codes = set(re.findall(r'\b(USD|CAD|EUR|GBP|AUD)\b',title))
    if not codes and re.search(r'(?:10-[QK]|8-K)\b',doc.get('source_form',''),re.I) and any(
            token == '$' for r in rows for _,token,_ in r):
        # Retain the source's currency mark; domicile alone does not establish USD.
        codes = {'$'}
    return title, (f"{next(iter(codes))} {next(iter(scales))}" if len(scales)==len(codes)==1 else None)


def _vertical(rows,col):
    blocks = {}
    metric = None
    for row in rows:
        labels = [(s,t) for s,t,_ in row if t]
        if not labels or labels[0][0].start != 0:
            continue
        label = labels[0][1]
        if len(labels)==1 and _metric(label):
            metric = _metric(label)
            blocks[metric] = []
        elif metric:
            if TOTAL.fullmatch(label):
                yield metric, blocks[metric], _amount(row,col,label)
                metric = None
            else:
                blocks[metric].append((label,_amount(row,col,label)))


def _horizontal_headers(rows, end):
    """Bind business columns to one printed quarterly date, whether on one line or two."""
    for idx,row in enumerate(rows[:5]):
        periods = [(span,match[1].strip()) for span,label,_ in row if (match:=QUARTER.fullmatch(label))]
        for span,printed_date in periods:
            next_row = idx + 1
            if not printed_date and next_row < len(rows):
                matches = [(s,label) for s,label,_ in rows[next_row]
                           if span.start <= s.start and s.stop <= span.stop and _date(label) == end]
                if len(matches) != 1:
                    continue
                span,printed_date = matches[0]
                next_row += 1
            if _date(printed_date) != end or next_row >= len(rows):
                continue
            cols = [(s,label) for s,label,_ in rows[next_row]
                    if label and s.start > 0 and span.start <= s.start and s.stop <= span.stop]
            if len(cols) >= 3 and TOTAL.fullmatch(cols[-1][1]):
                yield cols, rows[next_row+1:]


_FOOTER_TITLE = re.compile(r'reconciliation of (?:segment )?(?:net )?(?:sales|revenues?)|(?:sales|revenue) reconciliation', re.I)
_GROSS_SALES = re.compile(r'total (?:segment )?(?:sales|revenues?)', re.I)
_EXTERNAL_SALES = re.compile(r'(?:net )?(?:sales|revenues?) to (?:external|unaffiliated) customers|'
                             r'(?:total )?external (?:sales|revenues?)|consolidated (?:net )?(?:sales|revenues?)', re.I)


def _sales_footer(rows, cols):
    """The segment amounts share a row. Group-only adjustments follow it in the total column."""
    labelled = [(row[0][1].strip(' :'), row) for row in rows if row and row[0][0].start == 0 and row[0][1]]
    segment_cols, (total_col, _) = cols[:-1], cols[-1]
    for idx,(label,_) in enumerate(labelled):
        if not idx or not _FOOTER_TITLE.fullmatch(label):
            continue
        gross_label,gross_row = labelled[idx-1]
        if not _GROSS_SALES.fullmatch(gross_label):
            continue
        items = [(name,_amount(gross_row,col,name)) for col,name in segment_cols]
        subtotal = _amount(gross_row,total_col,gross_label)
        if not subtotal or any(cell is None for _,cell in items) or sum(cell.value for _,cell in items)!=subtotal.value:
            continue
        adjustments = []
        for name,row in labelled[idx+1:]:
            # A label alone cannot place a value in the group column. Any populated business column
            # starts a different section or represents a business-specific amount.
            if any(token for s,token,_ in row for col,_ in segment_cols if s.start < col.stop and s.stop > col.start):
                break
            amount = _amount(row,total_col,name)
            if not amount:
                break
            if _EXTERNAL_SALES.fullmatch(name) and adjustments:
                yield 'revenue', items + adjustments, amount
                break
            if not (re.search(r'eliminat',name,re.I) and re.search(r'intercompany|intersegment|intra[- ]?group',name,re.I)
                    and re.search(r'\b(?:sales|revenues?)\b',name,re.I)):
                break
            adjustments.append((name,amount))


def _horizontal(rows,end,title):
    for cols,lines in _horizontal_headers(rows,end):
        yield from _sales_footer(lines,cols)
        # A column-wise total needs explicit consolidated scope, or a named adjustment column.
        if not (re.search(r'consolidated basis',title,re.I) or any(ADJUSTMENT.search(t) for _,t in cols[:-1])):
            continue
        for line in lines:
            terms=[(s,t) for s,t,_ in line if t]
            if not terms or terms[0][0].start!=0:
                continue
            metric=_metric(terms[0][1])
            if metric:
                yield metric, [(label,_amount(line,s,label)) for s,label in cols[:-1]], _amount(line,*cols[-1])


def _disclosed_quarter(root,end):
    fields=[]
    for name in ('DocumentFiscalYearFocus','DocumentFiscalPeriodFocus','DocumentPeriodEndDate'):
        vals={text(n) for n in root.xpath('//*[@name=$name]',name='dei:'+name)}
        if len(vals)!=1:
            return None
        fields.append(vals.pop())
    year,quarter,printed=fields
    if not re.fullmatch(r'20\d{2}',year) or not re.fullmatch(r'Q[1-4]',quarter) or _date(printed)!=end:
        return None
    return year+quarter


def _bridges(doc,end):
    root=dom(doc['raw'])
    quarter=_disclosed_quarter(root,end)
    tables=[]
    anchors=defaultdict(list)
    for table in root.xpath('//table'):
        try:
            rows=layout(table)
        except (ValueError,TypeError):
            continue
        title,unit=_title_unit(table,rows,doc)
        if not unit:
            continue
        cols=list(_quarter_column(rows,end))
        if GROUP.search(title) and len(cols)==1:
            for row in rows:
                labels=[(s,t) for s,t,_ in row if t]
                if not labels or labels[0][0].start!=0:
                    continue
                metric=_metric(labels[0][1])
                amount=_amount(row,cols[0],labels[0][1]) if metric else None
                if amount:
                    anchors[(metric,unit)].append(amount)
        elif SEGMENT.search(title):
            tables.append((rows,title,unit,cols))
    for rows,title,unit,cols in tables:
        bridges = list(_vertical(rows,cols[0])) if len(cols)==1 else []
        bridges.extend(_horizontal(rows,end,title))
        for metric,items,total in bridges:
            matches=anchors[(metric,unit)]
            if not total or len({a.value for a in matches})!=1 or matches[0].value!=total.value:
                continue
            names=[name for name,_ in items]
            if len(names)<2 or len(set(names))!=len(names) or any(re.search(r'\b(?:total|subtotal)\b',n,re.I) for n in names):
                continue
            yield metric,unit,items,total,matches[0],quarter


def _write(findings,doc,period,metric,unit,items,table_total,anchor,quarter):
    title=LABELS[metric]
    def fact(cell):
        key=findings.fact(doc,cell.path,metric+' / '+cell.name,cell.value,unit,period,cell.name)
        findings.evidence[key].update(period_start=doc.get('period_start'),period_end=period)
        return key
    anchor_id=fact(anchor)
    total_id=fact(table_total)
    known=[(label,cell,fact(cell)) for label,cell in items if cell]
    missing=[label for label,cell in items if cell is None]
    if not known:
        return
    ids=[key for _,_,key in known]
    total=findings.calc(title+'：已披露带符号项目小计','sum',ids,unit)
    residual=findings.calc(title+'：集团减去已披露项目','difference',[anchor_id,total],unit)
    supporting=[anchor_id,total_id,*ids,total,residual]
    terms = [(name,key) for name,_,key in known]
    adjustments = [key for name,key in terms if ADJUSTMENT.search(name)]
    segments = [key for name,key in terms if not ADJUSTMENT.search(name)]
    split = ''
    if segments and adjustments and not missing:
        segment_sum=findings.calc(title+'：单列调整前各项之和','sum',segments,unit)
        adjust_sum=findings.calc(title+'：原表单列调整净额','sum',adjustments,unit)
        supporting.extend([segment_sum,adjust_sum])
        split=f' 单列调整前小计 {findings.value(segment_sum):,}，原表单列调整净额 {findings.value(adjust_sum):+,}。'
    rendered='，'.join(f'{label} {cell.value:+,}' for label,cell,_ in known)
    answer=f'{period} 止单季{title}（{unit}）：{rendered}；已列合计 {findings.value(total):,}，合并损益表 {anchor.value:,}。'
    answer+=split
    gap=findings.value(residual)
    if missing or gap:
        answer+=f' 尚待解释的净差为 {gap:,}。'
        if missing:
            answer+='缺少原表金额：'+'、'.join(missing)+'。'
        findings.gap(f'{title}的净差 {gap:,} {unit} 的组成未完全披露或未提取，不能指定其性质。')
    else:
        answer+=' 各项按原表符号相加，与合并报表相符。'
    gap_text = ''
    if missing:
        gap_text = '缺少原表金额：' + '、'.join(missing) + '。'
    if gap:
        gap_text += f' {title}仍有净差 {gap:,} {unit}，原表中未核实其完整构成。'
    summary = (f'{period} 止单季{title}（{unit}），表内可核各项合计 {findings.value(total):,}，'
               f'合并损益表为 {anchor.value:,}。' + split)
    findings.note(answer, supporting, kind='segment_reconciliation', metric=metric, period_end=period,
                  period_start=doc.get('period_start'), quarter=quarter, details=f'{title}（{unit}）：{rendered}。',
                  complete=not missing and not gap, summary=summary, missing_support=gap_text.strip())


def _latest_quarter_requested(question,history):
    # Fall back to the latest eligible source only when that is the requested time anchor.
    for turn in (question,*reversed(history)):
        if re.search(r'上一季|上季|上一年|去年|全年|年初|累计|年内|环比|同比|财年|第[一二三四1-4]季|(?:19|20)\d{2}|\bQ[1-4]\b|previous|prior|last year|year.to.date|full.year',turn,re.I):
            return False
        if re.search(r'最近一季|最新(?:一|的)?(?:季度|财季|季)|(?:most recent|latest) (?:fiscal )?quarter',turn,re.I):
            return True
    return False


def analyze(question, history, docs, findings, *, quarter_end=None, period_start=None):
    if re.search(r"现金|收现|\bcash\b", question, re.I) and not _BRIDGE_INTENT.search(question):
        findings.skip("本问的现金差额没有要求汇总分部并对回集团口径，分部调节表无法解释该差额。")
        return
    if re.search(r'利润率|盈利率|毛利率|每股|\b(?:margins?|EPS|per share)\b',question,re.I) and not re.search(
            r'营业利润额|利润金额|销售金额|营收金额|absolute (?:operating )?(?:income|sales|profit)',question,re.I):
        findings.gap('分部利润率和每股数不能直接相加。核对集团利润率需要同口径利润额及收入分母。')
        return
    if re.search(r'(?:只|仅)(?:看|问|查)[^。！？?]{1,24}(?:板块|部门|分部)',question) and not _BRIDGE_INTENT.search(question):
        findings.skip('本问只涉及一个部门，请查该部门的同期原表行。')
        return
    if (re.search(r'这个部门|该部门|这一?个分部|该分部|this (?:particular )?(?:segment|division)',question,re.I)
            and not re.search(r'集团|整[家体]|合并|各(?:个|块)?业务|全部分部|consolidated|company.wide',question,re.I)):
        findings.skip('本问只涉及一个部门，请查该部门的同期原表行。')
        return
    dated=[d for d in docs if d.get('period_end') and d['filename'].lower().endswith(('.htm','.html'))]
    if not quarter_end and _latest_quarter_requested(question,history):
        quarter_ends=[d['period_end'] for d in dated if d.get('period_start') and
                      75 <= (date.fromisoformat(d['period_end'])-date.fromisoformat(d['period_start'])).days+1 <= 105]
        quarter_end=max(quarter_ends,default=None)
    if not quarter_end:
        findings.gap('需要先确定用户追问的财季截止日，才能对回同季分部与合并报表。')
        return
    wanted=_requested(question)
    candidates=defaultdict(list)
    for doc in dated:
        if doc['period_end']!=quarter_end:
            continue
        start=doc.get('period_start')
        if start and (not 75 <= (date.fromisoformat(quarter_end)-date.fromisoformat(start)).days+1 <= 105 or
                      (period_start and start!=period_start)):
            continue
        for metric,unit,items,total,anchor,quarter in _bridges(doc,quarter_end):
            if metric in wanted:
                candidates[metric].append((doc,unit,items,total,anchor,quarter))
    for metric in wanted:
        choices=candidates[metric]
        if not choices:
            findings.gap(f'缺少截至 {quarter_end} 的单季{LABELS[metric]}分部表及可独立核对的同口径合并报表。')
            continue
        # Equivalent forms can corroborate. Never choose between incompatible decompositions.
        sigs={(unit,tuple(sorted((label,cell.value if cell else None) for label,cell in items)),anchor.value)
              for doc,unit,items,_,anchor,_ in choices}
        if len(sigs)>1:
            findings.gap(f'同一财季有不同{LABELS[metric]}分部调节构成，需确认披露版本及口径。')
            continue
        doc,unit,items,table_total,anchor,quarter=max(choices,key=lambda x: (bool(x[-1]),x[0]['published_at']))
        _write(findings,doc,quarter_end,metric,unit,items,table_total,anchor,quarter)


_BRIDGE_INTENT = re.compile(TOPICS["reconcile-segment-totals"] + "|" + 
    r'抵销|抵消|(?:加(?:起来|在一起|总|起)|合计|汇总|合并).{0,55}(?:集团|公司|收入|营收|利润|多少|对得上)|'
    r'(?:集团|公司|分部|各块|各条).{0,55}(?:相加|对(?:上|回|平)|加(?:起来|在一起|总)|汇总|合计|抵销)|'
    r'(?:segments?.{0,48}(?:sum|add|reconcil|consolidated|group total)|reconcil.{0,32}segment)', re.I)
_BRIDGE_DETAIL = re.compile(
    r'(?:中间|之间|其中|这|那|有些|哪些|哪几|相|还|差).{0,12}(?:差|项|抵销|调整|原因|构成)|'
    r'(?:what|which).{0,22}(?:adjustments?|differences?|gap)', re.I)
_UNSUPPORTED_DELIVERY = re.compile(
    r'经营现金|现金流|EPS|每股|利润率|增速|增长率|税率|未来|预测|下季度|股价|现金支付|'
    r'cash flow|per share|margin|guidance|forecast|growth rate', re.I)


def answer_parts(parts, notes, gaps, *, fiscal_target=None):
    """Expose only the requested reconciliation and preserve independently supported axes."""
    found = {n['metric']: n for n in notes if n.get('kind') == 'segment_reconciliation'}
    fiscal_target = fiscal_target or {}
    if any(fiscal_target.get(dim) and n.get(dim) != fiscal_target[dim]
           for n in found.values() for dim in ('period_start','period_end')):
        return {}
    output = {}
    active = []
    for part, question in parts.items():
        if _UNSUPPORTED_DELIVERY.search(question):
            active = []
            continue
        bridge = bool(_BRIDGE_INTENT.search(question))
        detail = bool(_BRIDGE_DETAIL.search(question))
        if not bridge and not (active and detail):
            active = []
            continue
        metrics = _requested(question) if bridge else list(active)
        active = metrics
        rows = [found[m] for m in metrics if m in found]
        missing_metrics = [m for m in metrics if m not in found]
        descriptions = [n['missing_support'] for n in rows if not n['complete'] and n.get('missing_support')]
        for metric in missing_metrics:
            matched = next((s for s in gaps if LABELS[metric] in s), None)
            descriptions.append(matched or f'缺少可独立核对的同季{LABELS[metric]}分部与合并原表。')
        if not rows and not gaps:
            continue
        result = {
            'evidence_ids': list(dict.fromkeys(k for n in rows for k in n['evidence_ids'])),
            'calculation_ids': list(dict.fromkeys(k for n in rows for k in n['calculation_ids'])),
        }
        if rows:
            result['period_end'] = rows[0]['period_end']
            printed = {n.get('quarter') for n in rows if n.get('quarter')}
            if fiscal_target.get('quarter') and printed and printed != {fiscal_target['quarter']}:
                continue
            if len(printed)==1:
                result['quarter'] = printed.pop()
            elif fiscal_target.get('period_end')==result['period_end']:
                result['quarter'] = fiscal_target.get('quarter')
            # The follow-up specifically asks what produced the gap; give its source line items.
            result['answer_sentence'] = ' '.join(n['details' if detail and not bridge else 'summary'] for n in rows)
        if descriptions:
            result['status'] = 'partial' if rows else 'gap'
            result['missing_support'] = ' '.join(descriptions)
        else:
            result['status'] = 'source_excerpt'
        output[part] = result
    return output
