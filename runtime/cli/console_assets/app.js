/* OpenMycelium console.
 *
 * Deliberately dependency-free: it ships inside a Python wheel, and a build
 * step would mean the console could be out of date with the runtime it
 * describes. Every response carries schemaVersion; the footer shows it, and a
 * mismatch is visible rather than silently mis-rendered.
 */
"use strict";

const EXPECTED_SCHEMA = 1;
const $ = (id) => document.getElementById(id);
const GIB = 1024 ** 3;

const state = { models: [], gate: null, running: false, schema: null };

function gib(bytes) {
  if (!bytes && bytes !== 0) return "—";
  return (bytes / GIB).toFixed(2) + " GiB";
}
function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function api(path, options) {
  const reply = await fetch(path, options);
  const body = await reply.json();
  if (body && body.schemaVersion) {
    state.schema = body.schemaVersion;
    $("schema").textContent =
      body.schemaVersion === EXPECTED_SCHEMA
        ? `console schema v${body.schemaVersion}`
        : `schema mismatch: runtime v${body.schemaVersion}, UI expects v${EXPECTED_SCHEMA}`;
  }
  if (!reply.ok) throw Object.assign(new Error(body.error || reply.statusText), { body });
  return body;
}

/* ------------------------------------------------------------- navigation */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-active"));
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("is-active"));
    tab.classList.add("is-active");
    $(tab.dataset.screen).classList.add("is-active");
    if (tab.dataset.screen === "fabric") loadFabric();
    if (tab.dataset.screen === "runtime") loadRuntime();
  });
});

/* ---------------------------------------------------------------- overview */
async function loadOverview() {
  try {
    const data = await api("/api/readiness");
    $("checks").innerHTML = data.checks.map((c) => `
      <div class="check">
        <span class="dot ${c.ok ? "ok" : "no"}"></span>
        <div><div>${esc(c.label)}</div>
             <div class="detail">${esc(c.detail || "")}</div></div>
        <span class="badge ${c.ok ? "ok" : "no"}">${c.ok ? "pass" : "fail"}</span>
      </div>`).join("");

    $("overview-devices").innerHTML = data.devices.length
      ? data.devices.map(deviceCard).join("")
      : `<p class="muted">no accelerators reported</p>`;

    // Wording matters: this is never described as one pool.
    $("capacity").textContent = data.aggregateLabel || "";
    $("capacity-note").textContent =
      "Capacity is aggregated across separate GPUs. It is not unified VRAM: " +
      "a tensor larger than one card still does not fit.";

    const rocm = data.rocmPrerequisite;
    $("rocm-states").innerHTML = rocm ? `
      ${stateRow("ROCm Python packages installed", rocm.packagesInstalled)}
      ${stateRow("ROCm WSL system runtime", rocm.systemRuntimeAvailable)}
      ${stateRow("ROCm GPU operation qualified", rocm.gpuQualified)}
      ${rocm.detail ? `<p class="hint">${esc(rocm.detail)}</p>` : ""}`
      : `<p class="muted">not reported</p>`;

    $("conn").className = "pill " + (data.ready ? "pill-live" : "pill-bad");
    // Say when an answer came from the cache. A stale "ready" shown as live
    // would be the console lying about the machine.
    $("conn").textContent = data.ready ? "ready" : "not ready";
    $("conn").title = data.cached
      ? `probed ${Math.round(data.cacheAgeSeconds)}s ago (cached)`
      : "probed just now";
  } catch (error) {
    $("checks").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
    $("conn").className = "pill pill-bad";
    $("conn").textContent = "unreachable";
  }
}

function stateRow(label, value) {
  return `<div class="check">
    <span class="dot ${value ? "ok" : "no"}"></span>
    <div>${esc(label)}</div>
    <span class="badge ${value ? "ok" : "no"}">${value ? "yes" : "no"}</span>
  </div>`;
}

