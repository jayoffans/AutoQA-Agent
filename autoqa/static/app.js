import {terminal, templates, planInfo, workflowStages} from './studio-model.js';
import {esc, badge, time, empty, metric, taskTable, stepTimeline, reportView} from './components.js';
const $ = id => document.getElementById(id);
let tasks = [], projects = [], stats = null, selected = null, detail = null, health = null, filter = 'all', events = [], eventAbort = null;
let screenshotUrl = null, screenshotKey = '', refreshBusy = false, generation = 0, pollTimer;
let legacyMigrated = false;
let apiToken = '';
try { apiToken = sessionStorage.getItem('autoqa-token') || ''; selected = sessionStorage.getItem('autoqa-selected-task'); } catch { /* UI preferences may be disabled */ }
function demoProject() { return projects.find(p => p.builtin && !p.url.includes('/api/')); }
function toast(message, error = false) {
  $('toast').textContent = message; $('toast').classList.toggle('error', error); $('toast').hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 6000);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{...(options.body ? {'Content-Type':'application/json'} : {}), ...(apiToken ? {Authorization:`Bearer ${apiToken}`} : {}), ...options.headers}});
  if (!response.ok) {
    let data; try { data = await response.json(); } catch { data = {detail:response.statusText}; }
    throw Error(response.status === 401 ? '服务需要访问令牌，请在 Settings 中填写 API Token。' : typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  }
  return response;
}
function projectFor(task) {
  const registered = projects.find(p => p.id === task.project_id);
  if (registered) return registered;
  const url = new URL(task.target_url || task.request.target_url);
  return {id:task.project_id || url.origin, name:task.project_name || url.hostname, url:url.origin, archived:true};
}
function allProjects() { return projects; }
function showView(view) {
  const names = {dashboard:'Dashboard', projects:'Projects', tasks:'Test tasks', workflow:'Agent workflow', reports:'Test reports', settings:'Settings'};
  view = names[view] ? view : 'dashboard';
  document.querySelectorAll('.view').forEach(v => v.hidden = v.id !== view+'-view');
  document.querySelectorAll('[data-view]').forEach(v => { v.classList.toggle('active', v.dataset.view === view); if (v.dataset.view === view) v.setAttribute('aria-current','page'); else v.removeAttribute('aria-current'); });
  $('page-label').textContent = names[view];
  if (['workflow','reports'].includes(view) && !selected && tasks.length) selectTask(tasks[0].id).catch(showError);
}
function navigate(view) { if (location.hash === '#'+view) showView(view); else location.hash = view; }
function showError(error) { toast(error.message, true); }
function renderDashboard() {
  if (!stats) return;
  const {passed, failed, running, awaiting_approval:approval, interrupted, success_rate:rate} = stats;
  $('dashboard-metrics').innerHTML = metric('Projects', stats.projects, '服务端持久化项目') + metric('Running Tests', running, `${stats.tasks} 个测试任务 · 全部历史`, 'violet') + metric('Passed', passed, '已通过的测试任务', 'green') + metric('Issues Found', stats.issues, '已持久化的问题条目', 'red');
  $('recent-runs').innerHTML = taskTable(tasks.slice(0,5), projectFor, selected, true);
  $('quality-pulse').innerHTML = `<div class="rate-ring" style="--rate:${rate || 0}"><div><strong>${rate === null ? '—' : rate+'%'}</strong><span>SUCCESS RATE</span></div></div><div class="pulse-caption">已通过 / 已完成任务</div><div class="pulse-legend"><span><i class="dot"></i> Passed <b>${passed}</b></span><span><i class="dot red-dot"></i> Failed <b>${failed}</b></span></div><p class="pulse-note">仅统计 Passed / Failed。<br>故障演示也计入失败任务。</p>`;
  const risks = [
    ...(approval ? [{tone:'amber', title:`${approval} 个任务等待审批`, text:'审核计划或关键操作后继续执行。', action:'tasks', state:'awaiting_approval'}] : []),
    ...(failed ? [{tone:'red', title:`${failed} 个任务存在运行问题`, text:'打开报告，复核诊断建议与执行证据。', action:'reports'}] : []),
    ...(interrupted ? [{tone:'amber', title:`${interrupted} 个任务执行中断`, text:'检查保留的证据，创建新任务重新验证。', action:'tasks', state:'interrupted'}] : [])
  ];
  $('risk-list').innerHTML = risks.length ? risks.map(r => `<button class="risk-row ${r.tone}" data-risk-view="${r.action}" ${r.state ? `data-risk-filter="${r.state}"` : ''}><span class="risk-icon">!</span><span><strong>${r.title}</strong><small>${r.text}</small></span><span>↗</span></button>`).join('') : `<div class="risk-clear"><span>✓</span><div><strong>暂无待处理的运行风险</strong><p>${tasks.length ? '持续关注下一次测试结果。' : '创建首个任务后，这里将展示风险与审批提醒。'}</p></div></div>`;
}
function renderProjects() {
  const list = allProjects(); $('project-count').textContent = list.length;
  $('project-list').innerHTML = list.map(p => {
    return `<article class="panel project-card"><div class="project-card-top"><span class="project-symbol">▤</span><span class="small-label">${p.builtin ? 'BUILT-IN' : 'PERSISTED'}</span></div><h2>${esc(p.name)}</h2><p class="mono">${esc(p.url)}</p><div class="project-stats"><span><b>${p.runs || 0}</b> runs</span><span><b>${p.passed || 0}</b> passed</span></div><button class="secondary" data-project="${esc(p.id)}">创建测试 →</button>${p.builtin ? '' : `<div class="project-controls"><button class="text-button" data-edit-project="${esc(p.id)}">编辑</button><button class="text-button" data-delete-project="${esc(p.id)}">归档项目</button></div>`}</article>`;
  }).join('');
}
function renderTasks() {
  $('task-count').textContent = tasks.length;
  const query = $('task-search').value.toLowerCase();
  const visible = tasks.filter(t => (filter === 'all' || (filter === 'running' ? ['running','queued'].includes(t.status) : t.status === filter)) && (t.goal+' '+t.target_url+' '+t.id+' '+projectFor(t).name).toLowerCase().includes(query));
  $('task-list').innerHTML = visible.length || !tasks.length ? taskTable(visible, projectFor, selected) : empty('没有匹配的任务', '调整搜索条件或状态筛选。', false);
}
function renderSelectors() {
  for (const id of ['workflow-task','report-task']) {
    const options = tasks.length ? tasks.map(t => `<option value="${esc(t.id)}">${esc((t.title || t.goal).slice(0,48))} · ${esc(t.id.slice(0,8))}</option>`).join('') : '<option value="">暂无测试任务</option>';
    if ($(id).innerHTML !== options) $(id).innerHTML = options;
    if (selected) $(id).value = selected;
    $(id).disabled = !tasks.length;
  }
  $('workflow-empty').innerHTML = selected ? '' : empty('一条工作流，从一个测试目标开始', '创建任务后，查看计划、审批、执行状态与运行证据。');
  if (!selected) { $('workflow-content').hidden = true; $('report-content').innerHTML = empty('尚无测试报告', '运行一个测试任务，生成结果、问题列表和证据报告。'); }
}
function renderAll() { renderDashboard(); renderTasks(); renderProjects(); renderSelectors(); }
async function loadHealth() {
  health = await (await api('/api/health')).json();
  if (health.demo_mode) {
    document.querySelectorAll('[data-action="new-project"], [data-view="settings"], a[href="/docs"], .secondary-label').forEach(e => e.hidden = true);
    document.querySelector('.workspace small').textContent = 'Public Demo · 每日有限额度';
    document.querySelector('.profile small').textContent = 'Public Demo · v1.0';
    $('settings-view').innerHTML = '<div class="info-note">Public Demo：服务配置由维护者管理。</div>';
    $('templates').innerHTML = '<button type="button" class="template" data-template="api">Login API Quality Demo</button>';
    $('target').readOnly = true; $('goal').readOnly = true;
    $('test-type').innerHTML = '<option value="api">API · Login 契约测试</option>';
    $('test-type').disabled = true;
    document.querySelector('.advanced').hidden = true;
    document.querySelector('.checkbox-row').hidden = true;
    document.querySelector('.templates-title span').textContent = '受控 Login API 执行';
    $('mode-label').title = 'Seed / Mock 演示使用受控接口，结果来自后端；不代表真实模型调用。';
  }
  $('mode-label').textContent = health.llm_provider === 'mock' ? 'Mock provider' : health.model_enabled ? 'Model connected' : 'Demo templates';
  $('connection-info').innerHTML = [['服务', 'Connected · v'+health.version], ['规划模型', health.model_enabled ? '已连接' : 'Demo / Structured'], ['数据库', health.database.toUpperCase()], ['Chrome DevTools MCP', health.mcp_enabled ? 'Enabled' : 'Disabled'], ['API 认证', health.authentication_required ? 'Enabled' : 'Local mode']].map(([k,v]) => `<div class="connection-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');
}
async function refresh() {
  if (refreshBusy) return;
  refreshBusy = true;
  try {
    const [list, projectList, dashboard] = await Promise.all(['/api/tasks','/api/projects','/api/dashboard'].map(async path => (await api(path)).json()));
    tasks = list; projects = projectList; stats = dashboard; renderAll();
    if (!legacyMigrated) await migrateLegacyData();
    if (selected) await loadDetail(selected);
    else if (tasks.length && ['workflow','reports'].includes(location.hash.slice(1))) await selectTask(tasks[0].id);
    if (!health) await loadHealth();
    $('connection-error').hidden = true; $('service-label').textContent = 'All systems operational'; $('service-dot').classList.remove('red-dot');
  } catch (error) {
    $('connection-error').hidden = false; $('connection-error-text').textContent = error.message;
    $('service-label').textContent = 'Connection required'; $('service-dot').classList.add('red-dot');
    if (!tasks.length) {
      $('dashboard-metrics').innerHTML = ['Projects','Running Tests','Passed','Issues Found'].map(label => metric(label,'—','数据读取失败')).join('');
      $('recent-runs').innerHTML = empty('暂时无法读取任务', '检查连接或访问令牌，然后点击重试。', false);
      $('quality-pulse').innerHTML = empty('等待连接', '成功率在连接后显示。', false);
    }
  } finally { refreshBusy = false; }
}
async function selectTask(id) {
  if (!id) return;
  selected = id; detail = null; events = []; generation++;
  try { sessionStorage.setItem('autoqa-selected-task', id); } catch { /* optional UI selection */ }
  eventAbort?.abort(); eventAbort = new AbortController();
  resetScreenshot(); renderAll(); $('workflow-content').hidden = true;
  $('workflow-empty').innerHTML = '<div class="plan-loading"><span class="spinner"></span> 正在加载执行详情…</div>';
  $('report-content').innerHTML = '<div class="skeleton"></div>';
  await loadDetail(id);
  if (selected === id) streamEvents(id, eventAbort.signal);
}
async function streamEvents(id, signal) {
  let cursor = 0;
  while (!signal.aborted && selected === id) {
    try {
      const r = await api(`/api/tasks/${id}/events?after=${cursor}`, {signal});
      const reader = r.body.getReader(), decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const {done, value} = await reader.read(); if (done) break;
        buffer += decoder.decode(value, {stream:true}); let split;
        while ((split = buffer.indexOf('\n\n')) >= 0) {
          const packet = buffer.slice(0, split); buffer = buffer.slice(split+2);
          const data = packet.split('\n').find(l => l.startsWith('data: '));
          if (data) {
            const event = JSON.parse(data.slice(6)); cursor = event.id;
            if (selected !== id) return;
            events.push(event); events = events.slice(-200); renderEvents();
            if (detail) renderStages(detail);
          }
        }
      }
      await refresh(); if (detail && terminal.has(detail.status)) return;
    } catch { if (signal.aborted) return; }
    await new Promise(resolve => setTimeout(resolve,1500));
  }
}
function renderEvents() {
  $('events').innerHTML = events.slice(-40).map(e => `<div class="event-line"><span>${esc(e.payload.node || e.kind)}<small>${esc(e.payload.message || e.payload.step?.description || e.payload.status || '')}</small></span><time>${time(e.at)}</time></div>`).join('') || '<p class="form-note">等待服务端事件…</p>';
}
function renderStages(task) {
  const stages = workflowStages(task,events).map(stage => { const persisted = task.workflow_steps?.find(s => s.step_name === stage.name); return persisted ? {...stage,state:persisted.status} : stage; });
  $('workflow-stages').innerHTML = stages.map((s,index) => `<article class="stage ${s.state.toLowerCase()}"><div class="stage-top"><span class="stage-icon">${s.state==='Completed' ? '✓' : String(index+1).padStart(2,'0')}</span>${badge(s.state)}</div><h3>${s.name}</h3><p>${esc(s.caption)}</p><div class="stage-progress"></div></article>`).join('');
}
function resetScreenshot() {
  if (screenshotUrl) URL.revokeObjectURL(screenshotUrl);
  screenshotUrl = null; screenshotKey = '';
  $('screenshot-box').innerHTML = '<div class="evidence-empty"><span>▧</span>浏览器截图将在执行后显示</div>';
}
async function renderEvidence(task, version) {
  const last = task.steps.at(-1), image = last?.artifacts?.screenshot;
  $('evidence-status').textContent = last ? `步骤 ${last.index+1} · ${last.duration_ms} ms` : '等待执行';
  document.querySelector('.evidence-panel .small-label').textContent = task.test_type === 'api' ? 'HTTP' : 'BROWSER';
  if (task.test_type === 'api') $('screenshot-box').innerHTML = last?.evidence ? `<pre class="api-evidence">${esc(JSON.stringify(last.evidence,null,2))}</pre>` : '<div class="evidence-empty">审核计划后将展示真实 HTTP 请求与响应证据</div>';
  if (image && screenshotKey !== task.id+image) {
    try {
      const blob = await (await api(`/api/tasks/${task.id}/artifacts/${encodeURIComponent(image)}`)).blob();
      if (version !== generation || task.id !== selected) return;
      if (screenshotUrl) URL.revokeObjectURL(screenshotUrl);
      screenshotUrl = URL.createObjectURL(blob); screenshotKey = task.id+image;
      $('screenshot-box').innerHTML = `<img alt="步骤 ${last.index+1} 的真实浏览器截图" src="${screenshotUrl}">`;
    } catch (error) { if (task.id === selected) $('screenshot-box').innerHTML = `<div class="evidence-empty">${esc(error.message)}<button class="text-button" data-action="refresh">重试加载</button></div>`; }
  } else if (!image && screenshotKey) resetScreenshot();
  if (task.id !== selected) return;
  $('diagnosis-box').innerHTML = task.diagnosis ? `<div class="diagnosis"><span class="small-label">${esc(task.diagnosis.category)}</span><strong>${esc(task.diagnosis.summary)}</strong><p>${esc(task.diagnosis.suggestion)}</p><small>证据归类 · 根因待确认</small></div>` : '';
  const artifacts = (task.artifacts || []).filter(n => ['trace.zip','devtools-mcp.json'].includes(n) || Object.values(last?.artifacts || {}).includes(n));
  $('artifact-links').innerHTML = artifacts.map(n => `<button data-artifact="${esc(n)}">↓ ${esc(n)}</button>`).join('');
}
async function loadDetail(id) {
  const version = generation;
  const task = await (await api(`/api/tasks/${id}`)).json();
  if (selected !== id || version !== generation) return;
  detail = task; $('workflow-empty').innerHTML = ''; $('workflow-content').hidden = false;
  if (!events.length && task.workflow_steps?.length) renderPersistedHistory(task);
  $('detail-id').textContent = id.slice(0,8)+' / '+(task.planner_mode || 'planning');
  const p = planInfo(task);
  $('workflow-context').innerHTML = `<div><span class="small-label">${esc(projectFor(task).name)}</span><h2>${esc(task.title || task.request.goal)}</h2></div>${badge(task.status)}`;
  renderStages(task);
  $('plan-summary').textContent = task.plan?.summary || (terminal.has(task.status) ? '本次任务未生成可执行计划。' : '正在读取页面并生成计划。');
  $('plan-metrics').innerHTML = `<div><span>Test Cases</span><strong>${task.plan ? p.cases : '—'}</strong><small>${p.count} 个操作步骤</small></div><div><span>Covered Modules</span><strong>${task.plan ? p.modules.length : '—'}</strong><small title="${esc(p.modules.join(', '))}">${esc(p.modules.join(', ') || '等待规划')}</small></div><div><span>Risk Level</span>${badge(p.risk)}<small>按写操作与审批要求分级</small></div>`;
  $('cancel-button').hidden = terminal.has(task.status);
  const approval = task.status === 'awaiting_approval'; $('approval-box').hidden = !approval;
  $('approval-box').innerHTML = approval ? `<div class="approval"><span class="approval-symbol">◷</span><div><strong>${task.approval?.scope === 'plan' ? 'Test Plan 已就绪，等待审核' : '关键操作等待审批'}</strong><p>${esc(task.approval?.message)}${task.approval?.step ? ' · '+esc(task.approval.step.description) : ''}</p></div><div class="approval-actions"><button class="primary" data-approval="true">批准并执行 →</button><button class="secondary" data-approval="false">拒绝</button></div></div>` : '';
  $('step-timeline').innerHTML = !task.plan && terminal.has(task.status) ? empty('计划未生成', '查看右侧诊断，检查目标地址或规划服务。', false) : stepTimeline(task);
  if (events.length) renderEvents(); else renderPersistedHistory(task);
  $('report-content').innerHTML = reportView(task, projectFor(task));
  $('report-button').disabled = !terminal.has(task.status);
  $('view-report-button').textContent = terminal.has(task.status) ? '查看测试报告 →' : '预览测试报告 →';
  await renderEvidence(task,version);
}
function updateType() {
  const smoke = $('test-type').value === 'smoke';
  const apiTest = $('test-type').value === 'api';
  $('type-note').textContent = health?.demo_mode ? `Public Demo：每日全局 ${health.demo_daily_limit} 个任务，每个来源 ${health.demo_ip_limit} 个。审核六类计划后执行受控接口；超额仍可查看示例报告。` : apiTest ? 'Login API Testing：Provider 生成六类请求计划，审核后真实执行。内置站点可控的密码校验缺陷用于展示 Finding；无密钥请配置 LLM_PROVIDER=mock。' : smoke ? '冒烟测试使用固定结构化计划：导航到目标地址并断言页面 body 可见，同时检查运行异常。' : '业务回归按测试目标规划；无模型时使用内置场景。写操作先等待审核。';
}
function pickTemplate(name) {
  const t = templates[name], p = name === 'api' ? projects.find(p => p.builtin && p.url.includes('/api/')) : demoProject();
  if (!p) { toast('请先连接项目服务。', true); return; }
  $('project').value = p.id; $('target').value = name === 'api' ? p.url : new URL(t.page, p.url).href; $('goal').value = t.goal;
  $('test-type').value = name === 'api' ? 'api' : 'regression'; $('step-timeout').value = name === 'retry' ? 500 : 8000; $('max-retries').value = name === 'retry' ? 3 : 1;
  document.querySelectorAll('[data-template]').forEach(b => b.classList.toggle('selected', b.dataset.template === name)); updateType();
}
function openTask(projectId) {
  const previousProject = $('project').value;
  const list = allProjects(); $('project').innerHTML = list.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
  if (list.some(p => p.id === previousProject)) $('project').value = previousProject;
  $('create-error').hidden = true;
  if (projectId) { const p = list.find(p => p.id === projectId); if (p) { $('project').value = p.id; $('target').value = p.url; $('goal').value = ''; } }
  else if (!$('target').value) pickTemplate('login');
  if (health?.demo_mode) pickTemplate('api');
  updateType(); $('task-dialog').showModal();
}
async function download(path, name) {
  const blob = await (await api(path)).blob(); const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url),3000);
}
$('task-form').addEventListener('submit',async e => {
  e.preventDefault(); $('run-button').disabled = true; $('run-button').textContent = '正在创建…'; $('create-error').hidden = true;
  let created = false;
  try {
    const request = {project_id:$('project').value, test_type:$('test-type').value, target_url:$('target').value, goal:$('goal').value, auto_approve_demo:$('demo-approve').checked, max_steps:Number($('max-steps').value), max_retries:Number($('max-retries').value), step_timeout_ms:Number($('step-timeout').value), task_timeout_seconds:Number($('task-timeout').value)};
    if ($('test-type').value === 'smoke') request.steps = [{action:'navigate',value:request.target_url,description:'打开目标页面'}, {action:'assert_visible',selector:'body',description:'校验页面主体可见（冒烟测试）'}];
    const task = await (await api('/api/tasks',{method:'POST',body:JSON.stringify(request)})).json(); created = true;
    $('task-dialog').close(); toast('任务已创建，测试计划将在工作流中展示。');
    await refresh(); await selectTask(task.id); navigate('workflow');
  } catch (error) { if (created) toast('任务已创建，详情加载失败：'+error.message, true); else { $('create-error').hidden = false; $('create-error').textContent = error.message; } }
  finally { $('run-button').disabled = false; $('run-button').textContent = '生成测试计划 →'; }
});
$('project-form').addEventListener('submit',async e => {
  e.preventDefault(); $('project-error').hidden = true;
  const url = new URL($('project-url').value);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password || !$('project-name').value.trim()) { $('project-error').textContent = '请输入项目名称和不含登录凭据的 HTTP / HTTPS 地址。'; $('project-error').hidden = false; return; }
  const submit = $('project-form').querySelector('[type="submit"]'); submit.disabled = true;
  try {
    const edit = $('project-form').dataset.edit;
    await api(edit ? `/api/projects/${edit}` : '/api/projects', {method:edit ? 'PATCH' : 'POST',body:JSON.stringify({name:$('project-name').value.trim(),url:url.href,description:$('project-description').value})});
    $('project-dialog').close(); $('project-form').reset(); delete $('project-form').dataset.edit; await refresh(); toast('项目已保存到服务端。');
  } catch (error) { $('project-error').textContent = error.message; $('project-error').hidden = false; }
  finally { submit.disabled = false; }
});
$('project').addEventListener('change',() => { const p = allProjects().find(p => p.id === $('project').value); if (p) $('target').value = p.url; document.querySelectorAll('[data-template]').forEach(b => b.classList.remove('selected')); });
$('test-type').addEventListener('change',updateType);
$('task-search').addEventListener('input',renderTasks);
for (const id of ['workflow-task','report-task']) $(id).addEventListener('change',() => selectTask($(id).value).catch(showError));
$('view-report-button').addEventListener('click',() => navigate('reports'));
$('report-button').addEventListener('click',() => { if (selected) download(`/api/tasks/${selected}/report`,'autoqa-studio-'+selected.slice(0,8)+'.html').catch(showError); });
$('cancel-button').addEventListener('click',async () => {
  $('cancel-button').disabled = true;
  try { await api(`/api/tasks/${selected}/cancel`,{method:'POST'}); await refresh(); toast('任务已取消，证据已保留。'); } catch (error) { showError(error); } finally { $('cancel-button').disabled = false; }
});
$('token-form').addEventListener('submit',async e => { e.preventDefault(); apiToken = $('api-token').value; try { sessionStorage.setItem('autoqa-token',apiToken); } catch { toast('访问令牌仅在当前页面生效。'); } health = null; eventAbort?.abort(); await refresh(); if (selected) { eventAbort = new AbortController(); streamEvents(selected,eventAbort.signal); } });
// Delegation keeps polling-driven components interactive without rebinding handlers.
document.addEventListener('click',async e => {
  const close = e.target.closest('[data-close]'); if (close) $(close.dataset.close).close();
  const template = e.target.closest('[data-template]'); if (template) pickTemplate(template.dataset.template);
  const task = e.target.closest('[data-task]'); if (task) { navigate('workflow'); selectTask(task.dataset.task).catch(showError); }
  const project = e.target.closest('[data-project]'); if (project) openTask(project.dataset.project);
  const editProject = e.target.closest('[data-edit-project]'); if (editProject) { const p = projects.find(p => p.id === editProject.dataset.editProject); $('project-form').dataset.edit=p.id; $('project-name').value=p.name; $('project-url').value=p.url; $('project-description').value=p.description; $('project-error').hidden=true; $('project-dialog').showModal(); }
  const deleteProject = e.target.closest('[data-delete-project]'); if (deleteProject) { deleteProject.disabled=true; try { await api(`/api/projects/${deleteProject.dataset.deleteProject}`,{method:'DELETE'}); await refresh(); toast('项目已归档，任务与报告仍保留。'); } catch (error) { showError(error); } }
  const filterButton = e.target.closest('[data-filter]'); if (filterButton) { filter = filterButton.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active',b===filterButton)); renderTasks(); }
  const risk = e.target.closest('[data-risk-view]'); if (risk) { if (risk.dataset.riskFilter) { filter = risk.dataset.riskFilter; document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active',b.dataset.filter === filter)); renderTasks(); } if (risk.dataset.riskView === 'reports') { const failed = tasks.find(t => t.status === 'failed'); if (failed) selectTask(failed.id).catch(showError); } navigate(risk.dataset.riskView); }
  const action = e.target.closest('[data-action]'); if (action) {
    const a = action.dataset.action;
    if (a === 'refresh') { health = null; await refresh(); }
    if (a === 'new-task') openTask();
    if (a === 'new-project') { delete $('project-form').dataset.edit; $('project-form').reset(); $('project-error').hidden = true; $('project-dialog').showModal(); }
    if (a === 'demo') { openTask(); pickTemplate(health?.demo_mode ? 'api' : 'login'); $('demo-approve').checked = false; }
    if (a === 'workflow') navigate('workflow');
  }
  const approval = e.target.closest('[data-approval]'); if (approval) {
    const id = selected; document.querySelectorAll('[data-approval]').forEach(b => b.disabled = true);
    try { await api(`/api/tasks/${id}/approve`,{method:'POST',body:JSON.stringify({approved:approval.dataset.approval==='true'})}); toast('审批结果已提交。'); await refresh(); }
    catch (error) { showError(error); } finally { document.querySelectorAll('[data-approval]').forEach(b => b.disabled = false); }
  }
  const artifact = e.target.closest('[data-artifact]'); if (artifact) download(`/api/tasks/${selected}/artifacts/${encodeURIComponent(artifact.dataset.artifact)}`,artifact.dataset.artifact).catch(showError);
});
window.addEventListener('hashchange',() => showView(location.hash.slice(1)));
window.addEventListener('pagehide',() => { eventAbort?.abort(); clearTimeout(pollTimer); if (screenshotUrl) URL.revokeObjectURL(screenshotUrl); });
$('templates').innerHTML = Object.entries(templates).map(([key,t]) => `<button type="button" class="template" data-template="${key}">${t.name}</button>`).join('');
function clock() { $('clock').textContent = new Date().toLocaleDateString('zh-CN',{month:'short',day:'numeric'}); }
async function poll() { await refresh(); pollTimer = setTimeout(poll,document.hidden ? 10000 : 2000); }
renderAll(); showView(location.hash.slice(1)); clock(); poll();

function renderPersistedHistory(task) {
  $('events').innerHTML = task.workflow_steps.map(s => `<div class="event-line"><span>${esc(s.step_name)} · ${esc(s.status)}<small>${esc(s.output?.error || s.output?.summary || '')}</small></span><time>${time(s.finished_at || s.started_at)}</time></div>`).join('');
}

async function migrateLegacyData() {
  // One-time import only: these keys are never used as the new business database.
  let oldProjects, oldTasks;
  try { oldProjects = JSON.parse(localStorage.getItem('autoqa-studio-projects') || '[]'); oldTasks = JSON.parse(localStorage.getItem('autoqa-studio-tasks') || '{}'); }
  catch { legacyMigrated = true; return; }
  const mapping = {'builtin-demo':demoProject()?.id};
  if (Array.isArray(oldProjects)) for (const p of oldProjects) {
    if (!p?.name || !p?.url) continue;
    let persisted = projects.find(v => v.name === p.name && v.url === p.url);
    if (!persisted) persisted = await (await api('/api/projects',{method:'POST',body:JSON.stringify({name:p.name,url:p.url,description:'Imported from the previous browser workspace'})})).json();
    mapping[p.id] = persisted.id;
  }
  for (const [id, meta] of Object.entries(oldTasks || {})) if (mapping[meta?.projectId] && tasks.some(t => t.id === id)) {
    await api(`/api/tasks/${id}/project`,{method:'PATCH',body:JSON.stringify({project_id:mapping[meta.projectId]})});
  }
  try { localStorage.removeItem('autoqa-studio-projects'); localStorage.removeItem('autoqa-studio-tasks'); } catch { /* read-only browser storage */ }
  legacyMigrated = true;
}
