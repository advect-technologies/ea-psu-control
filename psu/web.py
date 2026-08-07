"""Lightweight local web UI for the EA PSU controller.

Plain HTML + vanilla JS. No build step, no framework.
Status is polled via GET /api/status.
Control actions: remote on/off, output on/off, set voltage/current.
Profile upload / select / run with power-scale (kW).
Optional debug panel shows the decoded register-505 status bitmap.
"""

from __future__ import annotations

from aiohttp import web
from aiohttp.multipart import BodyPartReader
from loguru import logger as log

from .hardware import PowerSupply
from .profile import ProfileError, ProfilePlayer, ProfileStore

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PSU Controller</title>
  <style>
    :root {
      --bg: #0f1419;
      --card: #1a2332;
      --border: #2d3a4f;
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --green: #22c55e;
      --red: #ef4444;
      --yellow: #eab308;
      --blue: #3b82f6;
      --fault: #f97316;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 1.5rem;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
      gap: 0.75rem;
    }
    h1 { font-size: 1.4rem; font-weight: 600; }
    .status-bar {
      display: flex;
      gap: 1.25rem;
      align-items: center;
      font-size: 0.85rem;
      color: var(--muted);
      flex-wrap: wrap;
    }
    .dot {
      width: 10px; height: 10px; border-radius: 50%;
      display: inline-block; margin-right: 0.35rem;
    }
    .dot.on    { background: var(--green); box-shadow: 0 0 6px var(--green); }
    .dot.off   { background: var(--muted); }
    .dot.error { background: var(--fault); box-shadow: 0 0 6px var(--fault); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1rem;
      margin-bottom: 1.25rem;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.1rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.7rem;
    }
    .card h2 {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      font-weight: 600;
    }
    .value-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.5rem;
    }
    .value {
      font-size: 1.6rem;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }
    .target {
      font-size: 0.95rem;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }
    .unit { font-size: 0.85rem; color: var(--muted); margin-left: 0.2rem; }
    .actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    button {
      border: none;
      border-radius: 6px;
      padding: 0.55rem 0.9rem;
      font-size: 0.9rem;
      font-weight: 500;
      cursor: pointer;
      transition: opacity 0.15s;
    }
    button:hover { opacity: 0.9; }
    button:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn-on  { background: var(--green); color: #052e16; }
    .btn-off { background: var(--red);   color: #450a0a; }
    .btn-set { background: var(--blue);  color: #eff6ff; }
    .form-row {
      display: grid;
      grid-template-columns: 5.5rem 1fr;
      gap: 0.55rem 0.75rem;
      align-items: center;
    }
    .form-row label {
      font-size: 0.85rem;
      color: var(--muted);
      text-align: right;
    }
    .form-row input[type="number"],
    .form-row input[type="text"],
    .form-row select {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      padding: 0.45rem 0.6rem;
      font-size: 0.95rem;
      width: 100%;
      max-width: 12rem;
      font-variant-numeric: tabular-nums;
    }
    .form-row select { max-width: 100%; cursor: pointer; }
    .form-row input[type="file"] {
      font-size: 0.85rem;
      color: var(--muted);
      max-width: 100%;
    }
    .form-actions {
      grid-column: 2;
      margin-top: 0.15rem;
    }
    .meta { font-size: 0.8rem; color: var(--muted); }
    .footer {
      margin-top: 0.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
    }
    .toast {
      position: fixed;
      bottom: 1.25rem;
      right: 1.25rem;
      background: var(--card);
      border: 1px solid var(--border);
      padding: 0.7rem 1.1rem;
      border-radius: 8px;
      font-size: 0.9rem;
      opacity: 0;
      transition: opacity 0.25s;
      pointer-events: none;
      z-index: 50;
    }
    .toast.show { opacity: 1; }

    .progress {
      height: 6px;
      background: var(--bg);
      border-radius: 3px;
      overflow: hidden;
      margin-top: 0.25rem;
    }
    .progress > span {
      display: block;
      height: 100%;
      background: var(--blue);
      width: 0%;
      transition: width 0.3s linear;
    }
    .profile-running .card-manual { opacity: 0.55; }

    /* Debug panel */
    .debug-toggle {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.85rem;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
    }
    .debug-toggle input { accent-color: var(--blue); cursor: pointer; }
    #debug-panel { display: none; margin-top: 0.25rem; }
    #debug-panel.visible { display: block; }
    .bitmap-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
      font-variant-numeric: tabular-nums;
    }
    .bitmap-table th,
    .bitmap-table td {
      text-align: left;
      padding: 0.35rem 0.6rem;
      border-bottom: 1px solid var(--border);
    }
    .bitmap-table th {
      color: var(--muted);
      font-weight: 600;
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .bitmap-table tr:last-child td { border-bottom: none; }
    .bit-on  { color: var(--green); font-weight: 600; }
    .bit-off { color: var(--muted); }
    .bit-alarm { color: var(--fault); font-weight: 600; }
    .raw-hex {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      color: var(--blue);
    }
  </style>
</head>
<body>
  <header>
    <h1>PSU Controller</h1>
    <div class="status-bar">
      <span id="conn"><span class="dot off"></span>disconnected</span>
      <span id="remote"><span class="dot off"></span>remote off</span>
      <span id="output"><span class="dot off"></span>output off</span>
      <span id="profile-dot"><span class="dot off"></span>profile idle</span>
      <span id="sn" class="meta">SN: —</span>
      <label class="debug-toggle">
        <input type="checkbox" id="debug-toggle"> Debug
      </label>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <h2>Voltage</h2>
      <div class="value-row">
        <span><span class="value" id="u-act">—</span><span class="unit">V</span></span>
        <span class="target">set <span id="u-tgt">—</span> V</span>
      </div>
    </div>
    <div class="card">
      <h2>Current</h2>
      <div class="value-row">
        <span><span class="value" id="i-act">—</span><span class="unit">A</span></span>
        <span class="target">set <span id="i-tgt">—</span> A</span>
      </div>
    </div>
    <div class="card">
      <h2>Power</h2>
      <div class="value-row">
        <span><span class="value" id="p-act">—</span><span class="unit">W</span></span>
        <span class="target">set <span id="p-tgt">—</span> W</span>
      </div>
    </div>
  </div>

  <div class="grid card-manual">
    <div class="card">
      <h2>Remote / Output</h2>
      <div class="actions">
        <button class="btn-on"  id="remote-on">Remote On</button>
        <button class="btn-off" id="remote-off">Remote Off</button>
        <button class="btn-on"  id="output-on">Output On</button>
        <button class="btn-off" id="output-off">Output Off</button>
      </div>
    </div>
    <div class="card">
      <h2>Set Targets</h2>
      <div class="form-row">
        <label for="set-u">Voltage</label>
        <input type="number" id="set-u" step="0.1" min="0" placeholder="V">
        <label for="set-i">Current</label>
        <input type="number" id="set-i" step="0.01" min="0" placeholder="A">
        <div class="form-actions">
          <button class="btn-set" id="apply-set">Apply</button>
        </div>
      </div>
      <div class="meta" id="nom-hint">Nominals: —</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Upload Profile</h2>
      <div class="form-row">
        <label for="profile-file">CSV</label>
        <input type="file" id="profile-file" accept=".csv,text/csv">
        <div class="form-actions">
          <button class="btn-set" id="upload-profile">Upload</button>
        </div>
      </div>
      <div class="meta">Required columns: time, normalized_power</div>
    </div>
    <div class="card">
      <h2>Select Profile</h2>
      <div class="form-row">
        <label for="profile-select">Stored</label>
        <select id="profile-select">
          <option value="">— none —</option>
        </select>
      </div>
      <div class="meta" id="profile-info">No profile selected</div>
    </div>
    <div class="card">
      <h2>Run Profile</h2>
      <div class="form-row">
        <label for="power-scale">Scale</label>
        <input type="number" id="power-scale" step="0.1" min="0.1" placeholder="kW" value="1">
        <div class="form-actions actions">
          <button class="btn-on" id="profile-start">Start</button>
          <button class="btn-off" id="profile-stop" disabled>Stop</button>
        </div>
      </div>
      <div class="meta" id="profile-progress-text">Idle</div>
      <div class="progress"><span id="profile-progress-bar"></span></div>
    </div>
  </div>

  <div id="debug-panel">
    <div class="card">
      <h2>Register 505 — Device Status Bitmap</h2>
      <div class="meta">Raw: <span class="raw-hex" id="dbg-raw">—</span></div>
      <table class="bitmap-table">
        <thead>
          <tr><th>Field</th><th>Bits</th><th>Value</th></tr>
        </thead>
        <tbody id="dbg-body">
          <tr><td colspan="3" class="meta">No status data yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="footer">
    <span class="meta" id="last-error"></span>
    <span class="meta" id="last-update">Last update: —</span>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    const $ = (sel) => document.querySelector(sel);
    let profileRunning = false;
    let profiles = [];

    function toast(msg) {
      const el = $("#toast");
      el.textContent = msg;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 2200);
    }

    async function api(path, method = "GET", body = null) {
      const opts = { method, headers: {} };
      if (body != null) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || r.statusText);
      return data;
    }

    function setDot(id, on, label, error = false) {
      const cls = error ? "error" : (on ? "on" : "off");
      $(id).innerHTML = `<span class="dot ${cls}"></span>${label}`;
    }

    function bitClass(v, alarm) {
      if (alarm && v) return "bit-alarm";
      return v ? "bit-on" : "bit-off";
    }

    function yn(v) { return v ? "ON" : "off"; }

    function fmtDuration(s) {
      if (s == null || Number.isNaN(s)) return "—";
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = Math.floor(s % 60);
      if (h > 0) return `${h}h ${m}m`;
      if (m > 0) return `${m}m ${sec}s`;
      return `${sec}s`;
    }

    function renderDebug(bm) {
      if (!bm) {
        $("#dbg-raw").textContent = "—";
        $("#dbg-body").innerHTML =
          '<tr><td colspan="3" class="meta">No status data yet</td></tr>';
        return;
      }
      $("#dbg-raw").textContent = bm.raw_hex;
      const rows = [
        ["Control location", "0–4",
          `0x${bm.control_location.toString(16).padStart(2,"0")} (${bm.control_location_name})`,
          false],
        ["Config mode", "5", yn(bm.config_mode), false],
        ["MS type", "6", bm.ms_master ? "master" : "slave", false],
        ["Output state", "7", yn(bm.output_on), false],
        ["Regulation mode", "9–10",
          `${bm.regulation_mode_name} (${bm.regulation_mode})`, false],
        ["Remote", "11", yn(bm.remote), false],
        ["External sense", "14", yn(bm.external_sense), false],
        ["Alarms (any)", "15", yn(bm.alarms), true],
        ["OVP", "16", yn(bm.ovp), true],
        ["OCP", "17", yn(bm.ocp), true],
        ["OPP", "18", yn(bm.opp), true],
        ["OT", "19", yn(bm.ot), true],
        ["Power fail", "21", yn(bm.power_fail), true],
        ["MSP", "29", yn(bm.msp), true],
        ["REM-SB", "30", yn(bm.rem_sb), true],
      ];
      $("#dbg-body").innerHTML = rows.map(([f, bits, val, alarm]) => {
        const isOn = typeof val === "string" && val.toUpperCase() === "ON";
        const cls = bitClass(isOn || (alarm && val && val !== "off"), alarm);
        return `<tr>
          <td>${f}</td>
          <td>${bits}</td>
          <td class="${cls}">${val}</td>
        </tr>`;
      }).join("");
    }

    function setManualEnabled(enabled) {
      $("#remote-on").disabled  = !enabled || false;
      $("#remote-off").disabled = !enabled;
      $("#output-on").disabled  = !enabled;
      $("#output-off").disabled = !enabled;
      $("#set-u").disabled = !enabled;
      $("#set-i").disabled = !enabled;
      $("#apply-set").disabled = !enabled;
      $("#profile-start").disabled = !enabled;
      $("#profile-select").disabled = !enabled;
      $("#power-scale").disabled = !enabled;
      $("#upload-profile").disabled = !enabled;
      $("#profile-file").disabled = !enabled;
      document.body.classList.toggle("profile-running", !enabled);
    }

    function renderProfile(p) {
      profileRunning = !!(p && p.active);
      if (profileRunning) {
        setDot("#profile-dot", true, "profile running");
        setManualEnabled(false);
        $("#profile-stop").disabled = false;
        const pct = p.duration_s > 0 ? (p.elapsed_s / p.duration_s) * 100 : 0;
        $("#profile-progress-bar").style.width = Math.min(100, pct).toFixed(1) + "%";
        $("#profile-progress-text").textContent =
          `${p.profile_name}  ·  ${fmtDuration(p.elapsed_s)} / ${fmtDuration(p.duration_s)}  ·  ` +
          `norm=${p.normalized.toFixed(4)}  ·  P=${p.power_w.toFixed(0)} W` +
          (p.error ? `  ·  ERR: ${p.error}` : "");
      } else {
        setDot("#profile-dot", false, "profile idle");
        $("#profile-stop").disabled = true;
        $("#profile-progress-bar").style.width = "0%";
        if (p && p.error) {
          $("#profile-progress-text").textContent = "Error: " + p.error;
        } else if (p && p.profile_name && p.elapsed_s > 0 && p.elapsed_s >= p.duration_s - 0.5) {
          $("#profile-progress-text").textContent =
            `Finished: ${p.profile_name}`;
        } else {
          $("#profile-progress-text").textContent = "Idle";
        }
      }
    }

    function render(s) {
      const conn = s.connection;
      setDot("#conn", conn === "CONNECTED", conn.toLowerCase(), conn === "ERROR");
      setDot("#remote", s.remote_active, s.remote_active ? "remote on" : "remote off");
      setDot("#output", s.output_on, s.output_on ? "output on" : "output off");
      $("#sn").textContent = "SN: " + (s.serial_number || "—");

      const r = s.reading;
      if (r) {
        $("#u-act").textContent = r.voltage_v.toFixed(2);
        $("#u-tgt").textContent = r.target_voltage_v.toFixed(2);
        $("#i-act").textContent = r.current_a.toFixed(2);
        $("#i-tgt").textContent = r.target_current_a.toFixed(2);
        $("#p-act").textContent = r.power_w.toFixed(1);
        $("#p-tgt").textContent = r.target_power_w.toFixed(1);
      }

      if (s.nominals) {
        const pw = s.nominals.power_w != null
          ? ` / ${(s.nominals.power_w/1000).toFixed(1)} kW`
          : "";
        $("#nom-hint").textContent =
          `Nominals: ${s.nominals.voltage_v.toFixed(0)} V / ${s.nominals.current_a.toFixed(0)} A${pw}`;
      }

      $("#last-error").textContent = s.last_error ? ("Error: " + s.last_error) : "";
      $("#last-update").textContent = "Last update: " + new Date().toLocaleTimeString();

      renderProfile(s.profile);

      // Manual controls — only apply when not running a profile
      if (!profileRunning) {
        $("#remote-on").disabled  = s.remote_active;
        $("#remote-off").disabled = !s.remote_active;
        $("#output-on").disabled  = !s.remote_active || s.output_on;
        $("#output-off").disabled = !s.output_on;
        $("#set-u").disabled = false;
        $("#set-i").disabled = false;
        $("#apply-set").disabled = false;
        $("#profile-start").disabled = false;
        $("#profile-select").disabled = false;
        $("#power-scale").disabled = false;
        $("#upload-profile").disabled = false;
        $("#profile-file").disabled = false;
        document.body.classList.remove("profile-running");
      }

      if ($("#debug-toggle").checked) {
        renderDebug(s.status_bitmap);
      }
    }

    async function refreshProfiles() {
      try {
        const data = await api("/api/profiles");
        profiles = data.profiles || [];
        const sel = $("#profile-select");
        const current = sel.value;
        sel.innerHTML = '<option value="">— none —</option>' +
          profiles.map(n => `<option value="${n}">${n}</option>`).join("");
        if (current && profiles.includes(current)) sel.value = current;
        updateProfileInfo();
      } catch (e) {
        console.error(e);
      }
    }

    function updateProfileInfo() {
      const name = $("#profile-select").value;
      $("#profile-info").textContent = name
        ? `Selected: ${name}`
        : "No profile selected";
    }

    async function refresh() {
      try {
        const status = await api("/api/status");
        render(status);
      } catch (e) {
        console.error(e);
        toast("Status fetch failed");
      }
    }

    async function post(path, body, okMsg) {
      try {
        await api(path, "POST", body);
        toast(okMsg);
        setTimeout(refresh, 120);
      } catch (e) {
        toast(String(e.message || e));
      }
    }

    $("#remote-on").onclick  = () => post("/api/remote", { enabled: true },  "Remote ON");
    $("#remote-off").onclick = () => post("/api/remote", { enabled: false }, "Remote OFF");
    $("#output-on").onclick  = () => post("/api/output", { enabled: true },  "Output ON");
    $("#output-off").onclick = () => post("/api/output", { enabled: false }, "Output OFF");

    $("#apply-set").onclick = () => {
      if (profileRunning) { toast("Stop profile first"); return; }
      const u = parseFloat($("#set-u").value);
      const i = parseFloat($("#set-i").value);
      const body = {};
      if (!Number.isNaN(u)) body.voltage_v = u;
      if (!Number.isNaN(i)) body.current_a = i;
      if (body.voltage_v == null && body.current_a == null) {
        toast("Enter voltage and/or current");
        return;
      }
      post("/api/setpoints", body, "Setpoints applied");
    };

    $("#upload-profile").onclick = async () => {
      const fileInput = $("#profile-file");
      if (!fileInput.files || !fileInput.files[0]) {
        toast("Choose a CSV file first");
        return;
      }
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      try {
        const r = await fetch("/api/profiles/upload", { method: "POST", body: fd });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || r.statusText);
        toast(`Uploaded: ${data.name} (${data.n_points} pts, ${fmtDuration(data.duration_s)})`);
        fileInput.value = "";
        await refreshProfiles();
        $("#profile-select").value = data.name;
        updateProfileInfo();
      } catch (e) {
        toast(String(e.message || e));
      }
    };

    $("#profile-select").onchange = updateProfileInfo;

    $("#profile-start").onclick = () => {
      const name = $("#profile-select").value;
      const scale = parseFloat($("#power-scale").value);
      if (!name) { toast("Select a profile"); return; }
      if (!(scale > 0)) { toast("Enter power scale in kW"); return; }
      post("/api/profile/start", { name, power_scale_kw: scale }, "Profile started");
    };

    $("#profile-stop").onclick = () =>
      post("/api/profile/stop", {}, "Profile stopped");

    $("#debug-toggle").onchange = (e) => {
      const on = e.target.checked;
      $("#debug-panel").classList.toggle("visible", on);
      if (on) refresh();
    };

    refreshProfiles();
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


