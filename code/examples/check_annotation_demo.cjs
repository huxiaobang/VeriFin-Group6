/* Development-only DOM regression check. Does not launch/control a browser.
 * Usage: node examples/check_annotation_demo.cjs /path/to/node_modules/jsdom
 * jsdom is a QA dependency only, not a VeriFin runtime requirement.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.argv[2] || 'jsdom');
const source = fs.readFileSync(path.join(__dirname, 'annotation-demo.html'), 'utf8');
const downloads = [], issues = [], blobs = new Map();
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => issues.push(String(error)));
const dom = new JSDOM(source, {
  url: 'http://127.0.0.1/annotation-demo.html',
  runScripts: 'dangerously', // Our repository-owned generated page only.
  virtualConsole,
  beforeParse(window) {
    window.structuredClone = structuredClone;
    window.Blob = class {constructor(parts) {this.text = parts.join('');}};
    window.URL.createObjectURL = blob => {const id='blob:test-'+blobs.size;blobs.set(id,blob);return id;};
    window.URL.revokeObjectURL = id => blobs.delete(id);
    window.HTMLAnchorElement.prototype.click = function() {downloads.push({name:this.download, text:blobs.get(this.href)?.text});};
  }
});
const document = dom.window.document;
const clickLabel = (within, label) => {
  const found = [...within.querySelectorAll('button')].find(button => button.textContent===label);
  assert(found, 'Missing button: '+label); found.click();
};
const baseline = () => document.getElementById('baseline');
const required = () => document.getElementById('units');
const blank = JSON.parse(document.getElementById('annotation').textContent);
async function main() {
  assert(document.querySelector('h1').textContent.startsWith('DEMO'));
  assert(document.getElementById('status').textContent.includes('尚未填写人工判断'));
  const initialUnits = required().querySelectorAll('.card').length;
  const initialBaselineUnits = baseline().querySelectorAll('select').length;
  clickLabel(required(), '增加内容单元');
  assert.equal(required().querySelectorAll('.card').length, initialUnits+1);
  assert(baseline().querySelectorAll('select').length>initialBaselineUnits);
  const last = [...required().querySelectorAll('.card')].at(-1);
  clickLabel(last, '删除此单元');
  assert.equal(required().querySelectorAll('.card').length, initialUnits);
  const beforeAssertions = baseline().querySelectorAll('textarea').length;
  clickLabel(baseline(), '为这条 claim 增加 / 拆分一个断言');
  assert(baseline().querySelectorAll('textarea').length>beforeAssertions);
  const split = [...baseline().querySelectorAll('button')].filter(button => button.textContent==='删除此断言').at(-1);
  split.click();
  assert.equal(baseline().querySelectorAll('textarea').length, beforeAssertions);
  document.getElementById('attest').checked=true;
  document.getElementById('complete').click();
  assert.equal(downloads.length,0,'Blank template must never download as completed');
  assert(document.getElementById('status').textContent.includes('未填写'));
  const description = required().querySelector('textarea');
  description.value='DEMO 编辑后草稿 — 中文长引用测试，不是人工真值。';
  description.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  assert.equal(document.getElementById('attest').checked,false,'Editing must clear attestation');
  document.getElementById('draft-save').click();
  assert.equal(downloads.length,1);
  const exported = JSON.parse(downloads[0].text);
  assert.equal(exported.independent,false);
  assert.equal(exported.completed_at,null);
  assert.equal(exported.assessment.units[0].description,description.value);
  assert.equal(exported.assessment.baseline.assertions[0].verdict,null);
  // Exercise the actual async file-input handler, not importDraft directly.
  const input = document.getElementById('import');
  const file = {size:JSON.stringify(blank).length, text:async()=>JSON.stringify(blank)};
  Object.defineProperty(input,'files',{configurable:true,value:[file]});
  input.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(resolve=>setImmediate(resolve));
  assert(document.getElementById('status').textContent.includes('草稿已导入'));
  assert.equal(required().querySelector('textarea').value,blank.assessment.units[0].description);
  assert.equal(document.getElementById('attest').checked,false);
  const bad = structuredClone(blank);bad.packet_sha256='f'.repeat(64);
  Object.defineProperty(input,'files',{configurable:true,value:[{size:1000,text:async()=>JSON.stringify(bad)}]});
  input.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(resolve=>setImmediate(resolve));
  assert(document.getElementById('status').textContent.includes('不属于'));
  assert.equal(required().querySelector('textarea').value,blank.assessment.units[0].description);
  const longEvidence = baseline().querySelector('details pre');
  assert(longEvidence.textContent.includes('中文长引用'));
  assert(longEvidence.textContent.length>1000);
  assert.equal(document.querySelectorAll('script[src],link[href],img,iframe').length,0);
  assert.equal(issues.length,0,issues.join('\n'));
  console.log(JSON.stringify({status:'passed',environment:'jsdom DOM-only; no visual layout or native file-dialog verification',checks:['initial_render','add_delete_required_unit','add_delete_assertion','blank_completion_rejected','edit_clears_attestation','unsigned_draft_export','draft_import_roundtrip','wrong_packet_import_rejected','long_chinese_evidence_preserved','no_external_resource_elements'],completed_annotations:0,real_human_labels:0},null,2));
  dom.window.close();
}
main().catch(error=>{dom.window.close();console.error(error);process.exitCode=1;});
