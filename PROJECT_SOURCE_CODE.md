# Project source listing

Generated from the project files. The local `backend/.env` and `backend/.venv` are excluded.

> **Snapshot notice (2026-10-28):** This listing predates the current future-forecast work and is not an authoritative copy of the live source. Use the files in `frontend/`, `backend/app/`, `database/`, and `arduino/` as the current implementation. The current sensor compatibility and forecasting behavior is documented in `README.md`. In particular, `cloudiness_index` remains a legacy relative-index field and is never treated as NTU.

## AGENTS.md

````text
# Repository Guidelines — Life Under Water

## Scope
Draft 1 is a wired USB-serial IoT water-quality monitoring dashboard. Stack: Arduino C++ / NodeMCU ESP8266, HTML/CSS/vanilla JavaScript, Chart.js, Python FastAPI, PostgreSQL. No Wi-Fi dependency.

## Architecture
Sensors → NodeMCU → USB serial → FastAPI → PostgreSQL → FastAPI API → browser dashboard.
The browser must not read the COM port or access PostgreSQL directly.

## Current scope
Implement only Overview, Analytics, and Devices. No Demo Mode, Settings, Alerts, Reports, CSV export, ML, Random Forest, or XGBoost in Draft 1.

## Engineering
- Inspect and reuse existing files before changes.
- Make the smallest complete change; avoid unnecessary dependencies and abstractions.
- Keep raw voltage/counts separate from calibrated values.
- Never invent sensor readings or replace missing values with zero.
- Never present uncalibrated voltage as ppm/NTU or claim water is safe based only on these sensors.
- Use UTC timestamps in storage and local time in the UI.
- Keep secrets in environment variables; never commit credentials.
- Use parameterized SQL and validate API inputs.
- Test the relevant behavior and report only checks actually performed.
```

## README.md

````text
# Life Under Water — Draft 1

Responsive vanilla HTML/CSS/JavaScript dashboard with a FastAPI backend contract and PostgreSQL starter schema.

## Structure

```text
life_under_water/
├── AGENTS.md
├── README.md
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── api.js
│       ├── app.js
│       └── charts.js
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   └── app/
│       ├── __init__.py
│       └── main.py
└── database/schema.sql
```

## Run the frontend

From the project root:

```bash
python -m http.server 5500 --directory frontend
```

Open http://127.0.0.1:5500.

## Run the backend

Python 3.10+ is recommended.

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Use `cp .env.example .env` on macOS/Linux instead of `copy`.

The backend exposes `/api/health`, `/api/status`, `/api/overview`, `/api/readings/latest`, `/api/readings/history`, `/api/analytics/summary`, `/api/readings/pull`, and `/api/devices`. Until serial ingestion and database persistence are implemented, endpoints return honest empty/unavailable states rather than fabricated sensor readings.

## PostgreSQL

Create a database named `life_under_water`, then apply `database/schema.sql`. Set `DATABASE_URL` in `backend/.env`. Draft 1 includes the schema but the starter backend does not yet persist readings; that is the next integration step.

## Important sensor note

Temperature is measured in °C. TDS ppm and turbidity NTU must remain unavailable until the sensor modules are calibrated against suitable reference instruments. The UI supports calibration-pending states and will not convert raw voltage to calibrated units by guessing.
```

## frontend/index.html

````html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#071321">
  <title>Life Under Water | Monitoring</title>
  <link rel="stylesheet" href="./css/styles.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script defer src="./js/api.js"></script>
  <script defer src="./js/charts.js"></script>
  <script defer src="./js/app.js"></script>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar" id="sidebar">
      <a class="brand" href="#overview" aria-label="Life Under Water overview">
        <span class="brand-mark">◉</span>
        <span><strong>Life Under Water</strong><small>WATER QUALITY SYSTEM</small></span>
      </a>
      <div class="nav-caption">MONITORING</div>
      <nav class="nav" aria-label="Main navigation">
        <button class="nav-link active" data-page="overview"><span class="nav-icon">⌂</span>Overview</button>
        <button class="nav-link" data-page="analytics"><span class="nav-icon">⌁</span>Analytics</button>
        <button class="nav-link" data-page="devices"><span class="nav-icon">▣</span>Devices</button>
      </nav>
      <div class="sidebar-bottom">
        <div class="connection-line"><span class="status-dot warn" id="sidebarDot"></span><span id="sidebarConnection">Checking connection</span></div>
        <small>Draft 1 · Local monitoring</small>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <button class="mobile-menu" id="mobileMenu" aria-label="Toggle navigation">☰</button>
        <div class="page-heading"><span class="eyebrow">WATER QUALITY MONITORING</span><h1 id="pageTitle">Overview</h1></div>
        <div class="topbar-right">
          <span class="mode-label">USB SERIAL · API</span>
          <span class="system-pill"><span class="status-dot warn" id="headerDot"></span><span id="headerStatus">Connecting</span></span>
        </div>
      </header>
      <div id="pageContent" class="page-content" aria-live="polite">
        <section class="loading-panel"><span class="spinner"></span><p>Loading dashboard…</p></section>
      </div>
      <footer class="footer"><span>Life Under Water</span><span>Wired sensor monitoring</span><span id="footerUpdated">Waiting for sensor data</span></footer>
    </main>
  </div>
  <div id="toast" class="toast" role="status" aria-live="polite"></div>
