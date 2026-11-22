"""Acquire current SEC quarterly disclosures before choosing dates or facts."""
import re

from .data import DataError, parse_day
from .financial import duration_class
from .reporting_period import parse_reported_date
from .skill_calculations import latest_reported_target, previous_quarter, target_labels
from .source_scope import source_constraint


def mentions_latest_quarter(question):
    latest = re.search(r"最新|最近|\blatest\b|\bmost recent(?:ly)?\b", question, re.I)
    quarter = re.search(r"季度|[财財]季|\bquarter(?:ly)?\b", question, re.I)
    return bool(latest and quarter)


def requested(question):
    if not mentions_latest_quarter(question) or not source_constraint(question).permits_auto_sec:
        return False
    if re.search(r"(?<![a-z])(?:SEC|EDGAR)(?![a-z])", question, re.I):
        return True
    earnings = re.search(r"(?:最新|最近)(?:已|刚|剛|经|經|公布|公佈|披露|公告|发布|發佈|的|[一個个])*[财財]?季"
                         r"|(?:latest|most recent) (?:reported |announced |fiscal )*quarter", question, re.I)
    alternate_source = re.search(r"电话会|電話會|官网|官網|港交所|财报电话|\b(?:transcript|investor relations|HKEX|SEDAR)\b", question, re.I)
    return bool((earnings or latest_reported_target(question) == "previous") and not alternate_source and re.search(
        r"收入|[营營]收|利[润潤益]|[业業][绩績]|revenue|income|margin|earnings|profit", question, re.I))


def _call(name, **args):
    return {"action": "tool", "tool": name, "arguments": args,
            "reason": "核对截止日前最近的 SEC 业绩申报及其披露的财季。"}


def _release(links):
    if len(links) == 1:
        return links[0]
    candidates = [link for link in links if re.search(
        r"ex(?:hibit)?[-_.]?99[-_.]?1(?:[^0-9]|$)|earnings|press[-_]?release|pr\.htm", link, re.I)]
    return candidates[0] if len(candidates) == 1 else None


def _source_gap(result, message):
    status = result.get("data", {}).get("http_status")
    if status == 403:
        return message + " SEC 拒绝了本次访问（HTTP 403）。"
    if isinstance(status, int) and 400 <= status < 600:
        return message + f" SEC 返回 HTTP {status}。"
    return message


def _alternative_10q(rows, parent, periodic_end):
    disclosed = {(item["quarter"], parse_reported_date(item["end_date_text"]))
                 for item in parent.get("disclosed_quarters", [])}
    if len(disclosed) != 1:
        return None, None
    label, end = disclosed.pop()
    if not end or end < periodic_end:
        return None, None
    matches = [r for r in rows if r["form"] == "10-Q" and r["reportDate"] == end and r.get("url")]
    return (matches[0], label) if len(matches) == 1 else (None, None)


_PROFIT_METRICS = {"revenue", "operating_income"}


def reported_pair(cells):
    """Identify one same-source GAAP period in published operating statement cells."""
    if len(cells) != 2 or {r.get("metric") for r in cells} != _PROFIT_METRICS:
        return None
    first, second = cells
    dimensions = ("entity", "period_start", "period_end", "accession", "source_url", "snapshot_hash",
                  "form", "unit", "currency", "basis", "business_scope", "origin_context")
    labels = target_labels(first.get("fiscal_quarter") or "")
    if (len(labels) != 1 or target_labels(second.get("fiscal_quarter") or "") != labels
            or any(first.get(k) != second.get(k) for k in dimensions)
            or not all(first.get(k) for k in ("entity", "period_end", "accession", "source_url", "snapshot_hash", "currency"))
            or first.get("unit") != first["currency"] or first.get("basis") != "US-GAAP"
            or first.get("business_scope") != "consolidated"):
        return None
    return dict(entity=first["entity"], quarter=next(iter(labels)), period_start=first.get("period_start"),
                period_end=first["period_end"], accession=first["accession"], evidence_ids=[r["id"] for r in cells])


def _quarter_pair(result, entity):
    ids = result.get("data", {}).get("quarterly_gaap_operands", {})
    if set(ids) != _PROFIT_METRICS:
        return None
    period = reported_pair([r for r in result.get("evidence", []) if r["id"] in ids.values()])
    return period if period and period["entity"] == entity else None


def preceding_end(prior_end, anchor):
    """Use the adjacent reported end, also checking the current start when available."""
    try:
        end = parse_day(prior_end)
        newest = parse_day(anchor["period_end"])
        first = parse_day(anchor["period_start"]) if anchor.get("period_start") else None
    except DataError:
        return False
    return 70 <= (newest - end).days <= 105 and (first is None or (first - end).days == 1)


