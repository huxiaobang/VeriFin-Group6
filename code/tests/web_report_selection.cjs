// Run the delivered UI script with deterministic HTTP replies and a minimal DOM.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

test("the default follows the current question while history stays explicitly selectable", async () => {
  const nodes = new Map();
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
      textContent: "", innerHTML: "", value: "", close() {}, querySelectorAll() { return []; }
    });
    return nodes.get(id);
  }
  const questions = ["How were sales?", "What was cash flow?"];
  const summaries = questions.map((request, i) => ({version:i+1, request, status:"partial"}));
  const state = {
    reports:summaries.slice(0,1), current_report_version:null, session:{id:"s", scope:{}},
    episode:{id:"b", status:"waiting_for_clarification"}, messages:[], events:[]
  };
  const routes = [];
  async function fetch(path) {
    routes.push(path);
    let data;
    if (path === "/api/config") data = {configured:true, offline:true};
    else if (path === "/api/sessions") data = [];
    else if (path === "/api/session/s") data = JSON.parse(JSON.stringify(state));
    else if (path.includes("/report?version=")) data = summaries.at(Number(path.split("=").at(-1))-1);
    else if (path.includes("/charts.json?version=")) data = {groups:[]};
    else throw Error("Unexpected route: " + path);
    return {ok:true, json: async () => data};
  }
  const context = vm.createContext({document:{querySelector:() => ({content:"test"}), getElementById:element},
    fetch, setTimeout, clearTimeout});
  vm.runInContext(readFileSync(join(__dirname, "../verifin/web_assets/app.js"), "utf8"), context);
  await vm.runInContext('selectSession("s")', context);
  assert.match(element("report").textContent, /clarification/i);
  assert.equal(routes.some(p => p.includes("/report")), false);
  element("reportVersion").value = "1";
  await vm.runInContext('el("reportVersion").onchange()', context);
  // The change event starts asynchronous rendering.
  await new Promise(resolve => setImmediate(resolve));
  assert.match(element("report").innerHTML, /How were sales/);
  element("reportVersion").value = "";
  await vm.runInContext('el("reportVersion").onchange()', context);
  assert.match(element("report").textContent, /clarification/i);
  Object.assign(state, {reports:summaries, current_report_version:2, episode:{id:"b", status:"partial"}});
  await vm.runInContext("refresh()", context);
  assert.match(element("report").innerHTML, /What was cash flow/);
  assert.match(element("report").innerHTML, /report\.md\?version=2/);
  assert.ok(routes.includes("/api/session/s/charts.json?version=2"));
});