</body>
</html>
```

## frontend/css/styles.css

````css
:root{
 --bg:#071321;--sidebar:#0b1928;--panel:#122337;--panel2:#152a40;--border:#243b51;
 --cyan:#36d7d1;--blue:#57b8ec;--green:#42d3a0;--amber:#f4bd58;--red:#ff737d;
 --text:#f1f6fb;--muted:#9fb2c6;--muted2:#718ba5;--radius:15px;
 --shadow:0 12px 30px rgba(0,0,0,.12)
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
button,select{font:inherit}
button{cursor:pointer}
button:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid var(--cyan);outline-offset:3px}
.app-shell{min-height:100vh;display:grid;grid-template-columns:244px minmax(0,1fr)}
.sidebar{background:var(--sidebar);border-right:1px solid #1d3043;padding:20px 14px 18px;display:flex;flex-direction:column;min-height:100vh;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:12px;padding:0 5px 28px;color:var(--text);text-decoration:none}
.brand-mark{width:40px;height:40px;border-radius:13px;background:#123b4a;color:var(--cyan);display:grid;place-items:center;font-size:25px;flex-shrink:0}
.brand strong{font-size:15px;display:block;letter-spacing:-.2px}
.brand small{font-size:10px;letter-spacing:1.7px;color:var(--muted2);display:block;margin-top:4px}
.nav-caption,.eyebrow{font-size:10px;letter-spacing:2px;color:#79a4c8;font-weight:700}
.nav-caption{padding:0 10px 10px}
.nav{display:grid;gap:6px}
.nav-link{display:flex;align-items:center;gap:13px;border:0;border-left:2px solid transparent;border-radius:9px;background:transparent;color:#a9c0d6;text-align:left;padding:13px 12px;font-size:15px;transition:.18s}
.nav-link:hover{background:#11293a;color:#fff}
.nav-link.active{background:#123342;border-left-color:var(--cyan);color:var(--cyan);font-weight:650}
.nav-icon{width:18px;text-align:center;font-size:21px;line-height:1;color:#8db9dd}
.nav-link.active .nav-icon{color:var(--cyan)}
.sidebar-bottom{margin-top:auto;border-top:1px solid var(--border);padding:15px 7px 0;color:var(--muted);font-size:12px}
.sidebar-bottom small{display:block;color:var(--muted2);font-size:11px;margin-top:12px}
.connection-line{display:flex;align-items:center;gap:8px}
.status-dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--muted2);flex-shrink:0}
.status-dot.ok{background:var(--green);box-shadow:0 0 12px #42d3a044}
.status-dot.warn{background:var(--amber);box-shadow:0 0 12px #f4bd5844}
.status-dot.bad{background:var(--red);box-shadow:0 0 12px #ff737d44}
.main{min-width:0;display:flex;flex-direction:column;padding:0 30px}
.topbar{min-height:90px;border-bottom:1px solid #1c3043;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:18px 0}
.page-heading h1{font-size:28px;line-height:1.2;margin:5px 0 0;font-weight:720;letter-spacing:-.7px}
.topbar-right{display:flex;align-items:center;gap:18px}
.mode-label{font-size:10px;letter-spacing:1.5px;color:#7fa5c5}
.system-pill{display:flex;align-items:center;gap:8px;background:#102237;border:1px solid var(--border);padding:9px 12px;border-radius:100px;font-size:13px;color:#c9d8e7;white-space:nowrap}
.mobile-menu{display:none;background:var(--panel);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:8px 11px}
.page-content{padding:24px 0 30px;flex:1;min-height:calc(100vh - 145px)}
.page-intro{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:20px}
.page-intro h2{font-size:19px;margin:0 0 4px}
.page-intro p{color:var(--muted);margin:0;font-size:14px}
.muted{color:var(--muted)}
.grid{display:grid;gap:16px}
.metric-grid{grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:18px}
.metric-card,.panel,.device-card{background:linear-gradient(145deg,var(--panel),#101f31);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);min-width:0}
.metric-card{padding:19px 20px;position:relative;overflow:hidden}
.metric-top{display:flex;justify-content:space-between;align-items:center;gap:10px}
.metric-label{font-size:12px;letter-spacing:.6px;text-transform:uppercase;color:#b4c5d6;font-weight:650}
.metric-icon{height:39px;width:39px;display:grid;place-items:center;border-radius:12px;background:#16384a;color:var(--cyan);font-size:20px}
.metric-value{font-size:31px;line-height:1.25;letter-spacing:-.8px;font-weight:750;margin:13px 0 7px;overflow-wrap:anywhere}
.metric-value .unit{font-size:16px;color:#c0d1e0;font-weight:550;letter-spacing:0}
.metric-meta{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.tag{display:inline-flex;align-items:center;gap:6px;border-radius:6px;padding:4px 8px;font-size:11px;font-weight:700;white-space:nowrap}
.tag.ok{background:#123e36;color:#6ce0b3}.tag.warn{background:#47371b;color:#ffd17b}.tag.bad{background:#48242d;color:#ff9aa2}.tag.neutral{background:#20364b;color:#b4cce1}
.overview-grid{grid-template-columns:minmax(0,1.65fr) minmax(270px,1fr);align-items:stretch}
.panel{padding:20px}
.panel-header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:16px}
.panel-header h2,.panel h2{font-size:17px;line-height:1.35;margin:0;font-weight:680}
.panel-header p{margin:5px 0 0;color:var(--muted);font-size:13px}
.activity-list{display:grid;gap:0}
.activity-item{display:grid;grid-template-columns:10px minmax(0,1fr);gap:11px;padding:13px 0;border-bottom:1px solid #22374b}
.activity-item:last-child{border-bottom:0;padding-bottom:0}
.activity-bullet{width:9px;height:9px;border-radius:50%;background:var(--blue);margin-top:6px}
.activity-item strong{font-size:13px;font-weight:600;display:block}
.activity-item small{font-size:12px;color:var(--muted);display:block;margin-top:3px}
.status-list{display:grid;gap:0}
.status-row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 0;border-bottom:1px solid #22374b}
.status-row:last-child{border-bottom:0}
.status-name{display:flex;align-items:center;gap:10px;font-size:14px}
.status-name small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.suitability-grid{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:18px}
.suitability-card{background:#102135;border:1px solid var(--border);border-radius:12px;padding:16px}
.suitability-card h3{font-size:14px;margin:0 0 8px}
.suitability-card p{font-size:12px;color:var(--muted);margin:0}
.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.select,.btn{border:1px solid var(--border);border-radius:9px;padding:10px 13px;background:#11243a;color:var(--text);font-size:13px}
.select{min-width:120px}
.btn{font-weight:650;transition:.18s}
.btn:hover{border-color:#3e718b;background:#173249}
.btn.primary{background:#126274;border-color:#1c8792;color:#fff}
.btn.primary:hover{background:#16798b}
.btn:disabled{opacity:.55;cursor:not-allowed}
.chart-grid{grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:18px}
.chart-panel{min-height:310px}
.chart-wrap{height:235px;position:relative}
.chart-wrap canvas{max-width:100%}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:600px}
th,td{text-align:left;padding:12px 11px;border-bottom:1px solid #263c51;font-size:13px;white-space:nowrap}
th{color:#a9c1d5;font-weight:650;font-size:12px}
tbody tr:last-child td{border-bottom:0}
td{color:#e0eaf3}
.device-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.device-card{padding:20px}
.device-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.device-symbol{height:44px;width:44px;border-radius:12px;background:#16384a;color:var(--cyan);display:grid;place-items:center;font-size:22px}
.device-card h3{font-size:17px;margin:14px 0 3px}
.device-card p{font-size:13px;color:var(--muted);margin:0}
.device-details{margin-top:17px;border-top:1px solid var(--border);padding-top:8px}
.device-details .status-row{padding:10px 0}
.empty-state,.loading-panel{display:flex;align-items:center;justify-content:center;flex-direction:column;text-align:center;min-height:180px;padding:26px;border:1px dashed #2a455c;border-radius:13px;background:#0c1b2a;color:var(--muted)}
.empty-state strong{color:var(--text);font-size:15px;margin-bottom:5px}
.empty-state p,.loading-panel p{font-size:13px;margin:0}
.loading-panel{min-height:320px;border:0;background:transparent}
.spinner{width:28px;height:28px;border:3px solid #254155;border-top-color:var(--cyan);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.footer{border-top:1px solid #1c3043;padding:16px 0;display:flex;justify-content:space-between;gap:12px;color:var(--muted2);font-size:11px}
.toast{position:fixed;right:22px;bottom:22px;max-width:min(420px,calc(100vw - 44px));padding:13px 17px;background:#18354a;border:1px solid #31536a;color:#fff;border-radius:10px;box-shadow:var(--shadow);opacity:0;transform:translateY(10px);pointer-events:none;transition:.2s;z-index:20}
.toast.show{opacity:1;transform:translateY(0)}
@media(max-width:1100px){.app-shell{grid-template-columns:220px minmax(0,1fr)}.main{padding:0 22px}.overview-grid{grid-template-columns:1fr}.chart-grid{grid-template-columns:1fr}.chart-panel{min-height:300px}}
@media(max-width:760px){.app-shell{display:block}.sidebar{position:fixed;z-index:15;left:0;top:0;width:260px;transform:translateX(-105%);transition:transform .2s;box-shadow:var(--shadow)}.sidebar.open{transform:translateX(0)}.main{padding:0 16px}.topbar{min-height:82px;gap:10px}.mobile-menu{display:block}.page-heading{flex:1}.page-heading h1{font-size:24px}.eyebrow{font-size:9px;letter-spacing:1.2px}.mode-label{display:none}.system-pill{font-size:12px;padding:8px}.metric-grid{grid-template-columns:1fr}.metric-card{padding:17px}.metric-value{font-size:29px}.suitability-grid,.device-grid{grid-template-columns:1fr}.page-content{padding-top:18px}.page-intro{align-items:flex-start;flex-direction:column}.footer{flex-wrap:wrap}.panel{padding:16px}}
@media(min-width:761px) and (max-width:950px){.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.metric-card:last-child{grid-column:span 2}}
```

