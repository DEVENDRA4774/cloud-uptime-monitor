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
  
  // Render alert integration channels
  const activeAlerts = [];
  if (config.discord_enabled) activeAlerts.push("Discord");
  if (config.sns_enabled) activeAlerts.push("AWS SNS");
  $("#alertState").textContent = activeAlerts.length ? activeAlerts.join(" + ") : "Local Alerts Only";
  
  $("#backupState").textContent = config.s3_enabled ? "S3 Backups Enabled" : "S3 Backups Disabled";
  $("#intervalValue").textContent = `${config.check_interval || "--"}s`;
  $("#timeoutValue").textContent = `${config.request_timeout || "--"}s`;
  $("#healthyRange").textContent = `${config.healthy_status_min || "--"}-${config.healthy_status_max || "--"}`;
}

function renderSSL(sslDays) {
  if (sslDays === null || sslDays === undefined) {
    return `<span class="ssl-badge none">N/A (HTTP)</span>`;
  }
  const days = Number(sslDays);
  if (days >= 30) {
    return `<span class="ssl-badge valid">${days}d left</span>`;
  } else if (days >= 7) {
    return `<span class="ssl-badge warning">${days}d left</span>`;
  } else {
    return `<span class="ssl-badge critical">${days}d left</span>`;
  }
}

function renderTimeline(history) {
  const maxTimelineDots = 20;
  const arr = history || [];
  
  // Fill array to maxTimelineDots length with empty items if needed
  const filledHistory = [...arr];
  while (filledHistory.length < maxTimelineDots) {
    filledHistory.unshift({ is_up: null, timestamp: "" });
  }
  
  // Slice to last maxTimelineDots checks
  const targetHistory = filledHistory.slice(-maxTimelineDots);
  
  return `<div class="timeline-grid">` + 
    targetHistory.map((item) => {
      if (item.is_up === true) {
        return `<div class="timeline-dot up" data-tooltip="UP (Checked: ${formatTime(item.timestamp)})"></div>`;
      } else if (item.is_up === false) {
        return `<div class="timeline-dot down" data-tooltip="DOWN (Checked: ${formatTime(item.timestamp)})"></div>`;
      } else {
        return `<div class="timeline-dot empty"></div>`;
      }
    }).join("") + 
    `</div>`;
}