def full_quarter(start, end):
    try:
        return duration_class(start, end) == "quarter_length"
    except DataError:
        return False


def _preceding_filing(rows, anchor):
    candidates = [r for r in rows if r["form"] == "10-Q" and r.get("reportDate") and r.get("url")
                  and preceding_end(r["reportDate"], anchor)]
    return candidates[0] if len(candidates) == 1 else None


def acquire(entity, cutoff, question):
    """Yield one public tool action per step. Return a gap if the source is unavailable."""
    horizon = latest_reported_target(question)
    args = {"entity": entity, "source": "live"}
    if cutoff:
        args["cutoff"] = cutoff
    offset = 0
    fallback_notice = ""
    quarterly_filing = False
    while True:
        filters = {"earnings_only": True} if horizon == "previous" else {}
        result = yield _call("discover_filings", **args, forms=["8-K", "10-Q", "10-K"], offset=offset, **filters)
        data = result.get("data", {})
        if data.get("snapshot", {}).get("mode") != "live":
            return _source_gap(result, "未取得截止日前的最新 SEC 申报目录，无法确认最新已公布财季。")
        rows = data.get("filings", [])
        periodic_end = max((r["reportDate"] for r in rows if r["form"] in {"10-Q", "10-K"} and r["reportDate"]), default="")
        # Prefer a known earnings exhibit when a periodic report was filed the same day.
        candidates = [r for r in rows if r["form"] != "8-K" or not r.get("items")
                      or "2.02" in re.findall(r"\d+\.\d+", r["items"])]
        candidates.sort(key=lambda r: (r["filingDate"], r["form"] == "8-K" and bool(r.get("items"))), reverse=True)
        # A 10-Q can arrive days after its press release. Try that release
        # first, and verify its actual period against the periodic filing.
        companion = None
        if candidates and candidates[0]["form"] in {"10-Q", "10-K"} and periodic_end:
            companion = next((r for r in candidates if r["form"] == "8-K" and r["filingDate"] >= periodic_end
                              and "2.02" in (r.get("items") or "")), None)
            if companion:
                candidates.remove(companion)
                candidates.insert(0, companion)
        for row in candidates:
            if row["form"] != "8-K" and (row.get("reportDate") or "") < periodic_end:
                continue
            if not row.get("url"):
                return "已取得 SEC 目录，最新相关申报的正文无法读取，尚不能核实财季。"
            filing = dict(args, accession=row["accession"], primary_document=row["primaryDocument"], limit_chars=6000)
            if horizon == "previous" and row["form"] == "10-Q":
                filing["quarter_operands"] = True
            result = yield _call("read_filing", **filing)
            if not result.get("ok"):
                return _source_gap(result, "已取得 SEC 目录，但未能读取最新相关申报的正文，尚不能核实财季。")
            if row["form"] != "8-K":
                break
            parent = result["data"]
            if not row.get("items") and not re.search(
                    r"item\s+2\.02|results of operations|earnings release", result["evidence"][0]["text"], re.I):
                if parent.get("next_offset"):
                    # The visible prefix cannot rule out an earnings item. Hand
                    # the current filing to the Planner for further inspection.
                    return None
                continue
            exhibit = _release(parent.get("linked_documents", []))
            if not exhibit:
                if row is companion:
                    continue
                # Preserve links and primary text for a targeted read by the Planner.
                return None
            result = yield _call("read_filing", **filing, exhibit_document=exhibit)
            if result.get("ok"):
                ends = {e["period_end"] for e in result["evidence"] if e.get("fiscal_quarter")}
                if (row is companion and not ends) or (ends and max(ends) < periodic_end):
                    continue
                break

            failure = _source_gap(result, "已读取最新相关 8-K，但未能取得其业绩附件。")
            alternative, expected_label = _alternative_10q(rows, parent, periodic_end)
            if not alternative:
                return failure + " 未找到可核实为同季的 10-Q，最新季度数据尚未核实。"
            result = yield _call("read_filing", **args, accession=alternative["accession"],
                                 primary_document=alternative["primaryDocument"], limit_chars=6000, quarter_operands=True)
            ids = result.get("data", {}).get("quarterly_gaap_operands")
            if not ids:
                return failure + " " + _source_gap(result, "同季 10-Q 未能提供两项带完整期间的合并 GAAP 金额。")
            labels = {e.get("fiscal_quarter") for e in result["evidence"] if e["id"] in ids.values()}
            label = next(iter(labels)) if len(labels) == 1 else None
            if not label or target_labels(label) != {expected_label}:
                return failure + " 同季 10-Q 的财年及季度尚不能核实。"
            quarterly_filing = True
            fallback_notice = failure + " 金额、期间和计算均以同季 10-Q 为依据，并明确注明此次来源替换。"
            break
        else:
            offset = data.get("next_offset")
            if offset is None:
                return "截止日前的 SEC 目录中未找到可确认最新已公布财季的申报。"
            continue
        break

    is_margin = re.search(r"(?:[营營][业業]|经营|經營)(?:利[润潤]|利[益])率|operating (?:profit |income )?margin", question, re.I)
    if horizon == "previous":
        anchor = _quarter_pair(result, entity)
        if not anchor:
            return "尚未从已披露的 SEC 数据核实最新财季的编号及期末，无法确定它的上一季。"
        previous = previous_quarter(anchor["quarter"])
        prefix = f"SEC 申报已核实最新披露财季为 {anchor['quarter']}。本问所指上一季为 {previous}。"
        filing = _preceding_filing(rows, anchor) if not previous.endswith("Q4") else None
        if not filing:
            return prefix + "未找到可唯一对应上一季的独立季度申报及精确金额。"
        prior = yield _call("read_filing", **args, accession=filing["accession"],
                            primary_document=filing["primaryDocument"], limit_chars=6000, quarter_operands=True)
        target = _quarter_pair(prior, entity) if prior.get("ok") else None
        if (not target or target["quarter"] != previous or target["period_end"] != filing["reportDate"]
                or target["accession"] != filing["accession"]
                or not full_quarter(target["period_start"], target["period_end"])):
            return prefix + _source_gap(prior, "上一季的 10-Q 期间或合并 GAAP 金额尚未核实，无法交付该季利润率。")
        if is_margin:
            ids = prior["data"]["quarterly_gaap_operands"]
            calculation = yield _call("calculate", operation="margin", operand_ids=[ids["operating_income"], ids["revenue"]])
            if not calculation.get("ok"):
                return prefix + "已查得上一季申报，但两项原表金额未能完成利润率核算。"
        yield {"action": "research", "relative_quarter": target, "reason": prefix +
               "按本问交付上一季 10-Q 原表支持的金额、期间及计算。最新一期的业绩公告只用于核实基准财季。"}
        return None

    if horizon == "check_source":
        return None

    operands = result["data"].get("quarterly_gaap_operands")
    requested_dates = re.search(r"起[止訖]|起始日|開始日|开始日|start (?:and end|date)|(?:Form )?10-Q", question, re.I)
    if operands and requested_dates and not quarterly_filing:
        release = [e for e in result["evidence"] if e["id"] in operands.values()]
        ends = {e["period_end"] for e in release}
        matches = [r for r in rows if r["form"] == "10-Q" and r["reportDate"] in ends and r.get("url")]
        if len(matches) == 1:
            filing, = matches
            checked = yield _call("read_filing", **args, accession=filing["accession"],
                                  primary_document=filing["primaryDocument"], limit_chars=6000, quarter_operands=True)
            quarter_ids = checked.get("data", {}).get("quarterly_gaap_operands")
            if checked.get("ok"):
                quarterly_filing = True
            if quarter_ids:
                dated = [e for e in checked["evidence"] if e["id"] in quarter_ids.values()]
                released_values = {(e["metric"], e["value"], e["unit"], e["period_end"]) for e in release}
                filed_values = {(e["metric"], e["value"], e["unit"], e["period_end"]) for e in dated}
                if released_values != filed_values:
                    return "业绩公告与同季 10-Q 的金额或币种无法核对一致，暂不交付季度利润率。"
                operands = quarter_ids
    if operands and is_margin:
        calculated = yield _call("calculate", operation="margin", operand_ids=[operands["operating_income"], operands["revenue"]])
        if calculated.get("ok") and not re.search(
                r"同比|[环環]比|比[较較]|[变變]化|[归歸]因|[预預][计計測测]|原因|"
                r"compare|versus|change|growth|why|driver|outlook|forecast", question, re.I):
            yield {"action": "research", "reason":
                   (fallback_notice or "用本轮 SEC 申报目录、业绩附件及已保存的计算逐项回答原问。")
                   + "列出财年、季度、期末日期、收入和营业利润的原表单位、代入金额的百分比算式及所依据的申报链接。仅报告有证据的项目。"
                   + ("用同季 10-Q 的申报链接及其 XBRL 期间记录核对起止日。" if quarterly_filing else "")
                   + ("缺少起始日证据时明确留缺。" if requested_dates else "")}
    return None
