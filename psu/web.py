"""Lightweight local web UI for the EA PSU controller.

Plain HTML + vanilla JS. No build step, no framework.
Status is polled via GET /api/status.
Control actions: remote on/off, output on/off, set voltage/current.
Optional debug panel shows the decoded register-505 status bitmap.
"""

from __future__ import annotations

from aiohttp import web
from loguru import logger as log

from .hardware import PowerSupply

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
      grid-template-columns: 4.5rem 1fr;
      gap: 0.55rem 0.75rem;
      align-items: center;
    }
    .form-row label {
      font-size: 0.85rem;
      color: var(--muted);
      text-align: right;
    }
    .form-row input[type="number"] {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      padding: 0.45rem 0.6rem;
      font-size: 0.95rem;
      width: 100%;
      max-width: 9rem;
      font-variant-numeric: tabular-nums;
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
        <span class="target" id="p-pct">— %</span>
      </div>
    </div>
  </div>

  <div class="grid">
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
        $("#p-pct").textContent = r.power_pct.toFixed(1) + " %";
      }

      if (s.nominals) {
        $("#nom-hint").textContent =
          `Nominals: ${s.nominals.voltage_v.toFixed(0)} V / ${s.nominals.current_a.toFixed(0)} A`;
      }

      $("#last-error").textContent = s.last_error ? ("Error: " + s.last_error) : "";
      $("#last-update").textContent = "Last update: " + new Date().toLocaleTimeString();

      $("#remote-on").disabled  = s.remote_active;
      $("#remote-off").disabled = !s.remote_active;
      $("#output-on").disabled  = !s.remote_active || s.output_on;
      $("#output-off").disabled = !s.output_on;

      if ($("#debug-toggle").checked) {
        renderDebug(s.status_bitmap);
      }
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

    $("#debug-toggle").onchange = (e) => {
      const on = e.target.checked;
      $("#debug-panel").classList.toggle("visible", on);
      if (on) refresh();
    };

    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


def create_app(psu: PowerSupply) -> web.Application:
    app = web.Application()

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
                "target_voltage_pct": r.target_voltage_pct,
                "target_current_pct": r.target_current_pct,
                "timestamp": r.timestamp,
            },
            "status_bitmap": None if bm is None else bm.to_dict(),
        }
        return web.json_response(payload)

    async def remote(request: web.Request) -> web.Response:
        body = await request.json()
        enabled = bool(body.get("enabled", True))
        try:
            await psu.enable_remote(enabled)
            return web.json_response({"ok": True, "remote_active": enabled})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def output(request: web.Request) -> web.Response:
        body = await request.json()
        enabled = bool(body.get("enabled", True))
        try:
            await psu.enable_output(enabled)
            return web.json_response({"ok": True, "output_on": enabled})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def setpoints(request: web.Request) -> web.Response:
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

    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/remote", remote)
    app.router.add_post("/api/output", output)
    app.router.add_post("/api/setpoints", setpoints)

    return app


async def start_web(psu: PowerSupply, host: str, port: int) -> web.AppRunner:
    app = create_app(psu)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info(f"Web UI listening on http://{host}:{port}")
    return runner
