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

test("a pending question retains its request ID and options across reload and retries", async () => {
  const local = new Map(), tab = new Map(), submissions = [];
  const storage = map => ({getItem:key => map.get(key) ?? null, setItem:(key, value) => map.set(key, value),
    removeItem:key => map.delete(key)});
  const firstReport = {version:1, request:"Original question", status:"complete"};
  const newerReport = {version:2, request:"A separate question", status:"complete"};
  const state = {reports:[], session:{id:"s", title:"Research", scope:{}}, messages:[], events:[]};
  let nextId = 0;
  async function fetch(path, options) {
    let data, status = 200;
    if (path === "/api/config") data = {configured:true, offline:true};
    else if (path === "/api/sessions") data = [{id:"s", title:"Research"}, {id:"other", title:"Other"}];
    else if (path === "/api/session/s/ask") {
      const body = JSON.parse(options.body);
      submissions.push(body);
      if (submissions.length === 1) throw Error("Connection closed before acknowledgment");
      if (submissions.length === 2) {
        data = {running:true, request_id:body.request_id, status:"pending"};
        Object.assign(state, {running:true, requested_submission:data, latest_submission:data});
        status = 202;
      } else if (submissions.length === 3) {
        data = {error:"A research question is required"};
        status = 400;
      } else {
        // An earlier server can accept a new question without echoing its ID.
        data = {running:true};
        status = 202;
      }
    } else if (path === "/api/session/s" || path.startsWith("/api/session/s?request_id=")) data = state;
    else if (path === "/api/session/other") data = {...state, session:{id:"other", title:"Other", scope:{}}};
    else if (path.includes("/report?version=")) data = path.endsWith("=1") ? firstReport : newerReport;
    else if (path.includes("/charts.json?version=")) data = {groups:[]};
    else throw Error("Unexpected route: " + path);
    return {status, ok:status < 400, json:async () => data};
  }
  async function browser() {
    const nodes = new Map();
    function element(id) {
      if (!nodes.has(id)) nodes.set(id, {
        textContent:"", innerHTML:"", value:"", close() {}, querySelectorAll() { return []; }
      });
      return nodes.get(id);
    }
    const context = vm.createContext({document:{querySelector:() => ({content:"test"}), getElementById:element}, fetch,
      localStorage:storage(local), sessionStorage:storage(tab), crypto:{randomUUID:() => "intent-" + (++nextId)},
      setTimeout:() => 1, clearTimeout() {}});
    const run = source => vm.runInContext(source, context);
    await run(readFileSync(join(__dirname, "../verifin/web_assets/app.js"), "utf8"));
    return {element, run, submit:() => element("askForm").onsubmit({preventDefault() {}})};
  }

  let page = await browser();
  await page.run('selectSession("s")');
  for (const id of ["modelCalls", "toolCalls", "plannerSteps", "seconds"]) page.element(id).value = "3";
  page.element("message").value = firstReport.request;
  page.element("asOf").value = "2025-01-31T15:00:00-05:00";
  page.element("contextChars").value = "1000";
  await page.submit();
  assert.match(page.element("error").textContent, /Connection closed/);
  assert.equal(page.element("ask").disabled, true);
  assert.equal(JSON.parse(local.get("verifin/request/s")).request_id, "intent-1");
  await page.submit();
  assert.equal(submissions.length, 1);
  await page.run('selectSession("other")');
  assert.equal(page.element("ask").disabled, false);
  assert.equal(page.element("recoverySettings").open, false);
  await page.run('selectSession("s")');

  page = await browser();
  assert.equal(page.element("heading").textContent, "Research");
  assert.equal(page.element("recoverySettings").open, true);
  await page.element("recover").onclick();
  assert.deepEqual(submissions[1], submissions[0]);
  assert.equal(local.has("verifin/request/s"), true);
  const checkpoint = {request_id:"intent-1", status:"running", episode_id:"original"};
  Object.assign(state, {running:false, requested_submission:checkpoint, latest_submission:checkpoint});
  await page.run("refresh()");
  assert.equal(local.has("verifin/request/s"), true);
  assert.equal(page.element("ask").disabled, true);
  assert.equal(page.element("recover").disabled, false);
  Object.assign(state, {running:false, reports:[firstReport, newerReport], current_report_version:2,
    requested_submission:{request_id:"intent-1", status:"complete", report_version:1},
    latest_submission:{request_id:"different-client", status:"complete", report_version:2}});
  await page.run("refresh()");
  assert.equal(local.size, 0);
  assert.equal(page.element("reportVersion").value, "1");
  assert.match(page.element("report").innerHTML, /Original question/);

  page.element("message").value = firstReport.request;
  await page.submit();
  assert.equal(submissions[2].request_id, "intent-2");
  assert.equal(local.size, 0);
  assert.equal(page.element("ask").disabled, false);
  await page.submit();
  assert.equal(submissions[3].request_id, "intent-3");
  assert.equal(submissions[3].message, submissions[0].message);
  assert.equal(local.size, 0);
});