## frontend/js/api.js

````javascript
(() => {
  const API_BASE = window.LUW_API_BASE || "http://127.0.0.1:8000/api";
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`API returned HTTP ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }
  window.LUWApi = {
    base: API_BASE,
    health: () => request("/health"),
    status: () => request("/status"),
    overview: () => request("/overview"),
    latest: () => request("/readings/latest"),
    history: (range = "24h", sensor = "all") =>
      request(`/readings/history?range=${encodeURIComponent(range)}&sensor=${encodeURIComponent(sensor)}`),
    summary: (range = "24h") => request(`/analytics/summary?range=${encodeURIComponent(range)}`),
    devices: () => request("/devices"),
    pull: () => request("/readings/pull", { method: "POST" })
  };
})();
```

## frontend/js/app.js

````javascript
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const content = $("#pageContent");
  const state = { page: "overview", range: "24h", status: null, overview: null, latest: null, devices: null, history: null, summary: null, connected: false };
  const pageNames = { overview: "Overview", analytics: "Analytics", devices: "Devices" };
  let toastTimer;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
  }
  function fmtTime(value) {
    if (!value) return "Not available";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString();
  }
  function statusTag(status) {
    const s = String(status || "unavailable").toLowerCase();
    const cls = ["online","ok","valid","connected","available"].includes(s) ? "ok" :
      ["degraded","warning","stale","pending","calibration_pending"].includes(s) ? "warn" :
      ["offline","error","invalid","disconnected","critical"].includes(s) ? "bad" : "neutral";
    const label = ({ calibration_pending: "Calibration pending", unavailable: "Unavailable" })[s] || s.replaceAll("_"," ");
    return `<span class="tag ${cls}">${escapeHtml(label)}</span>`;
  }
  function valueText(value, unit, calibrationState) {
    if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) {
      return `<span class="muted">—</span><span class="unit"> ${escapeHtml(unit)}</span>`;
    }
    if (calibrationState && !["calibrated","valid","ok"].includes(String(calibrationState).toLowerCase())) {
      return `<span class="muted">—</span><span class="unit"> ${escapeHtml(unit)}</span>`;
    }
    return `${escapeHtml(Number(value).toLocaleString(undefined,{maximumFractionDigits:2}))}<span class="unit"> ${escapeHtml(unit)}</span>`;
  }
  function metricCard(title, icon, value, unit, updatedAt, sensorState, calibrationState) {
    const calibrated = calibrationState || "unavailable";
    const showValue = valueText(value, unit, calibrationState);
    const stateLabel = calibrationState && !["calibrated","valid","ok"].includes(String(calibrationState).toLowerCase())
      ? "Calibration pending" : (sensorState || "Waiting for data");
    return `<article class="metric-card">
      <div class="metric-top"><span class="metric-label">${title}</span><span class="metric-icon">${icon}</span></div>
      <div class="metric-value">${showValue}</div>
      <div class="metric-meta"><span>Updated ${escapeHtml(fmtTime(updatedAt))}</span>${statusTag(stateLabel)}</div>
    </article>`;
  }
  function emptyState(title, message) {
    return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
  }
  function activityMarkup(events) {
    if (!Array.isArray(events) || !events.length) return emptyState("No activity recorded", "System events will appear here when the backend receives device messages.");
    return `<div class="activity-list">${events.slice(0,8).map(e => `<div class="activity-item"><span class="activity-bullet"></span><div><strong>${escapeHtml(e.message || e.event || "System event")}</strong><small>${escapeHtml(fmtTime(e.timestamp))}</small></div></div>`).join("")}</div>`;
  }
  function sensorRows(sensors) {
    const entries = [
      ["NodeMCU ESP8266", sensors?.device?.status || sensors?.nodemcu_status || "unavailable", "USB serial device"],
      ["Temperature · DS18B20", sensors?.temperature?.status || "unavailable", "Temperature probe"],
      ["TDS sensor", sensors?.tds?.status || "unavailable", "Calibration required for ppm"],
      ["Turbidity sensor", sensors?.turbidity?.status || "unavailable", "Calibration required for NTU"]
    ];
    return `<div class="status-list">${entries.map(([name,status,sub]) => `<div class="status-row"><div class="status-name"><span class="status-dot ${["online","ok","connected","valid"].includes(String(status).toLowerCase())?"ok":(["offline","error","disconnected"].includes(String(status).toLowerCase())?"bad":"warn")}"></span><div>${escapeHtml(name)}<small>${escapeHtml(sub)}</small></div></div>${statusTag(status)}</div>`).join("")}</div>`;
  }
  function renderOverview() {
    const o = state.overview || {};
    const latest = state.latest || o.latest || {};
    const cal = o.calibration || latest.calibration || {};
    const t = latest.temperature || o.temperature || {};
    const d = latest.tds || o.tds || {};
    const u = latest.turbidity || o.turbidity || {};
    const timestamp = latest.timestamp || o.last_updated;
    const averages = o.averages || {};
    content.innerHTML = `
      <div class="page-intro"><div><h2>Water Quality Overview</h2><p>Live monitoring status and latest available sensor readings.</p></div><span class="muted">Last pull: ${escapeHtml(fmtTime(timestamp))}</span></div>
      <section class="grid metric-grid">
        ${metricCard("Water temperature","♨",t.value ?? t.celsius,"°C",t.timestamp || timestamp,t.status || "waiting_for_data","calibrated")}
        ${metricCard("Total dissolved solids","◉",d.value ?? d.ppm,"ppm",d.timestamp || timestamp,d.status,cal.tds || d.calibration_status || "calibration_pending")}
        ${metricCard("Turbidity","✧",u.value ?? u.ntu,"NTU",u.timestamp || timestamp,u.status,cal.turbidity || u.calibration_status || "calibration_pending")}
      </section>
      <section class="grid overview-grid">
        <article class="panel"><div class="panel-header"><div><h2>System Activity</h2><p>Recent device and sensor events</p></div>${statusTag(state.status?.system_status || (state.connected ? "online" : "offline"))}</div>${activityMarkup(o.activity || o.events)}</article>
        <article class="panel"><div class="panel-header"><div><h2>Sensor Status</h2><p>Connection and reading health</p></div></div>${sensorRows(state.status || o.sensor_status || {})}</article>
      </section>
      <section class="panel" style="margin-top:18px"><div class="panel-header"><div><h2>Average Sensor Values</h2><p>Window: ${escapeHtml(o.average_window || "Last 24 hours")} · Last data pull: ${escapeHtml(fmtTime(timestamp))}</p></div></div>
        <div class="grid suitability-grid">
          ${averageCard("Temperature", averages.temperature, "°C")}
          ${averageCard("TDS", averages.tds, "ppm")}
          ${averageCard("Turbidity", averages.turbidity, "NTU")}
        </div>
      </section>
      <section class="panel" style="margin-top:18px"><div class="panel-header"><div><h2>Purpose Suitability</h2><p>Assessments require calibrated data and suitable reference limits.</p></div></div>
        <div class="grid suitability-grid">
          ${suitabilityCard("Aquatic use", o.suitability?.aquatic)}
          ${suitabilityCard("Agricultural use", o.suitability?.agriculture)}
          ${suitabilityCard("Household use", o.suitability?.household)}
        </div>
      </section>`;
  }
  function averageCard(name, obj, unit) {
    const val = obj && (obj.value ?? obj.average);
    const usable = val !== null && val !== undefined && obj?.calibration_status === "calibrated";
    return `<div class="suitability-card"><h3>${name}</h3><div class="metric-value" style="font-size:25px">${usable ? `${escapeHtml(val)} <span class="unit">${unit}</span>` : "—"}</div><p>${usable ? `Valid samples: ${escapeHtml(obj.valid_samples ?? "Not available")}` : "Insufficient calibrated data"}</p></div>`;
  }
  function suitabilityCard(name, item) {
    const status = item?.status || "assessment_unavailable";
    return `<div class="suitability-card"><h3>${name}</h3><div style="margin:8px 0">${statusTag(status)}</div><p>${escapeHtml(item?.reason || "No validated assessment is available yet.")}</p></div>`;
  }
  function renderAnalytics() {
    const latest = state.latest || {};
    const summary = state.summary || {};
    const rows = [
      ["Temperature", latest.temperature?.value ?? latest.temperature?.celsius, "°C", "calibrated", summary.temperature],
      ["TDS", latest.tds?.value ?? latest.tds?.ppm, "ppm", latest.tds?.calibration_status || "calibration_pending", summary.tds],
      ["Turbidity", latest.turbidity?.value ?? latest.turbidity?.ntu, "NTU", latest.turbidity?.calibration_status || "calibration_pending", summary.turbidity]
    ];
    content.innerHTML = `
      <div class="page-intro"><div><h2>Water Quality Analytics</h2><p>Historical sensor readings, trends and statistical summary.</p></div>
        <div class="controls"><select class="select" id="rangeSelect" aria-label="Time range"><option value="1h" ${state.range==="1h"?"selected":""}>Last hour</option><option value="24h" ${state.range==="24h"?"selected":""}>Last 24 hours</option><option value="7d" ${state.range==="7d"?"selected":""}>Last 7 days</option></select><button class="btn" id="refreshAnalytics">Refresh Analytics</button><button class="btn primary" id="pullReading">Pull Latest Reading</button></div>
      </div>
      <section class="grid metric-grid">
        ${metricCard("Water temperature","♨",latest.temperature?.value ?? latest.temperature?.celsius,"°C",latest.temperature?.timestamp || latest.timestamp,latest.temperature?.status,"calibrated")}
        ${metricCard("Total dissolved solids","◉",latest.tds?.value ?? latest.tds?.ppm,"ppm",latest.tds?.timestamp || latest.timestamp,latest.tds?.status,latest.tds?.calibration_status || "calibration_pending")}
        ${metricCard("Turbidity","✧",latest.turbidity?.value ?? latest.turbidity?.ntu,"NTU",latest.turbidity?.timestamp || latest.timestamp,latest.turbidity?.status,latest.turbidity?.calibration_status || "calibration_pending")}
      </section>
      <section class="grid chart-grid">
        ${chartCard("Temperature Over Time","temperatureChart","°C")}
        ${chartCard("TDS Concentration Over Time","tdsChart","ppm")}
        ${chartCard("Turbidity Over Time","turbidityChart","NTU")}
      </section>
      <section class="panel"><div class="panel-header"><div><h2>Statistical Summary</h2><p>For the selected time range</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Sensor</th><th>Average</th><th>Minimum</th><th>Maximum</th><th>Latest</th><th>Valid samples</th><th>Status</th></tr></thead>
        <tbody>${rows.map(([name,value,unit,calStatus,stat]) => {
          const valid = stat && stat.calibration_status === "calibrated";
          const show = x => valid && x !== null && x !== undefined ? `${escapeHtml(x)} ${unit}` : "—";
          return `<tr><td>${name}</td><td>${show(stat?.average)}</td><td>${show(stat?.minimum)}</td><td>${show(stat?.maximum)}</td><td>${show(value)}</td><td>${escapeHtml(stat?.valid_samples ?? "—")}</td><td>${statusTag(calStatus)}</td></tr>`;
        }).join("")}</tbody></table></div>
      </section>`;
    $("#rangeSelect").addEventListener("change", e => { state.range = e.target.value; refreshAnalytics(); });
    $("#refreshAnalytics").addEventListener("click", refreshAnalytics);
    $("#pullReading").addEventListener("click", pullLatest);
    drawCharts();
  }
  function chartCard(title, canvasId, unit) {
    return `<article class="panel chart-panel"><div class="panel-header"><div><h2>${title}</h2><p>Selected range · ${unit}</p></div></div><div class="chart-wrap"><canvas id="${canvasId}" aria-label="${title}" role="img"></canvas></div></article>`;
  }
  function drawCharts() {
    if (!window.LUWCharts) return;
    const history = state.history || {};
    window.LUWCharts.draw("temperature","temperatureChart",history.temperature || [],"temperature","°C");
    window.LUWCharts.draw("tds","tdsChart",history.tds || [],"tds","ppm");
    window.LUWCharts.draw("turbidity","turbidityChart",history.turbidity || [],"turbidity","NTU");
  }
  function renderDevices() {
    const d = state.devices || {};
    const device = d.device || {};
    const sensors = d.sensors || {};
    const list = [
      {key:"temperature",name:"DS18B20 Temperature Sensor",icon:"♨",detail:"Measures water temperature",unit:"°C"},
      {key:"tds",name:"TDS Sensor",icon:"◉",detail:"Total dissolved solids · calibration required",unit:"ppm"},
      {key:"turbidity",name:"Turbidity Sensor",icon:"✧",detail:"Water clarity · calibration required",unit:"NTU"}
    ];
    content.innerHTML = `
      <div class="page-intro"><div><h2>Devices</h2><p>Hardware connection, sensor health and recent communication.</p></div><button class="btn" id="refreshDevices">Refresh Status</button></div>
      <section class="grid device-grid">
        <article class="device-card"><div class="device-top"><span class="device-symbol">▣</span>${statusTag(device.status || state.status?.device_status || "offline")}</div><h3>NodeMCU ESP8266</h3><p>USB serial monitoring device</p><div class="device-details">
          ${detailRow("Device ID",device.id || "Not reported")}
          ${detailRow("Connection",device.connection_type || "USB serial")}
          ${detailRow("Serial port",device.serial_port || "Not detected")}
          ${detailRow("Device uptime",device.uptime_seconds != null ? formatUptime(device.uptime_seconds) : "Unavailable")}
          ${detailRow("Last message",fmtTime(device.last_seen || device.last_message_at))}
          ${detailRow("Firmware",device.firmware_version || "Not reported")}
        </div></article>
        ${list.map(s => { const item=sensors[s.key] || {}; const value=item.value ?? item.celsius ?? item.ppm ?? item.ntu; return `<article class="device-card"><div class="device-top"><span class="device-symbol">${s.icon}</span>${statusTag(item.status || "unavailable")}</div><h3>${s.name}</h3><p>${s.detail}</p><div class="device-details">
          ${detailRow("Latest reading",value != null && (s.key==="temperature" || item.calibration_status==="calibrated") ? `${value} ${s.unit}` : "Unavailable")}
          ${detailRow("Last valid reading",fmtTime(item.timestamp))}
          ${detailRow("Calibration",item.calibration_status || (s.key==="temperature"?"Not verified":"Pending"))}
          ${detailRow("Error",item.error || "None reported")}
        </div></article>`; }).join("")}
        <article class="device-card"><div class="device-top"><span class="device-symbol">⌘</span>${statusTag(state.connected ? "online" : "offline")}</div><h3>Backend & Serial Reader</h3><p>Local data collection service</p><div class="device-details">
          ${detailRow("API",state.connected ? "Reachable" : "Disconnected")}
          ${detailRow("Serial reader",state.status?.serial_reader_status || "Unavailable")}
          ${detailRow("Last successful pull",fmtTime(state.status?.last_data_at))}
        </div></article>
      </section>`;
    $("#refreshDevices").addEventListener("click", loadAll);
  }
  function detailRow(label, value) {
    return `<div class="status-row"><span class="muted">${escapeHtml(label)}</span><span style="text-align:right;font-size:13px">${escapeHtml(value)}</span></div>`;
  }
  function formatUptime(seconds) {
    const n = Math.max(0, Number(seconds) || 0), days = Math.floor(n/86400), hours = Math.floor(n%86400/3600), minutes = Math.floor(n%3600/60);
    return `${days ? `${days}d ` : ""}${hours}h ${minutes}m`;
  }
  function render() {
    $("#pageTitle").textContent = pageNames[state.page];
    document.querySelectorAll(".nav-link").forEach(b => b.classList.toggle("active", b.dataset.page === state.page));
    if (state.page === "overview") renderOverview();
    if (state.page === "analytics") renderAnalytics();
    if (state.page === "devices") renderDevices();
  }
  function setConnection(connected, status) {
    state.connected = connected;
    state.status = status || state.status;
    const text = connected ? (status?.system_status || "Connected") : "Backend disconnected";
    $("#headerStatus").textContent = text.replaceAll("_"," ");
    $("#sidebarConnection").textContent = connected ? "Backend connected" : "Backend disconnected";
    const cls = connected ? "ok" : "bad";
    $("#headerDot").className = `status-dot ${cls}`;
    $("#sidebarDot").className = `status-dot ${cls}`;
  }
  async function loadAll() {
    try {
      const [health, status, overview, latest, devices] = await Promise.all([
        LUWApi.health(), LUWApi.status(), LUWApi.overview(), LUWApi.latest(), LUWApi.devices()
      ]);
      state.overview = overview; state.latest = latest; state.devices = devices;
      setConnection(true, status);
      if (state.page === "analytics") await refreshAnalytics(false);
      render();
      $("#footerUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (err) {
      setConnection(false, null);
      if (!state.overview) state.overview = {};
      if (!state.latest) state.latest = {};
      if (!state.devices) state.devices = {};
      render();
      $("#footerUpdated").textContent = "Waiting for backend connection";
    }
  }
  async function refreshAnalytics(showToast = true) {
    try {
      const [history, summary, latest] = await Promise.all([
        LUWApi.history(state.range), LUWApi.summary(state.range), LUWApi.latest()
      ]);
      state.history = history; state.summary = summary; state.latest = latest;
      if (state.page === "analytics") renderAnalytics();
      if (showToast) showToastMessage("Analytics refreshed.");
    } catch (err) {
      state.history = {}; state.summary = {};
      if (state.page === "analytics") renderAnalytics();
      if (showToast) showToastMessage("Backend unavailable. Analytics could not be refreshed.");
    }
  }
  async function pullLatest() {
    const button = $("#pullReading");
    if (!button) return;
    button.disabled = true; button.textContent = "Requesting…";
    try {
      const result = await LUWApi.pull();
      showToastMessage(result.message || "Reading request accepted.");
      await loadAll();
    } catch (err) {
      showToastMessage("Could not request a reading. Check the backend and USB serial connection.");
    } finally {
      const b = $("#pullReading");
      if (b) { b.disabled = false; b.textContent = "Pull Latest Reading"; }
    }
  }
  function showToastMessage(message) {
    const toast = $("#toast"); toast.textContent = message; toast.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
  }
  document.querySelectorAll(".nav-link").forEach(button => button.addEventListener("click", () => {
    state.page = button.dataset.page;
    $("#sidebar").classList.remove("open");
    render();
    if (state.page === "analytics") refreshAnalytics(false);
    if (state.page === "devices") loadAll();
  }));
  $("#mobileMenu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  document.addEventListener("click", e => {
    if (window.innerWidth <= 760 && !e.target.closest("#sidebar") && !e.target.closest("#mobileMenu")) $("#sidebar").classList.remove("open");
  });
  render();
  loadAll();
  setInterval(() => { if (document.visibilityState === "visible") loadAll(); }, 10000);
})();
```

## frontend/js/charts.js

````javascript
(() => {
  const instances = {};
  const colors = { temperature: "#55c7ee", tds: "#43d3a7", turbidity: "#f2c65f" };
  function destroy(id) {
    if (instances[id]) { instances[id].destroy(); delete instances[id]; }
  }
  function draw(id, canvasId, points, sensor, unit) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return;
    destroy(id);
    const safePoints = Array.isArray(points) ? points.filter(p => p && Number.isFinite(Number(p.value)) && p.timestamp) : [];
    if (!safePoints.length) {
      const parent = canvas.parentElement;
      parent.innerHTML = `<div class="empty-state"><strong>No historical data yet</strong><p>Chart will appear when calibrated readings are stored.</p></div>`;
      return;
    }
    instances[id] = new Chart(canvas, {
      type: "line",
      data: {
        labels: safePoints.map(p => new Date(p.timestamp).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})),
        datasets: [{ label: unit, data: safePoints.map(p => Number(p.value)), borderColor: colors[sensor],
          backgroundColor: `${colors[sensor]}20`, fill: true, tension: .32, pointRadius: 2.5,
          pointHoverRadius: 5, borderWidth: 2 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${ctx.parsed.y} ${unit}` } } },
        scales: {
          x: { ticks: { color: "#8fa8bd", maxTicksLimit: 7 }, grid: { color: "#263b4e" } },
          y: { title: { display: true, text: unit, color: "#a8bdcf" }, ticks: { color: "#8fa8bd" }, grid: { color: "#263b4e" } }
        }
      }
    });
  }
  window.LUWCharts = { draw, destroyAll: () => Object.keys(instances).forEach(destroy) };
})();
```

## backend/requirements.txt

````text
fastapi>=0.110,<1.0
uvicorn[standard]>=0.27,<1.0

fastapi
uvicorn[standard]
psycopg[binary]
python-dotenv
pyserial
```

