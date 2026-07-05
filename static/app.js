const state = {
  refreshMs: 5000,
  timer: null,
  status: null,
  logs: [],
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatMs(value) {
  if (value === null || value === undefined) return "--";
  return `${Number(value).toFixed(1)} ms`;
}

function formatTime(value) {
  if (!value) return "Not checked";
  const parsed = value.includes("T") ? new Date(value) : new Date(`${value.replace(" ", "T")}Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function latestFor(url) {
  return state.status?.latest?.find((item) => item.url === url) || { url, is_up: null };
}

function summaryFor(url) {
  return state.status?.summary?.find((item) => item.url === url) || null;
}

function setHealthPill() {
  const pill = $("#healthPill");
  const latest = state.status?.latest || [];
  const down = latest.filter((item) => item.is_up === false).length;
  const pending = latest.filter((item) => item.is_up === null).length;

  pill.className = "status-pill";
  if (down > 0) {
    pill.textContent = `${down} down`;
    pill.classList.add("bad");
  } else if (pending > 0) {
    pill.textContent = `${pending} pending`;
    pill.classList.add("pending");
  } else {
    pill.textContent = "All healthy";
    pill.classList.add("ok");
  }
}

function renderMetrics() {
  const summary = state.status?.summary || [];
  const latest = state.status?.latest || [];
  const config = state.status?.config || {};
  const totalChecks = summary.reduce((sum, row) => sum + row.total, 0);
  const totalUp = summary.reduce((sum, row) => sum + row.up_count, 0);
  const avgResponse = summary.length
    ? summary.reduce((sum, row) => sum + Number(row.avg_ms || 0), 0) / summary.length
    : 0;
  const healthy = latest.filter((item) => item.is_up === true).length;
  const overall = totalChecks ? ((totalUp / totalChecks) * 100).toFixed(2) : "--";

  $("#overallUptime").textContent = totalChecks ? `${overall}%` : "--";
  $("#overallTrend").textContent = totalChecks ? `${totalChecks} checks recorded` : "No checks yet";
  $("#healthyCount").textContent = `${healthy}/${latest.length}`;
  $("#totalCount").textContent = `${latest.length} monitored`;
  $("#avgResponse").textContent = summary.length ? formatMs(avgResponse) : "--";
  $("#alertState").textContent = config.discord_enabled ? "Discord on" : "Local only";
  $("#backupState").textContent = config.s3_enabled ? "S3 backups enabled" : "S3 backups disabled";
  $("#intervalValue").textContent = `${config.check_interval || "--"}s`;
  $("#timeoutValue").textContent = `${config.request_timeout || "--"}s`;
  $("#healthyRange").textContent = `${config.healthy_status_min || "--"}-${config.healthy_status_max || "--"}`;
}

function renderRows() {
  const tbody = $("#monitorRows");
  const filter = $("#filterInput").value.trim().toLowerCase();
  const urls = state.status?.urls || [];
  const rows = urls
    .filter((url) => url.toLowerCase().includes(filter))
    .map((url) => {
      const latest = latestFor(url);
      const summary = summaryFor(url);
      const statusClass = latest.is_up === true ? "up" : latest.is_up === false ? "down" : "pending";
      const statusText = latest.is_up === true ? "UP" : latest.is_up === false ? "DOWN" : "PENDING";
      const code = latest.status_code || "N/A";
      const response = latest.response_ms !== undefined ? formatMs(latest.response_ms) : "--";
      const uptime = summary ? ` · ${summary.uptime_pct}% uptime` : "";
      const safeUrl = escapeHtml(url);

      return `
        <tr>
          <td><span class="state-dot ${statusClass}">${statusText}</span></td>
          <td class="url-cell">${safeUrl}<span class="muted">${uptime}</span></td>
          <td>${code}</td>
          <td>${response}</td>
          <td>${formatTime(latest.timestamp)}</td>
          <td><button class="secondary-btn" data-stop="${encodeURIComponent(url)}">Stop</button></td>
          <td><button class="danger-btn" data-remove="${encodeURIComponent(url)}">Remove</button></td>
        </tr>
      `;
    })
    .join("");

  tbody.innerHTML = rows || `<tr><td colspan="6" class="muted">No monitors match this filter.</td></tr>`;
}

function renderActivity() {
  const feed = $("#activityFeed");
  feed.innerHTML = state.logs.map((log) => {
    const statusClass = log.is_up ? "up" : "down";
    const statusText = log.is_up ? "UP" : "DOWN";
    return `
      <div class="activity-item">
        <span class="state-dot ${statusClass}">${statusText}</span>
        <div>
          <strong>${escapeHtml(log.url)}</strong>
          <span>${formatTime(log.timestamp)} · code ${log.status_code || "N/A"}</span>
        </div>
        <strong>${formatMs(log.response_ms)}</strong>
      </div>
    `;
  }).join("") || `<p class="muted">No checks have been logged yet.</p>`;
}

function drawChart() {
  const canvas = $("#responseChart");
  const ctx = canvas.getContext("2d");
  const points = state.status?.chart || [];
  const width = canvas.width;
  const height = canvas.height;
  const padding = 34;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i += 1) {
    const y = padding + ((height - padding * 2) / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding, y);
    ctx.lineTo(width - padding, y);
    ctx.stroke();
  }

  if (points.length < 2) {
    ctx.fillStyle = "#667085";
    ctx.font = "16px Segoe UI, sans-serif";
    ctx.fillText("Waiting for more checks to draw the trend", padding, height / 2);
    return;
  }

  const values = points.map((point) => Number(point.avg_ms || 0));
  const max = Math.max(...values, 100);
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;

  ctx.beginPath();
  points.forEach((point, index) => {
    const x = padding + (plotWidth / (points.length - 1)) * index;
    const y = height - padding - (Number(point.avg_ms || 0) / max) * plotHeight;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 4;
  ctx.stroke();

  points.forEach((point, index) => {
    const x = padding + (plotWidth / (points.length - 1)) * index;
    const y = height - padding - (Number(point.avg_ms || 0) / max) * plotHeight;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = Number(point.uptime_pct) >= 100 ? "#0f9f6e" : "#d64545";
    ctx.fill();
  });

  ctx.fillStyle = "#667085";
  ctx.font = "13px Segoe UI, sans-serif";
  ctx.fillText(`0 ms`, padding, height - 10);
  ctx.fillText(`${max.toFixed(0)} ms`, padding, 18);
}

function renderAll() {
  renderMetrics();
  renderRows();
  renderActivity();
  drawChart();
  setHealthPill();
  $("#lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function loadData() {
  const [statusRes, logsRes] = await Promise.all([
    fetch("/api/status"),
    fetch("/api/logs?limit=80"),
  ]);
  state.status = await statusRes.json();
  state.logs = await logsRes.json();
  renderAll();
}

function scheduleRefresh() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(loadData, state.refreshMs);
}

async function checkNow() {
  const button = $("#checkNow");
  button.disabled = true;
  button.textContent = "Checking...";
  try {
    await fetch("/api/check-now", { method: "POST" });
    await loadData();
  } finally {
    button.disabled = false;
    button.textContent = "Check now";
  }
}

async function addUrl(event) {
  event.preventDefault();
  const input = $("#urlInput");
  const url = input.value.trim();
  if (!url) return;
  await fetch("/api/urls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  input.value = "";
  await loadData();
}

async function removeUrl(url) {
  await fetch("/api/urls", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  await loadData();
}

async function stopUrl(url) {
  await fetch("/api/urls/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  await loadData();
}

document.addEventListener("click", (event) => {
  const remove = event.target.closest("[data-remove]");
  if (remove) removeUrl(decodeURIComponent(remove.dataset.remove));

  const stop = event.target.closest("[data-stop]");
  if (stop) stopUrl(decodeURIComponent(stop.dataset.stop));

  const segment = event.target.closest("[data-refresh]");
  if (segment) {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    segment.classList.add("active");
    state.refreshMs = Number(segment.dataset.refresh);
    scheduleRefresh();
  }
});

$("#checkNow").addEventListener("click", checkNow);
$("#addUrlForm").addEventListener("submit", addUrl);
$("#filterInput").addEventListener("input", renderRows);

loadData().then(scheduleRefresh).catch((error) => {
  $("#lastUpdated").textContent = "Dashboard API unavailable";
  console.error(error);
});
