let pools = [];
let jobs = [];
let clusters = [];
let clusterNodes = [];
let queues = [];
let integrations = [];
let fabricPlans = [];
let fabricCapabilities = { devices: [], warnings: [] };
let modelCatalog = [];
let manifestPreviewKey = '';
let manifestWorkspaceID = '';
let workspaces = [];
let activeWorkspaceID = '';
let workspaceInventory = null;
let workspaceTab = 'overview';
let activeWorkspacePod = '';
let agentHubData = { agents:[], flows:[], runs:[], approvals:[], tools:[], memories:[], evaluations:[], traces:[], summary:{} };
let workspaceAgentData = null;
let operationsLinks = { prometheus:'http://127.0.0.1:9090', grafana:'http://127.0.0.1:3000' };
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
    const use = toggle.querySelector('use');
    if (use) use.setAttribute('href', theme === 'dark' ? '#icon-sun' : '#icon-moon');
  }
}
applyTheme(initialTheme);
const viewIcons = {
  overview:'dashboard', workspace:'layers', clusters:'server', discovery:'scan', pools:'cpu', workloads:'box', containers:'box', training:'training', inference:'radio', models:'database', mlops:'chart', planner:'calculator', fabric:'network', queues:'list', aiops:'activity', observability:'activity', agents:'bot', access:'users', settings:'settings'
};
function createInterfaceIcon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  svg.setAttribute('class', 'ui-icon');
  svg.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', `#icon-${name}`);
  svg.appendChild(use);
  return svg;
}
function initializeInterfaceIcons() {
  document.querySelectorAll('.nav-item[data-view]').forEach(button => {
    if (!button.querySelector('.ui-icon')) button.prepend(createInterfaceIcon(viewIcons[button.dataset.view] || 'dashboard'));
    const label = button.querySelector('span')?.textContent.trim();
    if (label) button.title = label;
  });
}
function setButtonLabel(button, label) {
  const target = button?.querySelector('span');
  if (target) target.textContent = label;
  else if (button) button.textContent = label;
}
initializeInterfaceIcons();
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
function tag(status) { const c = ['Running','Succeeded','Ready'].includes(status) ? 'green' : ['Queued','Pending','Degraded'].includes(status) ? 'amber' : status === 'Failed' ? 'red' : 'gray'; return `<span class="tag ${c}">${escapeHtml(status)}</span>`; }
function row(job) {
  const lifecycle = `<button data-workload-id="${escapeHtml(job.id)}" data-action="start">Start</button><button data-workload-id="${escapeHtml(job.id)}" data-action="stop">Stop</button><button data-workload-id="${escapeHtml(job.id)}" data-action="redeploy">Redeploy</button>`;
  const operations = job.kubernetes ? `<button class="ssh-button" data-workload-id="${escapeHtml(job.id)}" data-kube-operation="diagnostics">Inspect</button><button data-workload-id="${escapeHtml(job.id)}" data-kube-operation="logs">Logs</button><button data-workload-id="${escapeHtml(job.id)}" data-kube-operation="events">Events</button>${job.serviceName?`<button data-workload-id="${escapeHtml(job.id)}" data-kube-operation="service">Endpoint</button>`:''}<button data-workload-id="${escapeHtml(job.id)}" data-kube-operation="manifest">Manifest</button>` : '';
  const detail = job.lastError || job.statusMessage || job.statusReason;
  const error = detail ? `<span class="workload-error" title="${escapeHtml(detail)}">${escapeHtml(job.statusReason || (job.lastError ? 'Attention' : 'Detail'))}${job.restarts ? ` / ${job.restarts} restart${job.restarts === 1 ? '' : 's'}` : ''}</span>` : '';
  return `<tr><td><strong>${escapeHtml(job.name)}</strong>${job.cluster?`<small>${escapeHtml(job.cluster)}${job.namespace?` / ${escapeHtml(job.namespace)}`:''}</small>`:''}</td><td class="mono">${escapeHtml(job.type)}</td><td class="mono">${escapeHtml(job.pool)}</td><td>${escapeHtml(job.allocation)}</td><td class="mono">${escapeHtml(job.runtime)}</td><td>${tag(job.status)}${error}</td><td><div class="workload-controls">${lifecycle}${operations}<button data-workload-id="${escapeHtml(job.id)}" data-action="refresh">Refresh</button><button class="danger-button" data-workload-id="${escapeHtml(job.id)}" data-action="delete">Delete</button></div></td></tr>`;
}
function renderPools() {
  const content = pools.map(p => { const capacity=p.capacity||0;const available=p.available||0;const ratio=capacity?Math.max(0,Math.min(100,available/capacity*100)):(p.nodeCount?100:0);return `<div class="pool-row"><div><div class="pool-name">${escapeHtml(p.name)}</div><div class="pool-meta">${escapeHtml(p.vendor)} / ${escapeHtml(p.runtime)} / ${escapeHtml(p.sharingMode||'exclusive')} / ${p.nodeCount||0} nodes</div></div><div class="capacity"><i class="${ratio < 25 ? 'warn' : ''}" style="width:${ratio}%"></i></div><div class="pool-value">${available}/${capacity}</div></div>`;}).join('');
  byId('pool-list').innerHTML = content || '<div class="empty-inline">No accelerator pools configured. Create a policy after connecting real hardware.</div>';
  byId('pools-detail').innerHTML = pools.length ? pools.map(p => `<article class="panel"><p class="eyebrow">${escapeHtml(p.vendor)} / ${escapeHtml(p.runtime)}</p><h3>${escapeHtml(p.name)}</h3><div class="pool-capacity-grid"><span><b>${p.clusterCount||0}</b>clusters</span><span><b>${p.nodeCount||0}</b>nodes</span><span><b>${p.available||0}/${p.capacity||0}</b>available</span></div><div class="resource-meta"><span><b>Allocation</b>${escapeHtml((p.sharingMode||'exclusive')+(p.sliceProfile?` / ${p.sliceProfile}`:''))}</span><span><b>Resource</b>${escapeHtml(p.resourceName||'vendor default')}</span><span><b>Configured shares</b>${p.sharingReplicas||1} per device</span><span><b>Policy</b>${escapeHtml(p.policy)}</span><span><b>Selector</b>${escapeHtml(p.selector || 'none')}</span></div><div class="resource-actions">${tag((p.status||'unavailable').charAt(0).toUpperCase()+(p.status||'unavailable').slice(1))}<button class="danger-button" data-delete-pool="${escapeHtml(p.id)}">Remove</button></div></article>`).join('') : '<div class="empty-state"><strong>No pools configured.</strong><span>Create a logical placement policy for compatible hardware.</span></div>';
  const select = byId('job-pool'); if (select) select.innerHTML = '<option value="cpu-local">CPU / Kubernetes scheduler</option>' + pools.map(p => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} / ${escapeHtml(p.runtime)}</option>`).join('');
  refreshTopology();
}
function renderJobs() { const content = jobs.length ? jobs.map(row).join('') : '<tr><td colspan="7" class="mono">No workloads have been deployed yet.</td></tr>'; byId('workload-table').innerHTML = content; byId('workloads-all').innerHTML = content; byId('queued-jobs').textContent = jobs.filter(j => j.status === 'Queued').length; byId('workloads-count').textContent = jobs.length; byId('inference-count').textContent = jobs.filter(j => j.type === 'Inference').length; refreshTopology(); }
function modelRuntimeImage(runtime) { return ({ollama:'ollama/ollama:latest',vllm:'vllm/vllm-openai:latest',tgi:'ghcr.io/huggingface/text-generation-inference:latest',triton:'nvcr.io/nvidia/tritonserver:24.12-py3',bentoml:'bentoml/model-server:latest'})[runtime] || ''; }
function renderModelCatalog() {
  const versions = modelCatalog.flatMap(model => model.versions || []);
  byId('models-count').textContent = modelCatalog.length;
  byId('model-total').textContent = modelCatalog.length;
  byId('model-version-total').textContent = versions.length;
  byId('model-local-total').textContent = versions.filter(version => version.status === 'available' || version.sourceUri?.startsWith('ollama://')).length;
  byId('model-catalog-list').innerHTML = modelCatalog.length ? modelCatalog.map(model => `<article class="model-record"><header><div><p class="eyebrow">${escapeHtml(model.sourceType)} / ${escapeHtml(model.framework||'framework neutral')}</p><h3>${escapeHtml(model.name)}</h3><p>${escapeHtml(model.description||'No description supplied.')}</p></div><button class="danger-button" data-delete-model="${escapeHtml(model.id)}">Delete</button></header><div class="model-versions">${(model.versions||[]).map(version=>`<div class="model-version"><div><strong>${escapeHtml(version.version)}</strong><small>${escapeHtml(version.sourceUri)}</small></div><div><span>${escapeHtml(version.runtime)}</span><small>${escapeHtml(version.quantization||'standard precision')}</small></div><div><span>${version.sizeBytes?`${(version.sizeBytes/1073741824).toFixed(1)} GiB`:'size not reported'}</span><small>${version.parametersB?`${version.parametersB}B parameters`:escapeHtml(version.status)}</small></div>${tag(version.status==='available'?'Ready':'Registered')}</div>`).join('')}</div></article>`).join('') : '<div class="empty-state"><strong>No governed models.</strong><span>Register a model reference or synchronize the configured Ollama runtime.</span></div>';
  const select = byId('job-model-version');
  if (select) {
    const selected = select.value;
    select.innerHTML = '<option value="">No catalog model</option>' + modelCatalog.flatMap(model => (model.versions||[]).map(version => `<option value="${escapeHtml(version.id)}" data-runtime="${escapeHtml(version.runtime)}">${escapeHtml(model.name)} / ${escapeHtml(version.version)} / ${escapeHtml(version.runtime)}</option>`)).join('');
    select.value = versions.some(version => version.id === selected) ? selected : '';
  }
}
async function syncModelCatalog() { try { modelCatalog=(await api('/api/v1/model-catalog')).models||[];renderModelCatalog(); } catch(error) { byId('model-catalog-list').textContent=error.message; } }
async function syncWorkloads() {
  try {
    const remote = (await api('/api/v1/workloads')).workloads || [];
    jobs = remote.map(item => ({ id:item.id, name:item.name, type:item.kind === 'ollama-inference' ? 'Inference' : item.kind, pool:item.pool || 'unassigned', allocation:item.nodeName ? `${item.nodeName}${item.accelerators?` / ${item.accelerators} accelerator${item.accelerators===1?'':'s'}`:''}` : item.placement?.selectedNode ? `Preflight: ${item.placement.selectedNode}` : item.accelerators ? `${item.accelerators} accelerator${item.accelerators===1?'':'s'} requested` : 'Scheduler pending', runtime:item.kind === 'ollama-inference' ? 'Local Ollama' : item.runtime === 'kubernetes' ? `Kubernetes / ${item.schedulerBackend||'kubernetes'}${item.modelRuntime?` / ${item.modelRuntime}`:''}` : (item.image || 'Local'), status:(item.status||'unknown').charAt(0).toUpperCase() + (item.status||'unknown').slice(1), kubernetes:item.runtime==='kubernetes', cluster:item.clusterName||'', namespace:item.namespace||'', podName:item.podName||'', serviceName:item.serviceName||'', endpoint:item.endpoint||'', lastError:item.lastError||'', statusReason:item.statusReason||'', statusMessage:item.statusMessage||'', podPhase:item.podPhase||'', restarts:item.restarts||0, placement:item.placement||{}, sshHost:item.sshHost||'', sshPort:item.sshPort||22, sshUser:item.sshUser||'', sshConfigured:Boolean(item.sshHost&&item.sshUser) })); renderJobs();
  } catch (error) { byId('workloads-all').innerHTML = `<tr><td colspan="7">${escapeHtml(error.message)}</td></tr>`; }
}
function renderClusters() { byId('clusters-count').textContent = clusters.length; byId('cluster-list').innerHTML = clusters.length ? clusters.map(c => { const status=c.status==='ready'?'Running':c.status==='degraded'?'Degraded':'Pending';const updated=c.updatedAt?new Date(c.updatedAt).toLocaleString():'never';return `<article class="cluster-card"><header><div><h3>${escapeHtml(c.name)}</h3><p>${escapeHtml(c.type)}${c.version?` / ${escapeHtml(c.version)}`:''}</p></div>${tag(status)}</header><p>${escapeHtml(c.endpoint)}</p><div class="cluster-stats"><span><strong>${c.nodes}</strong><br />nodes</span><span><strong>${c.readyNodes||0}</strong><br />ready</span><span><strong>${c.accelerators}</strong><br />accelerators</span></div><p class="cluster-seen">${c.authenticated?'Authenticated API':'Inventory only'} / ${escapeHtml(c.namespace||'default')} / refreshed ${escapeHtml(updated)}</p><div class="resource-actions"><button data-refresh-cluster="${escapeHtml(c.id)}">Verify</button><button class="danger-button" data-delete-cluster="${escapeHtml(c.id)}">Remove</button></div></article>`;}).join('') : '<div class="empty-state"><strong>No clusters connected.</strong><span>Connect Kubernetes with a kubeconfig to deploy and operate workloads.</span></div>'; const select=byId('job-cluster');if(select)select.innerHTML='<option value="">Select a connected cluster</option>'+clusters.filter(c=>c.authenticated).map(c=>`<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)} / ${c.readyNodes||0} ready nodes</option>`).join('');refreshTopology(); }

function renderWorkspaceSelector() {
  byId('workspaces-count').textContent=workspaces.length;
  const selected=activeWorkspaceID;
  byId('workspace-select').innerHTML='<option value="">Select a workspace</option>'+workspaces.map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} / ${escapeHtml(item.clusterName)}</option>`).join('');
  activeWorkspaceID=workspaces.some(item=>item.id===selected)?selected:(workspaces[0]?.id||'');
  byId('workspace-select').value=activeWorkspaceID;
  const clusterSelect=byId('new-workspace-cluster');
  clusterSelect.innerHTML='<option value="">Select a connected cluster</option>'+clusters.filter(item=>item.authenticated).map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('');
  renderJobWorkspaces();
}

