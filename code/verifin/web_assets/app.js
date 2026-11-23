"use strict";
const token = document.querySelector('meta[name="verifin-token"]').content;
const el = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let selected = null, selectedReport = "", configured = false, timer = null, reportRequest = 0, refreshRequest = 0;
let currentSession = null, chartData = null, chartVersion = null, editingSession = null, editingScope = null;
let currentData = null, defaultContextChars = 200000;
const pendingRequests = new Map();
const postingRequests = new Set();

async function api(path, body) {
  const options = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json","X-VeriFin-Token":token}, body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Object.assign(Error(data.error || "Request failed"), {status:response.status});
  return data;
}
function fail(error) { el("error").textContent = error.message || error; }
function sessionPath(sid = selected) { return "/api/session/" + encodeURIComponent(sid); }
function number(value) { return typeof value === "number" ? value.toLocaleString(undefined, {maximumFractionDigits:6}) : "Unavailable"; }

function pendingRequest(sid) {
  if (pendingRequests.has(sid)) return pendingRequests.get(sid);
  try { return JSON.parse(localStorage.getItem("verifin/request/" + sid)); }
  catch { return null; }
}

function rememberRequest(sid, body) {
  try {
    const key = "verifin/request/" + sid;
    if (body) localStorage.setItem(key, JSON.stringify(body));
    else localStorage.removeItem(key);
    pendingRequests.delete(sid);
  } catch { pendingRequests.set(sid, body); }
}

function clearRequest(sid, id) {
  if (pendingRequest(sid)?.request_id === id) rememberRequest(sid, null);
}

function unfinished(receipt) {
  return receipt?.status === "pending" || receipt?.status === "running";
}

function renderRequest(sid, data) {
  const pending = pendingRequest(sid);
  const latest = data?.latest_submission;
  const request = pending || latest;
  const needsRecovery = Boolean(pending || unfinished(latest));
  const busy = data?.running || postingRequests.has(sid);
  el("ask").disabled = !data || !configured || busy || needsRecovery;
  el("recover").disabled = !data || busy;
  el("recoverId").placeholder = request?.request_id || "Request ID";
  el("requestInfo").textContent = [request?.request_id, pending?.message].filter(Boolean).join(" · ");
  if (needsRecovery && !busy) el("recoverySettings").open = true;
}