## backend/.env.example

````dotenv
DATABASE_URL=postgresql://postgres:password@localhost:5432/life_under_water
CORS_ORIGINS=http://127.0.0.1:5500,http://localhost:5500
```

## backend/app/__init__.py

_Empty file._

## backend/app/main.py

````python

from datetime import datetime, timedelta, timezone
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from app.database import get_connection

app = FastAPI(title="Life Under Water API", version="0.2.0")

origins = os.getenv(
    "CORS_ORIGINS",
    "http://127.0.0.1:5500,http://localhost:5500",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

DEVICE_KEY = os.getenv("DEVICE_KEY", "nodemcu-esp8266")
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}


def query_db(sql: str, params: tuple = (), *, one: bool = False):
    try:
        with get_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                if one:
                    return cur.fetchone()
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable. Check PostgreSQL and backend configuration.",
        ) from exc


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_device():
    return query_db(
        """
        SELECT id, device_key, display_name, firmware_version, created_at
        FROM devices
        WHERE device_key = %s
        LIMIT 1
        """,
        (DEVICE_KEY,),
        one=True,
    )


def latest_row():
    return query_db(
        """
        SELECT sr.*, d.device_key
        FROM sensor_readings sr
        JOIN devices d ON d.id = sr.device_id
        WHERE d.device_key = %s
        ORDER BY sr.recorded_at DESC, sr.id DESC
        LIMIT 1
        """,
        (DEVICE_KEY,),
        one=True,
    )


def sensor(value, unit, timestamp, valid, calibration_status, raw_voltage=None, raw_adc=None):
    if value is None:
        status = "waiting_for_data"
    elif valid:
        status = "ok"
    else:
        status = "invalid"

    return {
        "value": value,
        "unit": unit,
        "timestamp": timestamp,
        "status": status,
        "calibration_status": calibration_status,
        "raw_voltage": raw_voltage,
        "raw_adc": raw_adc,
    }


def format_latest(row):
    if not row:
        return {
            "timestamp": None,
            "temperature": sensor(None, "°C", None, False, "unverified"),
            "tds": sensor(None, "ppm", None, False, "calibration_pending"),
            "turbidity": sensor(None, "NTU", None, False, "calibration_pending"),
        }

    ts = row["recorded_at"]
    return {
        "timestamp": ts,
        "temperature": sensor(
            row["temperature_c"], "°C", ts,
            row["temperature_valid"], "unverified"
        ),
        "tds": sensor(
            row["tds_ppm"], "ppm", ts,
            row["tds_valid"], row["tds_calibration_status"],
            row["tds_voltage_v"], row["tds_raw_adc"]
        ),
        "turbidity": sensor(
            row["turbidity_ntu"], "NTU", ts,
            row["turbidity_valid"], row["turbidity_calibration_status"],
            row["turbidity_voltage_v"], row["turbidity_raw_adc"]
        ),
    }


def range_start(period: str):
    return datetime.now(timezone.utc) - RANGES[period]


def suitability(avg_temp, avg_tds, avg_turbidity):
    # These are deliberately not pass/fail water-safety certifications.
    return {
        "aquatic": {
            "status": "assessment_unavailable",
            "reason": "Species-specific limits and validated readings are required.",
        },
        "agriculture": {
            "status": "assessment_unavailable",
            "reason": "Crop, soil, irrigation and validated water-quality limits are required.",
        },
        "household": {
            "status": "assessment_unavailable",
            "reason": "These sensors alone cannot establish drinking-water safety.",
        },
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    row = query_db(
        "SELECT current_database() AS database, NOW() AS server_time",
        one=True,
    )
    return {
        "status": "ok",
        "service": "life-under-water-api",
        "database": row["database"],
        "timestamp": row["server_time"],
    }


@app.get("/api/status")
def status() -> dict[str, Any]:
    row = latest_row()
    last_data = row["recorded_at"] if row else None

    # A recent database row alone does not prove the USB connection is live.
    return {
        "system_status": "offline",
        "device_status": "unknown",
        "serial_reader_status": "not_configured",
        "last_data_at": last_data,
        "timestamp": now_iso(),
    }


@app.get("/api/readings/latest")
def readings_latest() -> dict[str, Any]:
    return format_latest(latest_row())


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    latest = latest_row()

    averages = query_db(
        """
        SELECT
            AVG(temperature_c) FILTER (WHERE temperature_valid) AS avg_temp,
            COUNT(*) FILTER (WHERE temperature_valid) AS temp_count,
            AVG(tds_ppm) FILTER (WHERE tds_valid) AS avg_tds,
            COUNT(*) FILTER (WHERE tds_valid) AS tds_count,
            AVG(turbidity_ntu) FILTER (WHERE turbidity_valid) AS avg_turbidity,
            COUNT(*) FILTER (WHERE turbidity_valid) AS turbidity_count
        FROM sensor_readings sr
        JOIN devices d ON d.id = sr.device_id
        WHERE d.device_key = %s
          AND sr.recorded_at >= NOW() - INTERVAL '24 hours'
        """,
        (DEVICE_KEY,),
        one=True,
    )

    events = query_db(
        """
        SELECT event_type, message, recorded_at
        FROM system_events se
        JOIN devices d ON d.id = se.device_id
        WHERE d.device_key = %s
        ORDER BY se.recorded_at DESC, se.id DESC
        LIMIT 10
        """,
        (DEVICE_KEY,),
    )

    return {
        "last_updated": latest["recorded_at"] if latest else None,
        "average_window": "Last 24 hours",
        "latest": format_latest(latest),
        "averages": {
            "temperature": {
                "value": averages["avg_temp"],
                "calibration_status": "unverified",
                "valid_samples": averages["temp_count"],
            },
            "tds": {
                "value": averages["avg_tds"],
                "calibration_status": "calibration_pending",
                "valid_samples": averages["tds_count"],
            },
            "turbidity": {
                "value": averages["avg_turbidity"],
                "calibration_status": "calibration_pending",
                "valid_samples": averages["turbidity_count"],
            },
        },
        "activity": [
            {
                "event_type": e["event_type"],
                "message": e["message"],
                "timestamp": e["recorded_at"],
            }
            for e in events
        ],
        "suitability": suitability(
            averages["avg_temp"], averages["avg_tds"], averages["avg_turbidity"]
        ),
    }


@app.get("/api/readings/history")
def readings_history(
    range: str = Query(default="24h", pattern="^(1h|24h|7d)$"),
    sensor: str = Query(default="all", pattern="^(all|temperature|tds|turbidity)$"),
) -> dict[str, Any]:
    rows = query_db(
        """
        SELECT sr.recorded_at, sr.temperature_c, sr.temperature_valid,
               sr.tds_ppm, sr.tds_valid, sr.turbidity_ntu, sr.turbidity_valid
        FROM sensor_readings sr
        JOIN devices d ON d.id = sr.device_id
        WHERE d.device_key = %s AND sr.recorded_at >= %s
        ORDER BY sr.recorded_at ASC
        """,
        (DEVICE_KEY, range_start(range)),
    )

    result = {"range": range, "sensor": sensor,
              "temperature": [], "tds": [], "turbidity": []}

    for row in rows:
        ts = row["recorded_at"]
        if sensor in ("all", "temperature") and row["temperature_valid"] and row["temperature_c"] is not None:
            result["temperature"].append({"timestamp": ts, "value": row["temperature_c"]})
        if sensor in ("all", "tds") and row["tds_valid"] and row["tds_ppm"] is not None:
            result["tds"].append({"timestamp": ts, "value": row["tds_ppm"]})
        if sensor in ("all", "turbidity") and row["turbidity_valid"] and row["turbidity_ntu"] is not None:
            result["turbidity"].append({"timestamp": ts, "value": row["turbidity_ntu"]})

    return result


@app.get("/api/analytics/summary")
def analytics_summary(
    range: str = Query(default="24h", pattern="^(1h|24h|7d)$")
) -> dict[str, Any]:
    row = query_db(
        """
        SELECT
          AVG(temperature_c) FILTER (WHERE temperature_valid) AS temp_avg,
          MIN(temperature_c) FILTER (WHERE temperature_valid) AS temp_min,
          MAX(temperature_c) FILTER (WHERE temperature_valid) AS temp_max,
          COUNT(*) FILTER (WHERE temperature_valid) AS temp_count,
          AVG(tds_ppm) FILTER (WHERE tds_valid) AS tds_avg,
          MIN(tds_ppm) FILTER (WHERE tds_valid) AS tds_min,
          MAX(tds_ppm) FILTER (WHERE tds_valid) AS tds_max,
          COUNT(*) FILTER (WHERE tds_valid) AS tds_count,
          AVG(turbidity_ntu) FILTER (WHERE turbidity_valid) AS turbidity_avg,
          MIN(turbidity_ntu) FILTER (WHERE turbidity_valid) AS turbidity_min,
          MAX(turbidity_ntu) FILTER (WHERE turbidity_valid) AS turbidity_max,
          COUNT(*) FILTER (WHERE turbidity_valid) AS turbidity_count
        FROM sensor_readings sr
        JOIN devices d ON d.id = sr.device_id
        WHERE d.device_key = %s AND sr.recorded_at >= %s
        """,
        (DEVICE_KEY, range_start(range)),
        one=True,
    )

    def stats(prefix, calibration):
        return {
            "average": row[f"{prefix}_avg"],
            "minimum": row[f"{prefix}_min"],
            "maximum": row[f"{prefix}_max"],
            "valid_samples": row[f"{prefix}_count"],
            "calibration_status": calibration,
        }

    return {
        "range": range,
        "temperature": stats("temp", "unverified"),
        "tds": stats("tds", "calibration_pending"),
        "turbidity": stats("turbidity", "calibration_pending"),
    }


@app.get("/api/devices")
def devices() -> dict[str, Any]:
    device = get_device()
    latest = latest_row()

    last_seen = latest["recorded_at"] if latest else None

    return {
        "device": {
            "id": device["device_key"] if device else DEVICE_KEY,
            "status": "unknown",
            "connection_type": "USB serial",
            "serial_port": os.getenv("SERIAL_PORT") or None,
            "uptime_seconds": None,
            "last_seen": last_seen,
            "firmware_version": device["firmware_version"] if device else None,
        },
        "sensors": {
            "temperature": {
                "status": "waiting_for_data" if not latest else (
                    "ok" if latest["temperature_valid"] else "invalid"
                ),
                "value": latest["temperature_c"] if latest else None,
                "timestamp": last_seen,
                "calibration_status": "unverified",
            },
            "tds": {
                "status": "waiting_for_data" if not latest else (
                    "ok" if latest["tds_valid"] else "calibration_pending"
                ),
                "value": latest["tds_ppm"] if latest else None,
                "timestamp": last_seen,
                "calibration_status": latest["tds_calibration_status"] if latest else "calibration_pending",
            },
            "turbidity": {
                "status": "waiting_for_data" if not latest else (
                    "ok" if latest["turbidity_valid"] else "calibration_pending"
                ),
                "value": latest["turbidity_ntu"] if latest else None,
                "timestamp": last_seen,
                "calibration_status": latest["turbidity_calibration_status"] if latest else "calibration_pending",
            },
        },
    }


@app.post("/api/readings/pull")
def readings_pull() -> dict[str, Any]:
    raise HTTPException(
        status_code=503,
        detail="USB serial reader is not configured yet.",
    )
```