function deviceCard(d) {
  const used = d.totalBytes - d.freeBytes;
  const pct = d.totalBytes ? (used / d.totalBytes) * 100 : 0;
  return `<div class="device">
    <div class="vendor ${esc(d.vendor)}">${esc(d.vendor)}</div>
    <div class="name">${esc(d.name)}</div>
    <div class="meter"><span style="width:${pct.toFixed(1)}%"></span></div>
    <div class="muted" style="font-size:12.5px">
      ${gib(used)} in use of ${gib(d.totalBytes)}${d.torch ? " · torch " + esc(d.torch) : ""}
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ fabric */
async function loadFabric(refresh) {
  $("fabric-list").innerHTML = `<p class="muted">probing…</p>`;
  try {
    const data = await api("/api/fabric" + (refresh ? "?refresh=1" : ""));
    const f = data.fabric || {};
    const devices = f.devices || [];
    $("fabric-list").innerHTML = devices.map((d) => `
      <article class="card">
        <div class="vendor ${esc(d.vendor || "")}">${esc(d.vendor || "")}</div>
        <div class="name" style="font-weight:600;margin-bottom:8px">${esc(d.name || "")}</div>
        <dl class="kv">
          <dt>identity</dt><dd class="mono">${esc(d.identity || "—")}</dd>
          <dt>source</dt><dd>${esc(d.identitySource || "—")} (${esc(d.identityConfidence || "—")})</dd>
          <dt>memory</dt><dd>${gib(d.totalBytes)} total, ${gib(d.freeBytes)} free</dd>
          <dt>runtime</dt><dd>${esc(d.torch || "—")}</dd>
          <dt>power</dt><dd>${d.powerWatts != null ? esc(d.powerWatts) + " W"
            : esc(d.powerUnavailableReason || "unavailable")}</dd>
        </dl>
      </article>`).join("") || `<p class="muted">no devices</p>`;

    const meta = [
      `${devices.length} device(s)`,
      f.crossVendor ? "cross-vendor" : "single vendor",
      f.identitiesUnique ? "identities unique" : "DUPLICATE IDENTITIES",
      f.fromCache ? "from cache" : "live probe",
    ].join(" · ");
    $("fabric-list").insertAdjacentHTML("beforeend",
      `<p class="hint span2">${esc(meta)}</p>`);
  } catch (error) {
    $("fabric-list").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}
$("refresh-fabric").addEventListener("click", () => loadFabric(true));

/* ------------------------------------------------------------------ models */
async function loadModels() {
  try {
    const data = await api("/api/models");
    state.models = data.models || [];
    $("model-list").innerHTML = state.models.map((m) => `
      <article class="card">
        <div class="name" style="font-weight:600">${esc(m.name)}</div>
        <dl class="kv" style="margin:8px 0">
          <dt>architecture</dt><dd>${esc(m.architecture || "—")}</dd>
          <dt>layers</dt><dd>${esc(m.layers ?? "—")}</dd>
          <dt>hidden</dt><dd>${esc(m.hidden ?? "—")}</dd>
          <dt>dtype</dt><dd>${esc(m.dtype || "—")}</dd>
          <dt>size</dt><dd>${gib(m.bytes)}</dd>
        </dl>
        <button class="btn btn-quiet" data-verify="${esc(m.name)}">Verify shards</button>
      </article>`).join("") || `<p class="muted">no models in the store</p>`;

    const options = state.models
      .map((m) => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("");
    $("plan-model").innerHTML = options;
    $("run-model").innerHTML = options;

    document.querySelectorAll("[data-verify]").forEach((button) => {
      button.addEventListener("click", () => verify(button.dataset.verify));
    });
  } catch (error) {
    $("model-list").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

async function verify(name) {
  $("verify-out").innerHTML = `<p class="muted">verifying ${esc(name)}…</p>`;
  const data = await api("/api/verify?model=" + encodeURIComponent(name));
  $("verify-out").innerHTML = `<article class="card">
    <h3>${esc(name)} <span class="badge ${data.ok ? "ok" : "no"}">
      ${data.ok ? "complete" : "problems"}</span></h3>
    <p class="hint">${esc(data.note || "")}</p>
    ${(data.problems || []).length
      ? `<ul>${data.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`
      : `<p class="muted">${(data.shards || []).length} shard(s) checked</p>`}
  </article>`;
}

/* -------------------------------------------------------------- placement */
async function loadPlan() {
  const name = $("plan-model").value;
  if (!name) return;
  $("plan-out").innerHTML = `<p class="muted">compiling placement…</p>`;
  try {
    const data = await api("/api/placement?model=" + encodeURIComponent(name));
    $("plan-out").innerHTML = data.ok ? renderPlan(data)
      : `<article class="card"><h3>Placement refused</h3>
         <p class="hint">${esc(data.detail || "")}</p></article>`;
  } catch (error) {
    $("plan-out").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}
$("plan-model").addEventListener("change", loadPlan);

function renderPlan(data) {
  const p = data.pipeline || {};
  const stages = data.stages || [];
  return `<article class="card">
    <dl class="kv">
      <dt>placement</dt><dd class="mono">${esc(data.placementId)}</dd>
      <dt>digest</dt><dd class="mono">${esc((data.manifestDigest || "").slice(0, 32))}</dd>
      <dt>boundary</dt><dd>after layer ${esc(p.boundaryAfterLayer)}</dd>
      <dt>transport</dt><dd>${esc(p.transport || "—")}</dd>
      <dt>activation</dt><dd>${esc(p.activationBytesPerToken ?? "—")} B per token</dd>
      <dt>ownership</dt><dd>${esc(data.totalTensors)} tensors,
        overlap ${esc(data.overlapCount)}
        <span class="badge ${data.exclusive ? "ok" : "no"}">
          ${data.exclusive ? "exclusive" : "OVERLAPPING"}</span></dd>
    </dl>
  </article>
  <div class="split">
    ${stageCard(stages[0])}
    <div class="boundary">activation boundary ·
      ${esc(p.activationBytesPerToken ?? "—")} B per token · ${esc(p.transport || "")}</div>
    ${stageCard(stages[1])}
  </div>
  ${(p.reasons || []).length ? `<article class="card"><h3>Why this split</h3>
    <ul>${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></article>` : ""}`;
}

function stageCard(stage) {
  if (!stage) return "";
  const layers = stage.layers || [];
  const span = layers.length ? `${layers[0]}–${layers[layers.length - 1]}` : "—";
  const holds = [stage.holdsEmbedding ? "embedding" : null,
                 stage.holdsLMHead ? "output head" : null].filter(Boolean).join(", ");
  return `<div class="stage ${esc(stage.runtime || stage.role || "")}">
    <div class="vendor ${esc(stage.runtime || "")}">${esc(stage.role || "")} ·
      ${esc(stage.runtime || "")}</div>
    <div style="font-weight:600;margin:4px 0">layers ${esc(span)}${holds ? " + " + esc(holds) : ""}</div>
    <dl class="kv">
      <dt>tensors</dt><dd>${esc(stage.tensorCount)}</dd>
      <dt>weights</dt><dd>${gib(stage.weightBytes)}</dd>
      <dt>KV cache</dt><dd>${gib(stage.kvBytes)}</dd>
      <dt>budget</dt><dd>${gib(stage.budgetBytes)}</dd>
      <dt>device</dt><dd class="mono">${esc(stage.deviceIdentity || "—")}</dd>
      <dt>identity</dt><dd>${esc(stage.identitySource || "—")}
        (${esc(stage.identityConfidence || "—")})</dd>
    </dl>
  </div>`;
}

/* --------------------------------------------------------------------- run */
async function checkGate() {
  const model = $("run-model").value;
  $("gates").innerHTML = `<p class="muted">checking…</p>`;
  try {
    const gate = await api("/api/run/gate?model=" + encodeURIComponent(model));
    state.gate = gate;
    $("gates").innerHTML = gate.gates.map((g) => `
      <div class="check">
        <span class="dot ${g.ok ? "ok" : "no"}"></span>
        <div><div>${esc(g.label)}</div>
             <div class="detail">${esc(g.detail || "")}</div></div>
        <span class="badge ${g.ok ? "ok" : "no"}">${g.ok ? "ok" : "no"}</span>
      </div>`).join("");

    if (gate.expectedAllocation) {
      $("alloc-card").hidden = false;
      $("alloc").innerHTML = `<div class="split">` +
        gate.expectedAllocation.map((a) => `
          <div class="stage ${esc(a.runtime)}">
            <div class="vendor ${esc(a.runtime)}">${esc(a.role)} · ${esc(a.runtime)}</div>
            <div style="margin:4px 0">layers ${esc(a.layers[0])}–${esc(a.layers[a.layers.length - 1])}</div>
            <div class="muted" style="font-size:12.5px">
              ${gib(a.weightBytes)} weights of ${gib(a.budgetBytes)} budget<br>
              <span class="mono">${esc(a.deviceIdentity)}</span></div>
          </div>`).join("") + `</div>
        <p class="hint">${esc(gate.note || "")}</p>`;
    }
    $("btn-start").disabled = !gate.canRun || state.running;
  } catch (error) {
    $("gates").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}
$("btn-gate").addEventListener("click", checkGate);

$("btn-start").addEventListener("click", async () => {
  const model = $("run-model").value;
  const prompt = $("run-prompt").value.trim();
  if (!prompt) { alert("A prompt is required."); return; }
  const alloc = (state.gate && state.gate.expectedAllocation) || [];
  const summary = alloc.map((a) =>
    `${a.role} (${a.runtime}): layers ${a.layers[0]}–${a.layers[a.layers.length - 1]}, ` +
    `${gib(a.weightBytes)}`).join("\n");
  if (!confirm(`Start a run on ${model}?\n\nThis occupies both GPUs:\n${summary}\n\n` +
               `The runtime serves one request at a time.`)) return;

  $("run-output").textContent = "";
  $("run-events").textContent = "";
  $("run-metrics").innerHTML = `<p class="muted">running…</p>`;
  try {
    await api("/api/run/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, prompt, maxNewTokens: Number($("run-tokens").value) }),
    });
    state.running = true;
    $("btn-start").disabled = true;
    $("btn-stop").disabled = false;
  } catch (error) {
    $("run-metrics").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
});

$("btn-stop").addEventListener("click", async () => {
  if (!confirm("Stop the run this console started?")) return;
  $("btn-stop").disabled = true;
  const data = await api("/api/run/stop", { method: "POST" });
  appendEvent(`stop requested: ${JSON.stringify(data)}`);
  loadRuntime();
});

/* ---------------------------------------------------------------- events */
function appendEvent(line) {
  const box = $("run-events");
  if (box.textContent === "—") box.textContent = "";
  box.textContent += line + "\n";
  box.scrollTop = box.scrollHeight;
}

function connectEvents() {
  const source = new EventSource("/api/run/events");
  source.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    if (event.kind === "stdout") {
      const box = $("run-output");
      if (box.textContent === "—") box.textContent = "";
      box.textContent += event.line + "\n";
      box.scrollTop = box.scrollHeight;
    } else if (event.kind === "result") {
      renderMetrics(event.result);
    } else if (event.kind === "finished") {
      state.running = false;
      $("btn-start").disabled = false;
      $("btn-stop").disabled = true;
      appendEvent(`finished, exit code ${event.exitCode}`);
      loadRuntime();
    } else {
      appendEvent(`${event.kind}: ${event.line || JSON.stringify(event)}`);
    }
  };
  source.onerror = () => { /* EventSource retries on its own */ };
}

function renderMetrics(payload) {
  // The coordinator emits {"result": ..., "health": ...}. generatedTokens is
  // the list of ids, not a count -- reading it as a number would show
  // "[object Object]" or a comma-separated wall of integers.
  const r = (payload && payload.result) || payload || {};
  const health = (payload && payload.health) || {};
  const count = Array.isArray(r.generatedTokens)
    ? r.generatedTokens.length : (r.generatedTokens ?? "—");
  const inter = r.interTokenMs || {};
  const rows = [
    ["TTFT", r.ttftMs != null ? Number(r.ttftMs).toFixed(0) + " ms" : "—"],
    ["decode", r.decodeTokensPerSecond != null
      ? Number(r.decodeTokensPerSecond).toFixed(2) + " tok/s" : "—"],
    ["generated", count + (r.stopReason ? ` (${esc(r.stopReason)})` : "")],
    ["inter-token", inter.p50 != null
      ? `p50 ${inter.p50} ms · p95 ${inter.p95 ?? "—"} ms` : "—"],
    ["state", health.state || "—"],
  ];
  $("run-metrics").innerHTML = `<dl class="kv">` +
    rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("") + `</dl>`;

  if (r.text) {
    const box = $("run-output");
    box.textContent = r.text;
    box.scrollTop = box.scrollHeight;
  }
}

/* --------------------------------------------------------------- runtime */
async function loadRuntime() {
  try {
    const work = await api("/api/workloads");
    const rows = (work.running || []).map((r) => `<tr>
      <td class="mono">${esc((r.runId || "").slice(0, 8))}</td>
      <td>${esc(r.model || "")}</td><td>${esc(r.state || "")}</td>
      <td>${esc(r.pid ?? "")}</td></tr>`).join("");
    $("workloads").innerHTML = rows
      ? `<table><tr><th>run</th><th>model</th><th>state</th><th>pid</th></tr>${rows}</table>`
      : `<p class="muted">no workload is running</p>` +
        ((work.stale || []).length
          ? `<p class="hint">${work.stale.length} stale record(s); the process is gone.
             Clear with <span class="mono">openmycelium stop --prune</span></p>` : "");

    const res = await api("/api/residency");
    $("residency").innerHTML = (res.devices || []).map((d) => `
      <div class="device">
        <div class="vendor ${esc(d.vendor)}">${esc(d.vendor)}</div>
        <div class="name">${esc(d.name)}</div>
        <div class="muted" style="font-size:12.5px">
          ${esc(d.usedMiB)} MiB in use of ${esc(d.totalMiB)} MiB
          ${d.source ? `<br><span class="hint">${esc(d.source)}</span>` : ""}
        </div>
      </div>`).join("") || `<p class="muted">no residency reported</p>`;

    const prov = await api("/api/provenance");
    const p = prov.provenance || {};
    $("provenance").innerHTML = `<dl class="kv">
      <dt>version</dt><dd>${esc(p.openmycelium)}</dd>
      <dt>content</dt><dd class="mono">${esc((p.installedContentSha256 || "").slice(0, 32))}</dd>
      <dt>files</dt><dd>${esc(p.pythonFiles ?? "—")}</dd>
      <dt>mccl</dt><dd>${esc(p.mcclVersion)} · ${esc(p.transport)}</dd>
      <dt>HSA runtime</dt><dd>${p.hsaRuntime
        ? esc(p.hsaRuntime.flavor) + " (" + esc(p.hsaRuntime.source) + ")" : "—"}</dd>
      <dt>event schema</dt><dd>v${esc(p.eventSchemaVersion)}</dd>
    </dl>`;
  } catch (error) {
    $("workloads").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ start */
loadOverview();
loadModels().then(loadPlan);
connectEvents();

/* Readiness asks both GPU interpreters to import torch, which takes seconds.
 * Polling it on a fixed timer let requests overlap and pile up faster than
 * they completed. This waits for the previous one to finish before scheduling
 * the next, so there is never more than one in flight. */
(function pollReadiness() {
  const PERIOD = 30000;
  setTimeout(async () => {
    try { await loadOverview(); } finally { pollReadiness(); }
  }, PERIOD);
})();