async function sessions() {
  const list = await api("/api/sessions");
  el("sessionList").innerHTML = list.map(s => `<button class="session ${s.id === selected ? "selected" : ""}" data-id="${esc(s.id)}">${esc(s.title)}</button>`).join("");
  el("sessionList").querySelectorAll("button").forEach(button => button.onclick = () => selectSession(button.dataset.id).catch(fail));
  return list;
}
async function selectSession(sid) {
  if (timer) clearTimeout(timer);
  selected = sid; selectedReport = ""; currentSession = null; currentData = null; chartData = null; reportRequest++;
  try { sessionStorage.setItem("verifin/session", sid); } catch { /* Session can still be selected from the list. */ }
  el("contextChars").value = "";
  el("asOf").value = ""; el("recoverId").value = ""; el("requestResult").textContent = "";
  el("recoverySettings").open = false;
  renderRequest(sid, null);
  el("error").textContent = ""; el("report").textContent = "Loading saved report…";
  el("sources").textContent = ""; el("reportVersion").disabled = true;
  el("charts").hidden = true; el("editScope").disabled = true;
  el("cancel").disabled = true;
  el("scopeDialog").close();
  await Promise.all([sessions(), refresh()]);
}
async function refresh() {
  if (!selected) return;
  const request = ++refreshRequest, sid = selected, pending = pendingRequest(sid);
  const query = pending?.request_id ? "?request_id=" + encodeURIComponent(pending.request_id) : "";
  const data = await api(sessionPath(sid) + query);
  if (sid !== selected || request !== refreshRequest) return;
  const receipt = data.requested_submission || data.latest_submission;
  if (receipt?.request_id === pending?.request_id && receipt?.status && !unfinished(receipt)) {
    clearRequest(sid, receipt.request_id);
    el("requestResult").textContent = receipt.question || "";
    if (!selectedReport && receipt.report_version && receipt.report_version !== data.current_report_version) {
      selectedReport = String(receipt.report_version);
    }
  }
  currentData = data;
  currentSession = data.session;
  el("contextChars").placeholder = String(currentSession.max_context_chars ?? defaultContextChars);
  el("heading").textContent = data.session.title;
  renderScope(data.session.scope);
  el("conversation").innerHTML = data.messages.map(m => `<div class="message ${m.role === "user" ? "user" : ""}"><span class="role">${esc(m.role)}</span>${esc(m.content)}</div>`).join("");
  renderRequest(sid, data);
  el("cancel").disabled = !data.running;
  el("editScope").disabled = data.running;
  const reviewedScope = data.episode?.reviewed_scope_version;
  const scopeChanged = reviewedScope !== undefined && reviewedScope !== data.session.scope_version;
  const currentReport = data.reports.find(r => r.version === data.current_report_version);
  el("status").textContent = data.running ? "Researching" : (scopeChanged ? "Scope changed · verification required" : (currentReport?.status || data.episode?.status || "Ready"));
  el("activity").innerHTML = data.events.slice(-8).reverse().map(e => `<p><b>${esc(e.type)}</b> ${esc(e.role || e.tool || "")} ${esc(e.summary || e.error_type || "")}</p>`).join("") || "No work recorded.";
  renderUsage(data.session_usage);
  if (data.error) fail(data.error);
  if (selectedReport && !data.reports.some(r => String(r.version) === selectedReport)) selectedReport = "";
  el("reportVersion").innerHTML = '<option value="">Current question</option>' + data.reports.map(r => `<option value="${esc(r.version)}">Version ${esc(r.version)} · saved as ${esc(r.archived_status || r.status)} · ${esc((r.request || "").slice(0,70))}</option>`).join("");
  el("reportVersion").value = selectedReport;
  el("reportVersion").disabled = !data.reports.length;
  if (timer) clearTimeout(timer);
  if (data.running) scheduleRefresh(sid);
  await loadReport();
}