## backend/app/database.py

````python

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing from backend/.env")

    return psycopg.connect(DATABASE_URL)
```

## backend/app/serial_reader.py

_Empty file._

## backend/test_db.py

````python

from app.database import get_connection


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), NOW()")
            database, server_time = cur.fetchone()

    print("Database connected successfully!")
    print("Database:", database)
    print("Server time:", server_time)


if __name__ == "__main__":
    main()
```

## database/schema.sql

````sql
CREATE TABLE IF NOT EXISTS devices (
    id BIGSERIAL PRIMARY KEY,
    device_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    firmware_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id BIGSERIAL PRIMARY KEY,
    device_id BIGINT REFERENCES devices(id),
    recorded_at TIMESTAMPTZ NOT NULL,
    temperature_c DOUBLE PRECISION,
    tds_raw_adc INTEGER,
    tds_voltage_v DOUBLE PRECISION,
    tds_ppm DOUBLE PRECISION,
    tds_calibration_status TEXT NOT NULL DEFAULT 'calibration_pending',
    turbidity_raw_adc INTEGER,
    turbidity_voltage_v DOUBLE PRECISION,
    turbidity_ntu DOUBLE PRECISION,
    turbidity_calibration_status TEXT NOT NULL DEFAULT 'calibration_pending',
    temperature_valid BOOLEAN NOT NULL DEFAULT FALSE,
    tds_valid BOOLEAN NOT NULL DEFAULT FALSE,
    turbidity_valid BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_recorded_at
    ON sensor_readings(recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_device_time
    ON sensor_readings(device_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS system_events (
    id BIGSERIAL PRIMARY KEY,
    device_id BIGINT REFERENCES devices(id),
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