function renderRows() {
  const tbody = $("#monitorRows");
  const filter = $("#filterInput").value.trim().toLowerCase();
  const allMonitors = state.status?.all_monitors || [];
  const rows = allMonitors
    .filter((m) => m.url.toLowerCase().includes(filter))
    .map((m) => {
      const url = m.url;
      const isActive = m.active;
      const latest = latestFor(url);
      const summary = summaryFor(url);
      const safeUrl = escapeHtml(url);
      const encoded = encodeURIComponent(url);

      if (!isActive) {
        // Stopped / paused monitor
        const uptime = summary ? ` · ${summary.uptime_pct}% uptime` : "";
        return `
          <tr style="opacity: 0.55">
            <td><span class="state-dot pending">PAUSED</span></td>
            <td class="url-cell">${safeUrl}<span class="muted">${uptime}</span></td>
            <td><span class="ssl-badge none">Paused</span></td>
            <td>${renderTimeline([])}</td>
            <td>--</td>
            <td>--</td>
            <td>Monitoring paused</td>
            <td><button class="resume-btn" data-resume="${encoded}">Resume</button></td>
            <td><button class="danger-btn" data-remove="${encoded}">Remove</button></td>
          </tr>
        `;
      }

      // Active monitor
      const statusClass = latest.is_up === true ? "up" : latest.is_up === false ? "down" : "pending";
      const statusText = latest.is_up === true ? "UP" : latest.is_up === false ? "DOWN" : "PENDING";
      const code = latest.status_code || "N/A";
      const response = latest.response_ms !== undefined ? formatMs(latest.response_ms) : "--";
      const uptime = summary ? ` · ${summary.uptime_pct}% uptime` : "";

      return `
        <tr>
          <td><span class="state-dot ${statusClass}">${statusText}</span></td>
          <td class="url-cell">${safeUrl}<span class="muted">${uptime}</span></td>
          <td>${renderSSL(latest.ssl_days)}</td>
          <td>${renderTimeline(latest.history)}</td>
          <td>${code}</td>
          <td>${response}</td>
          <td>${formatTime(latest.timestamp)}</td>
          <td><button class="secondary-btn" data-stop="${encoded}">Stop</button></td>
          <td><button class="danger-btn" data-remove="${encoded}">Remove</button></td>
        </tr>
      `;
    })
    .join("");

  tbody.innerHTML = rows || `<tr><td colspan="9" class="muted" style="text-align: center;">No website monitors found matching filter.</td></tr>`;
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
          <span>${formatTime(log.timestamp)} · status ${log.status_code || "N/A"}</span>
        </div>
        <span class="response-ms">${formatMs(log.response_ms)}</span>
      </div>
    `;
  }).join("") || `<p class="muted">No monitoring logs captured yet.</p>`;
}

function drawChart() {
  const canvas = $("#responseChart");
  const ctx = canvas.getContext("2d");
  const points = state.status?.chart || [];
  const width = canvas.width;
  const height = canvas.height;
  const padding = 50;

  ctx.clearRect(0, 0, width, height);

  const isDarkMode = !document.body.classList.contains("light-mode");

  // Background
  ctx.fillStyle = isDarkMode ? "rgba(7, 10, 19, 0.4)" : "rgba(255, 255, 255, 0.6)";
  ctx.fillRect(0, 0, width, height);

  if (points.length < 2) {
    ctx.fillStyle = isDarkMode ? "#94a3b8" : "#475569";
    ctx.font = "14px Plus Jakarta Sans, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("Trend analysis requires additional checks...", width / 2, height / 2);
    return;
  }

  const values = points.map((point) => Number(point.avg_ms || 0));
  const max = Math.max(...values, 100);
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;

  // Grid Lines and Y-Axis detail labels
  ctx.strokeStyle = isDarkMode ? "rgba(99, 102, 241, 0.08)" : "rgba(99, 102, 241, 0.12)";
  ctx.lineWidth = 1;
  ctx.fillStyle = isDarkMode ? "#94a3b8" : "#475569";
  ctx.font = "11px Plus Jakarta Sans, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  const gridLines = 4;
  for (let i = 0; i <= gridLines; i += 1) {
    const ratio = i / gridLines;
    const y = padding + plotHeight * (1 - ratio);
    
    ctx.beginPath();
    ctx.moveTo(padding, y);
    ctx.lineTo(width - padding, y);
    ctx.stroke();

    const val = max * ratio;
    ctx.fillText(`${val.toFixed(0)} ms`, padding - 10, y);
  }

  // Generate coordinates
  const coords = points.map((point, index) => {
    return {
      x: padding + (plotWidth / (points.length - 1)) * index,
      y: height - padding - (Number(point.avg_ms || 0) / max) * plotHeight,
      uptime: Number(point.uptime_pct || 0),
      timestamp: point.timestamp,
    };
  });

  // X-Axis Timestamp Details
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const step = Math.max(1, Math.floor(coords.length / 5));
  coords.forEach((c, i) => {
    if (i % step === 0 || i === coords.length - 1) {
      ctx.fillStyle = isDarkMode ? "#64748b" : "#475569";
      let label = "";
      if (c.timestamp) {
        const parsed = c.timestamp.includes("T") 
          ? new Date(c.timestamp) 
          : new Date(`${c.timestamp.replace(" ", "T")}Z`);
        if (!isNaN(parsed.getTime())) {
          label = parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
      }
      if (label) {
        ctx.fillText(label, c.x, height - padding + 10);
      }
    }
  });

  // Area fill under bezier curve
  ctx.beginPath();
  ctx.moveTo(coords[0].x, coords[0].y);
  for (let i = 0; i < coords.length - 1; i++) {
    const p0 = coords[i];
    const p1 = coords[i + 1];
    const cp1x = p0.x + (p1.x - p0.x) * 0.3;
    const cp1y = p0.y;
    const cp2x = p1.x - (p1.x - p0.x) * 0.3;
    const cp2y = p1.y;
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p1.x, p1.y);
  }
  ctx.lineTo(coords[coords.length - 1].x, height - padding);
  ctx.lineTo(coords[0].x, height - padding);
  ctx.closePath();

  const areaGrad = ctx.createLinearGradient(0, padding, 0, height - padding);
  areaGrad.addColorStop(0, "rgba(16, 185, 129, 0.22)"); 
  areaGrad.addColorStop(1, "rgba(16, 185, 129, 0.0)");
  ctx.fillStyle = areaGrad;
  ctx.fill();

  // Glow line (cubic spline)
  ctx.beginPath();
  ctx.moveTo(coords[0].x, coords[0].y);
  for (let i = 0; i < coords.length - 1; i++) {
    const p0 = coords[i];
    const p1 = coords[i + 1];
    const cp1x = p0.x + (p1.x - p0.x) * 0.3;
    const cp1y = p0.y;
    const cp2x = p1.x - (p1.x - p0.x) * 0.3;
    const cp2y = p1.y;
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p1.x, p1.y);
  }
  ctx.strokeStyle = "#10b981"; 
  ctx.lineWidth = 3.5;
  ctx.shadowColor = "rgba(16, 185, 129, 0.4)";
  ctx.shadowBlur = 8;
  ctx.stroke();
  ctx.shadowBlur = 0; // Reset shadow

  // Nodes (dots) on curve
  coords.forEach((c) => {
    ctx.beginPath();
    ctx.arc(c.x, c.y, 4, 0, Math.PI * 2);
    ctx.fillStyle = c.uptime >= 100 ? "#10b981" : "#f43f5e";
    ctx.fill();
    ctx.strokeStyle = isDarkMode ? "#070a13" : "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });

  // Interactive Hover Tooltip details
  if (window.hoverPoint) {
    const hp = coords[window.hoverPoint.index];
    if (hp) {
      // Draw vertical cursor line
      ctx.strokeStyle = isDarkMode ? "rgba(255, 255, 255, 0.2)" : "rgba(0, 0, 0, 0.15)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hp.x, padding);
      ctx.lineTo(hp.x, height - padding);
      ctx.stroke();
      ctx.setLineDash([]); 

      // Highlight Node
      ctx.beginPath();
      ctx.arc(hp.x, hp.y, 7, 0, Math.PI * 2);
      ctx.fillStyle = hp.uptime >= 100 ? "#10b981" : "#f43f5e";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.stroke();

      // Tooltip Box Dimension and Placement
      const boxW = 160;
      const boxH = 75;
      let boxX = hp.x + 15;
      let boxY = hp.y - boxH / 2;

      if (boxX + boxW > width) {
        boxX = hp.x - boxW - 15;
      }
      if (boxY < padding) {
        boxY = padding;
      }
      if (boxY + boxH > height - padding) {
        boxY = height - padding - boxH;
      }

      // Draw Tooltip Box
      ctx.fillStyle = isDarkMode ? "rgba(13, 17, 33, 0.95)" : "rgba(255, 255, 255, 0.95)";
      ctx.strokeStyle = isDarkMode ? "rgba(99, 102, 241, 0.4)" : "rgba(99, 102, 241, 0.25)";
      ctx.lineWidth = 1.5;
      ctx.shadowColor = "rgba(0, 0, 0, 0.25)";
      ctx.shadowBlur = 10;
      
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxW, boxH, 8);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0; 

      // Tooltip Content Text
      ctx.fillStyle = isDarkMode ? "#ffffff" : "#0f172a";
      ctx.font = "bold 12px Plus Jakarta Sans, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      
      let timeStr = "Check: --";
      if (hp.timestamp) {
        const d = hp.timestamp.includes("T") ? new Date(hp.timestamp) : new Date(`${hp.timestamp.replace(" ", "T")}Z`);
        if (!isNaN(d.getTime())) {
          timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
      }

      ctx.fillText(timeStr, boxX + 12, boxY + 12);
      ctx.font = "11px Plus Jakarta Sans, sans-serif";
      ctx.fillStyle = isDarkMode ? "#94a3b8" : "#475569";
      ctx.fillText(`Latency: ${window.hoverPoint.point.avg_ms.toFixed(1)} ms`, boxX + 12, boxY + 32);
      ctx.fillText(`Uptime: ${hp.uptime.toFixed(1)}%`, boxX + 12, boxY + 50);
    }
  }
}

function renderAll() {
  renderMetrics();
  renderRows();
  renderActivity();
  drawChart();
  setHealthPill();
  $("#lastUpdated").innerHTML = `<span class="live-dot"></span>Updated ${new Date().toLocaleTimeString()}`;
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
  
  const response = await fetch("/api/urls", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  
  if (response.ok) {
    input.value = "";
    await loadData();
  } else {
    const errorData = await response.json();
    alert(`Failed to add website: ${errorData.error}`);
  }
}

async function removeUrl(url) {
  if (!confirm(`Are you sure you want to remove ${url}?`)) return;
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

async function resumeUrl(url) {
  await fetch("/api/urls/resume", {
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

  const resume = event.target.closest("[data-resume]");
  if (resume) resumeUrl(decodeURIComponent(resume.dataset.resume));

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

// Theme Toggle Setup
const themeBtn = $("#themeToggle");
const currentTheme = localStorage.getItem("theme");
if (currentTheme === "light") {
  document.body.classList.add("light-mode");
  themeBtn.textContent = "☀️ Light";
} else {
  themeBtn.textContent = "🌙 Dark";
}

themeBtn.addEventListener("click", () => {
  document.body.classList.toggle("light-mode");
  const isLight = document.body.classList.contains("light-mode");
  localStorage.setItem("theme", isLight ? "light" : "dark");
  themeBtn.textContent = isLight ? "☀️ Light" : "🌙 Dark";
  drawChart();
});

// Interactive hover controls
const chartCanvas = $("#responseChart");
chartCanvas.addEventListener("mousemove", (event) => {
  const rect = chartCanvas.getBoundingClientRect();
  const scaleX = chartCanvas.width / rect.width;
  const scaleY = chartCanvas.height / rect.height;
  const mouseX = (event.clientX - rect.left) * scaleX;
  const mouseY = (event.clientY - rect.top) * scaleY;

  const points = state.status?.chart || [];
  if (points.length < 2) return;

  const max = Math.max(...points.map((p) => Number(p.avg_ms || 0)), 100);
  const padding = 50;
  const plotWidth = chartCanvas.width - padding * 2;
  const plotHeight = chartCanvas.height - padding * 2;

  let closest = null;
  let minDist = 40; 

  points.forEach((point, index) => {
    const x = padding + (plotWidth / (points.length - 1)) * index;
    const y = chartCanvas.height - padding - (Number(point.avg_ms || 0) / max) * plotHeight;
    const dist = Math.abs(mouseX - x);
    if (dist < minDist) {
      minDist = dist;
      closest = { index, x, y, point };
    }
  });

  if (JSON.stringify(closest) !== JSON.stringify(window.hoverPoint)) {
    window.hoverPoint = closest;
    drawChart();
  }
});

chartCanvas.addEventListener("mouseleave", () => {
  if (window.hoverPoint) {
    window.hoverPoint = null;
    drawChart();
  }
});

loadData().then(scheduleRefresh).catch((error) => {
  $("#lastUpdated").textContent = "Dashboard API unavailable";
  console.error(error);
});
