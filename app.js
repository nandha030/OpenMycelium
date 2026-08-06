let pools = [];
let jobs = [];
let clusters = [];
let queues = [];
let integrations = [];
let sessionUser = null;
let discoveryProfile = {};
const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
const storedTheme = localStorage.getItem('openmycelium-theme');
const initialTheme = storedTheme || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('openmycelium-theme', theme);
  const toggle = byId('theme-toggle');
  if (toggle) {
    toggle.setAttribute('aria-label', theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    toggle.title = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
  }
}
applyTheme(initialTheme);
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401) { window.location.replace('/login.html'); throw new Error('Authentication required'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
async function loadSession() {
  sessionUser = await api('/api/v1/auth/me');
  byId('session-user').firstChild.textContent = sessionUser.email;
  byId('session-role').textContent = sessionUser.role.replaceAll('_', ' ');
  if (sessionUser.role !== 'platform_admin') document.querySelectorAll('[data-view="access"],[data-view="settings"]').forEach(item => item.hidden = true);
}
async function loadControlHealth() {
  const badge = byId('control-status');
  try {
    const response = await fetch('/api/v1/ready');
    if (!response.ok) throw new Error('dependencies unavailable');
    badge.classList.remove('degraded');
    badge.lastChild.textContent = ' Control plane ready';
  } catch (error) {
    badge.classList.add('degraded');
    badge.lastChild.textContent = ' Control plane degraded';
  }
}
function tag(status) { const c = status === 'Running' ? 'green' : status === 'Queued' ? 'amber' : 'gray'; return `<span class="tag ${c}">${escapeHtml(status)}</span>`; }
function row(job) { const sshTitle=job.sshConfigured?'Show SSH connection command':'Configure SSH access for this workload'; return `<tr><td>${escapeHtml(job.name)}</td><td class="mono">${escapeHtml(job.type)}</td><td class="mono">${escapeHtml(job.pool)}</td><td>${escapeHtml(job.allocation)}</td><td class="mono">${escapeHtml(job.runtime)}</td><td>${tag(job.status)}</td><td><div class="workload-controls"><button data-workload-id="${escapeHtml(job.id)}" data-action="start">Start</button><button data-workload-id="${escapeHtml(job.id)}" data-action="stop">Stop</button><button data-workload-id="${escapeHtml(job.id)}" data-action="redeploy">Redeploy</button><button data-workload-id="${escapeHtml(job.id)}" data-action="refresh">Refresh</button><button class="ssh-button" data-workload-id="${escapeHtml(job.id)}" data-action="ssh" title="${sshTitle}">${job.sshConfigured?'SSH':'Add SSH'}</button><button class="danger-button" data-workload-id="${escapeHtml(job.id)}" data-action="delete">Delete</button></div></td></tr>`; }
function renderPools() {
  const content = pools.map(p => `<div class="pool-row"><div><div class="pool-name">${escapeHtml(p.name)}</div><div class="pool-meta">${escapeHtml(p.vendor)} / ${escapeHtml(p.runtime)}</div></div><div class="capacity"><i style="width:${p.enabled ? 100 : 0}%"></i></div><div class="pool-value">${p.enabled ? 'enabled' : 'disabled'}</div></div>`).join('');
  byId('pool-list').innerHTML = content || '<div class="empty-inline">No accelerator pools configured. Create a policy after connecting real hardware.</div>';
  byId('pools-detail').innerHTML = pools.length ? pools.map(p => `<article class="panel"><p class="eyebrow">${escapeHtml(p.vendor)} / ${escapeHtml(p.runtime)}</p><h3>${escapeHtml(p.name)}</h3><div class="resource-meta"><span><b>Policy</b>${escapeHtml(p.policy)}</span><span><b>Selector</b>${escapeHtml(p.selector || 'none')}</span></div><div class="resource-actions"><span class="tag ${p.enabled ? 'green' : 'gray'}">${p.enabled ? 'Enabled' : 'Disabled'}</span><button class="danger-button" data-delete-pool="${escapeHtml(p.id)}">Remove</button></div></article>`).join('') : '<div class="empty-state"><strong>No pools configured.</strong><span>Create a logical placement policy for compatible hardware.</span></div>';
  const select = byId('job-pool'); if (select) select.innerHTML = '<option value="cpu-local">CPU / local runtime</option>' + pools.map(p => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`).join('');
  refreshTopology();
}
function renderJobs() { const content = jobs.length ? jobs.map(row).join('') : '<tr><td colspan="7" class="mono">No workloads have been deployed yet.</td></tr>'; byId('workload-table').innerHTML = content; byId('workloads-all').innerHTML = content; byId('queued-jobs').textContent = jobs.filter(j => j.status === 'Queued').length; byId('workloads-count').textContent = jobs.length; byId('inference-count').textContent = jobs.filter(j => j.type === 'Inference').length; refreshTopology(); }
async function syncWorkloads() {
  try {
    const remote = (await api('/api/v1/workloads')).workloads || [];
    jobs = remote.map(item => ({ id:item.id, name:item.name, type:item.kind === 'ollama-inference' ? 'Inference' : item.kind, pool:item.pool || 'unassigned', allocation:item.accelerators ? `${item.accelerators} accelerator${item.accelerators===1?'':'s'}` : 'CPU / runtime managed', runtime:item.kind === 'ollama-inference' ? 'Ollama' : (item.image || 'OpenMycelium'), status:item.status.charAt(0).toUpperCase() + item.status.slice(1), sshHost:item.sshHost||'', sshPort:item.sshPort||22, sshUser:item.sshUser||'', sshConfigured:Boolean(item.sshHost&&item.sshUser) })); renderJobs();
  } catch (error) { byId('workloads-all').innerHTML = `<tr><td colspan="7">${escapeHtml(error.message)}</td></tr>`; }
}
function renderClusters() { byId('clusters-count').textContent = clusters.length; byId('cluster-list').innerHTML = clusters.length ? clusters.map(c => `<article class="cluster-card"><header><div><h3>${escapeHtml(c.name)}</h3><p>${escapeHtml(c.type)}</p></div>${tag(c.status === 'ready' ? 'Running' : 'Pending')}</header><p>${escapeHtml(c.endpoint)}</p><div class="cluster-stats"><span><strong>${c.nodes}</strong><br />nodes</span><span><strong>${c.accelerators}</strong><br />accelerators</span></div><div class="resource-actions"><button class="danger-button" data-delete-cluster="${escapeHtml(c.id)}">Remove</button></div></article>`).join('') : '<div class="empty-state"><strong>No clusters connected.</strong><span>Register a cluster, then install an agent to report nodes and accelerators.</span></div>'; refreshTopology(); }
function renderQueues() { byId('queue-list').innerHTML = queues.length ? queues.map(q => `<article class="panel"><p class="eyebrow">Priority ${q.priority}</p><h3>${escapeHtml(q.name)}</h3><div class="resource-meta"><span><b>Accelerators</b>${q.acceleratorQuota}</span><span><b>Memory</b>${q.memoryQuotaGB} GB</span><span><b>Preemption</b>${q.preemptionEnabled ? 'enabled' : 'disabled'}</span></div><div class="resource-actions"><button class="danger-button" data-delete-queue="${escapeHtml(q.id)}">Remove</button></div></article>`).join('') : '<div class="empty-state"><strong>No queues configured.</strong><span>Create a queue to apply priority and capacity limits.</span></div>'; }
async function syncResources() { const [clusterData,poolData,queueData,integrationData]=await Promise.all([api('/api/v1/clusters'),api('/api/v1/pools'),api('/api/v1/queues'),api('/api/v1/integrations')]);clusters=clusterData.clusters||[];pools=poolData.pools||[];queues=queueData.queues||[];integrations=integrationData.integrations||[];renderClusters();renderPools();renderQueues();renderIntegrations(); }
function setView(id) { document.querySelectorAll('.view').forEach(v => v.classList.toggle('visible', v.id === id || (id === 'inference' && v.id === 'ollama-console'))); document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === id)); const label = document.querySelector(`.nav-item[data-view="${id}"] span`)?.textContent.trim() || 'Control plane'; byId('view-label').textContent = label; byId('page-title').textContent = id === 'overview' ? 'Infrastructure overview' : label; document.querySelector('.sidebar').classList.remove('open'); }
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
byId('mobile-menu').addEventListener('click',()=>document.querySelector('.sidebar').classList.toggle('open'));
byId('theme-toggle').addEventListener('click', () => { applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); refreshTopology(); });
document.querySelectorAll('dialog').forEach(modal => {
  modal.querySelectorAll('.close, button[value="cancel"]').forEach(button => {
    button.type = 'button';
    button.setAttribute('formnovalidate', '');
    button.addEventListener('click', () => modal.close('cancel'));
  });
  modal.addEventListener('click', event => { if (event.target === modal) modal.close('cancel'); });
});
function validSubmission(event) {
  event.preventDefault();
  return event.currentTarget.closest('form').reportValidity();
}
const dialog = byId('workload-dialog');
['open-submit','workload-submit','training-submit','deploy-endpoint'].forEach(id => byId(id)?.addEventListener('click', () => dialog.showModal()));
byId('submit-job').addEventListener('click', async event => { if (!validSubmission(event)) return; const sshHost=byId('job-ssh-host').value.trim();const sshUser=byId('job-ssh-user').value.trim();const sshConfigured=Boolean(sshHost||sshUser);try { await api('/api/v1/workloads',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('job-name').value.trim(),kind:byId('job-type').value,pool:byId('job-pool').value,image:byId('job-image').value.trim(),accelerators:Number(byId('job-gpus').value),sshHost,sshUser,sshPort:sshConfigured?Number(byId('job-ssh-port').value):0})}); dialog.close(); await syncWorkloads(); await loadObservability(); setView('workloads'); } catch(error) { byId('job-result').textContent=error.message; } });
byId('refresh').addEventListener('click', () => { byId('refresh').textContent = 'Refreshed'; setTimeout(() => byId('refresh').textContent = 'Refresh', 900); });
const clusterDialog=byId('cluster-dialog');const poolDialog=byId('pool-dialog');const queueDialog=byId('queue-dialog');
byId('add-cluster').addEventListener('click',()=>clusterDialog.showModal());byId('create-pool').addEventListener('click',()=>poolDialog.showModal());byId('new-queue').addEventListener('click',()=>queueDialog.showModal());
byId('save-cluster').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/clusters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('cluster-name').value.trim(),endpoint:byId('cluster-endpoint').value.trim(),type:byId('cluster-type').value})});clusterDialog.close();await syncResources();await loadObservability();}catch(error){byId('cluster-result').textContent=error.message;}});
byId('save-pool').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/pools',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('pool-name').value.trim(),vendor:byId('pool-vendor').value,runtime:byId('pool-runtime').value,policy:byId('pool-policy').value,selector:byId('pool-selector').value.trim()})});poolDialog.close();await syncResources();await loadObservability();}catch(error){byId('pool-result').textContent=error.message;}});
byId('save-queue').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/queues',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('queue-name').value.trim(),priority:Number(byId('queue-priority').value),acceleratorQuota:Number(byId('queue-accelerators').value),memoryQuotaGB:Number(byId('queue-memory').value),preemptionEnabled:byId('queue-preemption').checked})});queueDialog.close();await syncResources();await loadObservability();}catch(error){byId('queue-result').textContent=error.message;}});
async function scanHardware(event) {
  const button = event?.currentTarget || byId('scan-hardware');
  const originalLabel = button.textContent;
  button.textContent = 'Scanning...';
  try {
    const response = await fetch('/api/v1/discovery/scan', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Hardware discovery failed');
    renderDiscovery(data);
    byId('discovery-summary').textContent = `Discovery complete. ${(data.accelerators || []).length} accelerator${(data.accelerators || []).length === 1 ? '' : 's'} reported by ${data.source || 'the local host'}.`;
  } catch (error) {
    byId('scan-source').textContent = 'scan failed';
    byId('discovery-results').textContent = error.message;
    byId('discovery-summary').textContent = `Discovery failed. ${error.message}`;
  } finally { button.textContent = originalLabel; }
}
function renderDiscovery(data) {
  discoveryProfile = { ...(data.hostProfile || {}), accelerators: data.accelerators || data.hostProfile?.accelerators || [] };
  byId('scan-source').textContent = data.source || 'local host';
  byId('available-gpus').textContent = (data.accelerators || []).length;
  byId('memory-capacity').textContent = (data.accelerators || []).length ? 'Reported' : '--';
  const host = data.hostProfile || {};
  const hostSummary = host.cpu ? `<div class="host-summary"><div><span>Host</span><strong>${escapeHtml(host.name)}</strong></div><div><span>CPU</span><strong>${escapeHtml(host.cpu)}</strong></div><div><span>Logical cores</span><strong>${escapeHtml(host.logicalCores)}</strong></div><div><span>Memory</span><strong>${escapeHtml(host.memoryGB)} GB</strong></div></div>` : '';
  const rows = (data.accelerators || []).map(a => `<div class="discovery-row"><strong>${escapeHtml(a.model)}</strong><span>${escapeHtml(a.vendor)}</span><span>${escapeHtml(a.memory || `${a.memory_gb} GB`)}</span>${tag(a.health || 'Unknown')}</div>`).join('');
  byId('discovery-results').innerHTML = hostSummary + (rows || 'No accelerator was detected. CPU-backed workloads and model planning remain available.');
  refreshTopology();
}

const topologyCanvas = byId('compute-topology');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let topologyFrame = 0;
function topologyNodePath(context, x, y, radius) {
  context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.closePath();
}
function drawTopology(time = 0) {
  if (!topologyCanvas) return;
  const rect = topologyCanvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.round(rect.width); const height = Math.round(rect.height);
  if (topologyCanvas.width !== Math.round(width * ratio) || topologyCanvas.height !== Math.round(height * ratio)) { topologyCanvas.width = Math.round(width * ratio); topologyCanvas.height = Math.round(height * ratio); }
  const context = topologyCanvas.getContext('2d'); context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, width, height);
  const style = getComputedStyle(document.documentElement);
  const color = name => style.getPropertyValue(name).trim();
  const compact = width < 620;
  const positions = compact ? [[.12,.2],[.5,.13],[.5,.5],[.88,.2],[.86,.79],[.14,.79],[.5,.88]] : [[.08,.28],[.31,.2],[.5,.5],[.72,.2],[.92,.5],[.17,.78],[.76,.8]];
  const accelerationCount = discoveryProfile.accelerators?.length || 0;
  const nodes = [
    { label:'Local host', detail:`${accelerationCount} accelerator${accelerationCount === 1 ? '' : 's'}`, color:color('--blue') },
    { label:'Pools', detail:`${pools.length} policies`, color:color('--mint') },
    { label:'Scheduler', detail:`${queues.length} queues`, color:color('--text-1'), core:true },
    { label:'Workloads', detail:`${jobs.length} managed`, color:color('--gold') },
    { label:'Model runtime', detail:`${jobs.filter(job=>job.runtime === 'Ollama').length} local`, color:color('--violet') },
    { label:'Clusters', detail:`${clusters.length} connected`, color:color('--blue') },
    { label:'Control plane', detail:'API / DB / NATS', color:color('--mint') }
  ].map((node, index) => ({ ...node, x:positions[index][0] * width, y:positions[index][1] * height }));
  context.lineWidth = 1;
  nodes.forEach((node, index) => {
    if (index === 2) return;
    const scheduler = nodes[2];
    context.beginPath(); context.moveTo(scheduler.x, scheduler.y); context.lineTo(node.x, node.y); context.strokeStyle = color('--border-strong'); context.stroke();
    const progress = reducedMotion ? .58 : ((time / 1900) + index * .14) % 1;
    const pulseX = scheduler.x + (node.x - scheduler.x) * progress; const pulseY = scheduler.y + (node.y - scheduler.y) * progress;
    topologyNodePath(context, pulseX, pulseY, 2.3); context.fillStyle = node.color; context.fill();
  });
  nodes.forEach(node => {
    const radius = node.core ? (compact ? 23 : 27) : (compact ? 18 : 21);
    topologyNodePath(context, node.x, node.y, radius + 5); context.fillStyle = color('--surface-2'); context.fill(); context.strokeStyle = color('--border'); context.stroke();
    topologyNodePath(context, node.x, node.y, radius); context.fillStyle = node.color; context.globalAlpha = node.core ? 1 : .88; context.fill(); context.globalAlpha = 1;
    context.textAlign = 'center'; context.textBaseline = 'top'; context.fillStyle = color('--text-1'); context.font = `600 ${compact ? 9 : 10}px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`; context.fillText(node.label, node.x, node.y + radius + 9);
    context.fillStyle = color('--text-3'); context.font = `500 ${compact ? 8 : 9}px ui-monospace, SFMono-Regular, Consolas, monospace`; context.fillText(node.detail, node.x, node.y + radius + 23);
  });
}
function refreshTopology() { drawTopology(performance.now()); }
function animateTopology(time) { drawTopology(time); topologyFrame = requestAnimationFrame(animateTopology); }
window.addEventListener('resize', refreshTopology);
if (reducedMotion) refreshTopology(); else topologyFrame = requestAnimationFrame(animateTopology);
byId('scan-hardware').addEventListener('click', scanHardware);
byId('discover').addEventListener('click', scanHardware);
document.querySelector('.copy-command').addEventListener('click', async () => { await navigator.clipboard.writeText(byId('install-command').textContent); document.querySelector('.copy-command').textContent = 'Copied'; setTimeout(() => document.querySelector('.copy-command').textContent = 'Copy command', 900); });
byId('copy-host-agent').addEventListener('click', async () => { await navigator.clipboard.writeText(byId('host-agent-command').textContent); byId('copy-host-agent').textContent = 'Copied'; setTimeout(() => byId('copy-host-agent').textContent = 'Copy command', 900); });
function plannerValue(id) { return Number(byId(id).value) || 0; }
function displayGb(value) { return `${value.toFixed(value < 10 ? 2 : 1)} GB`; }
const modelPresets = {
  'dense-8b': { total: 8, active: 8, layers: 32, kvHeads: 8, headDim: 128 },
  'dense-12b': { total: 12, active: 12, layers: 40, kvHeads: 8, headDim: 128 },
  'dense-70b': { total: 70, active: 70, layers: 80, kvHeads: 8, headDim: 128 },
  'moe-47b': { total: 47, active: 13, layers: 32, kvHeads: 8, headDim: 128 }
};
const devicePresets = {
  'gpu-24': { memory: 24, bandwidth: 1000, compute: 82, interconnect: 32 },
  'hbm-80': { memory: 80, bandwidth: 3350, compute: 990, interconnect: 450 },
  'hbm-141': { memory: 141, bandwidth: 4800, compute: 1800, interconnect: 900 },
  'hbm-192': { memory: 192, bandwidth: 5300, compute: 1300, interconnect: 896 },
  'unified-64': { memory: 64, bandwidth: 546, compute: 28, interconnect: 0 },
  'cpu-64': { memory: 64, bandwidth: 100, compute: 2, interconnect: 0 }
};
function setPlannerValues(values) {
  Object.entries(values).forEach(([id, value]) => { byId(id).value = value; });
  updatePlanner();
}
function updatePlanner() {
  const totalParams = plannerValue('total-params'); const activeParams = plannerValue('active-params'); const weightBits = plannerValue('weight-bits'); const kvBits = plannerValue('kv-bits');
  const layers = plannerValue('layers'); const kvHeads = plannerValue('kv-heads'); const headDim = plannerValue('head-dim'); const context = plannerValue('context'); const batch = Math.max(1, plannerValue('batch'));
  const devices = Math.max(1, Math.floor(plannerValue('device-count'))); const deviceMemory = plannerValue('available-memory'); const bandwidth = plannerValue('memory-bandwidth'); const compute = plannerValue('compute-tflops');
  const interconnect = plannerValue('interconnect-bandwidth'); const tensorParallel = Math.max(1, Math.floor(plannerValue('tensor-parallel'))); const pipelineParallel = Math.max(1, Math.floor(plannerValue('pipeline-parallel')));
  const utilization = Math.min(1, plannerValue('utilization') / 100); const reserve = Math.min(.5, plannerValue('reserve') / 100); const cpuOffload = plannerValue('cpu-offload'); const mode = byId('workload-mode').value;
  const effectiveWeightBits = mode === 'training' ? Math.max(16, weightBits) : weightBits;
  const rawWeights = totalParams * effectiveWeightBits / 8;
  const weights = rawWeights * (effectiveWeightBits <= 8 ? 1.12 : 1.05);
  const kv = (layers * batch * context * kvHeads * headDim * 2 * (kvBits / 8)) / 1e9;
  let training = 0;
  if (mode === 'lora') training = totalParams * .01 * 14 + activeParams * .025 * batch;
  if (mode === 'training') training = totalParams * 14 + activeParams * .06 * batch;
  const runtime = Math.max(1, (weights + kv + training) * .1);
  const required = weights + kv + training + runtime;
  const requestedParallel = tensorParallel * pipelineParallel;
  const topologyValid = requestedParallel <= devices;
  const parallelWidth = Math.max(1, Math.min(devices, requestedParallel));
  const replicas = Math.max(1, Math.floor(devices / parallelWidth));
  const shardingEfficiency = parallelWidth === 1 ? 1 : Math.max(.74, 1 - .025 * (parallelWidth - 1));
  const gpuRequired = Math.max(0, required - Math.min(cpuOffload, weights));
  const perDevice = gpuRequired / parallelWidth / shardingEfficiency;
  const usablePerDevice = deviceMemory * (1 - reserve);
  const usableCluster = deviceMemory * devices * (1 - reserve);
  const remaining = usablePerDevice - perDevice;
  const parallelOverhead = parallelWidth === 1 ? 0 : Math.min(38, Math.max(4, 15 - Math.log2(Math.max(1, interconnect)) * 1.3 + (parallelWidth - 2) * 1.1));
  const flops = activeParams * 2;
  const computeCeiling = flops > 0 ? compute * 1000 * devices * utilization / flops : 0;
  const offloadFraction = weights > 0 ? Math.min(cpuOffload, weights) / weights : 0;
  const offloadPenalty = 1 - offloadFraction * .92;
  const bandwidthCeiling = bandwidth * devices * utilization / Math.max(.1, weights) * offloadPenalty;
  const throughput = Math.max(0, Math.min(computeCeiling, bandwidthCeiling) * (1 - parallelOverhead / 100));
  const kvPerBatchShard = Math.max(.0001, (kv / batch) / parallelWidth);
  const fixedPerDevice = Math.max(0, perDevice - kv / parallelWidth / shardingEfficiency);
  const maxConcurrency = Math.max(0, Math.floor((usablePerDevice - fixedPerDevice) / kvPerBatchShard));
  const fits = topologyValid && remaining >= 0;
  const fit = byId('fit-result');
  fit.className = `fit-result ${fits ? 'ok' : 'no'}`;
  fit.textContent = !topologyValid ? `Parallel plan requests ${requestedParallel} devices but only ${devices} are available.` : fits ? `Fits on ${parallelWidth} device${parallelWidth === 1 ? '' : 's'} with ${displayGb(remaining)} headroom per device.` : `Does not fit: each device needs ${displayGb(Math.abs(remaining))} more usable memory.`;
  const fitChip = byId('fit-chip'); fitChip.className = `fit-chip ${fits ? 'ok' : 'no'}`; fitChip.textContent = fits ? 'Placement viable' : 'Capacity blocked';
  byId('planner-profile').textContent = `${devices} device${devices === 1 ? '' : 's'} / ${mode === 'lora' ? 'LoRA training' : mode === 'training' ? 'full training' : 'inference'}`;
  byId('weight-result').textContent = displayGb(weights); byId('kv-result').textContent = displayGb(kv); byId('training-result').textContent = displayGb(training); byId('overhead-result').textContent = displayGb(runtime);
  byId('total-result').textContent = displayGb(required); byId('per-device-result').textContent = displayGb(perDevice); byId('cluster-memory-result').textContent = displayGb(usableCluster); byId('remaining-result').textContent = fits ? displayGb(remaining) : `-${displayGb(Math.abs(remaining))}`;
  byId('flops-result').textContent = `${flops.toFixed(1)} GFLOPs`; byId('throughput-result').textContent = `${throughput.toFixed(1)} tok/s`; byId('max-batch-result').textContent = mode === 'inference' ? `${maxConcurrency} request${maxConcurrency === 1 ? '' : 's'}` : 'training mode'; byId('parallel-overhead-result').textContent = `${parallelOverhead.toFixed(1)}%`;
  const segments = [['memory-weights', weights], ['memory-kv', kv], ['memory-training', training], ['memory-runtime', runtime]];
  segments.forEach(([id, value]) => { byId(id).style.width = `${required ? value / required * 100 : 0}%`; });
  let placement = fits ? `${replicas > 1 ? `${replicas} replica groups; ` : ''}use TP=${tensorParallel}, PP=${pipelineParallel}. ${throughput < 1 ? 'Memory bandwidth is the likely bottleneck.' : 'Capacity is viable; benchmark the selected runtime before setting an SLO.'}` : 'Increase memory per device, increase compatible tensor/pipeline parallelism, reduce context or batch, or use quantization/offload.';
  if (mode !== 'inference') placement += ' Training memory includes estimated gradients, master weights, optimizer state, and activations; checkpoint and framework overhead can increase the real requirement.';
  byId('placement-result').textContent = placement;
}
byId('model-preset').addEventListener('change', event => { const preset = modelPresets[event.target.value]; if (preset) setPlannerValues({'total-params':preset.total,'active-params':preset.active,layers:preset.layers,'kv-heads':preset.kvHeads,'head-dim':preset.headDim}); });
byId('device-preset').addEventListener('change', event => { const preset = devicePresets[event.target.value]; if (preset) setPlannerValues({'available-memory':preset.memory,'memory-bandwidth':preset.bandwidth,'compute-tflops':preset.compute,'interconnect-bandwidth':preset.interconnect}); });
['workload-mode','total-params','active-params','weight-bits','kv-bits','layers','kv-heads','head-dim','context','batch','reserve','device-count','available-memory','memory-bandwidth','compute-tflops','interconnect-bandwidth','tensor-parallel','pipeline-parallel','utilization','cpu-offload'].forEach(id => byId(id).addEventListener('input', updatePlanner));
updatePlanner();
function renderIntegrations() { byId('agent-count').textContent = `${integrations.length} configured`; byId('integration-list').innerHTML = integrations.length ? integrations.map(item => `<div class="integration-row"><div><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.type)} / ${escapeHtml(item.target)}</p></div><div class="resource-actions">${tag(item.status === 'running' ? 'Running' : 'Configured')}<button class="danger-button" data-delete-integration="${escapeHtml(item.id)}">Remove</button></div></div>`).join('') : 'No integrations yet. Add the included OpenMycelium MCP server or an API-based agent.'; }
const integrationDialog = byId('integration-dialog');
byId('add-integration').addEventListener('click', () => integrationDialog.showModal());
byId('save-integration').addEventListener('click', async event => { if (!validSubmission(event)) return; try { await api('/api/v1/integrations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('integration-name').value.trim(),type:byId('integration-type').value,target:byId('integration-target').value.trim()})}); integrationDialog.close(); await syncResources(); } catch(error) { byId('integration-result').textContent=error.message; } });
const sshDialog=byId('ssh-dialog');const sshConfigDialog=byId('ssh-config-dialog');let activeSSHConnection=null;
function openSSHConfig(job){byId('ssh-config-title').textContent=`SSH access for ${job.name}`;byId('ssh-config-workload').value=job.id;byId('ssh-config-host').value=job.sshHost||'';byId('ssh-config-port').value=job.sshPort||22;byId('ssh-config-user').value=job.sshUser||'';byId('ssh-config-result').textContent='';sshConfigDialog.showModal();}
async function showSSHConnection(workloadID){const connection=await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/ssh`);activeSSHConnection=connection;byId('ssh-workload-name').textContent=`SSH to ${connection.name}`;byId('ssh-status').textContent=`${connection.user}@${connection.host}:${connection.port} / workload ${connection.status}`;byId('ssh-command').textContent=connection.command;sshDialog.showModal();}
document.addEventListener('click', async event => {
  const action=event.target.dataset.action;const workloadID=event.target.dataset.workloadId;
  try {
    if(action&&workloadID){
      if(action==='delete'){if(!window.confirm('Delete this workload record? This operation is permanent and recorded in the audit trail.'))return;await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}`,{method:'DELETE'});await syncWorkloads();await loadObservability();return;}
      if(action==='ssh'){const job=jobs.find(item=>item.id===workloadID);if(!job?.sshConfigured){openSSHConfig(job||{id:workloadID,name:'workload'});return;}await showSSHConnection(workloadID);return;}
      if(action!=='refresh')await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});await syncWorkloads();await loadObservability();return;
    }
    const resources=[['deleteCluster','clusters'],['deletePool','pools'],['deleteQueue','queues'],['deleteIntegration','integrations']];for(const [key,path] of resources){const id=event.target.dataset[key];if(id){if(!window.confirm('Remove this resource? This action is recorded in the audit trail.'))return;await api(`/api/v1/${path}/${encodeURIComponent(id)}`,{method:'DELETE'});await syncResources();await loadObservability();return;}}
    if(event.target.matches('[data-user-role]')){await api(`/api/v1/users/${encodeURIComponent(event.target.dataset.userRole)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:event.target.value})});await loadAccess();return;}
    if(event.target.matches('[data-user-active]')){await api(`/api/v1/users/${encodeURIComponent(event.target.dataset.userActive)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:event.target.dataset.active!=='true'})});await loadAccess();}
    if(event.target.matches('[data-delete-user]')){if(!window.confirm('Delete this user and all active sessions? This operation is permanent.'))return;await api(`/api/v1/users/${encodeURIComponent(event.target.dataset.deleteUser)}`,{method:'DELETE'});await loadAccess();await loadObservability();}
  } catch(error){window.alert(error.message);}
});
byId('copy-ssh-command').addEventListener('click',async()=>{await navigator.clipboard.writeText(byId('ssh-command').textContent);byId('copy-ssh-command').textContent='Copied';setTimeout(()=>byId('copy-ssh-command').textContent='Copy command',900);});
byId('edit-ssh').addEventListener('click',()=>{if(!activeSSHConnection)return;sshDialog.close();openSSHConfig({id:activeSSHConnection.workloadId,name:activeSSHConnection.name,sshHost:activeSSHConnection.host,sshPort:activeSSHConnection.port,sshUser:activeSSHConnection.user});});
byId('save-ssh-config').addEventListener('click',async event=>{if(!validSubmission(event))return;const workloadID=byId('ssh-config-workload').value;try{await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/ssh`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:byId('ssh-config-host').value.trim(),port:Number(byId('ssh-config-port').value),user:byId('ssh-config-user').value.trim()})});sshConfigDialog.close();await syncWorkloads();await showSSHConnection(workloadID);}catch(error){byId('ssh-config-result').textContent=error.message;}});
byId('refresh-workloads').addEventListener('click', async () => { await syncWorkloads(); byId('refresh-workloads').textContent='Refreshed'; setTimeout(()=>byId('refresh-workloads').textContent='Refresh',900); });
byId('chat-form').addEventListener('submit', event => { event.preventDefault(); const input = byId('chat-input'); const prompt = input.value.trim(); if (!prompt) return; const log = byId('chat-log'); log.insertAdjacentHTML('beforeend', `<div class="chat-message user">${escapeHtml(prompt)}</div>`); const response = /scan|discover/i.test(prompt) ? 'Open Discovery & install to run a real host inventory scan.' : /start|stop|redeploy|workload/i.test(prompt) ? 'Open Workloads to perform an audited lifecycle action.' : /model|ram|kv|moe/i.test(prompt) ? 'Open Model planner to calculate weights, KV cache, active MoE compute, and memory fit.' : 'I can help plan a workload or infrastructure action. Connected MCP and API agents appear in Agent hub.'; log.insertAdjacentHTML('beforeend', `<div class="chat-message assistant">${escapeHtml(response)}</div>`); input.value = ''; log.scrollTop = log.scrollHeight; });
async function scanOllama() {
  const button = byId('scan-ollama'); button.textContent = 'Discovering...';
  try {
    const response = await fetch('/api/v1/models'); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Ollama discovery failed');
    const models = data.models || []; const select = byId('ollama-model-select');
    byId('ollama-status').textContent = `${models.length} model${models.length === 1 ? '' : 's'} ready`; byId('ollama-status').className = 'tag green';
    byId('ollama-models').innerHTML = models.length ? models.map(model => `<div class="integration-row"><div><strong>${escapeHtml(model.name)}</strong><p>${(model.size / 1073741824).toFixed(1)} GB / local Ollama</p></div>${tag('Ready')}</div>`).join('') : 'Ollama is reachable but has no downloaded models.';
    select.innerHTML = models.length ? models.map(model => `<option value="${escapeHtml(model.name)}">${escapeHtml(model.name)}</option>`).join('') : '<option>No models found</option>'; select.disabled = !models.length; byId('deploy-ollama').disabled = !models.length;
  } catch (error) { byId('ollama-status').textContent = 'unavailable'; byId('ollama-status').className = 'tag amber'; byId('ollama-models').textContent = error.message; }
  finally { button.textContent = 'Discover Ollama models'; }
}
async function deployOllama() {
  const button = byId('deploy-ollama'); const model = byId('ollama-model-select').value; button.textContent = 'Deploying...';
  try { const response = await fetch('/api/v1/inference/deploy', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Deployment failed'); byId('ollama-deploy-result').textContent = `${model} is running as workload ${data.workload.id}. Generation endpoint: ${data.endpoint}`; syncWorkloads(); }
  catch (error) { byId('ollama-deploy-result').textContent = error.message; }
  finally { button.textContent = 'Deploy as workload'; }
}
byId('scan-ollama').addEventListener('click', scanOllama); byId('deploy-ollama').addEventListener('click', deployOllama);
byId('ollama-chat-form').addEventListener('submit', async event => {
  event.preventDefault(); const prompt = byId('ollama-prompt').value.trim(); const model = byId('ollama-model-select').value; const log = byId('ollama-chat-log');
  if (!prompt || byId('ollama-model-select').disabled) { log.insertAdjacentHTML('beforeend', '<div class="chat-message assistant">Discover a downloaded Ollama model first.</div>'); return; }
  log.insertAdjacentHTML('beforeend', `<div class="chat-message user">${escapeHtml(prompt)}</div>`); byId('ollama-prompt').value = ''; log.insertAdjacentHTML('beforeend', '<div class="chat-message assistant" id="ollama-thinking">Running local inference...</div>');
  try { const response = await fetch('/api/v1/inference/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model, prompt }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Generation failed'); byId('ollama-thinking').remove(); log.insertAdjacentHTML('beforeend', `<div class="chat-message assistant">${escapeHtml(data.response)}</div>`); syncWorkloads(); }
  catch (error) { byId('ollama-thinking').remove(); log.insertAdjacentHTML('beforeend', `<div class="chat-message assistant">${escapeHtml(error.message)}</div>`); }
  log.scrollTop = log.scrollHeight;
});
function serviceRow(label,value,status='Ready'){return `<div class="service-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(status)} / ${escapeHtml(value)}</strong></div>`;}
async function loadObservability(){try{const data=await api('/api/v1/observability/summary');byId('obs-running').textContent=data.workloads.running;byId('obs-workload-total').textContent=`${data.workloads.total} total / ${data.workloads.queued} queued / ${data.workloads.failed} failed`;byId('obs-clusters').textContent=data.inventory.clusters;byId('obs-accelerators').textContent=data.inventory.accelerators;byId('obs-users').textContent=data.governance.users;byId('obs-updated').textContent=new Date(data.timestamp).toLocaleTimeString();byId('service-health').innerHTML=serviceRow('API',data.services.api)+serviceRow('PostgreSQL',data.services.postgres?'connected':'not connected',data.services.postgres?'Ready':'Unavailable')+serviceRow('NATS',data.services.nats?'connected':'not connected',data.services.nats?'Ready':'Unavailable')+serviceRow('Ollama endpoint',data.services.ollama,'Configured');byId('workload-health').innerHTML=serviceRow('Running',data.workloads.running)+serviceRow('Queued',data.workloads.queued)+serviceRow('Failed',data.workloads.failed,data.workloads.failed===0?'Clear':'Attention')+serviceRow('Logical pools',data.inventory.pools);}catch(error){byId('service-health').textContent=error.message;}}
async function loadAccess(){if(sessionUser?.role!=='platform_admin')return;try{const [userData,auditData]=await Promise.all([api('/api/v1/users'),api('/api/v1/audit')]);const users=userData.users||[];byId('users-count').textContent=`${users.length} account${users.length===1?'':'s'}`;byId('users-list').innerHTML=users.length?users.map(user=>`<tr><td>${escapeHtml(user.email)}</td><td><select class="role-select" data-user-role="${escapeHtml(user.id)}" ${user.id===sessionUser.id?'disabled':''}><option value="platform_admin" ${user.role==='platform_admin'?'selected':''}>Platform admin</option><option value="operator" ${user.role==='operator'?'selected':''}>Operator</option><option value="viewer" ${user.role==='viewer'?'selected':''}>Viewer</option></select></td><td><button class="${user.active?'text-button':'danger-button'}" data-user-active="${escapeHtml(user.id)}" data-active="${user.active}" ${user.id===sessionUser.id?'disabled':''}>${user.active?'Active':'Disabled'}</button></td><td class="mono">${new Date(user.createdAt).toLocaleDateString()}</td><td><button class="danger-button" data-delete-user="${escapeHtml(user.id)}" ${user.id===sessionUser.id?'disabled':''}>Delete</button></td></tr>`).join(''):'<tr><td colspan="5">No users found.</td></tr>';const events=auditData.events||[];byId('audit-list').innerHTML=events.length?events.slice(0,30).map(event=>`<div class="audit-entry"><strong>${escapeHtml(event.subject)}</strong><span>${new Date(event.createdAt).toLocaleString()} / ${escapeHtml(event.payload?.actor||event.payload?.email||'system')}</span></div>`).join(''):'<div class="empty-inline">No audit events recorded.</div>';}catch(error){byId('audit-list').textContent=error.message;}}
const userDialog=byId('user-dialog');
byId('add-user').addEventListener('click',()=>{byId('user-result').textContent='';userDialog.showModal();});
byId('save-user').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:byId('new-user-email').value.trim(),password:byId('new-user-password').value,role:byId('new-user-role').value})});userDialog.close();byId('new-user-email').value='';byId('new-user-password').value='';byId('new-user-role').value='viewer';await loadAccess();await loadObservability();}catch(error){byId('user-result').textContent=error.message;}});
byId('refresh-observability').addEventListener('click',loadObservability);byId('refresh-access').addEventListener('click',loadAccess);
async function loadSettings() { try { const data=await api('/api/v1/settings'); byId('settings-organization').value=data.organization || ''; byId('settings-default-queue').value=data.defaultQueue || ''; byId('settings-oidc').value=data.oidcIssuer || ''; byId('settings-telemetry').value=data.telemetryEndpoint || ''; byId('settings-registration').checked=Boolean(data.registrationOpen); byId('settings-result').textContent='Settings loaded from the control plane.'; } catch(error) { byId('settings-result').textContent=error.message; } }
byId('save-settings').addEventListener('click', async () => { const settings={organization:byId('settings-organization').value.trim(),defaultQueue:byId('settings-default-queue').value.trim(),oidcIssuer:byId('settings-oidc').value,telemetryEndpoint:byId('settings-telemetry').value.trim(),registrationOpen:byId('settings-registration').checked}; try { const data=await api('/api/v1/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)}); byId('settings-result').textContent=`Saved for ${data.organization}.`; } catch(error) { byId('settings-result').textContent=error.message; } });
byId('logout').addEventListener('click', async () => { await fetch('/api/v1/auth/logout', { method: 'POST' }); window.location.replace('/login.html'); });
renderIntegrations();renderPools();renderJobs();renderClusters();renderQueues();
async function bootstrap(){try{await loadSession();await Promise.allSettled([loadControlHealth(),syncResources(),syncWorkloads(),loadObservability()]);if(sessionUser.role==='platform_admin')await Promise.allSettled([loadSettings(),loadAccess()]);}catch(error){console.error(error);}}
bootstrap();