function scheduleRefresh(sid) {
  timer = setTimeout(async () => {
    try { await refresh(); }
    catch (error) {
      if (sid === selected) { fail(error); scheduleRefresh(sid); }
    }
  }, 1200);
}
function renderUsage(usage) {
  if (!usage) { el("sessionUsage").textContent = "Usage unavailable."; return; }
  const cells = [[usage.model_calls,"Model attempts"],[usage.tool_calls,"Tool attempts"],[usage.planner_steps_complete ? usage.planner_steps : null,"Planner steps"],[usage.episodes,"Episodes"],[usage.active_seconds,"Active seconds"],[usage.prompt_tokens + usage.completion_tokens,"Known input/output tokens"]];
  el("sessionUsage").innerHTML = '<div class="usage-grid">' + cells.map(([value,label]) => `<div><strong>${esc(number(value))}</strong><small>${label}</small></div>`).join("") + '</div>' +
    `<p class="muted">${usage.token_usage_complete ? "All recorded model attempts reported input/output tokens." : "Token totals are incomplete: some calls did not report usage. Missing usage is not zero."} Cumulative across episodes; clarification is counted once. Monetary cost requires an explicit price configuration.</p>`;
}
function renderScope(scope) {
  const fields = [["Companies", (scope.entities || []).join(", ")], ["Period", [scope.period_start, scope.period_end].filter(Boolean).join(" → ")], ["Information cutoff", scope.cutoff], ["Metrics", (scope.metrics || []).join(", ")]];
  el("scope").innerHTML = fields.filter(([,value]) => value).map(([label,value]) => `<p><b>${esc(label)}</b><br>${esc(value)}</p>`).join("") +
    ((scope.requirements || []).length ? '<p><b>Required answers</b></p><ul>' + scope.requirements.map(r => `<li>${esc(r.text)}</li>`).join("") + '</ul>' : '<p class="muted">Required answers have not been resolved yet.</p>');
}
function renderSources(data, label) {
  const link = url => /^https?:\/\//i.test(url || "") ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开原文</a>` : "";
  const origin = (kind, id) => data.episode_material_ids?.[kind]?.includes(id) ? " · 本轮留存" : "";
  el("sources").innerHTML = `<p class="muted">${esc(label)}</p>` + [...(data.evidence || []).map(e => `<details><summary>${esc(e.entity)} · ${esc(e.metric || e.kind)}${origin("evidence", e.id)}</summary>${link(e.source_url)}<pre>${esc(JSON.stringify(e,null,2))}</pre></details>`), ...(data.calculations || []).map(c => `<details><summary>${esc(c.operation)} = ${esc(c.value)} ${esc(c.unit)}${origin("calculations", c.id)}</summary><pre>${esc(JSON.stringify(c,null,2))}</pre></details>`)].join("");
}
function renderReportClaims(r) {
  const claims = r.claims || [];
  const background = new Set(r.background_claim_ids || []);
  const verdict = c => {
    if (!r.verification_available || c.stale || !c.critic_verdict) return "";
    const v = c.critic_verdict;
    return `<p>${esc(v.basis)}</p><small>${esc(v.evidence_refs.join(", "))}</small>`;
  };
  const delivered = c => r.verification_available && !c.stale && c.status === "Partially Supported" && c.critic_verdict?.supported_text
    ? c.critic_verdict.supported_text : c.text;
  const card = c => `<div class="finding"><span class="badge">${esc(!r.verification_available || c.stale ? "未核验草稿" : c.status)}</span><p>${esc(delivered(c))}</p>${verdict(c)}${c.display_check ? "<p class=\"muted\">" + esc(c.display_check.label) + "</p>" : ""}<small>${esc([c.id, ...(c.evidence_ids || []), ...(c.calculation_ids || [])].filter(Boolean).join(", "))}</small></div>`;
  const answers = claims.filter(c => !background.has(c.id)).map(card).join("");
  const context = claims.filter(c => background.has(c.id)).map(card).join("");
  return answers + (context ? "<h3>Supporting background</h3>" + context : "");
}
function renderDeliveryStage(r) {
  if (!r.stage_message) return "";
  const evidence = r.episode_material_ids?.evidence?.length || 0;
  const calculations = r.episode_material_ids?.calculations?.length || 0;
  const material = evidence || calculations ? `<p>本轮留存 ${evidence} 条证据、${calculations} 项计算，可在来源列表中查看。</p>` : "";
  return `<div class="finding"><strong>${esc(r.stage_label)}</strong><p>${esc(r.stage_message)}</p>${material}</div>`;
}
function renderQuestionAnswers(rows) {
  if (!rows?.length) return "";
  return "<h3>Answers to your questions</h3>" + rows.map(a => {
    const text = [a.answer_text || a.answer_sentence, a.missing_support, a.stage_message].filter(Boolean).join(" ");
    const refs = [...new Set([a.claim_id, ...(a.answer_fragments || []).flatMap(f => f.claim_ids),
      ...(a.source_table ? [] : [...(a.evidence_ids || []), ...(a.calculation_ids || [])])].filter(Boolean))];
    if (a.draft_claim_ids?.length) refs.push("草稿编号 " + a.draft_claim_ids.join(", "));
    const t = a.source_table;
    const link = t && /^https?:\/\//i.test(t.source_url) ? `<a href="${esc(t.source_url)}" target="_blank" rel="noopener noreferrer">原表</a>` : "";
    const gaps = (t?.gaps || []).filter(gap => !text.includes(gap));
    const table = t ? (text.includes(t.summary) ? "" : `<p>${esc(t.summary)}</p>`) +
      `<div class="table-wrap"><table><caption>原表逐项核算（${esc(t.unit)}，小计与净利润不重复相加）</caption>` +
      `<thead><tr>${t.headers.map(v => `<th scope="col">${esc(v)}</th>`).join("")}</tr></thead>` +
      `<tbody>${t.rows.map(values => `<tr>${values.map(v => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` +
      `<small>${link} · ${esc(t.locator)}</small><details><summary>逐项证据与计算编号</summary><ul>` +
      t.trace.map(row => `<li>${esc(row.label)}：${esc([row.current_id,row.prior_id,row.contribution_id].filter(Boolean).join(", "))}</li>`).join("") +
      (a.attribution_sources || []).map(s => {
        const link = /^https?:\/\//i.test(s.source_url) ? `<a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">原文</a>` : "";
        return `<li>${esc(s.subject_label || s.display_label)}（管理层解释） · ${link} · ${esc(s.evidence_id)} · ${esc(s.locator)}</li>`;
      }).join("") +
      `</ul></details>${gaps.length ? `<p>${esc(gaps.join(" "))}</p>` : ""}` : "";
    return `<div class="finding"><strong>${esc(a.question)}</strong><p>${esc(a.status_label || a.status)} — ${esc(text)}</p><small>${esc(refs.join(", "))}</small>${table}</div>`;
  }).join("");
}
async function loadReport() {
  const sid = selected, version = selectedReport || currentData?.current_report_version, request = ++reportRequest;
  if (!sid || !currentData) return;
  if (!version) {
    const waiting = currentData.episode?.status === "waiting_for_clarification";
    el("report").textContent = waiting ? "Awaiting your clarification. See the question in the conversation." : "No report yet for the current question.";
    el("charts").hidden = true;
    if (currentData.episode || currentData.running) el("sources").textContent = "";
    else renderSources(currentData, "Current session evidence");
    return;
  }
  const r = await api(sessionPath(sid) + "/report?version=" + encodeURIComponent(version));
  if (sid !== selected || request !== reportRequest) return;
  el("report").innerHTML = (String(r.model_mode || "").startsWith("scripted_") ? '<p class="warning">Scripted integration test: planning and review are fixtures. No live model was called. Source provenance is recorded separately.</p>' : "") + '<p class="muted">Model mode: ' + esc(r.model_mode || "unknown") + '</p>' +
    `<p><b>Version ${esc(r.version)} · ${esc(r.status)}</b> · ${esc(r.stop_reason)}</p><p>${esc(r.request)}</p><p class="muted">Saved report snapshot. Findings and sources reflect this version; the current session or request may have changed.</p><details><summary>Scope used for this report · ${esc(r.scope_version)}</summary><pre>${esc(JSON.stringify(r.scope || {},null,2))}</pre></details>` +
    (r.stop_reason === "answer_mapping_needs_review" ? '<p>The saved version was marked complete. Its answer links now show an unresolved question.</p>' : "") +
    renderDeliveryStage(r) +
    (r.stop_reason === "local_context_limit" && r.version === currentData.current_report_version && !currentData.running && !el("contextSetting").hidden
      ? '<button type="button" id="adjustContext">调整字符上限并准备重试</button>' : "") +
    (r.skill_notes || []).map(n => `<div class="finding"><p>${esc(n.text)}</p><small>${esc([...(n.evidence_ids || []),...(n.calculation_ids || [])].join(", "))}</small></div>`).join("") +
    (r.skill_gap ? `<p>${esc(r.skill_gap)}</p>` : "") +
    renderQuestionAnswers(r.answer_sections || r.question_answers) +
    (r.limit_statements?.length ? '<details><summary>当前资料能确认到哪里</summary>' +
      r.limit_statements.map(text => `<p>${esc(text)}</p>`).join("") + '</details>' : "") +
    renderReportClaims(r) +
    '<h3>Unresolved items</h3>' + (r.unanswered || []).map(i => `<p>${esc(i.text)}</p>`).join("") + (r.issues || []).map(i => `<p>${esc(i.target_id)}: ${esc(i.display_type || i.issue_type)} — ${esc(i.missing_support)}</p>`).join("") +
    (r.tool_fault ? `<p>${esc(r.tool_fault.tool)}: ${esc(r.tool_fault.reason)}</p>` : "") +
    `<p class="muted">Model attempts: ${esc(r.usage?.model_calls)} · Tool attempts: ${esc(r.usage?.tool_calls)} · Active seconds: ${esc(number(r.usage?.active_seconds))}</p><div class="links">` +
    [["report.md","Markdown report"],["report.json","Full report JSON"],["evidence.csv","Evidence CSV"],["calculations.csv","Calculations CSV"]].map(([file,label]) => `<a href="${sessionPath(sid)}/${file}?version=${encodeURIComponent(r.version)}">${label}</a>`).join("") + '</div>';
  renderSources(r, "Sources saved with report version " + r.version);
  const adjust = el("adjustContext");
  if (adjust) adjust.onclick = () => {
    el("requestSettings").open = true;
    if (!el("message").value.trim()) el("message").value = "重新回答上一问";
    el("contextChars").focus();
  };
  const charts = await api(sessionPath(sid) + "/charts.json?version=" + encodeURIComponent(r.version));
  if (sid !== selected || request !== reportRequest) return;
  chartData = charts; chartVersion = r.version;
  el("charts").hidden = false;
  const previous = el("chartGroup").value;
  el("chartGroup").innerHTML = charts.groups.map(g => `<option value="${esc(g.id)}">${esc(g.title)} · ${esc(g.unit)} · ${esc(g.period_type)} · ${esc(g.business_scope)} · ${esc(g.accounting_basis)}</option>`).join("");
  if (charts.groups.some(g => g.id === previous)) el("chartGroup").value = previous;
  el("chartGroup").disabled = !charts.groups.length;
  renderChart();
}
function renderChart() {
  const group = chartData?.groups.find(g => g.id === el("chartGroup").value);
  const excluded = chartData?.excluded || [];
  const notes = excluded.length ? `<details><summary>${excluded.length} observations omitted from charts</summary>${excluded.map(item => `<p>${esc(item.record_id)}: ${esc(item.reason)}</p>`).join("")}</details>` : "";
  if (!group) { el("chartContent").innerHTML = '<p class="empty">No comparable numeric observations in this report.</p>' + notes; return; }
  const url = sessionPath() + "/chart.svg?version=" + encodeURIComponent(chartVersion) + "&group=" + encodeURIComponent(group.id);
  const visual = group.points.length <= 120 ? `<img class="chart" src="${url}" alt="${esc(group.title)} values by company and exact period"><a href="${url}" download="verifin-chart.svg">Download chart (SVG)</a>` : '<p class="muted">More than 120 observations: use the table and data export.</p>';
  el("chartContent").innerHTML = `<p class="muted">${esc(group.note)} Bars use a zero baseline. Exact periods and values are listed below.</p>` + visual +
    '<details><summary>Values and source references</summary><div class="table-wrap"><table><thead><tr><th>Company</th><th>Period</th><th>Value</th><th>Source / calculation IDs</th></tr></thead><tbody>' +
    group.points.map(p => `<tr><td>${esc(p.entity)}</td><td>${esc(p.period_start)} → ${esc(p.period_end)}</td><td>${esc(number(p.value))} ${esc(group.unit)}</td><td>${esc(p.record_ids.join(", "))}</td></tr>`).join("") + '</tbody></table></div></details>' + notes;
}