def create_app(
    psu: PowerSupply, player: ProfilePlayer | None = None
) -> web.Application:
    store = player._store if player is not None else ProfileStore()
    if player is None:
        player = ProfilePlayer(psu, store)

    app = web.Application()
    app["psu"] = psu
    app["player"] = player
    app["store"] = store

    def _profile_locked() -> bool:
        return player.active

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def status(_request: web.Request) -> web.Response:
        s = psu.status()
        r = s.reading
        n = s.nominals
        bm = s.status_bitmap
        payload = {
            "connection": s.connection.value,
            "remote_active": s.remote_active,
            "output_on": s.output_on,
            "serial_number": s.serial_number,
            "last_error": s.last_error,
            "nominals": None
            if n is None
            else {
                "voltage_v": n.voltage_v,
                "current_a": n.current_a,
                "power_w": n.power_w,
                "serial_number": n.serial_number,
            },
            "reading": None
            if r is None
            else {
                "voltage_v": r.voltage_v,
                "current_a": r.current_a,
                "power_w": r.power_w,
                "voltage_pct": r.voltage_pct,
                "current_pct": r.current_pct,
                "power_pct": r.power_pct,
                "target_voltage_v": r.target_voltage_v,
                "target_current_a": r.target_current_a,
                "target_power_w": r.target_power_w,
                "target_voltage_pct": r.target_voltage_pct,
                "target_current_pct": r.target_current_pct,
                "target_power_pct": r.target_power_pct,
                "timestamp": r.timestamp,
            },
            "status_bitmap": None if bm is None else bm.to_dict(),
            "profile": player.state().to_dict(),
        }
        return web.json_response(payload)

    async def remote(request: web.Request) -> web.Response:
        if _profile_locked():
            return web.json_response(
                {"ok": False, "error": "stop profile first"}, status=409
            )
        body = await request.json()
        enabled = bool(body.get("enabled", True))
        try:
            await psu.enable_remote(enabled)
            return web.json_response({"ok": True, "remote_active": enabled})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def output(request: web.Request) -> web.Response:
        if _profile_locked():
            return web.json_response(
                {"ok": False, "error": "stop profile first"}, status=409
            )
        body = await request.json()
        enabled = bool(body.get("enabled", True))
        try:
            await psu.enable_output(enabled)
            return web.json_response({"ok": True, "output_on": enabled})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def setpoints(request: web.Request) -> web.Response:
        if _profile_locked():
            return web.json_response(
                {"ok": False, "error": "stop profile first"}, status=409
            )
        body = await request.json()
        voltage_v = body.get("voltage_v")
        current_a = body.get("current_a")
        try:
            await psu.set_targets(
                voltage_v=float(voltage_v) if voltage_v is not None else None,
                current_a=float(current_a) if current_a is not None else None,
            )
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def list_profiles(_request: web.Request) -> web.Response:
        return web.json_response({"profiles": store.list_names()})

    async def upload_profile(request: web.Request) -> web.Response:
        if _profile_locked():
            return web.json_response(
                {"ok": False, "error": "stop profile first"}, status=409
            )
        reader = await request.multipart()
        field = await reader.next()
        # next() is typed Union[MultipartReader, BodyPartReader, None];
        # a simple file upload is always BodyPartReader.
        if not isinstance(field, BodyPartReader) or field.name != "file":
            return web.json_response(
                {"ok": False, "error": "expected multipart field 'file'"}, status=400
            )
        filename = field.filename or "upload.csv"
        data = await field.read()
        try:
            profile = store.save_upload(filename, data)
            return web.json_response(
                {
                    "ok": True,
                    "name": profile.name,
                    "n_points": profile.n_points,
                    "duration_s": profile.duration_s,
                }
            )
        except ProfileError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def profile_start(request: web.Request) -> web.Response:
        body = await request.json()
        name = body.get("name") or ""
        try:
            scale = float(body.get("power_scale_kw", 0))
        except (TypeError, ValueError):
            return web.json_response(
                {"ok": False, "error": "invalid power_scale_kw"}, status=400
            )
        try:
            await player.start(name, scale)
            return web.json_response({"ok": True, "profile": player.state().to_dict()})
        except ProfileError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def profile_stop(_request: web.Request) -> web.Response:
        try:
            await player.stop()
            return web.json_response({"ok": True, "profile": player.state().to_dict()})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/remote", remote)
    app.router.add_post("/api/output", output)
    app.router.add_post("/api/setpoints", setpoints)
    app.router.add_get("/api/profiles", list_profiles)
    app.router.add_post("/api/profiles/upload", upload_profile)
    app.router.add_post("/api/profile/start", profile_start)
    app.router.add_post("/api/profile/stop", profile_stop)

    return app


async def start_web(
    psu: PowerSupply,
    host: str,
    port: int,
    player: ProfilePlayer | None = None,
) -> web.AppRunner:
    app = create_app(psu, player=player)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info(f"Web UI listening on http://{host}:{port}")
    return runner
