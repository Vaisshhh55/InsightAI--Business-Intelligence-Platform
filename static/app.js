let currentDatasetId = "demo";
let currentAnalysis = null;
let charts = {};

const elements = {
  rowCount: document.getElementById("rowCount"),
  columnCount: document.getElementById("columnCount"),
  qualityScore: document.getElementById("qualityScore"),
  userProfile: document.getElementById("userProfile"),
  logoutBtn: document.getElementById("logoutBtn"),
  growthRate: document.getElementById("growthRate"),
  summaryText: document.getElementById("summaryText"),
  recommendationsList: document.getElementById("recommendationsList"),
  segmentsList: document.getElementById("segmentsList"),
  featureList: document.getElementById("featureList"),
  statusBox: document.getElementById("statusBox"),
  pageTitle: document.getElementById("pageTitle"),
  metricColumn: document.getElementById("metricColumn"),
  metricTotal: document.getElementById("metricTotal"),
  metricAverage: document.getElementById("metricAverage"),
  qualityScoreAnalytics: document.getElementById("qualityScoreAnalytics"),
  analyticsNarrative: document.getElementById("analyticsNarrative"),
  forecastDetails: document.getElementById("forecastDetails"),
  reportSummary: document.getElementById("reportSummary"),
  chatMessages: document.getElementById("chatMessages"),
};

function showView(viewName) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === viewName));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.dataset.viewSection === viewName));
  const titleMap = {
    overview: "Overview",
    upload: "Upload Dataset",
    analytics: "Analytics",
    forecasting: "Forecasting",
    assistant: "AI Assistant",
    reports: "Reports",
    settings: "Settings",
  };
  elements.pageTitle.textContent = titleMap[viewName] || "InsightAI";
  if (viewName === 'admin') {
    loadAdminPanel();
  }
}

function setSidebarCollapsed(isCollapsed) {
  document.getElementById("sidebar").classList.toggle("collapsed", isCollapsed);
}

function addChatMessage(text, sender) {
  const bubble = document.createElement("div");
  bubble.className = `message ${sender}`;
  bubble.textContent = text;
  elements.chatMessages.appendChild(bubble);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

function renderAnalysis(analysis) {
  currentAnalysis = analysis;
  currentDatasetId = analysis.dataset_name || currentDatasetId;
  elements.rowCount.textContent = analysis.row_count;
  elements.columnCount.textContent = analysis.column_count;
  elements.qualityScore.textContent = `${analysis.quality_score}/100`;
  elements.growthRate.textContent = `${analysis.growth_rate}%`;
  elements.metricColumn.textContent = analysis.metric_column || "—";
  elements.metricTotal.textContent = analysis.metric_total;
  elements.metricAverage.textContent = analysis.metric_average;
  elements.qualityScoreAnalytics.textContent = `${analysis.quality_score}/100`;
  elements.summaryText.textContent = `The uploaded dataset contains ${analysis.row_count} rows and ${analysis.column_count} columns. The system detected ${analysis.metric_column} as the primary metric and scored data quality at ${analysis.quality_score}/100.`;
  elements.analyticsNarrative.textContent = analysis.executive_narrative || `InsightAI identified ${analysis.feature_importance.length} key drivers and projected ${analysis.forecast.length} future points based on the current business trend.`;
  elements.reportSummary.textContent = `The current report package is based on ${analysis.dataset_name} and includes executive metrics, forecasts, and AI-generated recommendations.`;
  elements.recommendationsList.innerHTML = analysis.recommendations.map((item) => `<li>${item}</li>`).join("");
  elements.segmentsList.innerHTML = analysis.top_categories.length
    ? analysis.top_categories.map((item) => `<li>${item.name}: ${item.value}</li>`).join("")
    : "<li>No prominent segments detected.</li>";
  elements.featureList.innerHTML = analysis.feature_importance.length
    ? analysis.feature_importance.map((item) => `<li>${item.name}: ${item.score}</li>`).join("")
    : "<li>Explainability will appear once there are enough numeric drivers.</li>";
  elements.forecastDetails.innerHTML = analysis.forecast.length
    ? analysis.forecast.map((value, index) => `<div class="setting-row"><span>Forecast ${index + 1}</span><strong>${value}</strong></div>`).join("")
    : "<div class=\"card-copy\">Forecast insights will appear after the data is analyzed.</div>";
  renderForecastPlotly("forecastChart", analysis.forecast, analysis.time_series);
  renderForecastPlotly("forecastChartDetailed", analysis.forecast, analysis.time_series);
  populateFilters(analysis);
  awaitFetchAggregations();
  addChatMessage(analysis.assistant_response || "The assistant is ready to answer based on your dataset.", "bot");
}

function renderForecastPlotly(containerId, forecast, timeSeries) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const labels = timeSeries.length ? timeSeries.map((p) => p.label) : ["Current"];
  const values = timeSeries.length ? timeSeries.map((p) => p.value) : [0];
  const futureLabels = Array.from({ length: forecast.length }, (_, i) => `F+${i + 1}`);
  const traceHistory = { x: labels.concat(futureLabels), y: values.concat(forecast), mode: 'lines+markers', fill: 'tozeroy', line: {color: '#24d1b6'} };
  const layout = { margin: { t: 20, r: 20, l: 40, b: 40 }, template: 'plotly_white' };
  Plotly.react(el, [traceHistory], layout, {responsive: true});
}

