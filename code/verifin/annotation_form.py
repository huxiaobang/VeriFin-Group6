"""Build a self-contained E1 annotation editor from a blinded reviewer folder.

This tool never fills judgments. Completed exports still pass the existing
authoritative Python validator and require independent human review/adjudication.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys

from .evaluation import EvaluationError
from .model import _json_loads
from .outcome_scoring import ANNOTATION_VERSION, _assessment, _index


_VISIBLE_KEYS = {"packet_sha256", "status", "baseline", "candidate", "candidate_availability"}
_CONTEXT_KEYS = {"scope", "requirements", "claims", "evidence", "calculations", "messages", "session", "request", "tools"}
_ANNOTATION_KEYS = {"protocol", "packet_sha256", "source", "annotator_id", "independent", "completed_at", "guideline_version", "assessment"}


def _read(path):
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise EvaluationError("Reviewer material exceeds the 20 MB limit")
    return _json_loads(path.read_text(encoding="utf-8"))


def validate_reviewer_material(visible, annotation):
    """Reject administrative packets; preserve blank human fields unchanged."""
    if not isinstance(visible, dict) or set(visible) != _VISIBLE_KEYS:
        raise EvaluationError("Use reviewer-N/input.json, not an administrative outcome packet")
    digest = visible.get("packet_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise EvaluationError("Reviewer input needs its packet SHA-256 binding")
    availability = visible.get("candidate_availability")
    if availability not in {"candidate", "partial", "unavailable", "missing_revision"}:
        raise EvaluationError("Unknown candidate availability")
    if (visible.get("candidate") is None) != (availability in {"unavailable", "missing_revision"}):
        raise EvaluationError("Candidate availability disagrees with displayed material")
    for phase in ("baseline", "candidate"):
        context = visible.get(phase)
        if phase == "candidate" and context is None:
            continue
        if not isinstance(context, dict) or set(context) - _CONTEXT_KEYS:
            raise EvaluationError("Reviewer context contains unexpected administrative fields")
        for name in ("requirements", "claims", "evidence", "calculations"):
            _index(context.get(name), phase + "." + name)
        for claim in context["claims"]:
            if any(key in claim for key in ("status", "confidence", "support_status")):
                raise EvaluationError("Remove system support badges before independent review")
    if (not isinstance(annotation, dict) or set(annotation) != _ANNOTATION_KEYS
            or annotation.get("protocol") != ANNOTATION_VERSION
            or annotation.get("packet_sha256") != digest
            or annotation.get("source") != "independent_human"):
        raise EvaluationError("Annotation template is not bound to the blinded reviewer material")
    if not isinstance(annotation.get("annotator_id"), str) or not annotation["annotator_id"].strip():
        raise EvaluationError("Reviewer identity is required")
    if not isinstance(annotation.get("guideline_version"), str) or not annotation["guideline_version"].strip():
        raise EvaluationError("Annotation guideline version is required")
    assessment = annotation.get("assessment")
    if not isinstance(assessment, dict) or set(assessment) != {"units", "baseline", "candidate"}:
        raise EvaluationError("Invalid assessment structure")
    _index(assessment.get("units"), "required units")
    for phase in ("baseline", "candidate"):
        answer = assessment.get(phase)
        if visible[phase] is None:
            if answer is not None:
                raise EvaluationError("An unavailable candidate cannot receive judgments")
            continue
        if not isinstance(answer, dict) or set(answer) != {"claims_segmentation_complete", "units", "assertions"}:
            raise EvaluationError("Invalid answer assessment")
        _index(answer.get("assertions"), "assertions")
        _index(answer.get("units"), "unit judgments", "unit_id")
    # Deep copying also rejects non-finite values and removes caller aliases.
    return json.loads(json.dumps(visible, allow_nan=False)), json.loads(json.dumps(annotation, allow_nan=False))


def validate_completed_form(visible, annotation):
    """Local structural check only; the sealed admin packet remains authoritative."""
    visible, annotation = validate_reviewer_material(visible, annotation)
    from .outcome_scoring import _time
    if annotation.get("independent") is not True:
        raise EvaluationError("Explicit independent human attestation is required")
    _time(annotation.get("completed_at"), "annotation.completed_at")
    _assessment(visible, annotation["assessment"])
    return annotation


_CSS = """
:root{font-family:system-ui,-apple-system,sans-serif;color:#18283b;background:#eef2f7;line-height:1.5}*{box-sizing:border-box}
body{margin:0}header{background:#132a47;color:white;padding:22px max(24px,calc((100vw - 1240px)/2))}header h1{margin:0;font-size:25px}
main{max-width:1240px;margin:auto;padding:20px}h2{font-size:21px;margin-top:0}h3{font-size:17px;margin:4px 0 12px}p{margin:8px 0}
.note,.card{padding:18px;background:white;border:1px solid #d5dfea;border-radius:10px;margin:14px 0}.note{background:#fff8e4;border-color:#ead79d}
.muted{color:#50627a;font-size:14px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
label{display:block;font-size:14px;font-weight:600;margin:10px 0 4px}input:not([type=checkbox]),textarea,select{display:block;width:100%;border:1px solid #b8c6d8;border-radius:5px;padding:8px;font:inherit;color:inherit;background:white}
textarea{min-height:76px;resize:vertical}select[multiple]{min-height:90px}input[type=checkbox]{margin-right:8px}button,.file{display:inline-block;padding:9px 14px;border:1px solid #a9bad0;border-radius:6px;font:inherit;color:#183852;background:#fff;cursor:pointer}
button.primary{background:#1550a1;color:white;border-color:#1550a1}button.danger{color:#8b182c}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #73a5f4;outline-offset:2px}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:#f2f5f8;padding:12px;max-height:440px;overflow:auto}code{font-size:12px;overflow-wrap:anywhere}
details{margin:10px 0}summary{cursor:pointer;font-weight:600}.sticky{position:sticky;bottom:0;background:#f8fafdf5;padding:15px;border-top:1px solid #b8c6d8;box-shadow:0 -4px 16px #203a4d15}.sticky>div{max-width:1200px;margin:auto}
#status{white-space:pre-wrap;max-height:130px;overflow:auto}.error{color:#a41b28}.success{color:#19632d}.pill{background:#e7eef8;border-radius:20px;padding:2px 9px;font-size:12px}.claim{border-left:4px solid #92acd0;padding-left:12px}
@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:12px}.sticky{position:static}}@media print{.sticky,button,.file{display:none}details{display:block}body{background:white}}
"""


_SCRIPT = r"""
'use strict';
const MATERIAL = JSON.parse(document.getElementById('material').textContent);
const ORIGINAL = JSON.parse(document.getElementById('annotation').textContent);
let draft = structuredClone(ORIGINAL), dirty = false;
draft.independent = false; draft.completed_at = null;
const text = (tag, value, cls) => {const n=document.createElement(tag); n.textContent=value; if(cls)n.className=cls; return n;};
const button = (label, fn, cls) => {const n=text('button',label,cls); n.type='button'; n.addEventListener('click',fn); return n;};
const phases=['baseline','candidate'];
const phaseLabel={baseline:'原始回答 Baseline',candidate:'待评回答 Candidate'};
const unitVerdicts=['correct','partial','incorrect','missing','legitimate_gap','unassessable'];
const assertionVerdicts=['supported','unsupported','contradicted','unassessable'];
const status=(message,error=false)=>{const n=document.getElementById('status');n.textContent=message;n.className=error?'error':'success';};
const changed=()=>{dirty=true;draft.independent=false;draft.completed_at=null;document.getElementById('attest').checked=false;};
const nonempty=v=>typeof v==='string'&&v.trim().length>0;
function field(parent,label,value,set,{choices=null,multiple=false,lines=false}={}) {
  const holder=text('label',label);let input;
  if(choices){input=document.createElement('select');input.multiple=multiple;if(!multiple)input.append(new Option('— 请选择 —',''));for(const item of choices){const [id,caption]=Array.isArray(item)?item:[item,item];const opt=new Option(caption,id);opt.selected=multiple?(value||[]).includes(id):value===id;input.append(opt);}input.size=multiple?Math.min(6,Math.max(2,choices.length)):1;}
  else{input=document.createElement(lines?'textarea':'input');input.value=value||'';}
  input.addEventListener('change',()=>{set(multiple?Array.from(input.selectedOptions,o=>o.value):(choices?(input.value||null):input.value));changed();});holder.append(input);parent.append(holder);return input;
}
function check(parent,label,value,set){const l=text('label','');const n=document.createElement('input');n.type='checkbox';n.checked=value===true;n.addEventListener('change',()=>{set(n.checked);changed();});l.append(n,document.createTextNode(label));parent.append(l);}
function details(parent,label,value){const d=document.createElement('details');d.append(text('summary',label),text('pre',JSON.stringify(value,null,2)));parent.append(d);}
function freshId(prefix,rows){let i=1;const ids=new Set(rows.map(r=>r.id));while(ids.has(prefix+':'+i))i++;return prefix+':'+i;}
function refresh(){renderUnits();for(const phase of phases)renderPhase(phase);}
function renderUnits(){
 const root=document.getElementById('units');root.replaceChildren();
 root.append(text('h2','1 · 定义必需内容单元'),text('p','逐项决定原始请求需要什么，以及现有来源是否足以回答。可将一项要求拆成多个内容单元；不按系统完成状态填答案。','muted'));
 const requirements=MATERIAL.baseline.requirements;
 details(root,'原始要求、范围和对话',{request:MATERIAL.baseline.request,requirements,scope:MATERIAL.baseline.scope,messages:MATERIAL.baseline.messages});
 for(const unit of draft.assessment.units){const box=text('div','','card');box.append(text('code',unit.id));
  field(box,'对应原始要求',unit.requirement_id,v=>unit.requirement_id=v,{choices:requirements.map(r=>[r.id,r.id+' — '+r.text])});
  field(box,'必需内容描述',unit.description,v=>unit.description=v,{lines:true});
  field(box,'可回答性',unit.answerability,v=>unit.answerability=v,{choices:['answerable','legitimate_gap']});
  field(box,'判断依据（必填）',unit.rationale,v=>unit.rationale=v,{lines:true});
  box.append(button('删除此单元',()=>{const id=unit.id;draft.assessment.units=draft.assessment.units.filter(u=>u.id!==id);for(const p of phases){const a=draft.assessment[p];if(!a)continue;a.units=a.units.filter(u=>u.unit_id!==id);for(const s of a.assertions)s.requirement_unit_ids=s.requirement_unit_ids.filter(i=>i!==id);}changed();refresh();},'danger'));root.append(box);
 }
 root.append(button('增加内容单元',()=>{const id=freshId('unit:manual',draft.assessment.units);draft.assessment.units.push({id,requirement_id:requirements[0]?.id||null,description:'',answerability:null,rationale:''});for(const p of phases){if(draft.assessment[p])draft.assessment[p].units.push({unit_id:id,verdict:null,assertion_ids:[],rationale:''});}changed();refresh();}));
}
function renderPhase(phase){
 const root=document.getElementById(phase);root.replaceChildren();root.append(text('h2',(phase==='baseline'?'2':'3')+' · '+phaseLabel[phase]));
 const context=MATERIAL[phase],answer=draft.assessment[phase];if(!context){root.append(text('p','该回答不可用；不为缺失回答创造判断。'));return;}
 root.append(text('p','先把每条 claim 拆成可独立核验的断言，再给断言和必需内容单元分别下判断。','muted'));
 details(root,'查看全部证据和计算（含来源 URL、定位与快照信息）',{evidence:context.evidence,calculations:context.calculations});
 for(const claim of context.claims){const card=text('section','','card');card.append(text('h3',claim.id),text('p',claim.text,'claim'));details(card,'查看原始 claim 字段',claim);
  for(const assertion of answer.assertions.filter(a=>a.claim_id===claim.id)){const box=text('div','','card');box.append(text('code',assertion.id));
   field(box,'独立断言文本',assertion.text,v=>assertion.text=v,{lines:true});
   field(box,'支持情况',assertion.verdict,v=>assertion.verdict=v,{choices:assertionVerdicts});
   field(box,'相关必需内容单元（可多选）',assertion.requirement_unit_ids,v=>assertion.requirement_unit_ids=v,{choices:draft.assessment.units.map(u=>[u.id,u.id+' — '+(u.description||'未填写描述')]),multiple:true});
   field(box,'核对使用的证据 / 计算 ID（可多选）',assertion.evidence_refs,v=>assertion.evidence_refs=v,{choices:[...context.evidence,...context.calculations].map(e=>[e.id,e.id+' — '+(e.metric||e.kind||e.operation||'source')]),multiple:true});
   if(phase==='candidate')field(box,'对应原始回答断言（新断言可留空）',assertion.baseline_assertion_ids,v=>assertion.baseline_assertion_ids=v,{choices:draft.assessment.baseline.assertions.map(a=>[a.id,a.id+' — '+a.text.slice(0,100)]),multiple:true});
   field(box,'证据、反证或不确定性的理由（必填）',assertion.rationale,v=>assertion.rationale=v,{lines:true});
   box.append(button('删除此断言',()=>{const id=assertion.id;answer.assertions=answer.assertions.filter(a=>a.id!==id);for(const u of answer.units)u.assertion_ids=u.assertion_ids.filter(i=>i!==id);if(phase==='baseline'&&draft.assessment.candidate)for(const a of draft.assessment.candidate.assertions)a.baseline_assertion_ids=a.baseline_assertion_ids.filter(i=>i!==id);answer.claims_segmentation_complete=false;changed();refresh();},'danger'));card.append(box);
  }
  card.append(button('为这条 claim 增加 / 拆分一个断言',()=>{answer.assertions.push({id:freshId(phase==='baseline'?'before:manual':'after:manual',answer.assertions),claim_id:claim.id,text:'',requirement_unit_ids:[],verdict:null,evidence_refs:[],rationale:'',...(phase==='candidate'?{baseline_assertion_ids:[]}: {})});answer.claims_segmentation_complete=false;changed();refresh();}));root.append(card);
 }
 check(root,'我已逐条检查该回答，全部事实断言均已拆分且没有遗漏。',answer.claims_segmentation_complete,v=>answer.claims_segmentation_complete=v);
 root.append(text('h3','必需内容是否得到回答'));
 for(const judgment of answer.units){const unit=draft.assessment.units.find(u=>u.id===judgment.unit_id);const box=text('div','','card');box.append(text('p',judgment.unit_id+' — '+(unit?.description||'')));
  field(box,'内容完成情况',judgment.verdict,v=>judgment.verdict=v,{choices:unitVerdicts});
  field(box,'对应断言（必须同时在断言中关联该内容单元）',judgment.assertion_ids,v=>judgment.assertion_ids=v,{choices:answer.assertions.map(a=>[a.id,a.id+' — '+a.text.slice(0,100)]),multiple:true});
  field(box,'内容判断理由（必填）',judgment.rationale,v=>judgment.rationale=v,{lines:true});root.append(box);
 }
}
function index(rows,key,label,errors){const out=new Map();if(!Array.isArray(rows)){errors.push(label+' 必须是列表');return out;}for(const r of rows){if(!r||typeof r!=='object'||!nonempty(r[key])||out.has(r[key])){errors.push(label+' 存在空或重复 ID');continue;}out.set(r[key],r);}return out;}
function refs(value,allowed,label,errors){if(!Array.isArray(value)||value.some(v=>typeof v!=='string')||new Set(value).size!==value.length||value.some(v=>!allowed.has(v))){errors.push(label+' 引用了重复或不存在的 ID');return [];}return value;}
function validateComplete(data){
 const errors=[];const assessment=data.assessment;const requirements=new Set(MATERIAL.baseline.requirements.map(r=>r.id));const units=index(assessment.units,'id','内容单元',errors);const covered=new Set();
 if(!units.size)errors.push('至少需要一个内容单元');
 for(const [id,u] of units){if(!requirements.has(u.requirement_id))errors.push(id+' 对应要求不存在');covered.add(u.requirement_id);if(!nonempty(u.description)||!nonempty(u.rationale))errors.push(id+' 描述或理由未填写');if(!['answerable','legitimate_gap'].includes(u.answerability))errors.push(id+' 可回答性未填写');}
 if([...requirements].some(r=>!covered.has(r)))errors.push('内容单元没有覆盖所有原始要求');
 let prior=new Map();
 for(const phase of phases){const context=MATERIAL[phase],answer=assessment[phase];if(!context){if(answer!==null)errors.push('不可用 candidate 不应被评判');continue;}
  if(!answer||answer.claims_segmentation_complete!==true){errors.push(phase+' 未确认断言拆分完整');if(!answer)continue;}
  const claims=new Set(context.claims.map(c=>c.id)),sources=new Set([...context.evidence,...context.calculations].map(e=>e.id));const assertions=index(answer.assertions,'id',phase+' 断言',errors);const represented=new Set();
  for(const [id,a] of assertions){if(!claims.has(a.claim_id))errors.push(id+' claim 不存在');represented.add(a.claim_id);if(!nonempty(a.text)||!nonempty(a.rationale))errors.push(id+' 文本或理由未填写');if(!assertionVerdicts.includes(a.verdict))errors.push(id+' 支持情况未填写');refs(a.requirement_unit_ids,units,id+' 内容单元',errors);refs(a.evidence_refs,sources,id+' 证据',errors);if(phase==='candidate')refs(a.baseline_assertion_ids,prior,id+' 原始断言',errors);}
  if([...claims].some(id=>!represented.has(id)))errors.push(phase+' 存在没有断言判断的 claim');
  const judgments=index(answer.units,'unit_id',phase+' 内容判断',errors);
  if(judgments.size!==units.size||[...units.keys()].some(id=>!judgments.has(id)))errors.push(phase+' 缺少内容单元判断');
  for(const [id,u] of judgments){if(!units.has(id)){errors.push(id+' 内容单元不存在');continue;}if(!unitVerdicts.includes(u.verdict))errors.push(id+' 内容判断未填写');if(!nonempty(u.rationale))errors.push(id+' 内容判断理由未填写');const linked=refs(u.assertion_ids,assertions,id+' 断言',errors);
   if(linked.some(i=>!assertions.get(i).requirement_unit_ids?.includes(id)))errors.push(id+' 与断言的双向关联不一致');
   if(u.verdict==='missing'&&linked.length)errors.push(id+' missing 不应关联回答断言');
   if(['correct','legitimate_gap','partial','incorrect'].includes(u.verdict)&&!linked.length)errors.push(id+' 已回答内容必须关联断言');
   if(['correct','legitimate_gap'].includes(u.verdict)&&linked.some(i=>assertions.get(i).verdict!=='supported'))errors.push(id+' 完成内容依赖未支持断言');
   if(u.verdict==='correct'&&units.get(id).answerability!=='answerable')errors.push(id+' 合法证据缺口不能算实质正确回答');
   if(u.verdict==='legitimate_gap'&&units.get(id).answerability!=='legitimate_gap')errors.push(id+' 可回答内容不能算合法证据缺口');
  }if(phase==='baseline')prior=assertions;
 }
 return errors;
}
function importDraft(value){
 if(!value||typeof value!=='object'||value.protocol!==ORIGINAL.protocol||value.packet_sha256!==ORIGINAL.packet_sha256||value.annotator_id!==ORIGINAL.annotator_id||value.source!==ORIGINAL.source||value.guideline_version!==ORIGINAL.guideline_version)throw Error('文件不属于这份材料和本位标注者。');
 if(Object.keys(value).sort().join()!==Object.keys(ORIGINAL).sort().join())throw Error('存在未知顶层字段。');
 const a=value.assessment;if(!a||Object.keys(a).sort().join()!=='baseline,candidate,units'||!Array.isArray(a.units))throw Error('assessment 结构不正确。');
 for(const u of a.units){if(!u||typeof u.id!=='string'||typeof u.description!=='string'||typeof u.rationale!=='string')throw Error('内容单元结构不正确。');}
 for(const p of phases){const answer=a[p];if(!MATERIAL[p]){if(answer!==null)throw Error('缺失回答不能新增判断。');continue;}if(!answer||!Array.isArray(answer.units)||!Array.isArray(answer.assertions)||typeof answer.claims_segmentation_complete!=='boolean')throw Error('回答结构不正确。');for(const s of answer.assertions){if(!s||!nonempty(s.id)||!nonempty(s.claim_id)||typeof s.text!=='string'||typeof s.rationale!=='string'||!Array.isArray(s.requirement_unit_ids)||!Array.isArray(s.evidence_refs)||(p==='candidate'&&!Array.isArray(s.baseline_assertion_ids)))throw Error('断言结构不正确。');}for(const u of answer.units){if(!u||!nonempty(u.unit_id)||typeof u.rationale!=='string'||!Array.isArray(u.assertion_ids))throw Error('内容判断结构不正确。');}}
 draft=structuredClone(value);changed();refresh();status('草稿已导入；完成签署已清除，修改后需重新确认。');
}
function download(value,name){const blob=new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);dirty=false;}
document.getElementById('identity').textContent=ORIGINAL.annotator_id+' · '+ORIGINAL.guideline_version+' · packet '+MATERIAL.packet_sha256;
document.getElementById('draft-save').addEventListener('click',()=>{const copy=structuredClone(draft);copy.independent=false;copy.completed_at=null;download(copy,'annotation-draft.json');status('已导出未完成草稿；它不能作为已完成标注计分。');});
document.getElementById('check').addEventListener('click',()=>{const errors=validateComplete(draft);status(errors.length?errors.join('\n'):'结构检查通过。仍须由你核对来源并确认独立完成。',Boolean(errors.length));});
document.getElementById('complete').addEventListener('click',()=>{const errors=validateComplete(draft);if(!document.getElementById('attest').checked)errors.push('请确认由本人独立完成本次标注。');if(errors.length){status(errors.join('\n'),true);return;}const copy=structuredClone(draft);copy.independent=true;copy.completed_at=new Date().toISOString();download(copy,'annotation-completed.json');status('已导出你签署的标注。管理员仍需运行 Python 校验并完成独立复核与裁决。');});
document.getElementById('import').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>20_000_000)throw Error('文件超过 20 MB。');importDraft(JSON.parse(await file.text()));}catch(error){status(error.message||'草稿导入失败。',true);}finally{e.target.value='';}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
refresh();status('尚未填写人工判断。此页面不请求模型、不联网，也不会自动保存；请使用“导出草稿”。');
"""


def render_annotation_form(visible, annotation):
    visible, annotation = validate_reviewer_material(visible, annotation)
    # JSON is embedded as inert data; escape markup-significant characters so
    # quoted source text cannot close a script tag or execute in the page.
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'sha256-' + script_hash + "\'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'\">"
            "<title>VeriFin · 独立人工标注</title><style>" + _CSS + "</style></head><body>"
            '<header><h1>VeriFin · 独立人工标注</h1><p>原始回答与待评回答的内容和证据核验</p><p id="identity" class="muted" style="color:#c6d6ec"></p></header>'
            '<main><div class="note"><strong>这些字段需要你根据原始来源亲自判断。</strong><p>系统不会预填支持情况。无模型判分、无联网、无自动保存；草稿与已完成标注分开导出。请先阅读项目的 docs/ANNOTATION.md。</p><p>判断独立性、证据充分性及两位标注者之间的裁决仍由人负责。此表只辅助编辑和检查格式。</p></div>'
            '<section id="units" class="card"></section><div class="grid"><section id="baseline" class="card"></section><section id="candidate" class="card"></section></div></main>'
            '<div class="sticky"><div><div class="row"><button id="draft-save">导出草稿</button><label class="file">导入草稿<input id="import" type="file" accept="application/json,.json" style="display:none"></label><button id="check">检查完整性</button></div>'
            '<label><input id="attest" type="checkbox">我已独立检查所需来源与每个判断，未将系统标签当作真值；确认本份标注已完成。</label>'
            '<button id="complete" class="primary">签署并导出已完成标注</button><p id="status" role="status" aria-live="polite"></p></div></div>'
            '<script id="material" type="application/json">' + encoded(visible) + '</script><script id="annotation" type="application/json">' + encoded(annotation) + '</script><script>' + _SCRIPT + '</script></body></html>')


def build_annotation_form(reviewer_dir, output_path):
    folder, output = Path(reviewer_dir), Path(output_path)
    content = render_annotation_form(_read(folder / "input.json"), _read(folder / "annotation.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(content)
    return {"output": str(output.resolve()), "status": "pending_independent_human_assessment",
            "completed_annotations": 0, "truth_scored": False, "network_requests": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviewer-dir", required=True, help="One reviewer-N folder, not the administrative folder")
    parser.add_argument("--output", required=True, help="New HTML file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build_annotation_form(args.reviewer_dir, args.output), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print("Annotation form: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
