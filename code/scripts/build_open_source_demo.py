"""Build one offline HTML replay from real public retrieval/parser artifacts.

No model calls, gold fabrication, network assets or hidden live agent workflow.
All untrusted source text is inert JSON rendered with textContent.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verifin.public_benchmarks import load_public_dataset

REVISION = "cf3eed2d5984960bf06bb8145bcea5e80b0222a6"
WORK = ROOT.parents[1] / "work/open-source-audit"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_payload(args):
    report = read(args.results)
    if report.get("protocol") != "public-convfinqa-retrieval-v2":
        raise ValueError("Use the final source-block/newline-query v2 report")
    if report["source"]["file_sha256"] != digest(args.data):
        raise ValueError("Public input differs from the measured source snapshot")
    cases = load_public_dataset("convfinqa", args.data, split="dev", source_url=report["source"]["url"], revision=REVISION)
    lookup = {case.case_id: case for case in cases}
    selected = report["cases"]
    expected = report["selection"]["case_ids"]
    if len(selected) != len(expected) or {r["case_id"] for r in selected} != set(expected):
        raise ValueError("Result rows do not match the frozen selected IDs")
    rows = []
    blocks = {}
    for result in selected:
        case = lookup[result["case_id"]]
        gold = set(case.reference["source_ids"])
        source = {b["id"]: b for b in case.task_input["source_blocks"]}
        if not gold or case.reference["unmapped_source_locators"] or result["candidate_count"] != len(source):
            raise ValueError("Cannot display incomplete candidates or unmapped references as a finished replay")
        methods = {}
        for name, value in result["methods"].items():
            if not name.endswith("_top5"):
                continue
            ranked = value["passage_ids"]
            if len(ranked) > 5 or len(set(ranked)) != len(ranked) or set(ranked) - source.keys():
                raise ValueError("Result contains unknown or duplicated source IDs")
            recall = len(set(ranked) & gold) / len(gold)
            if abs(recall - value["evidence_recall"]) > 1e-9:
                raise ValueError("Saved recall disagrees with the original published qrels")
            methods[name] = value
        blocks.update(source)
        rows.append({"case_id": case.case_id, "original_id": case.original_id, "group_id": case.group_id,
                     "turn_index": case.turn_index, "question": case.task_input["question"],
                     "history": case.task_input["previous_user_questions"], "gold_ids": sorted(gold),
                     "candidate_count": len(source), "methods": methods})
    parser_report = read(args.parser_dir / "parser_probe_results.json")
    record = next(r for r in parser_report["records"] if Path(r["input"]).name == "parser_probe_R2.htm")
    structured = read(args.parser_dir / "parser_probe_R2.htm.structured.json")
    if structured["snapshot_hash"] != record["sha256"]:
        raise ValueError("Parser provenance differs from its measured source snapshot")
    table = next(t for t in structured["tables"] if t["status"] == "parsed" and any(r and r[0] == "Net sales" for r in t["rows"]))
    row_index = next(i for i, row in enumerate(table["rows"]) if row[0] == "Net sales")
    flat = (args.parser_dir / "parser_probe_R2.htm.flat.txt").read_text()
    position = flat.find("Net sales")
    if position < 0 or not record["checks"]["net_sales_row_preserved"]:
        raise ValueError("The selected parser comparison did not pass source-fidelity checks")
    # The notice ships in the code package and travels inside the single HTML;
    # it must not depend on a workstation's temporary download directory.
    license_text = (ROOT / "docs/OPEN_SOURCE_DATASETS_2026-11-06.NOTICE.txt").read_text()
    return {"built_at": datetime.now(timezone.utc).isoformat(), "kind": "offline_public_component_replay",
            "result_file": args.results.name, "result_sha256": digest(args.results),
            "measured_at": report["created_at"], "summary": report["summary"],
            "source": report["source"], "encoder": {k: report["encoder"][k] for k in ["model_id", "revision"]},
            "cases": rows, "blocks": blocks, "license": license_text,
            "parser": {"source": record["source"], "snapshot_hash": structured["snapshot_hash"],
                       "parser_version": structured["parser_version"], "flat_excerpt": flat[max(0, position - 350):position + 650],
                       "flat_total_chars": len(flat), "flat_excerpt_start": max(0, position - 350),
                       "headers": table["column_headers"], "rows": table["rows"][row_index:row_index + 3],
                       "locator": {k: v for k, v in table["locator"].items() if k != "snapshot_path"},
                       "checks": record["checks"], "financial_semantics_validated": False}}


CSS = r"""
:root{--ink:#142c37;--muted:#60747d;--line:#dbe5e8;--bg:#f2f6f6;--teal:#0c766d;--mint:#dff3e8;--gold:#a96c24}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}a{color:var(--teal);text-underline-offset:3px}header{background:#122f3a;color:#fff;padding:24px max(24px,calc((100vw - 1200px)/2));border-bottom:4px solid #6dd1b6}header .brand{font-size:14px;letter-spacing:.09em;color:#b7ded6}h1{font-size:32px;line-height:1.25;margin:12px 0}header p{max-width:850px;color:#d1e2e4;margin:10px 0}main{max-width:1250px;margin:0 auto;padding:26px 24px 50px}h2{font-size:23px;margin:0 0 8px}h3{font-size:17px;margin:0 0 8px}.muted{color:var(--muted)}.tag{display:inline-block;font-size:12px;font-weight:650;padding:3px 9px;border-radius:20px;background:#e4eeef;margin:3px 6px 3px 0}.tag.hit{background:var(--mint);color:#076040}.hero-tags .tag{background:#264753;color:#dcf4ef}.section{margin-bottom:28px}.card{background:#fff;border:1px solid var(--line);border-radius:15px;padding:21px;min-width:0;box-shadow:0 5px 20px #14303a05}.intro{display:flex;justify-content:space-between;gap:20px;align-items:start;margin:12px 0 17px}.kicker{font-size:12px;font-weight:750;letter-spacing:.1em;color:var(--teal);margin-bottom:7px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.control{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.control label{flex:1;min-width:190px;font-weight:600;font-size:13px}select,input,button{font:inherit;border:1px solid #b9cbd1;border-radius:8px;padding:10px 12px;background:#fff;color:var(--ink)}select,input{width:100%;margin-top:5px}button{cursor:pointer;font-weight:650}button:hover,select:hover{border-color:var(--teal)}button:disabled{opacity:.45;cursor:default}.nav{display:flex;gap:7px}.question{font-size:21px;font-weight:650;margin:13px 0 4px;line-height:1.45}.id{font:12px/1.5 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere;color:var(--muted)}details{margin-top:13px}summary{cursor:pointer;font-weight:600;color:var(--teal)}ol.history{margin:8px 0 0;padding-left:24px;color:#465d69}.pillbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}.metric{font-size:24px;font-weight:750}.metric-label{font-size:12px;color:var(--muted);display:block}.evidence-list{display:grid;gap:10px}.evidence{padding:12px 13px;border:1px solid #dce6e9;border-left:4px solid #bacdd4;border-radius:8px;background:#fbfcfd;min-width:0}.evidence.hit{border-left-color:#15956e;background:#f5fcf8}.evidence p{font-size:14px;line-height:1.6;margin:7px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}.evidence .rank{color:#526874;font-size:12px;font-weight:700}.hint{font-size:13px;color:var(--muted);margin-top:9px}.notice{border-left:3px solid #c8984c;padding:11px 14px;background:#fbf5e9;font-size:13px;border-radius:5px;margin:16px 0}.summary-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.summary-card{padding:15px;border:1px solid var(--line);border-radius:10px;background:#fff}.summary-card strong{font-size:15px}.bar-row{display:grid;grid-template-columns:52px 1fr 49px;gap:8px;align-items:center;font-size:12px;margin-top:9px}.bar{height:8px;background:#e8eff1;border-radius:10px;overflow:hidden}.bar>span{height:100%;display:block;background:#a7bec8;border-radius:10px}.bar>span.history{background:var(--teal)}.note{margin:0;font-size:13px;color:var(--muted)}.flat{font:14px/1.75 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:295px;overflow:auto;background:#f5f7f8;border-radius:8px;padding:15px;margin:10px 0}.table-wrap{overflow:auto;margin-top:10px}table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:10px 9px;border-bottom:1px solid #dbe5e8;text-align:right;white-space:nowrap}th{background:#eaf3f1;font-weight:650}td:first-child,th:first-child{text-align:left}tr.sales td{background:#e5f6ec;font-weight:750;color:#0b5f47}.provenance{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;background:#f5f7f8;padding:12px;border-radius:7px}.footer{border-top:1px solid var(--line);padding-top:22px;font-size:13px;color:var(--muted)}.empty{padding:26px;color:var(--muted);text-align:center}noscript{display:block;background:#ffe5c9;padding:20px}@media(max-width:800px){.grid,.summary-grid{grid-template-columns:1fr}.intro{display:block}h1{font-size:27px}main{padding:18px 14px}.card{padding:16px}header{padding:20px}.question{font-size:19px}.control label{min-width:160px}}
"""

BODY = r"""
<header><div class="brand">VERIFIN · GROUP 6</div><h1>公开评测回放</h1><p>用真实公开问题看证据检索的变化，再看财报表格如何保留期间与数值。所有结果来自已保存的本地实验。</p><div class="hero-tags"><span class="tag">离线单文件</span><span class="tag">无生成式模型调用</span><span class="tag">组件结果 · 非完整 Agent 演示</span></div></header>
<main>
<section class="section"><div class="intro"><div><div class="kicker">01 / RETRIEVAL</div><h2>追问需要前文：看证据找到了没有</h2><p class="muted" style="margin:0">ConvFinQA 官方 dev · <span id="scope"></span> · 同一候选集合，返回前 5 条。</p></div><span class="tag">真实 BM25 + 本地 Qwen Embedding</span></div>
<div class="summary-grid" id="summary"></div><p class="hint">上图为全部选定问题的平均证据 Recall@5；灰色只用当前问题，绿色同时包含此前用户问题。比例不是答案正确率。</p>
<div class="card"><div class="control"><label for="case-select" style="flex:3">选择真实问题<select id="case-select" aria-label="选择真实问题"></select></label><label for="method">检索方法<select id="method" aria-label="检索方法"><option value="bm25">BM25 · 词项匹配</option><option value="dense">Dense · Qwen 0.6B</option><option value="hybrid">Hybrid · RRF 融合</option></select></label><div class="nav"><button id="previous" aria-label="上一题">←</button><button id="next" aria-label="下一题">→</button></div></div><div class="question" id="question"></div><div class="id" id="case-id"></div><details id="history-panel" open><summary id="history-label">此前用户问题</summary><ol class="history" id="history"></ol></details><div class="hint" id="case-counts"></div></div>
<div class="grid" style="margin-top:18px"><article class="card"><div class="kicker">CURRENT QUESTION</div><h3>只用当前问题</h3><div id="current-metrics" class="pillbar"></div><div id="current-evidence" class="evidence-list"></div></article><article class="card"><div class="kicker">WITH USER HISTORY</div><h3>保留此前用户问题</h3><div id="history-metrics" class="pillbar"></div><div id="history-evidence" class="evidence-list"></div></article></div>
<details class="card" style="margin-top:16px"><summary>展开该题的公开参考证据</summary><p class="hint">这些位置来自官方标注，仅用于评估命中，不作为检索输入。没有命中标记的候选不等于事实错误。</p><div id="gold-evidence" class="evidence-list"></div></details>
<div class="notice">候选材料是数据集已给定的报告片段，尚未评价全报告搜索、答案生成或 Critic。没有向检索器提供未来问题或参考答案。默认显示官方顺序中的第一条追问；可查看全部问题。</div></section>
<section class="section"><div class="intro"><div><div class="kicker">02 / FINANCIAL TABLES</div><h2>同样四个数字，表头决定它们的意义</h2><p class="muted" style="margin:0">Apple 2025-03-29 10-Q 的 R2 附表 · 实际 HTML 提取结果。</p></div><a href="https://www.sec.gov/Archives/edgar/data/320193/000032019325000057/R2.htm" target="_blank" rel="noopener noreferrer">查看 SEC 原始附表 ↗</a></div>
<div class="grid"><article class="card"><div class="kicker">FLAT TEXT</div><h3>原有纯文本提取</h3><p class="note">数字仍存在，但行列与多层期间表头被压成一行。</p><pre id="flat" class="flat"></pre><p class="hint" id="flat-caption"></p></article><article class="card"><div class="kicker">STRUCTURED TABLE</div><h3>保留期间与列关系</h3><p class="note" id="table-units"></p><div class="table-wrap" id="table"></div><p class="hint">高亮原表第一条 Net sales。保留原始字符串，包括货币符号、逗号、空值与括号；没有让模型猜单位或期间。</p></article></div>
<details class="card" style="margin-top:16px"><summary>来源位置与复现信息</summary><pre class="provenance" id="parser-provenance"></pre></details><p class="hint">这是选定真实来源的结构保真检查；未验证所有财报格式或全部金融语义，其他复杂表格仍可能返回未解析状态。</p></section>
<footer class="footer"><strong>这份文件可以直接转发，浏览时无需网络。</strong><p>ConvFinQA：<a href="https://aclanthology.org/2022.emnlp-main.421/" target="_blank" rel="noopener noreferrer">Chen 等，EMNLP 2022</a> · <a href="https://github.com/czyssrs/ConvFinQA" target="_blank" rel="noopener noreferrer">官方数据与代码</a> · <a href="https://github.com/czyssrs/ConvFinQA/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">MIT 许可</a>。Qwen：<a href="https://huggingface.co/Qwen/Qwen3-Embedding-0.6B" target="_blank" rel="noopener noreferrer">模型与许可</a>。Apple 报告的原始版权另行保留；输入镜像和解析来源见上方复现信息。</p><details><summary>数据版本、实验记录与许可通知</summary><pre id="experiment-provenance" class="provenance"></pre><pre id="license" class="provenance"></pre></details></footer>
</main><noscript>请启用本地 JavaScript 以查看交互回放。该文件不请求网络，也不会调用模型。</noscript>
"""

JS = r"""
'use strict';
const D=JSON.parse(document.getElementById('payload').textContent);
const $=id=>document.getElementById(id);
function el(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
const pct=n=>(100*n).toFixed(1)+'%';
function metric(target,value,label){const box=el('div');box.append(el('span',value,'metric'),el('span',label,'metric-label'));target.append(box);}
function evidence(target,ids,gold){target.replaceChildren();if(!ids.length){target.append(el('div','没有返回匹配来源','empty'));return;}ids.forEach((id,i)=>{const b=D.blocks[id];const hit=gold.has(id);const card=el('div',undefined,'evidence'+(hit?' hit':''));const top=el('div');top.append(el('span','#'+(i+1)+' · '+b.locator,'rank'));if(hit)top.append(el('span','命中官方参考位置','tag hit'));card.append(top,el('p',b.text));target.append(card);});}
function render(){const index=Number($('case-select').value),c=D.cases[index],method=$('method').value;const gold=new Set(c.gold_ids);$('question').textContent=c.question;$('case-id').textContent=c.original_id;$('history').replaceChildren();c.history.forEach(q=>$('history').append(el('li',q)));$('history-label').textContent=c.history.length?'此前 '+c.history.length+' 个用户问题':'首轮问题，没有此前用户问题';$('case-counts').textContent='第 '+(c.turn_index+1)+' 轮 · 原 context 共 '+c.candidate_count+' 条候选来源 · 官方参考 '+gold.size+' 条';$('previous').disabled=index===0;$('next').disabled=index===D.cases.length-1;for(const [side,prefix] of [['current','current_question'],['history','history_questions']]){const m=c.methods[prefix+'_'+method+'_top5'];const target=$(side+'-metrics');target.replaceChildren();metric(target,pct(m.evidence_recall),'该题证据 Recall@5');target.append(el('span',m.all_evidence?'已找到全部参考位置':'仍有参考位置未进入前 5 条',m.all_evidence?'tag hit':'tag'));evidence($(side+'-evidence'),m.passage_ids,gold);}evidence($('gold-evidence'),c.gold_ids,gold);}
D.cases.forEach((c,i)=>{const option=el('option',String(i+1).padStart(3,'0')+' · '+c.group_id+' · 第 '+(c.turn_index+1)+' 轮');option.value=String(i);$('case-select').append(option);});
$('case-select').value=String(Math.max(0,D.cases.findIndex(c=>c.turn_index>0)));
$('case-select').addEventListener('change',render);$('method').addEventListener('change',render);
for(const [id,delta] of [['previous',-1],['next',1]])$(id).addEventListener('click',()=>{$('case-select').value=String(Number($('case-select').value)+delta);render();});
$('scope').textContent=D.cases.length+' 个问题 / '+new Set(D.cases.map(c=>c.group_id)).size+' 个原 context';
for(const [method,label] of [['bm25','BM25'],['dense','Qwen Dense'],['hybrid','Hybrid']]){const card=el('div',undefined,'summary-card');card.append(el('strong',label));for(const [prefix,title] of [['current_question','当前'],['history_questions','带历史']]){const value=D.summary.all.methods[prefix+'_'+method+'_top5'].macro_evidence_recall;const row=el('div',undefined,'bar-row'),bar=el('div',undefined,'bar'),fill=el('span',undefined,prefix==='history_questions'?'history':'');fill.style.width=Math.max(0,Math.min(100,value*100))+'%';bar.append(fill);row.append(el('span',title),bar,el('span',pct(value)));card.append(row);}$('summary').append(card);}
const p=D.parser;$('flat').textContent=p.flat_excerpt;$('flat-caption').textContent='显示包含 Net sales 的原始文本片段，起点 '+p.flat_excerpt_start+'；完整纯文本共 '+p.flat_total_chars.toLocaleString()+' 个字符。';$('table-units').textContent=p.headers[0][0];
const table=el('table'),head=el('thead'),first=el('tr'),second=el('tr');const name=el('th','原始行名称');name.rowSpan=2;first.append(name);let k=1;while(k<p.headers.length){const label=p.headers[k][0];let j=k+1;while(j<p.headers.length&&p.headers[j][0]===label)j++;const h=el('th',label);h.colSpan=j-k;first.append(h);k=j;}p.headers.slice(1).forEach(h=>second.append(el('th',h.slice(1).join(' / '))));head.append(first,second);table.append(head);const body=el('tbody');p.rows.forEach((r,i)=>{const tr=el('tr',undefined,i===0?'sales':'');r.forEach(v=>tr.append(el('td',v)));body.append(tr);});table.append(body);$('table').append(table);
$('parser-provenance').textContent=JSON.stringify({source_url:p.source.canonical_sec_url,download_mirror:p.source.download_url,snapshot_sha256:p.snapshot_hash,parser:p.parser_version,table_xpath:p.locator.dom_xpath,source_line:p.locator.source_line,checks:p.checks,financial_semantics_validated:p.financial_semantics_validated},null,2);
$('experiment-provenance').textContent=JSON.stringify({kind:D.kind,result_file:D.result_file,result_sha256:D.result_sha256,measured_at:D.measured_at,source:D.source,encoder:D.encoder},null,2);$('license').textContent=D.license;render();
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path, help="Real public-convfinqa-retrieval-v2 result JSON")
    parser.add_argument("--data", type=Path, default=WORK / "convfinqa/dev_turn.json")
    parser.add_argument("--parser-dir", type=Path, default=WORK / "parser_probe_results")
    parser.add_argument("--output", type=Path, default=ROOT / "examples/open-source-study.html")
    parser.add_argument("--replace", action="store_true", help="Replace only this explicitly selected generated HTML")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".html" or args.output.resolve() in {args.results.resolve(), args.data.resolve()}:
        parser.error("Output must be a distinct HTML file")
    if args.output.exists() and not args.replace:
        parser.error("Output exists; use a new path or explicitly --replace this generated preview")
    payload = build_payload(args)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for char, escape in [("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")]:
        encoded = encoded.replace(char, escape)
    policy_hash = base64.b64encode(hashlib.sha256(JS.encode()).digest()).decode()
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'sha256-'+policy_hash+'\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>VeriFin · 公开评测回放</title><style>'+CSS+'</style></head><body>'+BODY+
            '<script id="payload" type="application/json">'+encoded+'</script><script>'+JS+'</script></body></html>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "bytes": args.output.stat().st_size,
                      "cases": len(payload["cases"]), "unique_source_blocks": len(payload["blocks"]),
                      "result_sha256": payload["result_sha256"], "network_requests": 0, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