function renderJobWorkspaces() {
  const select=byId('job-workspace');
  if(!select)return;
  const selected=select.value;
  select.innerHTML='<option value="">No workspace / direct cluster</option>'+workspaces.map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} / ${escapeHtml(item.namespace)}</option>`).join('');
  select.value=workspaces.some(item=>item.id===selected)?selected:'';
}

function workspaceResourceTable(items,columns,empty) {
  if(!items?.length)return `<div class="empty-inline">${escapeHtml(empty)}</div>`;
  return `<div class="table-wrap"><table><thead><tr>${columns.map(column=>`<th>${escapeHtml(column.label)}</th>`).join('')}</tr></thead><tbody>${items.map(item=>`<tr>${columns.map(column=>`<td>${column.render(item)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

const agentWorkspaceTabs = new Set(['agents','flows','runs','tools','memory','approvals','evaluations','traces']);
function agentGraphPreview(flow) {
  const nodes=flow.graph?.nodes||[],edges=flow.graph?.edges||[];
  return `<div class="agent-graph-preview">${nodes.map(node=>`<span class="${escapeHtml(node.type)}" title="${escapeHtml(node.type)}">${escapeHtml(node.name||node.id)}</span>`).join('<i></i>')}</div><small>${nodes.length} nodes / ${edges.length} transitions / v${flow.version}</small>`;
}
function renderAgentHub() {
  const data=agentHubData,summary=data.summary||{};
  byId('agents-count').textContent=data.agents?.length||0;
  byId('agent-definition-count').textContent=data.agents?.length||0;
  byId('agent-flow-count').textContent=data.flows?.length||0;
  byId('agent-active-count').textContent=summary.running||0;
  byId('agent-approval-count').textContent=summary.waitingApproval||0;
  byId('agent-catalog-status').textContent=`${(data.agents||[]).filter(item=>item.status==='ready').length} ready`;
  byId('agent-flow-status').textContent=`${data.flows?.length||0} flows`;
  byId('agent-catalog-list').innerHTML=(data.agents||[]).length?(data.agents||[]).map(agent=>`<article class="agent-definition"><div><span class="agent-framework">${escapeHtml(agent.framework)}</span><h3>${escapeHtml(agent.name)}</h3><p>${escapeHtml(agent.description||'No description supplied.')}</p><small>${escapeHtml(agent.workspaceName||'Organization catalog')} / v${agent.version} / ${escapeHtml(agent.image)}</small></div><div class="agent-definition-actions">${tag(agent.status==='ready'?'Ready':agent.status)}<button data-agent-run="${escapeHtml(agent.id)}" data-agent-workspace="${escapeHtml(agent.workspaceId||'')}">Run</button><a href="/api/v1/agents/${encodeURIComponent(agent.id)}/a2a-card" target="_blank" rel="noopener">A2A</a><button class="danger-button" data-delete-agent="${escapeHtml(agent.id)}">Delete</button></div></article>`).join(''):'<div class="empty-inline">No agent definitions registered.</div>';
  byId('agent-flow-list').innerHTML=(data.flows||[]).length?(data.flows||[]).map(flow=>`<article class="agent-flow-record"><div><h3>${escapeHtml(flow.name)}</h3><p>${escapeHtml(flow.workspaceName)} / ${escapeHtml(flow.description||'Orchestration graph')}</p>${agentGraphPreview(flow)}</div><button class="danger-button" data-delete-agent-flow="${escapeHtml(flow.id)}">Delete</button></article>`).join(''):'<div class="empty-inline">No orchestration flows defined.</div>';
  byId('agent-run-list').innerHTML=(data.runs||[]).length?(data.runs||[]).slice(0,12).map(run=>`<article><div><strong>${escapeHtml(run.agentName)}</strong><small>${escapeHtml(run.workspaceName)} / ${new Date(run.createdAt).toLocaleString()}</small></div>${tag((run.status||'unknown').replaceAll('_',' '))}</article>`).join(''):'<div class="empty-inline">No agent runs recorded.</div>';
  populateAgentForms();
}
async function syncAgentOrchestration() {
  try{agentHubData=await api('/api/v1/agent-orchestration');renderAgentHub();}catch(error){byId('agent-catalog-list').innerHTML=`<div class="empty-inline">${escapeHtml(error.message)}</div>`;}
}
async function loadWorkspaceAgentData() {
  if(!activeWorkspaceID){workspaceAgentData=null;return;}
  try{workspaceAgentData=await api(`/api/v1/agent-orchestration?workspaceId=${encodeURIComponent(activeWorkspaceID)}`);}catch(error){workspaceAgentData={error:error.message,agents:[],flows:[],runs:[],tools:[],memories:[],approvals:[],evaluations:[],traces:[],summary:{}};}
}
function renderWorkspaceAgents() {
  const data=workspaceAgentData;
  if(!data){byId('workspace-content').innerHTML='<div class="empty-inline">Loading agent orchestration state...</div>';return;}
  if(data.error){byId('workspace-content').innerHTML=`<div class="empty-inline">${escapeHtml(data.error)}</div>`;return;}
  if(workspaceTab==='agents') {
    byId('workspace-content').innerHTML=`<div class="workspace-agent-toolbar"><div><p class="eyebrow">Runtime contracts</p><h3>Workspace agents</h3></div><button class="primary" data-create-workspace-agent>New agent</button></div>`+workspaceResourceTable(data.agents,[{label:'Agent',render:item=>`<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.framework)} / v${item.version}</small>`},{label:'Scope',render:item=>escapeHtml(item.workspaceName||'Organization')},{label:'Model',render:item=>escapeHtml(item.modelName||'runtime supplied')},{label:'Policy',render:item=>`${item.spec?.policy?.requireApproval?'Approval gated':'Automatic'}<small>${Number(item.spec?.policy?.tokenBudget||0).toLocaleString()} token budget</small>`},{label:'Status',render:item=>tag(item.status==='ready'?'Ready':item.status)},{label:'Controls',render:item=>`<div class="resource-actions"><button data-agent-run="${escapeHtml(item.id)}" data-agent-workspace="${escapeHtml(activeWorkspaceID)}">Run</button><a class="button-link" href="/api/v1/agents/${encodeURIComponent(item.id)}/a2a-card" target="_blank" rel="noopener">A2A Card</a></div>`}],'No agents are available in this workspace.');
  } else if(workspaceTab==='flows') {
    byId('workspace-content').innerHTML=`<div class="workspace-agent-toolbar"><div><p class="eyebrow">Durable graphs</p><h3>Agent flows</h3></div><button class="primary" data-create-agent-flow>New flow</button></div><div class="workspace-flow-grid">${(data.flows||[]).map(flow=>`<article><header><div><strong>${escapeHtml(flow.name)}</strong><small>${escapeHtml(flow.status)} / v${flow.version}</small></div>${tag(flow.status==='ready'?'Ready':flow.status)}</header>${agentGraphPreview(flow)}<p>${escapeHtml(flow.description||'No description supplied.')}</p></article>`).join('')||'<div class="empty-inline">No orchestration flows in this workspace.</div>'}</div>`;
  } else if(workspaceTab==='runs') {
    byId('workspace-content').innerHTML=`<div class="workspace-agent-toolbar"><div><p class="eyebrow">Durable execution</p><h3>Agent runs</h3></div><button class="primary" data-launch-workspace-agent>Launch run</button></div>`+workspaceResourceTable(data.runs,[{label:'Run',render:item=>`<strong>${escapeHtml(item.agentName)}</strong><small>${escapeHtml(item.id)}${item.flowName?` / ${escapeHtml(item.flowName)}`:''}</small>`},{label:'Current step',render:item=>escapeHtml(item.currentStep||'admission')},{label:'Budget',render:item=>`${Number(item.tokensUsed||0).toLocaleString()} / ${Number(item.tokenBudget||0).toLocaleString()}<small>$${Number(item.costUSD||0).toFixed(4)}</small>`},{label:'Started',render:item=>new Date(item.createdAt).toLocaleString()},{label:'Status',render:item=>tag((item.status||'unknown').replaceAll('_',' '))},{label:'Controls',render:item=>`<div class="resource-actions">${item.workloadId?`<button data-open-agent-workload="${escapeHtml(item.workloadId)}">Workload</button>`:''}<button data-show-agent-trace="${escapeHtml(item.id)}">Trace</button>${!['failed','cancelled','completed'].includes(item.status)?`<button class="danger-button" data-cancel-agent-run="${escapeHtml(item.id)}">Cancel</button>`:''}</div>`}],'No agent runs have been launched.');
  } else if(workspaceTab==='tools') {
    byId('workspace-content').innerHTML=`<div class="workspace-agent-toolbar"><div><p class="eyebrow">Capability gateway</p><h3>Tool bindings</h3></div><button class="primary" data-attach-agent-tool>Attach tool</button></div>`+workspaceResourceTable(data.tools,[{label:'Agent',render:item=>`<strong>${escapeHtml(item.agentName)}</strong>`},{label:'Integration',render:item=>escapeHtml(item.integrationName)},{label:'Permissions',render:item=>escapeHtml((item.permissions||[]).join(', ')||'none')},{label:'Approval',render:item=>tag(item.approved?'Ready':'Pending')},{label:'Created by',render:item=>escapeHtml(item.createdBy)}],'No MCP or API tools are bound to workspace agents.');
  } else if(workspaceTab==='memory') {
    byId('workspace-content').innerHTML=`<div class="workspace-agent-toolbar"><div><p class="eyebrow">State & checkpoints</p><h3>Memory profiles</h3></div><button class="primary" data-create-agent-memory>New profile</button></div>`+workspaceResourceTable(data.memories,[{label:'Profile',render:item=>`<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.mode)}</small>`},{label:'Provider',render:item=>escapeHtml(item.provider)},{label:'Retention',render:item=>`${item.retentionDays} days`},{label:'Checkpoints',render:item=>item.checkpointing?'Enabled':'Disabled'},{label:'Status',render:item=>tag(item.status==='ready'?'Ready':item.status)}],'No memory profiles are configured.');
  } else if(workspaceTab==='approvals') {
    byId('workspace-content').innerHTML=workspaceResourceTable(data.approvals,[{label:'Request',render:item=>`<strong>${escapeHtml(item.stepId||'admission')}</strong><small>${escapeHtml(item.runId)}</small>`},{label:'Reason',render:item=>escapeHtml(item.reason)},{label:'Requested',render:item=>`${escapeHtml(item.requestedBy)}<small>${new Date(item.createdAt).toLocaleString()}</small>`},{label:'Status',render:item=>tag(item.status==='approved'?'Ready':item.status==='pending'?'Pending':item.status)},{label:'Decision',render:item=>item.status==='pending'?`<div class="resource-actions"><button data-agent-approval="${escapeHtml(item.id)}" data-decision="approve">Approve</button><button class="danger-button" data-agent-approval="${escapeHtml(item.id)}" data-decision="reject">Reject</button></div>`:escapeHtml(item.resolvedBy||'resolved')}],'No human approval requests.');
  } else if(workspaceTab==='evaluations') {
    byId('workspace-content').innerHTML=`<div class="workspace-agent-toolbar"><div><p class="eyebrow">Quality & safety</p><h3>Agent evaluations</h3></div><button class="primary" data-record-agent-evaluation>Record evaluation</button></div>`+workspaceResourceTable(data.evaluations,[{label:'Agent',render:item=>`<strong>${escapeHtml(item.agentName)}</strong><small>${escapeHtml(item.runId||'definition evaluation')}</small>`},{label:'Suite',render:item=>escapeHtml(item.suite)},{label:'Score',render:item=>`${(Number(item.score||0)*100).toFixed(1)}%`},{label:'Metrics',render:item=>escapeHtml(Object.entries(item.metrics||{}).map(([key,value])=>`${key}: ${value}`).join(' / ')||'none')},{label:'Created',render:item=>new Date(item.createdAt).toLocaleString()}],'No evaluation results recorded.');
  } else {
    byId('workspace-content').innerHTML=workspaceResourceTable(data.traces,[{label:'Time',render:item=>new Date(item.createdAt).toLocaleString()},{label:'Run',render:item=>`<strong>${escapeHtml(item.runId)}</strong><small>${escapeHtml(item.stepId||'control plane')}</small>`},{label:'Event',render:item=>escapeHtml(item.eventType.replaceAll('_',' '))},{label:'Attributes',render:item=>`<code>${escapeHtml(JSON.stringify(item.payload||{}))}</code>`}],'No agent trace events recorded.');
  }
}

function renderWorkspaceContent() {
  const inventory=workspaceInventory;
  if(!inventory){byId('workspace-content').innerHTML='<div class="empty-inline">Loading workspace inventory...</div>';return;}
  const workspace=inventory.workspace;
  if(agentWorkspaceTabs.has(workspaceTab)){renderWorkspaceAgents();return;}
  if(workspaceTab==='overview') {
    const releases=(inventory.releases||[]).slice(0,6);
    byId('workspace-content').innerHTML=`<div class="workspace-overview-grid"><section><p class="eyebrow">Execution boundary</p><h3>${escapeHtml(workspace.clusterName)} / ${escapeHtml(workspace.namespace)}</h3><div class="workspace-detail-list"><span><b>Queue</b>${escapeHtml(workspace.queue||'default')}</span><span><b>StorageClass</b>${escapeHtml(workspace.storageClass||'cluster default')}</span><span><b>Network policy</b>${escapeHtml(workspace.networkPolicy)} / declared</span><span><b>Created by</b>${escapeHtml(workspace.createdBy||'system')}</span></div></section><section><p class="eyebrow">Recent releases</p>${releases.length?releases.map(release=>`<div class="workspace-release"><div><strong>${escapeHtml(release.name)}</strong><small>${escapeHtml(release.sourceType)} / ${new Date(release.createdAt).toLocaleString()}</small></div>${tag(release.status==='applied'?'Ready':release.status)}</div>`).join(''):'<div class="empty-inline">No Celium AI+ or manifest releases yet.</div>'}</section></div>`;
  } else if(workspaceTab==='deployments') {
    const controllers=inventory.controllers||[];
    byId('workspace-content').innerHTML=workspaceResourceTable(controllers,[{label:'Controller',render:item=>`<strong>${escapeHtml(item.kind)} / ${escapeHtml(item.name)}</strong>${item.releaseId?`<small>${escapeHtml(item.releaseId)}</small>`:''}`},{label:'Readiness',render:item=>`${item.ready}/${item.desired}`},{label:'Status',render:item=>tag(item.status)},{label:'Created',render:item=>new Date(item.createdAt).toLocaleString()}],'No deployments, jobs, or scheduled runs in this workspace.');
  } else if(workspaceTab==='pods') {
    byId('workspace-content').innerHTML=workspaceResourceTable(inventory.pods,[{label:'Pod',render:item=>`<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.node||'not scheduled')} / ${escapeHtml(item.podIp||'no IP')}</small>`},{label:'Containers',render:item=>`${item.ready}/${item.containers} ready<small>${escapeHtml((item.images||[]).join(', '))}</small>`},{label:'Restarts',render:item=>String(item.restarts||0)},{label:'Phase',render:item=>tag(item.phase)},{label:'Access',render:item=>`<div class="resource-actions"><button data-workspace-pod="${escapeHtml(item.name)}" data-workspace-pod-action="logs">Logs</button><button data-workspace-pod="${escapeHtml(item.name)}" data-workspace-pod-action="terminal">Terminal</button></div>`}],'No pods have been scheduled in this workspace.');
  } else if(workspaceTab==='services') {
    byId('workspace-content').innerHTML=workspaceResourceTable(inventory.services,[{label:'Service',render:item=>`<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.type)} / ${escapeHtml(item.clusterIp||'headless')}</small>`},{label:'Ports',render:item=>escapeHtml((item.ports||[]).map(port=>`${port.port}${port.nodePort?` to ${port.nodePort}`:''}/${port.protocol}`).join(', '))},{label:'External access',render:item=>item.externalUrl?`<a href="${escapeHtml(item.externalUrl)}" target="_blank" rel="noopener">${escapeHtml(item.externalUrl)}</a>`:'Authenticated workspace gateway'},{label:'Controls',render:item=>`<div class="resource-actions"><button data-open-workspace-service="${escapeHtml(item.name)}">Open application</button><button data-copy-workspace-service="${escapeHtml(item.name)}">Copy address</button></div>`}],'No Kubernetes Services expose applications in this workspace.');
  } else if(workspaceTab==='operations') {
    byId('workspace-content').innerHTML=`<div class="workspace-operation-list"><div><p class="eyebrow">Pod sessions</p><h3>Logs & audited commands</h3><p>Select a pod to read logs or run a bounded, non-interactive command through Kubernetes exec.</p></div>${(inventory.pods||[]).map(item=>`<article><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.phase)} / ${escapeHtml(item.node||'pending')}</small></div><div class="resource-actions"><button data-workspace-pod="${escapeHtml(item.name)}" data-workspace-pod-action="logs">Logs</button><button data-workspace-pod="${escapeHtml(item.name)}" data-workspace-pod-action="terminal">Terminal</button></div></article>`).join('')||'<div class="empty-inline">No pod sessions available.</div>'}</div>`;
  } else if(workspaceTab==='storage') {
    byId('workspace-content').innerHTML=workspaceResourceTable(inventory.storage,[{label:'Claim',render:item=>`<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.storageClass||'default StorageClass')}</small>`},{label:'Requested',render:item=>escapeHtml(item.requested)},{label:'Capacity',render:item=>escapeHtml(item.capacity)},{label:'Phase',render:item=>tag(item.phase)}],'No persistent volumes, model caches, or checkpoint claims.');
  } else if(workspaceTab==='configuration') {
    byId('workspace-content').innerHTML=`<div class="manifest-policy"><strong>Secret values are redacted</strong><p>Workspace inventory returns configuration names and Secret types only. Data is never sent to the browser.</p></div>`+workspaceResourceTable(inventory.configuration,[{label:'Kind',render:item=>escapeHtml(item.kind)},{label:'Name',render:item=>`<strong>${escapeHtml(item.name)}</strong>`},{label:'Type',render:item=>escapeHtml(item.type||'configuration')},{label:'Created',render:item=>new Date(item.createdAt).toLocaleString()}],'No ConfigMaps or Secrets in this workspace.');
  } else {
    byId('workspace-content').innerHTML=`<div class="workspace-policy-strip"><span><b>CPU quota</b>${escapeHtml(workspace.cpuQuota||'not declared')}</span><span><b>Memory quota</b>${workspace.memoryQuotaGB||0} GiB</span><span><b>Accelerator quota</b>${workspace.acceleratorQuota||0}</span><span><b>Policy profile</b>${escapeHtml(workspace.networkPolicy)} / declared</span></div>`+workspaceResourceTable(inventory.events,[{label:'Time',render:item=>item.timestamp?new Date(item.timestamp).toLocaleString():'unknown'},{label:'Object',render:item=>`<strong>${escapeHtml(item.object)}</strong><small>${escapeHtml(item.reason)}</small>`},{label:'Event',render:item=>escapeHtml(item.message)},{label:'Count',render:item=>String(item.count||1)}],'No Kubernetes events reported.');
  }
}

function renderWorkspace() {
  const workspace=workspaces.find(item=>item.id===activeWorkspaceID);
  byId('workspace-empty').hidden=Boolean(workspace);
  byId('workspace-shell').hidden=!workspace;
  if(!workspace)return;
  byId('workspace-organization').textContent=workspace.organization;
  byId('workspace-name').textContent=workspace.name;
  byId('workspace-target').textContent=`${workspace.clusterName} / ${workspace.namespace} / ${workspace.queue||'default'} queue`;
  const inventory=workspaceInventory;
  const controllers=inventory?.controllers||[],pods=inventory?.pods||[],services=inventory?.services||[],nodes=inventory?.nodes||[];
  byId('workspace-controller-count').textContent=controllers.length;
  byId('workspace-controller-health').textContent=`${controllers.filter(item=>['Ready','Succeeded'].includes(item.status)).length} healthy`;
  byId('workspace-pod-count').textContent=pods.length;
  byId('workspace-pod-health').textContent=`${pods.filter(item=>item.phase==='Running'||item.phase==='Succeeded').length} running or complete`;
  byId('workspace-service-count').textContent=services.length;
  byId('workspace-accelerator-count').textContent=nodes.reduce((total,node)=>total+(node.accelerators||[]).reduce((sum,item)=>sum+(item.available||0),0),0);
  byId('workspace-node-health').textContent=`${nodes.filter(item=>item.ready&&item.schedulable).length}/${nodes.length} nodes ready`;
  renderWorkspaceContent();
}

async function syncWorkspaces(loadInventory=true) {
  try {
    workspaces=(await api('/api/v1/workspaces')).workspaces||[];
    renderWorkspaceSelector();
    if(loadInventory&&activeWorkspaceID)await loadWorkspaceInventory();else renderWorkspace();
  } catch(error){byId('workspace-content').innerHTML=`<div class="empty-inline">${escapeHtml(error.message)}</div>`;}
}

async function loadWorkspaceInventory() {
  if(!activeWorkspaceID){workspaceInventory=null;renderWorkspace();return;}
  byId('workspace-content').innerHTML='<div class="empty-inline">Reconciling live Kubernetes inventory...</div>';
  try{const [inventoryResult,agentResult]=await Promise.allSettled([api(`/api/v1/workspaces/${encodeURIComponent(activeWorkspaceID)}/inventory`),api(`/api/v1/agent-orchestration?workspaceId=${encodeURIComponent(activeWorkspaceID)}`)]);if(inventoryResult.status==='rejected')throw inventoryResult.reason;workspaceInventory=inventoryResult.value;workspaceAgentData=agentResult.status==='fulfilled'?agentResult.value:{error:agentResult.reason?.message||'Agent orchestration unavailable',agents:[],flows:[],runs:[],tools:[],memories:[],approvals:[],evaluations:[],traces:[]};}catch(error){workspaceInventory=null;workspaceAgentData=null;byId('workspace-content').innerHTML=`<div class="empty-inline">${escapeHtml(error.message)}</div>`;return;}
  renderWorkspace();
}
function renderClusterNodes(){byId('cluster-node-count').textContent=`${clusterNodes.length} node${clusterNodes.length===1?'':'s'}`;byId('cluster-node-list').innerHTML=clusterNodes.length?clusterNodes.map(node=>{const accelerators=(node.accelerators||[]).map(a=>`${a.vendor} ${a.model}: ${a.available}/${a.capacity}`).join(' / ')||'CPU only';const availability=`${node.cpuAvailable} CPU / ${node.memoryAvailable} memory`;return `<article class="node-row"><div class="node-identity"><span class="node-health ${node.ready&&node.schedulable?'ready':'degraded'}"></span><div><strong>${escapeHtml(node.name)}</strong><small>${escapeHtml(node.clusterName)} / ${escapeHtml((node.roles||[]).join(', '))} / ${escapeHtml(node.internalIp||'no address')}</small></div></div><div><span>Available</span><strong>${escapeHtml(availability)}</strong><small>${node.runningPods||0} active pods</small></div><div><span>Accelerators</span><strong>${escapeHtml(accelerators)}</strong><small>${escapeHtml(node.architecture)} / ${escapeHtml(node.containerRuntime)}</small></div><div>${tag(node.ready&&node.schedulable?'Ready':'Degraded')}</div></article>`;}).join(''):'<div class="empty-inline">No persisted node inventory. Verify a connected cluster.</div>';}

function renderFabric() {
  const clusterSelect = byId('fabric-cluster');
  const selectedCluster = clusterSelect?.value || '';
  if (clusterSelect) {
    clusterSelect.innerHTML = '<option value="">Choose a verified cluster</option>' + clusters.filter(cluster => cluster.authenticated).map(cluster => `<option value="${escapeHtml(cluster.id)}">${escapeHtml(cluster.name)} / ${cluster.readyNodes || 0} ready nodes</option>`).join('');
    clusterSelect.value = clusters.some(cluster => cluster.id === selectedCluster) ? selectedCluster : (clusters.find(cluster => cluster.authenticated)?.id || '');
  }
  byId('fabric-count').textContent = fabricPlans.length;
  byId('fabric-device-count').textContent = `${fabricCapabilities.devices.length} devices`;
  byId('fabric-devices').innerHTML = fabricCapabilities.devices.length ? fabricCapabilities.devices.map(device => `<div class="fabric-device"><div><strong>${escapeHtml(device.vendor)} ${escapeHtml(device.model)}</strong><small>${escapeHtml(device.node)} / ${escapeHtml(device.runtime)} / ${escapeHtml(device.tier)}</small></div><span>${Number(device.availableGiB).toFixed(1)} GiB<small>${escapeHtml(device.memorySource)}</small></span></div>`).join('') : '<div class="empty-inline">No addressable devices with known memory are available.</div>';
  byId('fabric-plans').innerHTML = fabricPlans.length ? fabricPlans.map(plan => `<article class="fabric-plan"><header><div><strong>${escapeHtml(plan.name)}</strong><small>${escapeHtml(plan.logicalAddress)}</small></div>${tag(plan.status === 'ready' ? 'Ready' : plan.status)}</header><div class="fabric-plan-stats"><span><b>${Number(plan.tensorGiB).toFixed(2)} GiB</b>logical tensor</span><span><b>${plan.selectedDevices}</b>placements</span><span><b>${escapeHtml(plan.strategy)}</b>${escapeHtml(plan.consistency)}</span><span><b>${Number(plan.physicalFootprintGiB).toFixed(2)} GiB</b>physical footprint</span></div><div class="fabric-shards">${(plan.placements || []).map(placement => `<span title="${escapeHtml(placement.transferMode)}" style="--share:${Math.max(3, placement.lengthGiB / plan.physicalFootprintGiB * 100)}%"><b>${escapeHtml(placement.vendor)}</b>${Number(placement.offsetGiB).toFixed(1)}-${Number(placement.offsetGiB + placement.lengthGiB).toFixed(1)} GiB</span>`).join('')}</div><p>${escapeHtml((plan.warnings || []).join(' '))}</p><button class="danger-button" data-delete-fabric="${escapeHtml(plan.id)}">Delete plan</button></article>`).join('') : '<div class="empty-inline">No tensor plans compiled.</div>';
  const workloadPlan = byId('job-fabric-plan');
  if (workloadPlan) {
    const current = workloadPlan.value;
    const target = byId('job-cluster')?.value || '';
    workloadPlan.innerHTML = '<option value="">No memory-fabric plan</option>' + fabricPlans.filter(plan => !target || plan.clusterId === target).map(plan => `<option value="${escapeHtml(plan.id)}">${escapeHtml(plan.name)} / ${escapeHtml(plan.strategy)} / ${Number(plan.tensorGiB).toFixed(1)} GiB</option>`).join('');
    workloadPlan.value = fabricPlans.some(plan => plan.id === current) ? current : '';
  }
}

async function loadFabricCapabilities() {
  const clusterID = byId('fabric-cluster')?.value || '';
  if (!clusterID) { fabricCapabilities = { devices: [], warnings: [] }; renderFabric(); return; }
  const memory = Number(byId('fabric-device-memory')?.value || 0);
  const host = Boolean(byId('fabric-host-memory')?.checked);
  fabricCapabilities = await api(`/api/v1/fabric/capabilities?clusterId=${encodeURIComponent(clusterID)}&defaultDeviceMemoryGiB=${encodeURIComponent(memory)}&includeHostMemory=${host}`);
  renderFabric();
}

async function syncFabric() {
  try {
    fabricPlans = (await api('/api/v1/fabric/plans')).plans || [];
    renderFabric();
    await loadFabricCapabilities();
  } catch (error) {
    byId('fabric-result').textContent = error.message;
  }
}
function renderQueues() { byId('queue-list').innerHTML = queues.length ? queues.map(q => `<article class="panel"><p class="eyebrow">Priority ${q.priority}</p><h3>${escapeHtml(q.name)}</h3><div class="resource-meta"><span><b>Accelerators</b>${q.acceleratorQuota}</span><span><b>Memory</b>${q.memoryQuotaGB} GB</span><span><b>Preemption</b>${q.preemptionEnabled ? 'enabled' : 'disabled'}</span></div><div class="resource-actions"><button class="danger-button" data-delete-queue="${escapeHtml(q.id)}">Remove</button></div></article>`).join('') : '<div class="empty-state"><strong>No queues configured.</strong><span>Create a queue to declare priority and capacity policy.</span></div>'; }
async function syncResources() { const [clusterData,poolData,queueData,integrationData]=await Promise.all([api('/api/v1/clusters'),api('/api/v1/pools'),api('/api/v1/queues'),api('/api/v1/integrations')]);clusters=clusterData.clusters||[];pools=poolData.pools||[];queues=queueData.queues||[];integrations=integrationData.integrations||[];const nodeResults=await Promise.allSettled(clusters.map(cluster=>api(`/api/v1/clusters/${encodeURIComponent(cluster.id)}/nodes`)));clusterNodes=nodeResults.flatMap(result=>result.status==='fulfilled'?(result.value.nodes||[]):[]);renderClusters();renderClusterNodes();renderPools();renderQueues();renderIntegrations(); }
function renderManifestClusters() { const select=byId('manifest-cluster');if(!select)return;const selected=select.value;select.innerHTML='<option value="">Select a connected cluster</option>'+clusters.filter(cluster=>cluster.authenticated).map(cluster=>`<option value="${escapeHtml(cluster.id)}">${escapeHtml(cluster.name)} / ${cluster.readyNodes||0} ready nodes</option>`).join('');select.value=clusters.some(cluster=>cluster.id===selected)?selected:''; }
const manifestExample = `apiVersion: apps/v1
kind: Deployment
metadata:
  name: internet-test
spec:
  replicas: 1
  selector:
    matchLabels:
      app: internet-test
  template:
    metadata:
      labels:
        app: internet-test
    spec:
      containers:
        - name: web
          image: nginx:alpine
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: 100m
              memory: 64Mi
---
apiVersion: v1
kind: Service
metadata:
  name: internet-test
spec:
  type: NodePort
  selector:
    app: internet-test
  ports:
    - port: 80
      targetPort: 80`;
function currentManifestKey(){return JSON.stringify({workspaceId:manifestWorkspaceID,clusterId:byId('manifest-cluster').value,namespace:byId('manifest-namespace').value.trim(),manifest:byId('manifest-yaml').value});}
function renderManifestResult(data){const resources=data.resources||[];byId('manifest-count').textContent=`${resources.length} resource${resources.length===1?'':'s'}`;byId('manifest-resources').innerHTML=resources.length?resources.map(item=>`<article class="manifest-resource"><div><strong>${escapeHtml(item.kind)} / ${escapeHtml(item.name)}</strong><small>${escapeHtml(item.namespace)} / ${escapeHtml(item.apiVersion)}</small></div>${item.images?.length?`<code>${escapeHtml(item.images.join(', '))}</code>`:'<code>configuration resource</code>'}${tag(item.dryRun==='applied'?'Running':'Ready')}</article>`).join(''):'<div class="empty-inline">No resources returned.</div>';byId('manifest-result').textContent=`${data.action==='apply'?'Applied':'Dry-run accepted'} ${resources.length} resource${resources.length===1?'':'s'} on ${data.clusterName} / ${data.namespace}.`;}
function renderManagedManifestResources(data){const resources=data.resources||[];byId('managed-resource-count').textContent=`${resources.length} deployed`;byId('managed-resource-count').className=`tag ${resources.length?'green':'gray'}`;byId('managed-manifest-resources').innerHTML=resources.length?resources.map(item=>`<article class="managed-manifest-resource"><div><p class="eyebrow">${escapeHtml(item.apiVersion)}</p><strong>${escapeHtml(item.kind)} / ${escapeHtml(item.name)}</strong><small>${escapeHtml(item.namespace)} / created ${new Date(item.createdAt).toLocaleString()}</small></div><div><span>${item.desired?`${item.ready}/${item.desired} ready`:'Managed resource'}</span><small>${item.images?.length?escapeHtml(item.images.join(', ')):'No container image'}</small></div>${tag(item.status||'Applied')}</article>`).join(''):'<div class="empty-inline">No OpenMycelium-managed YAML resources are present in this namespace.</div>';}
async function loadManagedManifestResources(){const clusterId=byId('manifest-cluster').value;if(!clusterId){byId('managed-resource-count').textContent='Select a cluster';byId('managed-resource-count').className='tag gray';byId('managed-manifest-resources').innerHTML='<div class="empty-inline">Select a cluster to load deployed resources.</div>';return;}byId('managed-resource-count').textContent='Loading';try{const query=new URLSearchParams({clusterId,namespace:byId('manifest-namespace').value.trim()});renderManagedManifestResources(await api(`/api/v1/manifests/resources?${query}`));}catch(error){byId('managed-resource-count').textContent='Unavailable';byId('managed-resource-count').className='tag amber';byId('managed-manifest-resources').innerHTML=`<div class="empty-inline">${escapeHtml(error.message)}</div>`;}}
async function runManifest(action){const clusterId=byId('manifest-cluster').value;const manifest=byId('manifest-yaml').value;if((!clusterId&&!manifestWorkspaceID)||!manifest.trim()){byId('manifest-result').textContent='Select a workspace or connected cluster and paste Kubernetes YAML.';return;}const button=byId(action==='preview'?'preview-manifest':'apply-manifest');button.disabled=true;const original=button.textContent;button.textContent=action==='preview'?'Validating...':'Applying...';try{const data=await api(`/api/v1/manifests/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:manifestWorkspaceID,clusterId,namespace:byId('manifest-namespace').value.trim(),manifest})});renderManifestResult(data);if(action==='preview'){manifestPreviewKey=currentManifestKey();byId('apply-manifest').disabled=false;}else{manifestPreviewKey='';byId('apply-manifest').disabled=true;await Promise.allSettled([syncResources(),syncWorkloads(),loadObservability(),loadManagedManifestResources(),syncWorkspaces(false)]);if(activeWorkspaceID)await loadWorkspaceInventory();}}catch(error){byId('manifest-result').textContent=error.message;manifestPreviewKey='';byId('apply-manifest').disabled=true;}finally{if(action==='preview')button.disabled=false;button.textContent=original;}}
const shell = document.querySelector('.shell');
const sidebar = document.querySelector('.sidebar');
const navigationBreakpoint = window.matchMedia('(max-width: 950px)');
function syncNavigationState() {
  const compactViewport = navigationBreakpoint.matches;
  if (compactViewport) shell.classList.remove('sidebar-collapsed');
  else shell.classList.toggle('sidebar-collapsed', localStorage.getItem('openmycelium-sidebar') === 'collapsed');
  const expanded = compactViewport ? sidebar.classList.contains('open') : !shell.classList.contains('sidebar-collapsed');
  const navigationIcon = byId('mobile-menu').querySelector('use');
  if (navigationIcon) navigationIcon.setAttribute('href', compactViewport ? '#icon-menu' : '#icon-panel-left');
  byId('mobile-menu').setAttribute('aria-expanded', String(expanded));
  byId('mobile-menu').setAttribute('aria-label', compactViewport ? (expanded ? 'Close navigation' : 'Open navigation') : (expanded ? 'Collapse navigation' : 'Expand navigation'));
  byId('mobile-menu').title = byId('mobile-menu').getAttribute('aria-label');
}
function closeNavigationDrawer() {
  sidebar.classList.remove('open');
  shell.classList.remove('nav-open');
  syncNavigationState();
}
function toggleNavigation() {
  if (navigationBreakpoint.matches) {
    const open = !sidebar.classList.contains('open');
    sidebar.classList.toggle('open', open);
    shell.classList.toggle('nav-open', open);
  } else {
    const collapsed = !shell.classList.contains('sidebar-collapsed');
    shell.classList.toggle('sidebar-collapsed', collapsed);
    localStorage.setItem('openmycelium-sidebar', collapsed ? 'collapsed' : 'expanded');
  }
  syncNavigationState();
  requestAnimationFrame(refreshTopology);
}
function setView(id) { document.querySelectorAll('.view').forEach(v => v.classList.toggle('visible', v.id === id || (id === 'inference' && v.id === 'ollama-console'))); document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === id)); const label = document.querySelector(`.nav-item[data-view="${id}"] span`)?.textContent.trim() || 'Control plane'; byId('view-label').textContent = label; byId('page-title').textContent = id === 'overview' ? 'Infrastructure overview' : label; if (navigationBreakpoint.matches) closeNavigationDrawer(); }
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelector('[data-view="containers"]').addEventListener('click',()=>{manifestWorkspaceID='';renderManifestClusters();loadManagedManifestResources();});
document.querySelector('[data-view="models"]').addEventListener('click',syncModelCatalog);
document.querySelector('[data-view="mlops"]').addEventListener('click',loadMLOps);
document.querySelector('[data-view="aiops"]').addEventListener('click',loadAIOps);
byId('load-manifest-example').addEventListener('click',()=>{byId('manifest-yaml').value=manifestExample;manifestPreviewKey='';byId('apply-manifest').disabled=true;byId('manifest-result').textContent='Example loaded. Select a cluster, then preview it.';});
byId('preview-manifest').addEventListener('click',()=>runManifest('preview'));
byId('apply-manifest').addEventListener('click',()=>{if(currentManifestKey()!==manifestPreviewKey){byId('manifest-result').textContent='The target or YAML changed. Preview it again before applying.';byId('apply-manifest').disabled=true;return;}runManifest('apply');});
['manifest-cluster','manifest-namespace','manifest-yaml'].forEach(id=>byId(id).addEventListener('input',()=>{if(id!=='manifest-yaml')manifestWorkspaceID='';if(currentManifestKey()!==manifestPreviewKey)byId('apply-manifest').disabled=true;}));
byId('manifest-cluster').addEventListener('change',loadManagedManifestResources);
byId('manifest-namespace').addEventListener('change',loadManagedManifestResources);
byId('refresh-managed-resources').addEventListener('click',loadManagedManifestResources);
byId('mobile-menu').addEventListener('click', toggleNavigation);
byId('nav-scrim').addEventListener('click', closeNavigationDrawer);
navigationBreakpoint.addEventListener('change', () => { closeNavigationDrawer(); syncNavigationState(); });
syncNavigationState();
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
byId('job-cluster').closest('label').insertAdjacentHTML('beforebegin','<label>Workspace <span>recommended</span><select id="job-workspace"><option value="">No workspace / direct cluster</option></select></label>');
byId('job-image').closest('label').insertAdjacentHTML('afterend','<div class="form-grid"><label>Private registry secret <span>optional</span><input id="job-image-pull-secret" placeholder="registry-credentials" /></label><label>Catalog model <span>optional</span><select id="job-model-version"><option value="">No catalog model</option></select></label></div>');
byId('job-pool').closest('label').insertAdjacentHTML('afterend', '<label>Hypha tensor plan<select id="job-fabric-plan"><option value="">No memory-fabric plan</option></select></label>');
byId('job-storage').closest('fieldset').insertAdjacentHTML('afterend','<fieldset><legend>Distributed scheduling</legend><div class="form-grid"><label>Scheduler backend<select id="job-scheduler"><option value="kubernetes">Kubernetes scheduler</option><option value="kueue">Kueue admission</option><option value="volcano">Volcano gang scheduler</option></select></label><label>Queue / LocalQueue<input id="job-queue" placeholder="research" /></label><label>Gang minimum members<input id="job-gang-min" type="number" min="0" max="128" value="0" /></label><label>PriorityClass <span>optional</span><input id="job-priority-class" placeholder="high-priority" /></label><label>Topology placement<select id="job-topology-mode"><option value="none">Scheduler default</option><option value="compact">Compact for low latency</option><option value="spread">Spread for resilience</option></select></label><label>Topology key<input id="job-topology-key" value="kubernetes.io/hostname" /></label><label>Network fabric<select id="job-network-mode"><option value="standard">Standard Ethernet</option><option value="rdma">RDMA / RoCE</option><option value="infiniband">InfiniBand</option></select></label></div><p class="input-note">Gang mode validates aggregate capacity before release. Kueue and Volcano must already be installed in the target cluster. RDMA modes configure transport variables; use an RDMA-capable pool selector to constrain placement.</p></fieldset>');
function updateDistributedSchedulingForm(){const batchKind=['training','finetuning','batch'].includes(byId('job-type').value);const backend=byId('job-scheduler').value;byId('job-scheduler').querySelector('option[value="kueue"]').disabled=!batchKind;byId('job-gang-min').disabled=!batchKind;if(!batchKind){byId('job-scheduler').value='kubernetes';byId('job-gang-min').value=0;}else if(backend!=='kubernetes'&&Number(byId('job-replicas').value)>1&&Number(byId('job-gang-min').value)===0){byId('job-gang-min').value=byId('job-replicas').value;}}
function applyWorkspaceTarget(workspaceID) {
  const workspace=workspaces.find(item=>item.id===workspaceID);
  byId('job-cluster').disabled=Boolean(workspace);
  byId('job-namespace').disabled=Boolean(workspace);
  if(workspace){byId('job-cluster').value=workspace.clusterId;byId('job-namespace').value=workspace.namespace;byId('job-queue').value=workspace.queue||'';}
  byId('job-result').textContent=workspace?`This run will be released into ${workspace.name} on ${workspace.clusterName} / ${workspace.namespace}.`:'Celium AI+ validates capacity before creating Kubernetes resources.';
  renderFabric();
}
function openCelium(workspaceID='') {
  renderJobWorkspaces();
  byId('job-workspace').value=workspaces.some(item=>item.id===workspaceID)?workspaceID:'';
  applyWorkspaceTarget(byId('job-workspace').value);
  dialog.showModal();
}
['open-submit','workload-submit','training-submit','deploy-endpoint','deploy-container','mlops-launch'].forEach(id => byId(id)?.addEventListener('click', () => openCelium('')));
['workspace-celium','workspace-run-ai'].forEach(id=>byId(id)?.addEventListener('click',()=>openCelium(activeWorkspaceID)));
byId('job-workspace').addEventListener('change',event=>applyWorkspaceTarget(event.target.value));
byId('job-type').addEventListener('change',updateDistributedSchedulingForm);byId('job-scheduler').addEventListener('change',updateDistributedSchedulingForm);byId('job-replicas').addEventListener('input',updateDistributedSchedulingForm);updateDistributedSchedulingForm();
byId('submit-job').addEventListener('click', async event => { if (!validSubmission(event)) return;const button=event.currentTarget;const selectedWorkspace=byId('job-workspace').value;button.disabled=true;button.textContent='Launching...';try { await api('/api/v1/workloads',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('job-name').value.trim(),kind:byId('job-type').value,runtime:'kubernetes',workspaceId:selectedWorkspace,clusterId:byId('job-cluster').value,namespace:byId('job-namespace').value.trim(),pool:byId('job-pool').value,fabricPlanId:byId('job-fabric-plan').value,image:byId('job-image').value.trim(),imagePullSecret:byId('job-image-pull-secret').value.trim(),model:byId('job-model').value.trim(),modelVersionId:byId('job-model-version').value,command:byId('job-command').value.trim(),accelerators:Number(byId('job-gpus').value),cpu:byId('job-cpu').value.trim(),memory:byId('job-memory').value.trim(),replicas:Number(byId('job-replicas').value),storageGB:Number(byId('job-storage').value),schedulerBackend:byId('job-scheduler').value,queueName:byId('job-queue').value.trim(),gangMinAvailable:Number(byId('job-gang-min').value),priorityClass:byId('job-priority-class').value.trim(),topologyMode:byId('job-topology-mode').value,topologyKey:byId('job-topology-key').value.trim(),networkMode:byId('job-network-mode').value,serviceType:byId('job-service-type').value,port:Number(byId('job-port').value)})}); dialog.close(); await Promise.allSettled([syncWorkloads(),loadObservability(),syncWorkspaces(false)]);if(selectedWorkspace){activeWorkspaceID=selectedWorkspace;byId('workspace-select').value=selectedWorkspace;await loadWorkspaceInventory();setView('workspace');}else setView('workloads'); } catch(error) { byId('job-result').textContent=error.message; } finally {button.disabled=false;button.textContent='Launch run';} });
byId('job-topology-mode').addEventListener('change',event=>{if(event.target.value==='spread'&&byId('job-topology-key').value==='kubernetes.io/hostname')byId('job-topology-key').value='topology.kubernetes.io/zone';if(event.target.value==='compact'&&byId('job-topology-key').value==='topology.kubernetes.io/zone')byId('job-topology-key').value='kubernetes.io/hostname';});
byId('job-model-version').addEventListener('change',event=>{const option=event.target.selectedOptions[0];if(!option?.value)return;const runtime=option.dataset.runtime||'custom';const image=modelRuntimeImage(runtime);if(image)byId('job-image').value=image;byId('job-type').value='inference';byId('job-port').value=runtime==='ollama'?11434:runtime==='tgi'?80:8000;byId('job-model').value='';});
byId('refresh').addEventListener('click', async () => { await Promise.allSettled([loadControlHealth(),syncResources(),syncWorkloads(),loadObservability(),syncFabric(),syncModelCatalog(),loadMLOps(),loadAIOps(),syncAgentOrchestration()]);await syncWorkspaces();renderManifestClusters(); setButtonLabel(byId('refresh'), 'Refreshed'); setTimeout(() => setButtonLabel(byId('refresh'), 'Refresh'), 900); });
const clusterDialog=byId('cluster-dialog');const poolDialog=byId('pool-dialog');const queueDialog=byId('queue-dialog');const workspaceDialog=byId('workspace-dialog');const workspacePodDialog=byId('workspace-pod-dialog');
byId('add-cluster').addEventListener('click',()=>clusterDialog.showModal());byId('create-pool').addEventListener('click',()=>poolDialog.showModal());byId('new-queue').addEventListener('click',()=>queueDialog.showModal());
byId('pool-sharing-mode').addEventListener('change',event=>{const examples={exclusive:'nvidia.com/gpu',mig:'nvidia.com/mig-1g.10gb','time-slicing':'nvidia.com/gpu.shared',mps:'nvidia.com/gpu.shared','device-plugin':'vendor.example.com/gpu-slice'};byId('pool-resource-name').placeholder=examples[event.target.value]||'';byId('pool-result').textContent=event.target.value==='mig'?'MIG slices provide hardware memory and fault isolation. The profile must already be configured by NVIDIA MIG Manager.':event.target.value==='exclusive'?'Leave the resource blank to use the vendor default, or enter an exact device-plugin resource.':'Shared resources must already be advertised by the cluster device plugin. Shares do not add physical GPU memory.';});
byId('new-workspace').addEventListener('click',()=>{renderWorkspaceSelector();byId('workspace-create-result').textContent='The workspace maps to one cluster namespace. Policies and credentials remain namespace-scoped.';workspaceDialog.showModal();});
byId('save-workspace').addEventListener('click',async event=>{if(!validSubmission(event))return;const button=event.currentTarget;button.disabled=true;button.textContent='Creating...';try{const workspace=await api('/api/v1/workspaces',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('new-workspace-name').value.trim(),clusterId:byId('new-workspace-cluster').value,namespace:byId('new-workspace-namespace').value.trim(),queue:byId('new-workspace-queue').value.trim(),storageClass:byId('new-workspace-storage').value.trim(),networkPolicy:byId('new-workspace-policy').value,cpuQuota:byId('new-workspace-cpu').value.trim(),memoryQuotaGB:Number(byId('new-workspace-memory').value),acceleratorQuota:Number(byId('new-workspace-accelerators').value)})});workspaceDialog.close();activeWorkspaceID=workspace.id;await syncWorkspaces();setView('workspace');}catch(error){byId('workspace-create-result').textContent=error.message;}finally{button.disabled=false;button.textContent='Create workspace';}});
byId('workspace-select').addEventListener('change',async event=>{activeWorkspaceID=event.target.value;workspaceInventory=null;workspaceAgentData=null;renderWorkspace();if(activeWorkspaceID)await loadWorkspaceInventory();});
byId('refresh-workspace').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;button.textContent='Reconciling...';try{await syncWorkspaces();}finally{button.disabled=false;button.textContent='Refresh';}});
document.querySelectorAll('[data-workspace-tab]').forEach(button=>button.addEventListener('click',async()=>{workspaceTab=button.dataset.workspaceTab;document.querySelectorAll('[data-workspace-tab]').forEach(item=>item.classList.toggle('active',item===button));if(agentWorkspaceTabs.has(workspaceTab)&&!workspaceAgentData)await loadWorkspaceAgentData();renderWorkspaceContent();}));
byId('workspace-deploy-container').addEventListener('click',()=>{openCelium(activeWorkspaceID);byId('job-type').value='interactive';byId('job-model').value='';byId('job-image').focus();});
byId('workspace-deploy-yaml').addEventListener('click',()=>{const workspace=workspaces.find(item=>item.id===activeWorkspaceID);if(!workspace)return;manifestWorkspaceID=workspace.id;renderManifestClusters();byId('manifest-cluster').value=workspace.clusterId;byId('manifest-namespace').value=workspace.namespace;manifestPreviewKey='';byId('apply-manifest').disabled=true;byId('manifest-result').textContent=`YAML will be registered as a release in ${workspace.name}. Preview it before applying.`;setView('containers');});

async function loadWorkspacePodLogs(){if(!activeWorkspaceID||!activeWorkspacePod)return;const container=byId('workspace-pod-container').value.trim();byId('workspace-pod-note').textContent='Loading the latest 500 log lines from Kubernetes...';try{const query=new URLSearchParams({tail:'500'});if(container)query.set('container',container);const data=await api(`/api/v1/workspaces/${encodeURIComponent(activeWorkspaceID)}/pods/${encodeURIComponent(activeWorkspacePod)}/logs?${query}`);byId('workspace-pod-output').textContent=data.logs||'No log output.';byId('workspace-pod-note').textContent='Logs loaded directly from the selected workspace pod.';}catch(error){byId('workspace-pod-output').textContent=error.message;byId('workspace-pod-note').textContent='Kubernetes could not return logs for this pod.';}}
async function runWorkspacePodCommand(){if(!activeWorkspaceID||!activeWorkspacePod)return;const command=byId('workspace-pod-command').value.trim();if(!command){byId('workspace-pod-note').textContent='Enter a non-interactive command first.';return;}const button=byId('workspace-run-command');button.disabled=true;button.textContent='Running...';try{const data=await api(`/api/v1/workspaces/${encodeURIComponent(activeWorkspaceID)}/pods/${encodeURIComponent(activeWorkspacePod)}/exec`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({container:byId('workspace-pod-container').value.trim(),command})});byId('workspace-pod-output').textContent=[data.stdout,data.stderr].filter(Boolean).join('\n')||'Command completed without output.';byId('workspace-pod-note').textContent=`Command ${data.succeeded?'completed':'returned an error'}; its digest was recorded in the audit trail.`;}catch(error){byId('workspace-pod-output').textContent=error.message;byId('workspace-pod-note').textContent='The audited command session failed.';}finally{button.disabled=false;button.textContent='Run command';}}
document.addEventListener('click',event=>{const pod=event.target.dataset.workspacePod;if(pod){activeWorkspacePod=pod;byId('workspace-pod-title').textContent=pod;byId('workspace-pod-output').textContent='Choose logs or enter a command.';byId('workspace-pod-note').textContent='Commands are non-interactive Kubernetes exec sessions and are represented by digest in the audit trail.';workspacePodDialog.showModal();if(event.target.dataset.workspacePodAction==='logs')loadWorkspacePodLogs();return;}const serviceName=event.target.dataset.openWorkspaceService||event.target.dataset.copyWorkspaceService;if(!serviceName)return;const service=(workspaceInventory?.services||[]).find(item=>item.name===serviceName);if(!service)return;const gateway=new URL(service.gatewayUrl,window.location.origin).href;const target=service.externalUrl||gateway;if(event.target.dataset.openWorkspaceService)window.open(target,'_blank','noopener');else navigator.clipboard.writeText(target).then(()=>{event.target.textContent='Copied';setTimeout(()=>event.target.textContent='Copy address',900);});});
byId('workspace-load-logs').addEventListener('click',loadWorkspacePodLogs);
byId('workspace-run-command').addEventListener('click',runWorkspacePodCommand);
byId('copy-workspace-output').addEventListener('click',async event=>{await navigator.clipboard.writeText(byId('workspace-pod-output').textContent);event.currentTarget.textContent='Copied';setTimeout(()=>event.currentTarget.textContent='Copy output',900);});
byId('refresh-clusters').addEventListener('click',async()=>{await syncResources();byId('refresh-clusters').textContent='Refreshed';setTimeout(()=>byId('refresh-clusters').textContent='Refresh',900);});
byId('save-cluster').addEventListener('click',async event=>{if(!validSubmission(event))return;const button=event.currentTarget;button.disabled=true;button.textContent='Verifying...';try{await api('/api/v1/clusters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('cluster-name').value.trim(),endpoint:byId('cluster-endpoint').value.trim(),type:'kubernetes',namespace:byId('cluster-namespace').value.trim(),storageClass:byId('cluster-storage-class').value.trim(),kubeconfig:byId('cluster-kubeconfig').value})});clusterDialog.close();await syncResources();await loadObservability();}catch(error){byId('cluster-result').textContent=error.message;}finally{button.disabled=false;button.textContent='Connect and verify';}});
byId('save-pool').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/pools',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('pool-name').value.trim(),vendor:byId('pool-vendor').value,runtime:byId('pool-runtime').value,policy:byId('pool-policy').value,selector:byId('pool-selector').value.trim(),resourceName:byId('pool-resource-name').value.trim(),sharingMode:byId('pool-sharing-mode').value,sliceProfile:byId('pool-slice-profile').value.trim(),sharingReplicas:Number(byId('pool-sharing-replicas').value)})});poolDialog.close();await syncResources();await loadObservability();}catch(error){byId('pool-result').textContent=error.message;}});
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
function topologyRoundedRect(context, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.lineTo(x + width - r, y);
  context.quadraticCurveTo(x + width, y, x + width, y + r);
  context.lineTo(x + width, y + height - r);
  context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  context.lineTo(x + r, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - r);
  context.lineTo(x, y + r);
  context.quadraticCurveTo(x, y, x + r, y);
  context.closePath();
}
function drawTopology(time = 0) {
  if (!topologyCanvas) return;
  const rect = topologyCanvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.round(rect.width); const height = Math.round(rect.height);
  if (topologyCanvas.width !== Math.round(width * ratio) || topologyCanvas.height !== Math.round(height * ratio)) { topologyCanvas.width = Math.round(width * ratio); topologyCanvas.height = Math.round(height * ratio); }
  const context = topologyCanvas.getContext('2d'); context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, width, height);
  const palette = { background:'#090d0f', grid:'rgba(125, 154, 158, .075)', line:'rgba(140, 168, 171, .24)', surface:'#12191c', border:'#2a383c', text:'#f2f7f5', muted:'#839095', blue:'#3297ff', mint:'#25c79a', gold:'#ffb443', violet:'#a78bfa' };
  context.fillStyle = palette.background; context.fillRect(0, 0, width, height);
  context.lineWidth = 1; context.strokeStyle = palette.grid;
  const gridSize = width < 480 ? 24 : 32;
  for (let x = .5; x < width; x += gridSize) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke(); }
  for (let y = .5; y < height; y += gridSize) { context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
  const compact = width < 480;
  const positions = compact ? [[.25,.15],[.75,.15],[.5,.4],[.25,.65],[.25,.89],[.75,.65],[.75,.89]] : [[.15,.25],[.5,.17],[.5,.52],[.85,.25],[.84,.79],[.16,.79],[.5,.87]];
  const accelerationCount = discoveryProfile.accelerators?.length || 0;
  const nodes = [
    { label:'Local host', detail:`${accelerationCount} accelerator${accelerationCount === 1 ? '' : 's'}`, color:palette.blue, code:'HOST' },
    { label:'Accelerator pools', detail:`${pools.length} placement policies`, color:palette.mint, code:'POOL' },
    { label:'OpenMycelium scheduler', detail:`${queues.length} active queues`, color:palette.mint, code:'CONTROL', core:true },
    { label:'Workloads', detail:`${jobs.length} managed releases`, color:palette.gold, code:'RUN' },
    { label:'Model runtime', detail:`${jobs.filter(job=>job.runtime === 'Ollama').length} local endpoints`, color:palette.violet, code:'MODEL' },
    { label:'Clusters', detail:`${clusters.length} connected fabrics`, color:palette.blue, code:'K8S' },
    { label:'Control plane', detail:'API / DB / NATS', color:palette.mint, code:'READY' }
  ].map((node, index) => ({ ...node, x:positions[index][0] * width, y:positions[index][1] * height }));
  const scheduler = nodes[2];
  nodes.forEach((node, index) => {
    if (index === 2) return;
    const bendY = scheduler.y + (node.y - scheduler.y) * .52;
    context.beginPath(); context.moveTo(scheduler.x, scheduler.y); context.bezierCurveTo(scheduler.x, bendY, node.x, bendY, node.x, node.y); context.strokeStyle = palette.line; context.lineWidth = 1; context.stroke();
    const progress = reducedMotion ? .62 : ((time / 2200) + index * .135) % 1;
    const inverse = 1 - progress;
    const pulseX = inverse ** 3 * scheduler.x + 3 * inverse ** 2 * progress * scheduler.x + 3 * inverse * progress ** 2 * node.x + progress ** 3 * node.x;
    const pulseY = inverse ** 3 * scheduler.y + 3 * inverse ** 2 * progress * bendY + 3 * inverse * progress ** 2 * bendY + progress ** 3 * node.y;
    context.shadowColor = node.color; context.shadowBlur = 10; topologyNodePath(context, pulseX, pulseY, 2.4); context.fillStyle = node.color; context.fill(); context.shadowBlur = 0;
  });
  nodes.forEach(node => {
    const nodeWidth = compact ? Math.min(126, width * .42) : Math.min(node.core ? 174 : 154, width * .23);
    const nodeHeight = node.core ? 62 : 54;
    const left = node.x - nodeWidth / 2; const top = node.y - nodeHeight / 2;
    context.shadowColor = node.core ? 'rgba(37, 199, 154, .24)' : 'rgba(0, 0, 0, .32)'; context.shadowBlur = node.core ? 20 : 12;
    topologyRoundedRect(context, left, top, nodeWidth, nodeHeight, 7); context.fillStyle = palette.surface; context.fill(); context.shadowBlur = 0; context.strokeStyle = node.core ? node.color : palette.border; context.lineWidth = node.core ? 1.4 : 1; context.stroke();
    context.fillStyle = node.color; context.fillRect(left, top + 9, 2, nodeHeight - 18);
    topologyNodePath(context, left + 14, top + 15, 3); context.fillStyle = node.color; context.fill();
    context.textAlign = 'left'; context.textBaseline = 'middle'; context.fillStyle = node.color; context.font = `650 ${compact ? 7 : 8}px ui-monospace, SFMono-Regular, Consolas, monospace`; context.fillText(node.code, left + 23, top + 15);
    context.fillStyle = palette.text; context.font = `650 ${compact ? 9 : 10}px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`; context.fillText(node.label, left + 12, top + 33);
    context.fillStyle = palette.muted; context.font = `500 ${compact ? 7 : 8}px ui-monospace, SFMono-Regular, Consolas, monospace`; context.fillText(node.detail, left + 12, top + 46);
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
function renderIntegrations() { byId('agent-count').textContent = `${integrations.length} registered`; byId('integration-list').innerHTML = integrations.length ? integrations.map(item => `<div class="integration-row"><div><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.type)} / ${escapeHtml(item.target)}</p></div><div class="resource-actions">${tag(item.status === 'running' ? 'Running' : 'Registered')}<button class="danger-button" data-delete-integration="${escapeHtml(item.id)}">Remove</button></div></div>`).join('') : 'No integrations registered.'; }
const integrationDialog = byId('integration-dialog');
byId('add-integration').addEventListener('click', () => integrationDialog.showModal());
byId('save-integration').addEventListener('click', async event => { if (!validSubmission(event)) return; try { await api('/api/v1/integrations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('integration-name').value.trim(),type:byId('integration-type').value,target:byId('integration-target').value.trim()})}); integrationDialog.close(); await Promise.all([syncResources(),syncAgentOrchestration()]);if(activeWorkspaceID)await loadWorkspaceAgentData(); } catch(error) { byId('integration-result').textContent=error.message; } });
const agentDialog=byId('agent-dialog'),agentFlowDialog=byId('agent-flow-dialog'),agentRunDialog=byId('agent-run-dialog'),agentToolDialog=byId('agent-tool-dialog'),agentMemoryDialog=byId('agent-memory-dialog'),agentEvaluationDialog=byId('agent-evaluation-dialog');
function setSelectOptions(select,placeholder,items,value,label){if(!select)return;const selected=value??select.value;select.innerHTML=`<option value="">${escapeHtml(placeholder)}</option>`+items.map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(label(item))}</option>`).join('');select.value=items.some(item=>item.id===selected)?selected:'';}
function populateAgentForms(){
  const versions=modelCatalog.flatMap(model=>(model.versions||[]).map(version=>({...version,modelName:model.name})));
  setSelectOptions(byId('agent-workspace'),'Organization catalog',workspaces,byId('agent-workspace')?.value,item=>`${item.name} / ${item.namespace}`);
  setSelectOptions(byId('agent-flow-workspace'),'Select workspace',workspaces,byId('agent-flow-workspace')?.value,item=>`${item.name} / ${item.namespace}`);
  setSelectOptions(byId('agent-run-workspace'),'Select workspace',workspaces,byId('agent-run-workspace')?.value,item=>`${item.name} / ${item.namespace}`);
  setSelectOptions(byId('agent-model-version'),'No catalog model',versions,byId('agent-model-version')?.value,item=>`${item.modelName} / ${item.version} / ${item.runtime}`);
  const poolSelect=byId('agent-pool');if(poolSelect){const selected=poolSelect.value;poolSelect.innerHTML='<option value="cpu-local">CPU / Kubernetes scheduler</option>'+pools.map(item=>`<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} / ${escapeHtml(item.runtime)}</option>`).join('');poolSelect.value=pools.some(item=>item.name===selected)?selected:'cpu-local';}
  filterAgentRunOptions();
}
function filterAgentRunOptions(){
  const workspaceID=byId('agent-run-workspace')?.value||activeWorkspaceID||'';
  const agents=(agentHubData.agents||[]).filter(item=>!item.workspaceId||item.workspaceId===workspaceID);
  const flows=(agentHubData.flows||[]).filter(item=>item.workspaceId===workspaceID);
  setSelectOptions(byId('agent-run-agent'),'Select agent',agents,byId('agent-run-agent')?.value,item=>`${item.name} / ${item.framework}`);
  setSelectOptions(byId('agent-run-flow'),'Direct agent run',flows,byId('agent-run-flow')?.value,item=>`${item.name} / v${item.version}`);
}
function openAgentDialog(workspaceID=''){populateAgentForms();byId('agent-workspace').value=workspaces.some(item=>item.id===workspaceID)?workspaceID:'';byId('agent-result').textContent='The image must be reachable by the selected Kubernetes cluster. Credentials remain in workspace-scoped image pull secrets.';agentDialog.showModal();}
function openAgentRun(agentID='',workspaceID=''){populateAgentForms();const target=workspaceID||activeWorkspaceID||workspaces[0]?.id||'';byId('agent-run-workspace').value=target;filterAgentRunOptions();if((agentHubData.agents||[]).some(item=>item.id===agentID))byId('agent-run-agent').value=agentID;byId('agent-run-result').textContent='The run is admitted against workspace policy, then released as a labeled Kubernetes workload. Approval-gated agents wait before deployment.';agentRunDialog.showModal();}
function openAgentFlow(workspaceID=''){populateAgentForms();byId('agent-flow-workspace').value=workspaceID||activeWorkspaceID||'';agentFlowDialog.showModal();}
byId('new-agent').addEventListener('click',()=>openAgentDialog(''));
byId('workspace-new-agent').addEventListener('click',()=>openAgentDialog(activeWorkspaceID));
byId('new-agent-flow').addEventListener('click',()=>openAgentFlow(''));
byId('agent-run-workspace').addEventListener('change',filterAgentRunOptions);
byId('save-agent').addEventListener('click',async event=>{if(!validSubmission(event))return;const button=event.currentTarget;button.disabled=true;button.textContent='Registering...';try{await api('/api/v1/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('agent-name').value.trim(),workspaceId:byId('agent-workspace').value,description:byId('agent-description').value.trim(),framework:byId('agent-framework').value,image:byId('agent-image').value.trim(),command:byId('agent-command').value.trim(),port:Number(byId('agent-port').value),modelVersionId:byId('agent-model-version').value,version:Number(byId('agent-version').value),spec:{cpu:byId('agent-cpu').value.trim(),memoryRequest:byId('agent-memory-request').value.trim(),accelerators:Number(byId('agent-accelerators').value),pool:byId('agent-pool').value,storageGB:Number(byId('agent-storage').value),serviceType:'ClusterIP',policy:{tokenBudget:Number(byId('agent-token-budget').value),timeoutSeconds:Number(byId('agent-timeout').value),maxRetries:2,requireApproval:byId('agent-require-approval').checked},memory:{mode:byId('agent-memory-mode').value,retentionDays:Number(byId('agent-retention').value),checkpointing:byId('agent-checkpointing').checked}}})});agentDialog.close();await syncAgentOrchestration();if(activeWorkspaceID){await loadWorkspaceAgentData();renderWorkspaceContent();}}catch(error){byId('agent-result').textContent=error.message;}finally{button.disabled=false;button.textContent='Register agent';}});
byId('save-agent-flow').addEventListener('click',async event=>{if(!validSubmission(event))return;try{const graph=JSON.parse(byId('agent-flow-graph').value);await api('/api/v1/agent-flows',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:byId('agent-flow-workspace').value,name:byId('agent-flow-name').value.trim(),description:byId('agent-flow-description').value.trim(),version:Number(byId('agent-flow-version').value),graph})});agentFlowDialog.close();await syncAgentOrchestration();if(activeWorkspaceID){await loadWorkspaceAgentData();renderWorkspaceContent();}}catch(error){byId('agent-flow-result').textContent=error.message;}});
byId('launch-agent-run').addEventListener('click',async event=>{if(!validSubmission(event))return;const button=event.currentTarget;button.disabled=true;button.textContent='Admitting...';try{const input=JSON.parse(byId('agent-run-input').value||'{}');const workspaceID=byId('agent-run-workspace').value;await api('/api/v1/agent-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:workspaceID,agentId:byId('agent-run-agent').value,flowId:byId('agent-run-flow').value,input,tokenBudget:Number(byId('agent-run-budget').value)})});agentRunDialog.close();activeWorkspaceID=workspaceID;workspaceTab='runs';document.querySelectorAll('[data-workspace-tab]').forEach(item=>item.classList.toggle('active',item.dataset.workspaceTab==='runs'));await Promise.all([syncAgentOrchestration(),syncWorkloads(),loadWorkspaceInventory()]);setView('workspace');}catch(error){byId('agent-run-result').textContent=error.message;}finally{button.disabled=false;button.textContent='Launch agent run';}});
byId('save-agent-tool').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/agent-tools',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:activeWorkspaceID,agentId:byId('agent-tool-agent').value,integrationId:byId('agent-tool-integration').value,permissions:byId('agent-tool-permissions').value.split(',').map(item=>item.trim()).filter(Boolean),approved:byId('agent-tool-approved').checked})});agentToolDialog.close();await loadWorkspaceAgentData();renderWorkspaceContent();}catch(error){byId('agent-tool-result').textContent=error.message;}});
byId('save-agent-memory').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/agent-memory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:activeWorkspaceID,name:byId('agent-memory-name').value.trim(),mode:byId('agent-memory-profile-mode').value,provider:byId('agent-memory-provider').value,retentionDays:Number(byId('agent-memory-days').value),checkpointing:byId('agent-memory-checkpoint').checked})});agentMemoryDialog.close();await loadWorkspaceAgentData();renderWorkspaceContent();}catch(error){byId('agent-memory-result').textContent=error.message;}});
byId('save-agent-evaluation').addEventListener('click',async event=>{if(!validSubmission(event))return;try{const metrics=JSON.parse(byId('agent-evaluation-metrics').value||'{}');await api('/api/v1/agent-evaluations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspaceId:activeWorkspaceID,agentId:byId('agent-evaluation-agent').value,runId:byId('agent-evaluation-run').value,suite:byId('agent-evaluation-suite').value.trim(),score:Number(byId('agent-evaluation-score').value),metrics})});agentEvaluationDialog.close();await Promise.all([syncAgentOrchestration(),loadWorkspaceAgentData()]);renderWorkspaceContent();}catch(error){byId('agent-evaluation-result').textContent=error.message;}});
const modelDialog=byId('model-dialog');
byId('add-model').addEventListener('click',()=>{byId('catalog-result').textContent='OpenMycelium stores metadata and a source reference. Runtime pods fetch artifacts with their approved credentials.';modelDialog.showModal();});
byId('catalog-source-type').addEventListener('change',event=>{const examples={huggingface:'hf://Qwen/Qwen2.5-7B-Instruct',ollama:'ollama://gemma4:12b',oci:'oci://registry.example.com/models/model:1',s3:'s3://bucket/path/model',gcs:'gcs://bucket/path/model',azure:'azure://container/path/model',nfs:'nfs:///models/model',http:'https://models.example.com/model.tar',local:'C:\\models\\model'};byId('catalog-source-uri').placeholder=examples[event.target.value]||'';});
byId('sync-ollama-catalog').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;button.textContent='Synchronizing...';try{const result=await api('/api/v1/model-catalog-sync/ollama',{method:'POST'});await syncModelCatalog();byId('model-sync-status').textContent=`${result.discovered} discovered`;byId('model-sync-status').className='tag green';}catch(error){byId('model-catalog-list').textContent=error.message;byId('model-sync-status').textContent='sync failed';byId('model-sync-status').className='tag amber';}finally{button.disabled=false;button.textContent='Sync Ollama';}});
byId('save-model').addEventListener('click',async event=>{if(!validSubmission(event))return;const button=event.currentTarget;button.disabled=true;try{await api('/api/v1/model-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:byId('catalog-model-name').value.trim(),version:byId('catalog-model-version').value.trim(),sourceType:byId('catalog-source-type').value,sourceUri:byId('catalog-source-uri').value.trim(),runtime:byId('catalog-runtime').value,framework:byId('catalog-framework').value.trim(),format:byId('catalog-format').value.trim(),quantization:byId('catalog-quantization').value.trim(),parametersB:Number(byId('catalog-parameters').value),sizeBytes:Math.round(Number(byId('catalog-size').value)*1073741824),license:byId('catalog-license').value.trim(),description:byId('catalog-description').value.trim()})});modelDialog.close();await syncModelCatalog();}catch(error){byId('catalog-result').textContent=error.message;}finally{button.disabled=false;}});
const sshDialog=byId('ssh-dialog');const sshConfigDialog=byId('ssh-config-dialog');let activeSSHConnection=null;
function openSSHConfig(job){byId('ssh-config-title').textContent=`SSH access for ${job.name}`;byId('ssh-config-workload').value=job.id;byId('ssh-config-host').value=job.sshHost||'';byId('ssh-config-port').value=job.sshPort||22;byId('ssh-config-user').value=job.sshUser||'';byId('ssh-config-result').textContent='';sshConfigDialog.showModal();}
async function showSSHConnection(workloadID){const connection=await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/ssh`);activeSSHConnection=connection;byId('ssh-workload-name').textContent=`SSH to ${connection.name}`;byId('ssh-status').textContent=`${connection.user}@${connection.host}:${connection.port} / workload ${connection.status}`;byId('ssh-command').textContent=connection.command;sshDialog.showModal();}
document.addEventListener('click', async event => {
  const action=event.target.dataset.action;const workloadID=event.target.dataset.workloadId;
  try {
    if(action&&workloadID){
      if(action==='delete'){const job=jobs.find(item=>item.id===workloadID);const message=job?.kubernetes?'Delete this workload, its Kubernetes resources, Service, and model storage? This is permanent.':'Delete this workload record? This operation is permanent.';if(!window.confirm(message))return;await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}`,{method:'DELETE'});await syncWorkloads();await loadObservability();return;}
      if(action==='ssh'){const job=jobs.find(item=>item.id===workloadID);if(!job?.sshConfigured){openSSHConfig(job||{id:workloadID,name:'workload'});return;}await showSSHConnection(workloadID);return;}
      if(action!=='refresh')await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});await syncWorkloads();await loadObservability();return;
    }
    const kubeOperation=event.target.dataset.kubeOperation;if(kubeOperation&&workloadID){await showKubernetesOperation(workloadID,kubeOperation);return;}
    const refreshClusterID=event.target.dataset.refreshCluster;if(refreshClusterID){event.target.disabled=true;event.target.textContent='Verifying...';try{await api(`/api/v1/clusters/${encodeURIComponent(refreshClusterID)}/refresh`,{method:'POST'});await syncResources();await loadObservability();}finally{event.target.disabled=false;event.target.textContent='Verify';}return;}
    const resources=[['deleteCluster','clusters'],['deletePool','pools'],['deleteQueue','queues'],['deleteIntegration','integrations']];for(const [key,path] of resources){const id=event.target.dataset[key];if(id){if(!window.confirm('Remove this resource? This action is recorded in the audit trail.'))return;await api(`/api/v1/${path}/${encodeURIComponent(id)}`,{method:'DELETE'});await syncResources();await loadObservability();return;}}
    const deleteModel=event.target.dataset.deleteModel;if(deleteModel){if(!window.confirm('Delete this model and all of its versions? Referenced models cannot be deleted.'))return;await api(`/api/v1/model-catalog/${encodeURIComponent(deleteModel)}`,{method:'DELETE'});await syncModelCatalog();return;}
    const runAgent=event.target.dataset.agentRun;if(runAgent!==undefined){openAgentRun(runAgent,event.target.dataset.agentWorkspace||activeWorkspaceID);return;}
    if(event.target.hasAttribute('data-create-workspace-agent')){openAgentDialog(activeWorkspaceID);return;}
    if(event.target.hasAttribute('data-create-agent-flow')){openAgentFlow(activeWorkspaceID);return;}
    if(event.target.hasAttribute('data-launch-workspace-agent')){openAgentRun('',activeWorkspaceID);return;}
    if(event.target.hasAttribute('data-attach-agent-tool')){const available=(workspaceAgentData?.agents||[]);setSelectOptions(byId('agent-tool-agent'),'Select agent',available,'',item=>`${item.name} / ${item.framework}`);setSelectOptions(byId('agent-tool-integration'),'Select integration',integrations,'',item=>`${item.name} / ${item.type}`);byId('agent-tool-result').textContent='Bindings are scoped to the active workspace and recorded in the audit trail.';agentToolDialog.showModal();return;}
    if(event.target.hasAttribute('data-create-agent-memory')){byId('agent-memory-result').textContent='The profile records policy and provider selection; secret material stays outside the browser.';agentMemoryDialog.showModal();return;}
    if(event.target.hasAttribute('data-record-agent-evaluation')){const agents=workspaceAgentData?.agents||[];const runs=workspaceAgentData?.runs||[];setSelectOptions(byId('agent-evaluation-agent'),'Select agent',agents,'',item=>`${item.name} / ${item.framework}`);setSelectOptions(byId('agent-evaluation-run'),'Definition evaluation',runs,'',item=>`${item.agentName} / ${item.status} / ${item.id}`);byId('agent-evaluation-result').textContent='Results are linked to the workspace, agent, and optional run, then included in MLOps reporting and the audit trail.';agentEvaluationDialog.showModal();return;}
    const deleteAgent=event.target.dataset.deleteAgent;if(deleteAgent){if(!window.confirm('Delete this unused agent definition? Agents with run history must be retained for auditability.'))return;await api(`/api/v1/agents/${encodeURIComponent(deleteAgent)}`,{method:'DELETE'});await syncAgentOrchestration();return;}
    const deleteFlow=event.target.dataset.deleteAgentFlow;if(deleteFlow){if(!window.confirm('Delete this unused orchestration flow?'))return;await api(`/api/v1/agent-flows/${encodeURIComponent(deleteFlow)}`,{method:'DELETE'});await syncAgentOrchestration();return;}
    const cancelRun=event.target.dataset.cancelAgentRun;if(cancelRun){if(!window.confirm('Cancel this agent run and remove its Kubernetes runtime?'))return;await api(`/api/v1/agent-runs/${encodeURIComponent(cancelRun)}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'cancel'})});await Promise.all([syncAgentOrchestration(),syncWorkloads(),loadWorkspaceAgentData()]);renderWorkspaceContent();return;}
    const approval=event.target.dataset.agentApproval;if(approval){await api('/api/v1/agent-approvals',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:approval,decision:event.target.dataset.decision})});await Promise.all([syncAgentOrchestration(),syncWorkloads(),loadWorkspaceAgentData()]);renderWorkspaceContent();return;}
    const workload=event.target.dataset.openAgentWorkload;if(workload){setView('workloads');const job=jobs.find(item=>item.id===workload);if(job?.kubernetes)await showKubernetesOperation(workload,'diagnostics');return;}
    const traceRun=event.target.dataset.showAgentTrace;if(traceRun){workspaceTab='traces';document.querySelectorAll('[data-workspace-tab]').forEach(item=>item.classList.toggle('active',item.dataset.workspaceTab==='traces'));renderWorkspaceContent();return;}
    if(event.target.matches('[data-user-role]')){await api(`/api/v1/users/${encodeURIComponent(event.target.dataset.userRole)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:event.target.value})});await loadAccess();return;}
    if(event.target.matches('[data-user-active]')){await api(`/api/v1/users/${encodeURIComponent(event.target.dataset.userActive)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:event.target.dataset.active!=='true'})});await loadAccess();}
    if(event.target.matches('[data-delete-user]')){if(!window.confirm('Delete this user and all active sessions? This operation is permanent.'))return;await api(`/api/v1/users/${encodeURIComponent(event.target.dataset.deleteUser)}`,{method:'DELETE'});await loadAccess();await loadObservability();}
  } catch(error){window.alert(error.message);}
});
byId('copy-ssh-command').addEventListener('click',async()=>{await navigator.clipboard.writeText(byId('ssh-command').textContent);byId('copy-ssh-command').textContent='Copied';setTimeout(()=>byId('copy-ssh-command').textContent='Copy command',900);});
byId('edit-ssh').addEventListener('click',()=>{if(!activeSSHConnection)return;sshDialog.close();openSSHConfig({id:activeSSHConnection.workloadId,name:activeSSHConnection.name,sshHost:activeSSHConnection.host,sshPort:activeSSHConnection.port,sshUser:activeSSHConnection.user});});
byId('save-ssh-config').addEventListener('click',async event=>{if(!validSubmission(event))return;const workloadID=byId('ssh-config-workload').value;try{await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/ssh`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:byId('ssh-config-host').value.trim(),port:Number(byId('ssh-config-port').value),user:byId('ssh-config-user').value.trim()})});sshConfigDialog.close();await syncWorkloads();await showSSHConnection(workloadID);}catch(error){byId('ssh-config-result').textContent=error.message;}});
byId('refresh-workloads').addEventListener('click', async () => { await syncWorkloads(); byId('refresh-workloads').textContent='Refreshed'; setTimeout(()=>byId('refresh-workloads').textContent='Refresh',900); });
const kubeOperationDialog=byId('kube-operation-dialog');let kubeOperationText='';let activeKubeWorkload='';
async function showKubernetesOperation(workloadID,operation){
  activeKubeWorkload=workloadID;const job=jobs.find(item=>item.id===workloadID);byId('kube-operation-label').textContent=`KUBERNETES / ${operation.toUpperCase()}`;byId('kube-operation-title').textContent=job?.name||'Kubernetes workload';byId('kube-operation-meta').textContent=`${job?.cluster||'cluster'} / ${job?.namespace||'namespace'}${job?.podName?` / ${job.podName}`:''}`;byId('kube-operation-output').textContent='Loading from Kubernetes...';document.querySelectorAll('[data-workspace-operation]').forEach(button=>button.classList.toggle('active',button.dataset.workspaceOperation===operation));if(!kubeOperationDialog.open)kubeOperationDialog.showModal();
  try{const data=await api(`/api/v1/workloads/${encodeURIComponent(workloadID)}/${operation}`);if(operation==='logs')kubeOperationText=data.logs||'No log output.';else if(operation==='manifest')kubeOperationText=data.manifest||'';else if(operation==='diagnostics'||operation==='storage')kubeOperationText=JSON.stringify(data,null,2);else if(operation==='service')kubeOperationText=`Service: ${data.name||'not configured'}\nNamespace: ${data.namespace||''}\nType: ${data.type||''}\nEndpoint: ${data.endpoint||'pending'}\nPort: ${data.port||''}`;else kubeOperationText=(data.events||[]).map(item=>`${new Date(item.timestamp).toLocaleString()}  ${item.type}  ${item.reason}\n${item.message}  (x${item.count})`).join('\n\n')||'No Kubernetes events reported.';byId('kube-operation-output').textContent=kubeOperationText;}catch(error){kubeOperationText=error.message;byId('kube-operation-output').textContent=error.message;}
}
byId('copy-kube-output').addEventListener('click',async()=>{await navigator.clipboard.writeText(kubeOperationText);byId('copy-kube-output').textContent='Copied';setTimeout(()=>byId('copy-kube-output').textContent='Copy',900);});
document.querySelectorAll('[data-workspace-operation]').forEach(button=>button.addEventListener('click',()=>{if(activeKubeWorkload)showKubernetesOperation(activeKubeWorkload,button.dataset.workspaceOperation);}));
byId('run-kube-exec').addEventListener('click',async event=>{const command=byId('kube-exec-command').value.trim();if(!activeKubeWorkload||!command){byId('kube-operation-note').textContent='Enter a non-interactive command to run inside the workload container.';return;}const button=event.currentTarget;button.disabled=true;button.textContent='Running...';try{const data=await api(`/api/v1/workloads/${encodeURIComponent(activeKubeWorkload)}/exec`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command})});kubeOperationText=[data.stdout,data.stderr].filter(Boolean).join('\n')||'Command completed without output.';byId('kube-operation-output').textContent=kubeOperationText;byId('kube-operation-label').textContent='KUBERNETES / COMMAND';}catch(error){kubeOperationText=error.message;byId('kube-operation-output').textContent=error.message;}finally{button.disabled=false;button.textContent='Run command';}});
byId('run-kube-probe').addEventListener('click',async event=>{if(!activeKubeWorkload)return;const button=event.currentTarget;button.disabled=true;button.textContent='Probing...';try{const data=await api(`/api/v1/workloads/${encodeURIComponent(activeKubeWorkload)}/probe`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({method:byId('kube-probe-method').value,path:byId('kube-probe-path').value,body:''})});kubeOperationText=data.body||'Service returned an empty response.';byId('kube-operation-output').textContent=kubeOperationText;byId('kube-operation-label').textContent=`SERVICE PROBE / ${data.method}`;}catch(error){kubeOperationText=error.message;byId('kube-operation-output').textContent=error.message;}finally{button.disabled=false;button.textContent='Probe service';}});
byId('job-model').addEventListener('input',event=>{if(event.target.value.trim()){if(!byId('job-image').value.trim())byId('job-image').value='ollama/ollama:latest';byId('job-port').value=11434;byId('job-type').value='inference';}});
byId('chat-form')?.addEventListener('submit', event => { event.preventDefault(); });
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
function formatBytes(value){const bytes=Number(value)||0;if(bytes<1024)return `${bytes} B`;const units=['KiB','MiB','GiB','TiB','PiB'];let amount=bytes,index=-1;do{amount/=1024;index++;}while(amount>=1024&&index<units.length-1);return `${amount.toFixed(amount>=100?0:amount>=10?1:2)} ${units[index]}`;}
function titleCase(value){return String(value||'unknown').replaceAll('-',' ').replace(/\b\w/g,letter=>letter.toUpperCase());}
function metricBars(items){const values=items||[];const maximum=Math.max(1,...values.map(item=>Number(item.value)||0));return values.length?values.map(item=>`<div class="metric-bar"><div><span>${escapeHtml(titleCase(item.label))}</span><b>${item.value}</b></div><i><span style="width:${Math.max(2,(Number(item.value)||0)/maximum*100)}%"></span></i></div>`).join(''):'<div class="empty-inline">No operational samples yet.</div>';}
async function loadMLOps(){try{const data=await api('/api/v1/operations/mlops');const summary=data.summary||{};byId('mlops-active').textContent=summary.activeWorkloads||0;byId('mlops-workload-total').textContent=`${summary.workloads||0} managed / ${summary.failedWorkloads||0} failed`;byId('mlops-models').textContent=summary.models||0;byId('mlops-model-versions').textContent=`${summary.modelVersions||0} versions`;byId('mlops-storage').textContent=formatBytes(summary.modelBytes);byId('mlops-success').textContent=`${Number(summary.nonFailedPercent||0).toFixed(1)}%`;byId('mlops-release-total').textContent=`${summary.releases||0} releases / ${summary.workspaces||0} workspaces`;byId('mlops-updated').textContent=new Date(data.timestamp).toLocaleTimeString();byId('mlops-status-bars').innerHTML=metricBars(data.workloadStatus);byId('mlops-kind-bars').innerHTML=metricBars(data.workloadKinds);byId('mlops-runtime-bars').innerHTML=metricBars(data.runtimes);byId('mlops-model-list').innerHTML=(data.models||[]).length?(data.models||[]).map(model=>`<tr><td><strong>${escapeHtml(model.name)}</strong></td><td>${model.versions}</td><td class="mono">${escapeHtml((model.runtimes||[]).join(', ')||'custom')}</td><td>${formatBytes(model.sizeBytes)}</td><td>${model.deployments}</td><td>${model.activeDeployments}</td></tr>`).join(''):'<tr><td colspan="6" class="empty-inline">No governed models registered.</td></tr>';byId('mlops-workload-list').innerHTML=(data.workloads||[]).length?(data.workloads||[]).slice(0,50).map(item=>{const workspace=workspaces.find(candidate=>candidate.id===item.workspaceId);return `<tr><td><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.releaseId||item.id)}</small></td><td>${escapeHtml(titleCase(item.kind))}</td><td>${escapeHtml(item.model||item.image||'not attached')}</td><td>${escapeHtml(workspace?.name||item.clusterName||'local')}<small>${escapeHtml(item.nodeName||item.namespace||'placement pending')}</small></td><td class="mono">${escapeHtml(item.modelRuntime||item.runtime||'unknown')}</td><td>${item.restarts||0}</td><td>${tag(titleCase(item.status))}</td></tr>`;}).join(''):'<tr><td colspan="7" class="empty-inline">No AI workloads deployed.</td></tr>';byId('mlops-release-list').innerHTML=(data.releases||[]).length?(data.releases||[]).map(item=>`<article><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.workspaceName)} / ${escapeHtml(item.sourceType)} / ${new Date(item.createdAt).toLocaleString()}</small></div>${tag(titleCase(item.status))}</article>`).join(''):'<div class="empty-inline">No workspace releases recorded.</div>';}catch(error){byId('mlops-status-bars').innerHTML=`<div class="empty-inline">${escapeHtml(error.message)}</div>`;}}
function healthRow(label,ready,detail){return `<div class="service-row"><span>${escapeHtml(label)}</span><strong class="${ready?'health-ready':'health-down'}">${ready?'Ready':'Unavailable'} / ${escapeHtml(detail)}</strong></div>`;}
async function loadAIOps(){try{const data=await api('/api/v1/operations/aiops');const summary=data.summary||{};operationsLinks=data.links||operationsLinks;byId('aiops-alerts').textContent=summary.alerts||0;byId('aiops-alert-count').textContent=summary.alerts||0;byId('aiops-ready-nodes').textContent=`${summary.readyNodes||0}/${summary.totalNodes||0}`;byId('aiops-cluster-total').textContent=`${summary.clusters||0} clusters`;byId('aiops-sessions').textContent=summary.activeSessions||0;byId('aiops-user-total').textContent=`${summary.activeUsers||0} active users`;byId('aiops-actions').textContent=summary.actions24h||0;byId('aiops-restarts').textContent=`${summary.workloadRestarts||0} workload restarts`;byId('aiops-updated').innerHTML='<i></i> '+new Date(data.timestamp).toLocaleTimeString();byId('prometheus-url').textContent=operationsLinks.prometheus;byId('grafana-url').textContent=operationsLinks.grafana;byId('prometheus-state').classList.toggle('unavailable',!data.services?.prometheus);byId('grafana-state').classList.toggle('unavailable',!data.services?.grafana);byId('prometheus-state').lastChild.textContent=data.services?.prometheus?' Prometheus ready':' Prometheus unavailable';byId('grafana-state').lastChild.textContent=data.services?.grafana?' Grafana ready':' Grafana unavailable';const alerts=data.alerts||[];byId('aiops-severity').textContent=alerts.some(item=>item.severity==='critical')?'Critical':alerts.length?'Attention':'Clear';byId('aiops-severity').className=`tag ${alerts.some(item=>item.severity==='critical')?'red':alerts.length?'amber':'green'}`;byId('aiops-alert-list').innerHTML=alerts.length?alerts.map(item=>`<article class="alert-item ${escapeHtml(item.severity)}"><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p><small>${escapeHtml(item.resource)}</small></div><span>${escapeHtml(item.severity)}</span></article>`).join(''):'<div class="empty-state compact-empty"><strong>No active operational alerts.</strong><span>Reconciled workloads, dependencies, and clusters are within their reported state.</span></div>';byId('aiops-service-list').innerHTML=healthRow('Control-plane API',Boolean(data.services?.api),'authenticated')+healthRow('PostgreSQL',Boolean(data.services?.postgres),'system of record')+healthRow('NATS',Boolean(data.services?.nats),'event transport')+healthRow('Prometheus',Boolean(data.services?.prometheus),'metrics collector')+healthRow('Grafana',Boolean(data.services?.grafana),'dashboard service');byId('aiops-cluster-list').innerHTML=(data.clusters||[]).length?(data.clusters||[]).map(item=>`<div class="cluster-health"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.status)} / refreshed ${new Date(item.updatedAt).toLocaleString()}</small></div><span>${item.readyNodes}/${item.nodes} ready</span></div>`).join(''):'<div class="empty-inline">No clusters connected.</div>';byId('aiops-user-privacy').textContent=sessionUser?.role==='viewer'?'identities redacted':'operator visibility';byId('aiops-user-list').innerHTML=(data.users||[]).length?(data.users||[]).map(item=>`<tr><td><strong>${escapeHtml(item.identity)}</strong></td><td>${escapeHtml(item.role.replaceAll('_',' '))}</td><td>${tag(item.active?'Ready':'Disabled')}</td><td>${item.activeSessions}</td><td>${item.actions24h}</td><td>${item.lastActionAt?new Date(item.lastActionAt).toLocaleString():'No recorded activity'}</td></tr>`).join(''):'<tr><td colspan="6" class="empty-inline">No user usage records.</td></tr>';byId('aiops-activity-list').innerHTML=(data.activity||[]).length?(data.activity||[]).map(item=>`<article><span>${new Date(item.createdAt).toLocaleString()}</span><strong>${escapeHtml(item.subject)}</strong><small>${escapeHtml(item.actor)}</small></article>`).join(''):'<div class="empty-inline">No audited operational activity.</div>';}catch(error){byId('aiops-alert-list').innerHTML=`<div class="empty-inline">${escapeHtml(error.message)}</div>`;}}
function serviceRow(label,value,status='Ready'){return `<div class="service-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(status)} / ${escapeHtml(value)}</strong></div>`;}
async function loadObservability(){try{const data=await api('/api/v1/observability/summary');byId('obs-running').textContent=data.workloads.running;byId('obs-workload-total').textContent=`${data.workloads.total} total / ${data.workloads.queued} queued / ${data.workloads.failed} failed`;byId('obs-clusters').textContent=data.inventory.clusters;byId('obs-accelerators').textContent=data.inventory.accelerators;byId('obs-users').textContent=data.governance.users;byId('obs-updated').textContent=new Date(data.timestamp).toLocaleTimeString();byId('service-health').innerHTML=serviceRow('API',data.services.api)+serviceRow('PostgreSQL',data.services.postgres?'connected':'not connected',data.services.postgres?'Ready':'Unavailable')+serviceRow('NATS',data.services.nats?'connected':'not connected',data.services.nats?'Ready':'Unavailable')+serviceRow('Ollama endpoint',data.services.ollama,'Configured');byId('workload-health').innerHTML=serviceRow('Running',data.workloads.running)+serviceRow('Queued',data.workloads.queued)+serviceRow('Failed',data.workloads.failed,data.workloads.failed===0?'Clear':'Attention')+serviceRow('Logical pools',data.inventory.pools);}catch(error){byId('service-health').textContent=error.message;}}
async function loadAccess(){if(sessionUser?.role!=='platform_admin')return;try{const [userData,auditData]=await Promise.all([api('/api/v1/users'),api('/api/v1/audit')]);const users=userData.users||[];byId('users-count').textContent=`${users.length} account${users.length===1?'':'s'}`;byId('users-list').innerHTML=users.length?users.map(user=>`<tr><td>${escapeHtml(user.email)}</td><td><select class="role-select" data-user-role="${escapeHtml(user.id)}" ${user.id===sessionUser.id?'disabled':''}><option value="platform_admin" ${user.role==='platform_admin'?'selected':''}>Platform admin</option><option value="operator" ${user.role==='operator'?'selected':''}>Operator</option><option value="viewer" ${user.role==='viewer'?'selected':''}>Viewer</option></select></td><td><button class="${user.active?'text-button':'danger-button'}" data-user-active="${escapeHtml(user.id)}" data-active="${user.active}" ${user.id===sessionUser.id?'disabled':''}>${user.active?'Active':'Disabled'}</button></td><td class="mono">${new Date(user.createdAt).toLocaleDateString()}</td><td><button class="danger-button" data-delete-user="${escapeHtml(user.id)}" ${user.id===sessionUser.id?'disabled':''}>Delete</button></td></tr>`).join(''):'<tr><td colspan="5">No users found.</td></tr>';const events=auditData.events||[];byId('audit-list').innerHTML=events.length?events.slice(0,30).map(event=>`<div class="audit-entry"><strong>${escapeHtml(event.subject)}</strong><span>${new Date(event.createdAt).toLocaleString()} / ${escapeHtml(event.payload?.actor||event.payload?.email||'system')}</span></div>`).join(''):'<div class="empty-inline">No audit events recorded.</div>';}catch(error){byId('audit-list').textContent=error.message;}}
const userDialog=byId('user-dialog');
byId('add-user').addEventListener('click',()=>{byId('user-result').textContent='';userDialog.showModal();});
byId('save-user').addEventListener('click',async event=>{if(!validSubmission(event))return;try{await api('/api/v1/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:byId('new-user-email').value.trim(),password:byId('new-user-password').value,role:byId('new-user-role').value})});userDialog.close();byId('new-user-email').value='';byId('new-user-password').value='';byId('new-user-role').value='viewer';await loadAccess();await loadObservability();}catch(error){byId('user-result').textContent=error.message;}});
byId('refresh-observability').addEventListener('click',loadObservability);byId('refresh-mlops').addEventListener('click',loadMLOps);byId('refresh-aiops').addEventListener('click',loadAIOps);byId('refresh-access').addEventListener('click',loadAccess);
byId('open-prometheus').addEventListener('click',()=>window.open(operationsLinks.prometheus,'_blank','noopener'));byId('open-grafana').addEventListener('click',()=>window.open(operationsLinks.grafana,'_blank','noopener'));
async function loadSettings() { try { const data=await api('/api/v1/settings'); byId('settings-organization').value=data.organization || ''; byId('settings-default-queue').value=data.defaultQueue || ''; byId('settings-oidc').value=data.oidcIssuer || ''; byId('settings-telemetry').value=data.telemetryEndpoint || ''; byId('settings-registration').checked=Boolean(data.registrationOpen); byId('settings-result').textContent='Settings loaded from the control plane.'; } catch(error) { byId('settings-result').textContent=error.message; } }
byId('save-settings').addEventListener('click', async () => { const settings={organization:byId('settings-organization').value.trim(),defaultQueue:byId('settings-default-queue').value.trim(),oidcIssuer:byId('settings-oidc').value,telemetryEndpoint:byId('settings-telemetry').value.trim(),registrationOpen:byId('settings-registration').checked}; try { const data=await api('/api/v1/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)}); byId('settings-result').textContent=`Saved for ${data.organization}.`; } catch(error) { byId('settings-result').textContent=error.message; } });
byId('fabric-cluster').addEventListener('change', loadFabricCapabilities);
byId('fabric-device-memory').addEventListener('change', loadFabricCapabilities);
byId('fabric-host-memory').addEventListener('change', loadFabricCapabilities);
byId('job-cluster').addEventListener('change', renderFabric);
byId('create-fabric-plan').addEventListener('click', async event => {
  const button = event.currentTarget; button.disabled = true; button.textContent = 'Compiling...';
  try {
    const payload = { name:byId('fabric-name').value.trim(), clusterId:byId('fabric-cluster').value, tensorName:byId('fabric-tensor').value.trim(), tensorGiB:Number(byId('fabric-size').value), strategy:byId('fabric-strategy').value, consistency:byId('fabric-consistency').value, defaultDeviceMemoryGiB:Number(byId('fabric-device-memory').value), reservePercent:Number(byId('fabric-reserve').value), includeHostMemory:byId('fabric-host-memory').checked };
    const plan = await api('/api/v1/fabric/plans', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
    byId('fabric-result').textContent = `${plan.logicalAddress} compiled across ${plan.selectedDevices} placement${plan.selectedDevices === 1 ? '' : 's'}.`;
    await syncFabric();
  } catch (error) { byId('fabric-result').textContent = error.message; }
  finally { button.disabled = false; button.textContent = 'Compile plan'; }
});
document.addEventListener('click', async event => {
  const id = event.target.dataset.deleteFabric;
  if (!id) return;
  if (!window.confirm('Delete this Hypha tensor plan? Deletion is blocked while a workload still references it.')) return;
  try { await api(`/api/v1/fabric/plans/${encodeURIComponent(id)}`, { method:'DELETE' }); await syncFabric(); }
  catch (error) { byId('fabric-result').textContent = error.message; }
});
byId('logout').addEventListener('click', async () => { await fetch('/api/v1/auth/logout', { method: 'POST' }); window.location.replace('/login.html'); });
renderIntegrations();renderPools();renderJobs();renderClusters();renderQueues();renderModelCatalog();renderManifestClusters();renderWorkspaceSelector();renderWorkspace();renderAgentHub();
async function bootstrap(){try{await loadSession();await syncResources();await Promise.allSettled([loadControlHealth(),syncWorkloads(),loadObservability(),syncFabric(),syncModelCatalog()]);await syncWorkspaces();await Promise.allSettled([loadMLOps(),loadAIOps(),syncAgentOrchestration()]);renderManifestClusters();if(sessionUser.role==='platform_admin')await Promise.allSettled([loadSettings(),loadAccess()]);}catch(error){console.error(error);}}
bootstrap();
