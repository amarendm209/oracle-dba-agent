const el = (id) => document.getElementById(id);
const SNAPSHOT_TABS = [
  ["active_sessions", "Active sessions"],
  ["blocking_sessions", "Blocking"],
  ["current_waits", "Current waits"],
  ["top_wait_events", "Wait events"],
  ["top_sql", "Top SQL"],
  ["tablespaces", "Tablespaces"],
  ["io", "I/O"],
  ["long_operations", "Long ops"],
  ["segments", "Segments"],
  ["invalid_objects", "Invalid objects"],
  ["parameters", "Parameters"],
];

let snapshot = {};
let activeTab = "active_sessions";
let timer = null;

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

async function loadTarget() {
  const res = await fetch("/api/target");
  const t = await res.json();
  el("target").textContent = `${t.user ?? "no user"} @ ${t.dsn} · MCP: ${t.mcp_server}`;
}

async function loadTools() {
  const res = await fetch("/api/tools");
  if (!res.ok) return;
  const tools = await res.json();
  el("tools").innerHTML = "";
  tools.forEach((tool) => {
    const button = document.createElement("button");
    button.textContent = tool.name;
    button.title = tool.description;
    button.onclick = () => runTool(tool.name);
    el("tools").appendChild(button);
  });
}

async function runTool(name) {
  const out = el("tool-output");
  out.classList.remove("hidden");
  out.textContent = `calling ${name}…`;
  const res = await fetch(`/api/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const body = await res.json();
  out.textContent = JSON.stringify(res.ok ? body.result : body, null, 2);
}

function renderSummary(report) {
  const instance = report.instance || {};
  const sessions = report.sessions || {};
  const cards = [
    ["Status", report.status],
    ["Critical", report.counts.critical],
    ["Warnings", report.counts.warning],
    ["Active sessions", sessions.active_sessions ?? "—"],
    ["Blocked sessions", sessions.blocked_sessions ?? "—"],
    ["Instance", instance.instance_name ?? "—"],
    ["Uptime (h)", instance.uptime_hours ?? "—"],
  ];
  el("summary").innerHTML = cards
    .map(
      ([label, value]) =>
        `<div class="card"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(
          value
        )}</div></div>`
    )
    .join("");
}

function renderFindings(findings) {
  if (!findings.length) {
    el("findings").innerHTML = '<p class="empty">No findings — all rules passed.</p>';
    return;
  }
  el("findings").innerHTML = findings
    .map((f) => {
      const solutions = (f.solutions || [])
        .map((s) => `<li>${escapeHtml(s)}</li>`)
        .join("");
      const sql = (f.remediation_sql || [])
        .map((s) => `<pre>${escapeHtml(s)}</pre>`)
        .join("");
      const evidence = f.evidence && f.evidence.length
        ? `<details><summary>Evidence (${f.evidence.length})</summary><pre>${escapeHtml(
            JSON.stringify(f.evidence, null, 2)
          )}</pre></details>`
        : "";
      return `<article class="finding ${escapeHtml(f.severity)}">
        <h3><span class="badge ${escapeHtml(f.severity)}">${escapeHtml(f.severity)}</span>
        ${escapeHtml(f.title)}<span class="badge">${escapeHtml(f.category)}</span></h3>
        <p>${escapeHtml(f.detail)}</p>
        ${solutions ? `<h4>Recommended solutions</h4><ul>${solutions}</ul>` : ""}
        ${sql ? `<h4>Remediation SQL</h4>${sql}` : ""}
        ${evidence}
      </article>`;
    })
    .join("");
}

function renderTable(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    el("tables").innerHTML = '<p class="empty">No rows.</p>';
    return;
  }
  const columns = Object.keys(rows[0]);
  const head = columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((c) => {
            const value = row[c];
            const long = typeof value === "string" && value.length > 60;
            return `<td class="${long ? "wrap" : ""}">${escapeHtml(value)}</td>`;
          })
          .join("")}</tr>`
    )
    .join("");
  el("tables").innerHTML = `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderTabs() {
  el("tabs").innerHTML = SNAPSHOT_TABS.map(
    ([key, label]) =>
      `<button class="tab ${key === activeTab ? "active" : ""}" data-key="${key}">${escapeHtml(
        label
      )} (${(snapshot[key] || []).length})</button>`
  ).join("");
  el("tabs")
    .querySelectorAll(".tab")
    .forEach((tab) =>
      tab.addEventListener("click", () => {
        activeTab = tab.dataset.key;
        renderTabs();
        renderTable(snapshot[activeTab] || []);
      })
    );
}

async function diagnose() {
  const button = el("refresh");
  button.disabled = true;
  const banner = el("banner");
  banner.className = "banner";
  banner.textContent = "Running diagnosis…";
  try {
    const res = await fetch("/api/diagnose");
    const report = await res.json();
    if (!res.ok) {
      banner.className = "banner error";
      banner.textContent = `Diagnosis failed: ${report.detail || res.statusText}`;
      return;
    }
    snapshot = report.snapshot || {};
    banner.className = `banner ${report.status}`;
    banner.textContent = `${report.headline} · ${report.counts.critical} critical, ${report.counts.warning} warning · ${new Date(
      report.generated_at
    ).toLocaleTimeString()}`;
    renderSummary(report);
    renderFindings(report.findings || []);
    renderTabs();
    renderTable(snapshot[activeTab] || []);
  } catch (err) {
    banner.className = "banner error";
    banner.textContent = `Diagnosis failed: ${err}`;
  } finally {
    button.disabled = false;
  }
}

function setAutoRefresh(enabled) {
  if (timer) clearInterval(timer);
  timer = enabled ? setInterval(diagnose, 30000) : null;
}

el("refresh").addEventListener("click", diagnose);
el("auto").addEventListener("change", (e) => setAutoRefresh(e.target.checked));

loadTarget();
loadTools();
diagnose();
setAutoRefresh(true);