el("reportVersion").onchange = () => { selectedReport = el("reportVersion").value; el("charts").hidden = true; loadReport().catch(fail); };
el("chartGroup").onchange = renderChart;
el("create").onclick = async () => {
  try { const s = await api("/api/sessions", {title:el("title").value || "Untitled"}); el("title").value = ""; await selectSession(s.id); }
  catch (error) { fail(error); }
};
async function submitRequest(sid, body) {
  postingRequests.add(sid);
  renderRequest(sid, currentData);
  el("error").textContent = "";
  try {
    let response;
    try { response = await api(sessionPath(sid) + "/ask", body); }
    catch (error) {
      if (error.status === 400 || error.status === 404) clearRequest(sid, body.request_id);
      throw error;
    }
    if (!response.running || response.request_id !== body.request_id) clearRequest(sid, body.request_id);
    if (selected !== sid) return;
    el("recoverId").value = "";
    el("requestResult").textContent = response.question || "";
    selectedReport = response.report_version ? String(response.report_version) : "";
    if (el("message").value === body.message) {
      el("message").value = ""; el("contextChars").value = ""; el("asOf").value = "";
    }
    await refresh();
  } catch (error) {
    if (selected === sid) fail(error);
  } finally {
    postingRequests.delete(sid);
    if (selected === sid) renderRequest(sid, currentData);
  }
}