function populateFilters(analysis) {
  const yearSelect = document.getElementById('filterYear');
  const catSelect = document.getElementById('filterCategory');
  if (!analysis.time_series) return;
  // populate years from time_series labels if they include year
  const years = new Set();
  analysis.time_series.forEach(ts => {
    if (ts.label) {
      const parts = ts.label.split(' ');
      const y = parts[parts.length - 1];
      if (/^\d{4}$/.test(y)) years.add(y);
    }
  });
  yearSelect.innerHTML = '<option value="all">All</option>' + Array.from(years).sort().map(y => `<option value="${y}">${y}</option>`).join('');
  catSelect.innerHTML = '<option value="all">All</option>' + (analysis.dataset_columns||[]).slice(0,5).map(c => `<option value="${c}">${c}</option>`).join('');
  yearSelect.addEventListener('change', () => awaitFetchAggregations());
  catSelect.addEventListener('change', () => awaitFetchAggregations());
}

async function awaitFetchAggregations(){
  try{
    const year = document.getElementById('filterYear')?.value || 'all';
    const category = document.getElementById('filterCategory')?.value || 'all';
    const resp = await fetch(`/api/aggregations?dataset_id=${encodeURIComponent(currentDatasetId)}&year=${year}&category=${encodeURIComponent(category)}`);
    const payload = await resp.json();
    if (payload.error) return;
    // update charts with returned series
    renderForecastPlotly('forecastChartForecast', payload.forecast || [], payload.time_series || []);
  }catch(e){
    console.warn('aggregation fetch failed', e);
  }
}

async function authenticate(endpoint) {
  const email = document.getElementById("emailInput").value;
  const password = document.getElementById("passwordInput").value;
  const response = await fetch(`/auth/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const payload = await response.json();
  const authStatus = document.getElementById("authStatus");
  if (!response.ok) {
    authStatus.textContent = payload.error || "Authentication failed.";
    return;
  }
  authStatus.textContent = payload.message || "Authentication completed.";
  await refreshSession();
  // fetch csrf token into session for subsequent requests
  try{
    const r = await fetch('/auth/csrf', {credentials: 'same-origin'});
    const p = await r.json();
    window._csrf = p.csrf_token;
  }catch(e){}
}

async function ensureCsrf(){
  if(window._csrf) return window._csrf;
  try{
    const r = await fetch('/auth/csrf', {credentials: 'same-origin'});
    if(!r.ok) return null;
    const p = await r.json();
    window._csrf = p.csrf_token;
    return window._csrf;
  }catch(e){
    return null;
  }
}

async function refreshSession() {
  const response = await fetch("/auth/session", { credentials: 'same-origin' });
  if (!response.ok) {
    window.location.href = "/";
    return;
  }
  const payload = await response.json();
  if (!payload.authenticated) {
    window.location.href = "/";
    return;
  }
  if (elements.userProfile) {
    const email = payload.user?.email || "user";
    const role = payload.user?.role || 'user';
    elements.userProfile.innerHTML = `
      <div class="avatar">${email.charAt(0).toUpperCase()}</div>
      <div>
        <strong>${email}</strong>
        <p>${role}</p>
      </div>
    `;
    document.querySelectorAll('.admin-only').forEach(el => { el.style.display = (role === 'admin') ? '' : 'none' });
    document.getElementById('profileBtn')?.addEventListener('click', () => { window.location.href = '/profile' });
  }
}

async function logout() {
  await fetch("/auth/logout", { method: "POST", credentials: 'same-origin' });
  window.location.href = "/";
}

async function analyzeFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  const statusBox = document.getElementById("statusBox");
  if (statusBox) statusBox.textContent = "Analyzing your dataset...";
  await ensureCsrf();
  const headers = {};
  if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
  let response = await fetch("/upload", { method: "POST", body: formData, headers, credentials: 'same-origin' });
  if(response.status === 403){
    // try to refresh token once and retry
    await ensureCsrf();
    if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
    response = await fetch("/upload", { method: "POST", body: formData, headers, credentials: 'same-origin' });
  }
  const analysis = await response.json();
  if (analysis.error) {
    if (statusBox) statusBox.textContent = analysis.error;
    return;
  }
  renderAnalysis(analysis);
  if (statusBox) statusBox.textContent = `Analysis completed for ${analysis.dataset_name}.`;
}

function wireEvents() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => showView(item.dataset.view));
  });
  document.querySelectorAll("[data-view-link]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewLink));
  });
  // admin nav click
  document.querySelectorAll('.admin-only').forEach(el => el.addEventListener('click', () => showView('admin')));
  document.getElementById("sidebarToggle").addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    const collapsed = sidebar.classList.contains("collapsed");
    setSidebarCollapsed(!collapsed);
  });
  document.getElementById("mobileToggle").addEventListener("click", () => setSidebarCollapsed(false));
  document.getElementById("uploadForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("fileInput");
    if (!fileInput.files[0]) {
      const statusBox = document.getElementById("statusBox");
      if (statusBox) statusBox.textContent = "Choose a file first.";
      return;
    }
    await analyzeFile(fileInput.files[0]);
  });
  const profileBtn = document.getElementById('profileBtn');
  if (profileBtn) {
    profileBtn.addEventListener('click', () => {
      window.location.href = '/profile';
    });
  }
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', async () => {
      await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
      window.location.href = '/';
    });
  }
  document.getElementById("assistantButton").addEventListener("click", async () => {
    const input = document.getElementById("assistantInput");
    if (!input.value.trim()) return;
    addChatMessage(input.value, "user");
    await ensureCsrf();
    const headers = { "Content-Type": "application/json" };
    if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
    let response = await fetch("/assistant", { method: "POST", headers, body: JSON.stringify({ dataset_id: currentDatasetId, message: input.value }), credentials: 'same-origin' });
    if(response.status === 403){
      await ensureCsrf();
      if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
      response = await fetch("/assistant", { method: "POST", headers, body: JSON.stringify({ dataset_id: currentDatasetId, message: input.value }), credentials: 'same-origin' });
    }
    const payload = await response.json();
    addChatMessage(payload.reply, "bot");
    input.value = "";
  });
  document.getElementById("exportExcelBtn").addEventListener("click", () => {
    if (currentAnalysis) window.location.href = `/export-report/${currentDatasetId}`;
  });
  document.getElementById("exportPdfBtn").addEventListener("click", () => {
    if (currentAnalysis) window.location.href = `/export-pdf/${currentDatasetId}`;
  });
  document.getElementById("registerButton").addEventListener("click", () => authenticate("register"));
  document.getElementById("loginButton").addEventListener("click", () => authenticate("login"));
  if (elements.logoutBtn) {
    elements.logoutBtn.addEventListener("click", async ()=>{
      const headers = {};
      if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
      await fetch('/auth/logout', {method:'POST', headers});
      logout();
    });
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  wireEvents();
  await refreshSession();
  showView("overview");
  renderAnalysis({
    dataset_name: "demo",
    row_count: 0,
    column_count: 0,
    quality_score: 0,
    growth_rate: 0,
    metric_column: "—",
    metric_total: 0,
    metric_average: 0,
    forecast: [],
    time_series: [],
    top_categories: [],
    feature_importance: [],
    recommendations: ["Upload a dataset to generate AI-driven recommendations."],
    assistant_response: "The assistant is ready to answer based on your dataset.",
  });
});

async function loadAdminPanel(){
  const container = document.querySelector('[data-view-section="admin"]');
  if(!container){
    // create a simple admin view
    const adminSection = document.createElement('section');
    adminSection.className = 'view';
    adminSection.setAttribute('data-view-section','admin');
    adminSection.innerHTML = `<div class="card"><div class="card-header"><div><p class="card-kicker">Admin</p><h3>Administration Console</h3></div></div><div style="display:flex; gap:16px; flex-wrap:wrap;"><div style="flex:1 1 320px;"><h4>Users</h4><div id="adminUsers"></div></div><div style="flex:1 1 320px;"><h4>Audit Logs</h4><div id="adminAudit"></div></div><div style="flex:1 1 640px;"><h4>Datasets</h4><div id="adminDatasets"></div></div></div></div>`;
    document.querySelector('.main-panel').appendChild(adminSection);
  }
  try{
    const resp = await fetch('/admin/users');
    const payload = await resp.json();
    const list = document.getElementById('adminUsers');
    if(!list) return;
    list.innerHTML = payload.users.map(u => `<div style="padding:8px;border-bottom:1px solid #eee; display:flex; justify-content:space-between;"><div><strong>${u.email}</strong><div>${u.role}</div></div><div><button data-id="${u.id}" class="promoteBtn">Promote</button> <button data-id="${u.id}" class="demoteBtn">Demote</button> <button data-id="${u.id}" class="removeBtn">Remove</button></div></div>`).join('');
    document.querySelectorAll('.promoteBtn').forEach(btn => btn.addEventListener('click', async (e)=>{
      const id = e.target.dataset.id;
      const headers = {};
      if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
      await fetch(`/admin/promote/${id}`, {method:'POST', headers});
      await loadAdminPanel();
    }));
    document.querySelectorAll('.demoteBtn').forEach(btn => btn.addEventListener('click', async (e)=>{
      const id = e.target.dataset.id;
      const headers = {};
      if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
      await fetch(`/admin/demote/${id}`, {method:'POST', headers});
      await loadAdminPanel();
    }));
    document.querySelectorAll('.removeBtn').forEach(btn => btn.addEventListener('click', async (e)=>{
      if(!confirm('Remove this user? This is irreversible.')) return;
      const id = e.target.dataset.id;
      const headers = {};
      if(window._csrf) headers['X-CSRF-Token'] = window._csrf;
      await fetch(`/admin/remove_user/${id}`, {method:'POST', headers});
      await loadAdminPanel();
    }));
    // load audit logs with simple pagination
    try{
      const auditOffset = window._admin_audit_offset || 0;
      const a = await fetch(`/admin/audit_logs?limit=50&offset=${auditOffset}`);
      const pa = await a.json();
      const auditEl = document.getElementById('adminAudit');
      if(auditEl){
        auditEl.innerHTML = `<div style="display:flex; gap:8px; align-items:center; margin-bottom:8px;"><button id="auditPrev">Prev</button><button id="auditNext">Next</button></div>` + pa.audit_logs.map(l => `<div style="padding:6px;border-bottom:1px solid #f3f3f3;"><small>${l.created_at}</small><div><strong>${l.action}</strong> target: ${l.target} by user:${l.user_id}</div><div>${l.details}</div></div>`).join('');
        document.getElementById('auditPrev').addEventListener('click', ()=>{
          window._admin_audit_offset = Math.max(0, (window._admin_audit_offset||0) - 50);
          loadAdminPanel();
        });
        document.getElementById('auditNext').addEventListener('click', ()=>{
          window._admin_audit_offset = (window._admin_audit_offset||0) + 50;
          loadAdminPanel();
        });
      }
    }catch(e){}
    // load datasets
    try{
      const d = await fetch('/admin/datasets');
      const pd = await d.json();
      const dsEl = document.getElementById('adminDatasets');
      if(dsEl){
        dsEl.innerHTML = pd.datasets.map(ds => `<div style="padding:8px;border-bottom:1px solid #eee;"><strong>${ds.dataset_id}</strong> (${ds.filename})<div>rows:${ds.row_count} cols:${ds.column_count} q:${ds.quality_score}</div></div>`).join('');
      }
    }catch(e){}
  }catch(e){
    console.warn('admin panel load failed', e);
  }
}