el("askForm").onsubmit = async event => {
  event.preventDefault(); if (!selected) return;
  const sid = selected;
  if (el("ask").disabled || pendingRequest(sid)) return;
  try {
    const override = el("contextChars").value;
    const body = {message:el("message").value, request_id:crypto.randomUUID(),
      budget:{max_model_calls:Number(el("modelCalls").value), max_tool_calls:Number(el("toolCalls").value),
        max_planner_steps:Number(el("plannerSteps").value), max_seconds:Number(el("seconds").value)}};
    if (el("asOf").value.trim()) body.as_of = el("asOf").value.trim();
    if (override) {
      const limit = Number(override);
      if (!Number.isSafeInteger(limit) || limit < 1) throw Error("请输入正整数作为字符上限。");
      body.max_context_chars = limit;
    }
    rememberRequest(sid, body);
    await submitRequest(sid, body);
  } catch (error) { fail(error); }
};
el("recover").onclick = async () => {
  if (!selected || el("recover").disabled) return;
  const sid = selected, pending = pendingRequest(sid);
  const id = el("recoverId").value.trim() || pending?.request_id || currentData?.latest_submission?.request_id;
  if (!id) { fail("Enter a request ID."); return; }
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(id)) { fail("Enter a valid request ID."); return; }
  const body = pending?.request_id === id ? pending : {request_id:id};
  if (!pending) rememberRequest(sid, body);
  await submitRequest(sid, body);
};
el("cancel").onclick = async () => { try { await api(sessionPath() + "/cancel", {}); el("status").textContent = "Stopping…"; } catch (error) { fail(error); } };
el("editScope").onclick = () => {
  if (!currentSession) return;
  editingSession = {id:currentSession.id, version:currentSession.scope_version};
  editingScope = JSON.parse(JSON.stringify(currentSession.scope));
  el("scopeEntities").value = (editingScope.entities || []).join(", ");
  el("scopeMetrics").value = (editingScope.metrics || []).join(", ");
  el("scopeStart").value = editingScope.period_start || "";
  el("scopeEnd").value = editingScope.period_end || "";
  el("scopeCutoff").value = editingScope.cutoff || "";
  el("scopeRequirements").value = (editingScope.requirements || []).map(r => r.text).join("\n");
  el("scopeError").textContent = ""; el("scopeDialog").showModal();
};
el("scopeDismiss").onclick = () => el("scopeDialog").close();
el("scopeForm").onsubmit = async event => {
  event.preventDefault();
  if (!editingSession || editingSession.id !== selected) return;
  const list = id => el(id).value.split(",").map(x => x.trim()).filter(Boolean);
  const texts = el("scopeRequirements").value.split("\n").map(x => x.trim()).filter(Boolean);
  const used = new Set();
  const requirements = texts.map(text => {
    const prior = (editingScope.requirements || []).find(r => r.text === text && !used.has(r.id));
    const id = prior ? prior.id : "req:" + crypto.randomUUID(); used.add(id); return {id,text};
  });
  const scope = {...editingScope, entities:list("scopeEntities"), metrics:list("scopeMetrics"), period_start:el("scopeStart").value || null, period_end:el("scopeEnd").value || null, cutoff:el("scopeCutoff").value.trim() || null, requirements};
  const sid = editingSession.id;
  el("scopeSave").disabled = true;
  try { await api(sessionPath(sid) + "/scope", {scope, expected_version:editingSession.version}); el("scopeDialog").close(); if (selected === sid) await refresh(); }
  catch (error) { el("scopeError").textContent = error.message; }
  finally { el("scopeSave").disabled = false; }
};
(async () => {
  const config = await api("/api/config"); configured = config.configured;
  el("contextSetting").hidden = Boolean(config.offline);
  if (config.max_context_chars) defaultContextChars = config.max_context_chars;
  el("config").textContent = config.offline ? "离线试用 · 可以查看已保存的真实模型回答。新问题只使用本地资料，不产生 API 费用。" : configured ? "Model configured: " + config.model + ". Inspect evidence and unresolved items." : "Model is not configured. Set VERIFIN_API_KEY, VERIFIN_BASE_URL and VERIFIN_MODEL in the server environment, then restart. Saved reports, scope editing and charts remain available.";
  const list = await sessions();
  let sid;
  try { sid = sessionStorage.getItem("verifin/session"); } catch { /* Browser storage may be disabled. */ }
  if (sid && list.some(session => session.id === sid)) await selectSession(sid);
})().catch(fail);
