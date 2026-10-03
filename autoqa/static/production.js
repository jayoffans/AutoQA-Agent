import {loadLocale, preferredLocale, locale, t} from './i18n.js';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text = key => esc(t(key));
const json = value => esc(JSON.stringify(value, null, 2));
const date = value => value ? new Date(value.endsWith('Z') ? value : value + 'Z').toLocaleString(locale(), {timeZone:'Asia/Shanghai'}) : '—';
let me = null, csrf = '', workspace = '', projects = [], tasks = [], stats = {}, model = {}, selectedProject = '', selectedTask = '', detail = null;
let authMode = 'login', currentView = 'dashboard', pollBusy = false;
let refreshing = null, identityVersion = 0;
let onboarding = null, providerConfigs = [];
let apiModeProject = '', apiNotice = null;
let plannerDetailId = '', plannerSpecId = '';
const planningProjects = new Set();
const plannerFailures = new Map();
const executingPlans = new Set();
const apiRunSelections = new Map();
let apiRunRenderVersion = 0;
let reportRunId = '', reportData = null, reportRenderVersion = 0;
const reportSelection = new Set(), reportEvidence = new Map();
let reportAnalyzing = false;
const views = ['dashboard','projects','tasks','workflow','findings','reports','model'];
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 5000); }
async function api(path, method = 'GET', body) {
  const version = identityVersion;
  const multipart = body instanceof FormData;
  const send = () => fetch(path, {method, credentials:'same-origin', headers:{'X-Workspace-ID':workspace, 'X-CSRF-Token':csrf,
    ...(body !== undefined && !multipart ? {'Content-Type':'application/json'} : {})}, ...(body !== undefined ? {body:multipart?body:JSON.stringify(body)} : {})});
  let response = await send();
  if (response.status === 401 && (!path.startsWith('/api/auth/') || path === '/api/auth/me')) {
    if (!refreshing) refreshing = fetch('/api/auth/refresh', {method:'POST',credentials:'same-origin',
      headers:{'X-CSRF-Token':csrf}}).then(async result => {
        if (!result.ok) throw Error(t('sessionExpired'));
        const data = await result.json();
        if (version === identityVersion) csrf = data.csrf_token;
      }).finally(() => refreshing = null);
    try { await refreshing; if (version === identityVersion) response = await send(); } catch {}
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    if (response.status === 401 && me && version === identityVersion) { resetIdentity(); shell(); }
    const isPlanner = path.endsWith('/test-plans/generate') && !!error.detail?.code;
    const failure = Error(isPlanner ? plannerFailure(error.detail) : error.detail?.message || error.detail || t('requestFailed'));
    if (isPlanner) failure.planner = {plan_id:error.detail.plan_id, message:failure.message};
    throw failure;
  }
  if (version !== identityVersion) throw Error(t('requestFailed'));
  return response.status === 204 ? null : response.json();
}
function resetIdentity() {
  plannerFailures.clear();
  identityVersion++; me=null; workspace=''; csrf=''; projects=[]; tasks=[]; stats={}; model={};
  onboarding=null; providerConfigs=[];
  apiModeProject=''; apiNotice=null;
  plannerDetailId=''; plannerSpecId=''; apiRunSelections.clear(); apiRunRenderVersion++;
  reportRunId=''; reportData=null; reportSelection.clear(); reportEvidence.clear(); reportRenderVersion++; reportAnalyzing=false;
  selectedTask=''; selectedProject=''; detail=null; currentView='dashboard'; authMode='login';
  clearPasswords(); $('dialog').close(); $('dialog').innerHTML='';
}
const button = (key, action, id = '', kind = 'secondary') => `<button class="${kind}" type="button" data-action="${action}" data-id="${esc(id)}">${text(key)}</button>`;
const field = (key, name, value = '', type = 'text', required = false) => `<label>${text(key)}<input name="${name}" type="${type}" value="${esc(value)}" ${required ? 'required' : ''} ${type === 'password' ? 'autocomplete="off"' : ''}></label>`;
const area = (key, name, value = '', rows = 3) => `<label>${text(key)}<textarea name="${name}" rows="${rows}">${esc(value)}</textarea></label>`;
const jarea = (key, name, value = {}) => `<label>${text(key)}<textarea class="json-editor" name="${name}">${json(value)}</textarea></label>`;
const languageSelect = () => `<label class="sr-only" for="language">${text('switchLanguage')}</label><select id="language"><option value="zh-CN" ${locale()==='zh-CN'?'selected':''}>简体中文</option><option value="en-US" ${locale()==='en-US'?'selected':''}>English</option></select>`;
function empty(title, subtitle = '', action = '') { return `<section class="panel empty-product"><h2>${text(title)}</h2><p>${text(subtitle)}</p>${action ? button(subtitle, action, '', 'primary') : ''}</section>`; }
function badge(status) { return `<span class="badge ${esc(status)}">${text(status)}</span>`; }
function metric(key, value) { return `<article class="panel metric"><span>${text(key)}</span><strong>${esc(value ?? 0)}</strong></article>`; }
function shell() {
  if (!me) {
    $('app').innerHTML = `<section class="panel auth-shell"><div class="language-control">${languageSelect()}</div><div class="brand">AutoQA Studio</div><h1>${text('platformTitle')}</h1><p>${text('loginNote')}</p>
      <form id="auth-form">${field('email','email','','email',true)}${authMode==='register'?field('nickname','nickname'):''}${field('password','password','','password',true)}<small>${text('passwordHint')}</small>
      <div class="button-row"><button class="primary" type="submit">${text(authMode)}</button>${button(authMode==='login'?'register':'login','auth-mode')}</div></form></section>`;
    return;
  }
  routeView();
  if (needsOnboarding() && currentView !== 'model') { renderOnboarding(); return; }
  $('app').innerHTML = `<aside class="sidebar product-sidebar"><a class="brand" href="#dashboard"><span class="brandmark">A<span>↗</span></span><span>AutoQA <b>Studio</b></span></a>
    <label for="workspace-selector">${text('workspace')}</label><select id="workspace-selector">${me.workspaces.map(w=>`<option value="${esc(w.id)}" ${w.id===workspace?'selected':''}>${esc(w.name === '个人工作区' ? t('personal') : w.name)}</option>`).join('')}</select>
    <nav>${views.filter(v=>v!=='model').map(v=>`<a class="nav-item ${v===currentView?'active':''}" href="#${v}">${text(v)}</a>`).join('')}<p class="nav-section">${text('settings')}</p><a class="nav-item ${currentView==='model'?'active':''}" href="#settings/providers">${text('model')}</a></nav>
    <div class="sidebar-bottom"><p>${esc(me.nickname)}</p><p>${esc(me.email)}</p>${button('logout','logout')}</div></aside>
    <main id="main"><header class="topbar"><strong id="page-title">${text(currentView)}</strong><div class="product-top">${languageSelect()}${button('refresh','refresh')}</div></header><div class="content product-content"><div id="content"></div></div></main>`;
  render();
}
function needsOnboarding() { return !!onboarding && !onboarding.onboarding_completed; }
function routeView() {
  const requested = location.hash.slice(1), modelPage = ['model','settings/providers'].includes(requested);
  const planPage = requested.match(/^projects\/([A-Za-z0-9-]+)\/test-plans\/([A-Za-z0-9-]+)$/);
  const apiPage = requested.match(/^projects\/([A-Za-z0-9-]+)\/api-testing$/) || planPage;
  const reportPage = requested.match(/^test-runs\/([A-Za-z0-9-]+)\/report$/);
  if (reportRunId !== (reportPage?.[1] || '')) reportAnalyzing=false;
  reportRunId = reportPage?.[1] || '';
  plannerDetailId = planPage?.[2] || '';
  apiModeProject = apiPage?.[1] || '';
  if (apiPage) selectedProject = apiPage[1];
  currentView = needsOnboarding() && !(onboarding.step===2 && modelPage) ? 'onboarding' : modelPage ? 'model' : reportPage ? 'reports' : apiPage ? 'projects' : views.includes(requested) ? requested : 'dashboard';
  const hash = currentView==='model' ? 'settings/providers' : (apiPage && currentView==='projects') || (reportPage && currentView==='reports') ? requested : currentView;
  if (location.hash !== '#'+hash) history.replaceState(null, '', '#'+hash);
}
function renderOnboarding() {
  const step = onboarding.step;
  const steps = ['onboardingWelcomeStep','onboardingModelStep','onboardingProjectStep','onboardingFinishStep'];
  let body = '';
  if (step === 1) body = `<div class="setup-symbol" aria-hidden="true">A↗</div><h1>${text('onboardingWelcome')}</h1><p class="setup-subtitle">${text('onboardingTagline')}</p><p>${text('onboardingWelcomeNote')}</p>${button('onboardingStart','onboarding-start','','primary')}`;
  else if (step === 2) body = `<div class="setup-symbol" aria-hidden="true">✦</div><h1>${text('onboardingModelTitle')}</h1><p class="setup-subtitle">${text('onboardingModelNote')}</p><p>${text('onboardingModelOptional')}</p>
    <div class="button-row">${button('onboardingConfigureModel','onboarding-model','','primary')}${button('onboardingLater','onboarding-skip-model')}</div>`;
  else if (step === 3) body = `<div class="setup-symbol" aria-hidden="true">◇</div><h1>${text('onboardingProjectTitle')}</h1><p>${text('onboardingProjectNote')}</p>
    <form id="onboarding-project-form">${field('name','name','','text',true)}<label>${text('onboardingTestType')}<select name="project_type"><option value="api">${text('onboardingApi')}</option><option value="web">${text('onboardingWeb')}</option></select></label>
    <button class="primary" type="submit">${text('onboardingCreateProject')}</button></form>`;
  else body = `<div class="setup-symbol setup-success" aria-hidden="true">✓</div><h1>${text('onboardingReady')}</h1><p class="setup-subtitle">${text('onboardingReadyNote')}</p><p>${text('onboardingNextNote')}</p>${button('onboardingEnterDashboard','onboarding-complete','','primary')}`;
  $('app').innerHTML = `<div class="setup-shell"><header class="setup-header"><div class="brand">AutoQA <b>Studio</b></div><div class="product-top">${languageSelect()}${button('logout','logout')}</div></header>
    <section class="setup-layout"><div class="setup-intro"><span class="setup-kicker">${text('onboardingFirstUse')}</span><h2>${text('onboardingIntro')}</h2><p>${esc(me.workspaces.find(w=>w.id===onboarding.workspace_id)?.name)}</p>
    <ol class="setup-steps" aria-label="${text('onboardingSteps')}">${steps.map((key,i)=>`<li class="${i+1<step?'done':i+1===step?'active':''}" ${i+1===step?'aria-current="step"':''}><span aria-hidden="true">${i+1<step?'✓':i+1}</span>${text(key)}</li>`).join('')}</ol></div>
    <section class="panel setup-card" aria-labelledby="setup-step-label"><span id="setup-step-label" class="setup-kicker">${esc(t('onboardingStepCount',{step}))}</span>${body}</section></section></div>`;
  const nameInput = document.querySelector('#onboarding-project-form [name="name"]');
  if (nameInput) nameInput.maxLength = 80;
}
function firstUseProgress() {
  const ownWorkspace = onboarding.workspace_id === workspace;
  const items = [['onboardingAccountDone',true,''],['onboardingWorkspaceDone',true,''],
    ['onboardingConfigureModel',onboarding.model_configured,'model'],
    ['onboardingCreateProject',onboarding.project_created,'new-project'],
    ['onboardingFirstTest',onboarding.first_test_completed,'new-task']];
  return `<section class="panel product-panel first-use-progress"><div class="progress-heading"><div><span class="setup-kicker">${text('onboardingFirstUse')}</span><h2>${text('onboardingProgress')}</h2></div><span>${esc(t('onboardingProgressCount',{done:items.filter(item=>item[1]).length,total:items.length}))}</span></div>
    <p>${text('onboardingProgressNote')}</p><ul>${items.map(([key,done,action])=>`<li class="${done?'done':'pending'}"><span class="progress-check" aria-hidden="true">${done?'✓':'○'}</span><span>${text(key)}</span><small>${text(done?'onboardingDone':'onboardingPending')}</small>${!done && ownWorkspace?button('onboardingContinue',action):''}</li>`).join('')}</ul></section>`;
}
function table(rows, showProject = true) {
  if (!rows.length) return empty('noTasks','firstTask','new-task');
  return `<div class="table-container"><table class="product-table"><thead><tr><th>${text('title')}</th>${showProject?`<th>${text('project')}</th>`:''}<th>${text('status')}</th><th>${text('passed')} / ${text('failed')}</th><th>${text('time')}</th><th></th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.title)}</td>${showProject?`<td>${esc(row.project_name)}</td>`:''}<td>${badge(row.status)}</td><td>${row.metrics?.passed ?? 0} / ${row.metrics?.failed ?? 0}</td><td>${date(row.run?.started_at || row.created_at)}</td><td>${button('view','task',row.id)}</td></tr>`).join('')}</tbody></table></div>`;
}
function render() {
  if (me && needsOnboarding() && currentView !== 'model') { renderOnboarding(); return; }
  if (!me || !$('content')) return;
  document.querySelectorAll('.nav-item').forEach(a=>a.classList.toggle('active', a.hash===(currentView==='model'?'#settings/providers':'#'+currentView)));
  $('page-title').textContent = t(plannerDetailId?'plannerDetail':apiModeProject?'apiTesting':currentView);
  const owner = me.workspaces.find(w=>w.id===workspace)?.role === 'owner';
  let content = `<div class="page-heading"><div><h1>${text(plannerDetailId?'plannerDetail':apiModeProject?'apiTesting':currentView)}</h1><p>${text(plannerDetailId?'plannerDetailHint':apiModeProject?'apiSpecHint':'productNote')}</p></div>${apiModeProject?'':currentView==='model'?(owner?button('addModel','add-model','','primary'):''):button(currentView==='projects'?'newProject':'newTask',currentView==='projects'?'new-project':'new-task','','primary')}</div>`;
  if (!model.configured && currentView !== 'model' && !apiModeProject && !reportRunId) content += `<div class="product-notice">${text('modelMissing')} ${button('goModel','model')}</div>`;
  if (currentView === 'dashboard') {
    if (model.configured) content += `<div class="product-notice model-state" role="status">${text(providerConfigs.find(p=>p.is_active)?.status==='connected'?'modelConnected':'modelConfigured')} ${esc(model.model)} ${button('model','model')}</div>`;
    content += `<section class="panel product-panel"><h2>${text('welcomeBack')}：${esc(me.nickname)}</h2><p>${text('currentWorkspace')}：${esc(me.workspaces.find(w=>w.id===workspace)?.name)}</p></section>`;
    if (onboarding && !onboarding.first_test_completed) content += firstUseProgress();
    if (projects.length || tasks.length || !onboarding) {
      content += `<div class="metrics">${metric('projects',stats.projects)}${metric('tasks',stats.tasks)}${metric('issues',stats.issues)}${metric('executions',stats.executions)}</div>`;
      content += `<div class="metrics">${metric('today',stats.today_runs)}${metric('passed',stats.passed)}${metric('failed',stats.failed)}${metric('system_error',stats.system_errors)}</div>`;
    }
    if (!projects.length) content += empty('noProjects','firstProject','new-project');
    if (tasks.length) content += `<section class="panel product-panel"><h2>${text('history')}</h2>${table(tasks.slice(0,10))}</section>`;
  } else if (currentView === 'projects') {
    if (!apiModeProject) content += projects.length ? `<div class="project-grid">${projects.map(p=>`<article class="panel project-card"><h2>${esc(p.name)}</h2><p>${text(p.project_type)} · ${text(p.environment)}</p><p class="mono">${p.url?esc(p.url):text('onboardingTargetPending')}</p><p>${esc(p.description)}</p><div class="button-row">${button('open','project',p.id)}${button('newTask','new-task',p.id)}</div></article>`).join('')}</div>` : empty('noProjects','firstProject','new-project');
    if (selectedProject) content += '<div id="project-detail"></div>';
  } else if (currentView === 'tasks') content += table(tasks);
  else if (currentView === 'model') content += modelForm();
  else if (currentView === 'findings') {
    const findings = tasks.flatMap(row=>row.findings.map(f=>({...f, task:row})));
    content += findings.length ? findings.map(f=>`<article class="finding"><h2>${esc(f.title)}</h2><p>${text('severity')}: ${text(f.severity)} · ${text('unconfirmed')}</p><p>${text('affected')}: ${esc(f.evidence?.affected || f.task.request.target_url)}</p><h3>${text('aiSuggestion')}</h3><p>${esc(f.suggestion)}</p><details><summary>${text('findingEvidence')}</summary><pre>${json(f.evidence)}</pre></details>${button('view','task',f.task.id)}</article>`).join('') : empty('noFindings');
  } else if (currentView === 'reports') {
    if (reportRunId) content = '<div id="api-quality-report" aria-live="polite"></div>';
    else {
    const reports = tasks.filter(row=>row.report);
    content += reports.length ? `<p>${text('reportNote')}</p>${table(reports)}` : empty('noReports');
    if (detail?.report) content += `<section class="panel product-panel"><h2>${esc(detail.title)}</h2>${button('downloadReport','report',detail.id)}</section>`;
    }
  } else if (currentView === 'workflow') content += workflowView();
  $('content').innerHTML = content;
  if (reportRunId) loadApiReport(reportRunId).catch(e=>toast(e.message));
}
function modelForm() {
  const role = me.workspaces.find(w=>w.id===workspace)?.role;
  const names = {deepseek:'DeepSeek',openai:'OpenAI',compatible:t('compatibleProvider')};
  return `${needsOnboarding()?`<div class="product-notice">${text('onboardingModelCenter')} ${button('onboardingBack','back-onboarding')}${model.configured?button('onboardingNext','onboarding-skip-model','','primary'):''}</div>`:''}
    <section class="panel product-panel model-summary"><h2>${text('currentModel')}</h2><p>${model.configured?esc(model.model):text('modelMissing')}</p><p>${text('modelNotice')}</p><p>${text('encryptedNotice')}</p><p>${text('sessionNotice')}</p>${role!=='owner'?`<p>${text('ownerOnly')}</p>`:''}</section>
    <div class="provider-grid">${providerConfigs.map(p=>`<article class="panel product-panel provider-card" data-provider-id="${esc(p.id)}"><div class="provider-heading"><h2>${esc(names[p.provider] || p.provider)}</h2>${p.is_active?`<span class="badge passed">${text('activeModel')}</span>`:`<span class="badge">${text('standbyModel')}</span>`}</div>
      <dl><div><dt>${text('modelName')}</dt><dd>${esc(p.model)}</dd></div><div><dt>${text('status')}</dt><dd class="provider-status ${esc(p.status)}">${text('providerStatus_'+p.status)}</dd></div><div><dt>${text('apiBaseUrl')}</dt><dd class="mono">${esc(p.base_url)}</dd></div><div><dt>${text('apiKey')}</dt><dd class="mono">${esc(p.masked_key)}</dd></div><div><dt>${text('saveMode')}</dt><dd>${text(p.save_mode==='session'?'sessionOnly':'workspaceSave')}</dd></div></dl>
      ${role==='owner'?`<div class="button-row">${button('testConnection','test-saved-model',p.id)}${!p.is_active?button('setActiveModel','activate-model',p.id):''}${button('delete','delete-model',p.id)}</div>`:''}</article>`).join('')}</div>
    ${role==='owner'?`<section class="panel product-panel"><h2>${text('members')}</h2><form id="member-form">${field('memberEmail','email','','email',true)}<input type="hidden" name="role" value="member"><button class="secondary" type="submit">${text('addMember')}</button></form></section>`:''}`;
}
function modelDialog() {
  $('dialog').innerHTML = `<form id="model-form"><div class="dialog-heading"><h2>${text('addModel')}</h2>${button('cancel','close')}</div><div class="dialog-body"><p>${text('modelNotice')}</p><div class="product-form-grid"><label>${text('provider')}<select name="provider" id="provider-select"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="compatible">${text('compatibleProvider')}</option></select></label>${field('modelName','model','deepseek-chat','text',true)}</div>
    ${field('apiBaseUrl','base_url','https://api.deepseek.com','url',true)}${field('apiKey','api_key','','password',true)}<fieldset><legend>${text('saveMode')}</legend><label><input type="radio" name="save_mode" value="session">${text('sessionOnly')}</label><label><input type="radio" name="save_mode" value="workspace" checked>${text('workspaceSave')}</label></fieldset><p>${text('connectionCostNote')}</p></div>
    <div class="dialog-footer">${button('testConnection','test-model')}<button class="primary" type="submit">${text('saveConfig')}</button></div></form>`;
  $('model-form').elements.api_key.minLength = 8;
  $('model-form').elements.api_key.maxLength = 512;
  $('model-form').elements.model.maxLength = 160;
  $('dialog').showModal();
}
function workflowView() {
  if (!detail) return empty('noExecution');
  const plan = detail.plan;
  const review = detail.status==='awaiting_approval';
  return `<section class="panel product-panel"><h2>${esc(detail.title)} ${badge(detail.status)}</h2><p>${esc(detail.request.goal)}</p><div class="workflow-strip">${detail.workflow_steps.map(s=>`<div class="workflow-chip">${esc(t(s.step_name))}<br>${badge(s.status)}</div>`).join('')}</div>
    ${plan?`<h3>${text('plan')}</h3><p>${esc(plan.summary)}</p><p>${text('scope')}: ${esc(plan.scope)}</p><p>${text('preconditions')}: ${esc((plan.preconditions||[]).join(' · '))}</p>`:''}
    ${review ? `<p class="product-notice">${text('reviewNote')}</p>${detail.approval?.scope==='plan' ? `<label>${text('planEditor')}<textarea id="plan-editor" class="json-editor" rows="20">${json(plan)}</textarea></label>
      <div id="case-controls">${(plan.cases || plan.steps || []).map((c,i)=>`<div class="case-row"><span>${esc(c.case_id || i+1)}</span><strong>${esc(c.name || c.description)}</strong>${button('deleteCase','delete-case',i)}</div>`).join('')}</div><div class="button-row">${button('addCase','add-case')}${button('savePlan','save-plan')}${button('regenerate','regenerate',detail.id)}</div>`:''}
      <div class="button-row">${button('approve','approve',detail.id,'primary')}${button('reject','reject',detail.id)}</div>`:''}
    ${!['passed','failed','system_error','cancelled','interrupted'].includes(detail.status)?button('cancel','cancel-task',detail.id):''}</section>
    <section class="panel product-panel"><h2>${text('executionResults')}</h2><p>${text('evidencePrivacy')}</p><div class="table-container"><table class="product-table"><thead><tr><th>${text('caseId')}</th><th>${text('caseName')}</th><th>${text('status')}</th><th>${text('expected')}</th><th>${text('actual')}</th><th>${text('duration')}</th><th>${text('evidence')}</th></tr></thead><tbody>${(plan?.steps || []).map((s,i)=>{
      const result = [...detail.steps].reverse().find(r=>r.index===i);
      const artifacts = result?.artifacts || {};
      return `<tr><td>${esc(s.case_id || i+1)}</td><td>${esc(s.description)}</td><td>${badge(result?.status || (result ? result.ok?'passed':'failed' : ['passed','failed','system_error','cancelled','interrupted'].includes(detail.status)?'skipped':'waiting'))}</td><td><pre>${json(result?.expected || s)}</pre></td><td><pre>${json(result?.actual || result?.error || '')}</pre></td><td>${result?.duration_ms ?? '—'} ms</td><td>${Object.values(artifacts).filter(a=>/\.(json|html|png)$/.test(a)).map(a=>button('open','artifact',a)).join('')}</td></tr>`;
    }).join('')}</tbody></table></div>${detail.report?button('downloadReport','report',detail.id):''}</section>`;
}
async function refresh() {
  if (!me) return;
  const version = identityVersion, activeWorkspace = workspace;
  const previousProgress = JSON.stringify(onboarding);
  const data = await Promise.all([api('/api/projects'),api('/api/tasks'),api('/api/dashboard'),api('/api/provider'),api('/api/onboarding'),api('/api/settings/providers')]);
  if (!me || version !== identityVersion || activeWorkspace !== workspace) return;
  const wasOnboarding = needsOnboarding();
  [projects,tasks,stats,model,onboarding,providerConfigs] = data;
  if (selectedTask) detail = tasks.find(row=>row.id===selectedTask) || null;
  // Initial background reads must not replace the setup form while the user is typing.
  if (needsOnboarding() && previousProgress === JSON.stringify(onboarding) && document.querySelector('.setup-shell')) return;
  if (wasOnboarding !== needsOnboarding()) { routeView(); shell(); } else render();
  if (currentView==='projects' && selectedProject) await projectDetail(selectedProject);
}
async function projectDetail(id) {
  const project = projects.find(p=>p.id===id);
  if (!project || !$('project-detail')) return;
  if (plannerDetailId && apiModeProject===id) { await plannerDetail(plannerDetailId,id); return; }
  if (apiModeProject===id && project.project_type==='api') { await apiTesting(id); return; }
  const [env,endpoints,history] = await Promise.all([api(`/api/projects/${id}/environment`),api(`/api/projects/${id}/endpoints`),api(`/api/projects/${id}/runs`)]);
  $('project-detail').innerHTML = `<section class="panel product-panel"><h2>${esc(project.name)}</h2><div class="button-row">${button('edit','edit-project',id)}${button('archive','archive-project',id)}${project.project_type==='api'?button('apiTesting','api-testing',id,'primary'):''}</div><h3>${text('history')}</h3>${history.length?table(history,false):`<p>${text('noHistory')}</p>`}</section>
    ${project.project_type==='api'?`<section class="panel product-panel"><h2>${text('openapi')}</h2><form id="openapi-form">${field('openapiUrl','url','','url')}<button class="primary" type="submit">${text('import')}</button></form><label>${text('upload')}<input id="openapi-file" type="file" accept=".json,.yaml,.yml"></label><h3>${text('endpoints')}</h3><pre>${esc(endpoints.map(e=>e.method+' '+e.path+' '+e.summary).join('\n'))}</pre><form id="manual-form">${jarea('manualEndpoints','endpoints',endpoints.length?endpoints:[{path:'/users',method:'GET',summary:'',parameters:[],request_body:{},responses:{}}])}<button class="secondary" type="submit">${text('manualSave')}</button></form></section>`:''}
    <section class="panel product-panel"><h2>${text('environmentConfig')}</h2><form id="environment-form">${field('baseUrl','base_url',env.base_url,'url',true)}${jarea('headers','headers',env.headers)}${jarea('variables','variables',env.variables)}${jarea('secrets','secrets')}
      <p>${text('secretNames')}: ${esc(env.secret_names.join(', ') || '—')}</p><h3>${text('account')}</h3>${env.has_account?`<p>${text('accountSaved')}</p>`:''}<div class="product-form-grid">${field('username','username')}${field('password','password','','password')}</div><p>${text('secretKeep')}</p><div class="button-row"><button class="primary" type="submit">${text('save')}</button>${button('deleteConfig','delete-environment',id)}</div></form></section>`;
}
async function apiTesting(id) {
  const version=identityVersion;
  const [specs,endpoints,plans]=await Promise.all([api(`/api/projects/${id}/api-specs`),api(`/api/projects/${id}/api-endpoints`),api(`/api/projects/${id}/test-plans`)]);
  if (version!==identityVersion || selectedProject!==id || apiModeProject!==id || plannerDetailId || !$('project-detail')) return;
  if (!specs.some(s=>s.id===plannerSpecId)) plannerSpecId=specs.at(-1)?.id || '';
  const busy=planningProjects.has(workspace+':'+id);
  const failure=plannerFailures.get(workspace+':'+id);
  $('project-detail').innerHTML=`<section class="panel product-panel"><div class="progress-heading"><h2>${esc(projects.find(p=>p.id===id)?.name)}</h2>${button('backProject','back-project',id)}</div>
    <form id="api-spec-upload-form" data-project-id="${esc(id)}"><label>${text('apiChooseFile')}<input name="file" type="file" accept=".json,.yaml,.yml" required></label><p>${text('apiSpecHint')}</p><button class="primary" type="submit">${text('uploadApiSpec')}</button></form>
    ${apiNotice?.project_id===id?`<p class="api-import-success" role="status">${text('apiParsed')} · ${text('endpointCount')}: ${esc(apiNotice.count)}</p>`:''}
    <div class="progress-heading"><h3>${text('endpointCount')}: ${endpoints.length}</h3><button type="button" class="primary" data-action="generate-api-plan" data-id="${esc(id)}" ${!specs.length||!model.configured||busy?'disabled':''}>${text(busy?'plannerGenerating':'generatePlan')}</button></div>
    ${specs.length?`<label>${text('plannerSpec')}<select id="planner-spec">${specs.map(s=>`<option value="${esc(s.id)}" data-endpoint-count="${esc(s.endpoint_count)}" ${s.id===plannerSpecId?'selected':''}>${esc(s.name)} · ${esc(s.version)}</option>`).join('')}</select></label>`:''}
    <p id="planner-budget">${esc(plannerBudget(specs.find(s=>s.id===plannerSpecId)?.endpoint_count || 0))}</p>
    ${failure?`<p class="planner-failure" role="alert">${esc(failure.message)}</p>`:''}
    <p class="planner-notice" role="status">${text(busy?'plannerGenerating':!model.configured?'plannerMissingModel':'plannerHint')}</p>${!model.configured?button('goModel','model'):''}</section>
    <section class="panel product-panel"><h2>${text('plannerPlans')}</h2>${plans.length?`<div class="planner-list">${plans.map(p=>`<article class="planner-card" data-plan-id="${esc(p.id)}"><div class="progress-heading"><h3>${date(p.created_at)}</h3><span class="badge ${esc(p.status)}">${text('plannerStatus_'+p.status)}</span></div><p>${esc(p.provider)} · ${esc(p.model)} · ${text('plannerCaseCount')}: ${p.case_count}</p>${plannerCounts(p.category_counts)}${p.status==='failed'?`<p>${text('plannerFailed')}</p>`:''}${button('view','view-api-plan',p.id)}</article>`).join('')}</div>`:`<p>${text('plannerEmpty')}</p>`}</section>
    <section class="panel product-panel"><h2>${text('apiDocuments')}</h2>${specs.length?`<ul class="api-spec-list">${specs.map(s=>`<li data-spec-id="${esc(s.id)}"><span><strong>${esc(s.name)}</strong> · ${esc(s.version)} · ${esc(s.format.toUpperCase())} · ${text('endpointCount')}: ${s.endpoint_count}</span>${button('delete','delete-api-spec',s.id)}</li>`).join('')}</ul>`:`<p>${text('apiNoDocuments')}</p>`}</section>
    <section class="panel product-panel"><h2>${text('endpoints')}</h2>${endpoints.length?`<div class="api-endpoint-table"><table class="product-table"><thead><tr><th>${text('apiMethod')}</th><th>${text('apiPath')}</th><th>${text('apiSummary')}</th><th>${text('apiParameters')}</th></tr></thead><tbody>${endpoints.map(e=>`<tr><td><strong>${esc(e.method)}</strong></td><td class="mono">${esc(e.path)}</td><td>${esc(e.summary)}</td><td><details><summary>${text('apiParameters')} (${e.parameters.length})</summary><pre>${json(e.parameters)}</pre><h4>${text('apiRequestSchema')}</h4><pre>${json(e.request_schema)}</pre><h4>${text('apiResponseSchema')}</h4><pre>${json(e.response_schema)}</pre></details></td></tr>`).join('')}</tbody></table></div>`:`<p>${text('noEndpoints')}</p>`}</section>`;
}
function plannerCounts(counts) {
  return `<div class="planner-counts">${['positive','negative','boundary','security'].map(key=>`<span>${text('plannerCategory_'+key)} <strong>${esc(counts[key] || 0)}</strong></span>`).join('')}</div>`;
}
function plannerBudget(count) {
  return t('plannerBudget',{count,batch:2,calls:Math.ceil(count/2)});
}
function plannerFailure(detail) {
  const codes=['json_parse','schema_validation','unknown_endpoint','duplicate_case','missing_category_coverage',
    'endpoint_case_limit','context_too_large','provider_timeout','provider_failure','persistence_failure','planner_timeout'];
  const code=codes.includes(detail.code)?detail.code:'planner_failure';
  const messages=[t('plannerError_'+code),t('plannerZeroSaved')];
  const categories=['positive','negative','boundary','security'];
  for(const batch of detail.diagnostics?.batches || []) {
    for(const endpoint of batch.endpoints || []) {
      if(!batch.endpoint_ids?.includes(endpoint.endpoint_id)) continue;
      if(code==='endpoint_case_limit' && endpoint.case_count>12) messages.push(t('plannerLimitDetail',{
        endpoint:endpoint.endpoint_id,count:endpoint.case_count,limit:12}));
      if(code==='missing_category_coverage' && endpoint.missing_categories?.length) messages.push(t('plannerCoverageDetail',{
        endpoint:endpoint.endpoint_id,categories:endpoint.missing_categories.filter(c=>categories.includes(c)).map(c=>t('plannerCategory_'+c)).join(', ')}));
    }
  }
  if(detail.diagnostics) {
    const usage=detail.diagnostics.known_usage;
    messages.push(t('plannerUsage',{status:t('plannerUsage_'+detail.diagnostics.usage_status),
      tokens:Number.isInteger(usage?.total_tokens)?usage.total_tokens:t('plannerUsage_unknown')}));
  }
  return messages.join(' ');
}
async function plannerDetail(planId,projectId) {
  const version=identityVersion;
  const [plan,cases,endpoints,preview,runs]=await Promise.all([api(`/api/test-plans/${planId}`),api(`/api/test-plans/${planId}/cases`),api(`/api/projects/${projectId}/api-endpoints`),api(`/api/test-plans/${planId}/execution-preview`),api(`/api/test-plans/${planId}/runs`)]);
  if(version!==identityVersion || plannerDetailId!==planId || !$('project-detail')) return;
  if(plan.project_id!==projectId) throw Error(t('requestFailed'));
  $('project-detail').innerHTML=`<section class="panel product-panel"><div class="progress-heading"><h2>${text('plannerDetail')}</h2>${button('plannerBack','api-testing',projectId)}</div><p><span class="badge ${esc(plan.status)}">${text('plannerStatus_'+plan.status)}</span> · ${esc(plan.provider)} · ${esc(plan.model)}</p><p>${text('plannerCaseCount')}: ${plan.case_count} · ${date(plan.created_at)}</p>${plannerCounts(plan.category_counts)}<p>${text(plan.status==='failed'?'plannerFailed':'plannerHint')}</p></section>
    <section class="panel product-panel execution-review"><h2>${text('executionReview')}</h2><p>${text('baseUrl')}: <span class="mono">${esc(preview.base_url || t('executionMissingTarget'))}</span></p><p>${text('executionHint')}</p><div class="button-row">${plan.status==='ready'?button('executionApprove','approve-api-plan',planId,'primary'):''}<button class="primary" type="button" data-action="execute-api-plan" data-approved="${plan.status==='approved'}" data-id="${esc(planId)}" ${plan.status!=='approved' || runs.some(r=>r.status==='running') || executingPlans.has(workspace+':'+planId)?'disabled':''}>${text('executionStart')}</button>${button('edit','edit-project',projectId)}</div></section>
    <section id="api-run-results" class="panel product-panel" aria-live="polite"></section>
    <div class="planner-cases">${cases.map(c=>{const e=endpoints.find(e=>e.id===c.endpoint_id),p=preview.cases.find(p=>p.case_id===c.id);return `<article class="panel product-panel planner-case"><div class="progress-heading"><h2>${esc(c.name)}</h2><span class="badge">${esc(c.priority)}</span></div><p class="mono">${esc(e?.method)} ${esc(e?.path)}</p><span class="badge">${text('plannerCategory_'+c.category)}</span><p>${esc(c.description)}</p><h3>${text('plannerSteps')}</h3><ol>${c.steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol><h3>${text('plannerExpected')}</h3><p>${esc(c.expected_result)}</p>${p?`<p>${text(p.requires_review?'executionScenarioReview':'executionDefaults')}</p>${p.error?`<p role="alert">${esc(p.error)}</p>`:''}<label>${text('executionRequest')}<textarea class="json-editor execution-request" data-case-id="${esc(c.id)}" rows="8" ${plan.status==='ready'?'':'readonly'}>${json(p.config.request_data)}</textarea></label><label>${text('executionExpectedStatus')}<input class="execution-status" data-case-id="${esc(c.id)}" type="number" min="100" max="599" required value="${esc(p.config.expected_status)}" ${plan.status==='ready'?'':'readonly'}></label><label>${text('executionAssertions')}<textarea class="json-editor execution-assertions" data-case-id="${esc(c.id)}" rows="5" ${plan.status==='ready'?'':'readonly'}>${json(p.config.assertions || [])}</textarea></label>`:''}</article>`;}).join('')}</div>`;
  await renderApiRuns(planId,version,runs);
}
async function renderApiRuns(planId,version=identityVersion,runs) {
  const revision=++apiRunRenderVersion,key=workspace+':'+planId;
  runs=runs || await api(`/api/test-plans/${planId}/runs`);
  const selectedId=apiRunSelections.get(key) || runs[0]?.id;
  const selected=selectedId?await api(`/api/test-runs/${selectedId}`):null;
  if(revision!==apiRunRenderVersion || version!==identityVersion || plannerDetailId!==planId || !$('api-run-results')) return;
  const blocked=executingPlans.has(key) || runs.some(r=>r.status==='running');
  const summary=r=>{const s=r.summary;return `<span class="badge ${esc(s.status || r.status)}">${text(s.status || r.status)}</span> · ${text('plannerCaseCount')}: ${s.total_cases} · ${text('passed')}: ${s.passed_cases} · ${text('failed')}: ${s.failed_cases} · ${text('execution_error')}: ${s.execution_error_cases} · ${text('executionPending')}: ${s.pending_cases}`;};
  const element=$('api-run-results');
  const open=new Set([...element.querySelectorAll('details[open][data-execution-id]')].map(d=>d.dataset.executionId));
  element.innerHTML=`<h2>${text('executionRuns')}</h2>${runs.length?`<label>${text('executionSelectRun')}<select id="api-run-selector">${runs.map(r=>`<option value="${esc(r.id)}" ${r.id===selectedId?'selected':''}>${date(r.started_at)} · ${esc(r.id.slice(0,8))} · ${text(r.summary.status || r.status)}</option>`).join('')}</select></label>`:`<p>${text('executionNoRuns')}</p>`}
    ${runs.map(r=>`<p class="api-run-summary" data-run-id="${esc(r.id)}">${summary(r)} · ${date(r.started_at)}</p>`).join('')}
    ${selected?`<section class="api-selected-run" data-run-id="${esc(selected.id)}"><h3>${text('executionDetail')} · ${esc(selected.id)}</h3><p>${summary(selected)}</p><p>${text('executionStarted')}: ${date(selected.started_at)} · ${text('executionFinished')}: ${date(selected.finished_at)}</p>${selected.summary.classification_inferred?`<p>${text('executionLegacy')}</p>`:''}${selected.status==='interrupted'?`<p>${text('executionInterrupted')}</p>`:''}<p>${text('executionRerunHint')}</p><button type="button" data-action="rerun-api-run" data-id="${esc(selected.id)}" ${blocked || selected.status==='running'?'disabled':''}>${text('executionRerun')}</button>${selected.status!=='running'?button('qualityView','view-api-report',selected.id):''}
    ${selected.executions.map(e=>`<details class="api-execution" data-execution-id="${esc(e.id)}" ${open.has(e.id)?'open':''}><summary>${esc(e.result_data?.case_name || e.case_id)} · ${esc(e.method)} ${esc(e.url)} · HTTP ${esc(e.response_status ?? '—')} · ${text(e.status)} · ${e.duration_ms} ms</summary>${e.classification_inferred?`<p>${text('executionLegacy')}</p>`:''}<p>${esc(e.result_data?.error_code || '')} ${esc(e.error_message)}</p><h4>${text('executionRequest')}</h4><pre>${json(e.request_data)}</pre><h4>${text('executionResponse')}</h4><pre>${esc(e.response_body)}</pre>${e.result_data?.response_body_truncated?`<p>${text('executionEvidenceTruncated')}</p>`:''}<h4>${text('executionAssertionResults')}</h4>${e.result_data?`<pre>${json(e.result_data.assertion_results)}</pre>`:`<p>${text('executionLegacyEvidence')}</p>`}</details>`).join('')}</section>`:''}`;
  const control=document.querySelector('[data-action="execute-api-plan"]');
  if(control) control.disabled=control.dataset.approved!=='true' || blocked || !!document.querySelector('[data-action="approve-api-plan"]');
}
async function approveApiPlan(planId) {
  const cases=[...document.querySelectorAll('.execution-request')].map(input=>{
    const status=[...document.querySelectorAll('.execution-status')].find(s=>s.dataset.caseId===input.dataset.caseId);
    if(!status.reportValidity()) throw Error(t('executionExpectedStatus'));
    const assertions=[...document.querySelectorAll('.execution-assertions')].find(a=>a.dataset.caseId===input.dataset.caseId);
    return {case_id:input.dataset.caseId,request_data:parse(input.value),expected_status:Number(status.value),assertions:parse(assertions.value)};
  });
  await api(`/api/test-plans/${planId}/approve`,'POST',{cases});
  await plannerDetail(planId,selectedProject);
  toast(t('plannerStatus_approved'));
}
async function loadApiReport(runId) {
  const version=identityVersion, revision=++reportRenderVersion;
  const data=await api(`/api/test-runs/${runId}/report`);
  if(version!==identityVersion || revision!==reportRenderVersion || reportRunId!==runId || !$('api-quality-report')) return;
  if(reportData?.metadata.run_id!==runId) {
    reportSelection.clear(); reportEvidence.clear();
    data.findings.slice(0,3).forEach(f=>reportSelection.add(f.source_execution_id));
  }
  reportData=data; drawApiReport();
}
function drawApiReport() {
  if(!reportData || !$('api-quality-report')) return;
  const d=reportData,s=d.summary,m=d.metadata;
  const finding=f=>`<article class="panel product-panel quality-finding" data-finding-id="${esc(f.id)}"><div class="progress-heading"><h3>${esc(f.title)}</h3><span class="badge ${esc(f.severity)}">${text(f.severity)}</span></div><label class="quality-pick"><input class="quality-selection" type="checkbox" value="${esc(f.source_execution_id)}" ${reportSelection.has(f.source_execution_id)?'checked':''}>${text('qualitySelect')}</label><p>${text('severity')}: ${text(f.severity)} · ${esc(f.severity_reason)} · ${text('plannerCategory_'+f.category)}</p><p>${text('qualityRoot')}</p><h4>${text('expected')}</h4><pre>${esc(f.expected)}</pre><h4>${text('actual')}</h4><pre>${esc(f.actual)}</pre>${f.expected_truncated || f.actual_truncated?`<p>${text('executionEvidenceTruncated')}</p>`:''}<p>${esc(f.recommendation)}</p>${qualityEvidence(f.source_execution_id)}${qualityAnalysis(d.analysis[f.source_execution_id])}</article>`;
  $('api-quality-report').innerHTML=`<section class="panel product-panel"><div class="progress-heading"><h1>${text('qualityTitle')}</h1>${button('plannerBack','quality-back',m.plan_id)}</div><p>${esc(m.project_name)} · ${text('plan')}: ${esc(m.plan_id)}</p><p>Run: ${esc(m.run_id)} · Report: ${esc(d.id)}</p><p>${text('qualityCreated')}: ${date(m.created_at)} · ${text('executionStarted')}: ${date(m.started_at)} · ${text('executionFinished')}: ${date(m.finished_at)}</p>${s.incomplete?`<p class="product-notice">${text('qualityIncomplete')}</p>`:''}${s.classification_inferred?`<p>${text('executionLegacy')}</p>`:''}<div class="metrics quality-metrics">${metric('qualityTotal',s.total_cases)}${metric('passed',s.passed_cases)}${metric('failed',s.failed_cases)}${metric('execution_error',s.execution_error_cases)}${metric('executionPending',s.pending_cases)}${metric('qualityPassRate',s.pass_rate===null?'—':s.pass_rate.toFixed(2)+'%')}${metric('duration',s.duration_ms===null?'—':s.duration_ms+' ms')}</div><p>${text('qualityRateNote')}</p><div class="button-row">${button('qualityHtml','quality-export','html')}${button('qualityMarkdown','quality-export','md')}</div></section>
    <section class="panel product-panel"><h2>${text('scope')}</h2><p>${text('qualityExecuted')}: ${d.scope.executed_cases}</p><ul class="quality-endpoints">${d.scope.tested_endpoints.map(e=>`<li><strong>${esc(e.method)}</strong> <span class="mono">${esc(e.path)}</span></li>`).join('')}</ul>${plannerCounts(d.scope.case_categories)}<h3>${text('qualityCases')}</h3>${d.cases.map(c=>`<details class="quality-case"><summary>${esc(c.case_name)} · ${esc(c.method)} ${esc(c.endpoint)} · ${text(c.status==='pending'?'executionPending':c.status)}</summary><pre>${json(c)}</pre>${c.execution_id?qualityEvidence(c.execution_id):''}</details>`).join('')}</section>
    <section class="panel product-panel quality-ai-controls"><h2>${text('qualityAI')}</h2><p>${text('qualityRoot')}</p><p>${text('qualityCost')}</p><p>${text('qualityMax')} · ${text('qualityCache')}</p>${!model.configured?`<p>${text('qualityNoProvider')}</p>${button('goModel','model')}`:''}<div class="button-row"><button type="button" class="primary" data-action="quality-analyze" ${!model.configured || reportAnalyzing?'disabled':''}>${text('qualityAnalyze')}</button><button type="button" data-action="quality-reanalyze" ${!model.configured || reportAnalyzing?'disabled':''}>${text('qualityRefresh')}</button></div></section>
    <section class="quality-failures"><h2>${text('qualityFailures')}</h2>${d.findings.filter(f=>f.kind==='failed').map(finding).join('') || `<p>${text('qualityNone')}</p>`}</section><section class="quality-errors"><h2>${text('qualityErrors')}</h2>${d.findings.filter(f=>f.kind==='execution_error').map(finding).join('') || `<p>${text('qualityNone')}</p>`}</section>`;
}
function qualityEvidence(id) {
  return `<details class="quality-evidence" data-quality-execution="${esc(id)}"><summary>${text('findingEvidence')}</summary><div class="quality-evidence-content">${reportEvidence.has(id)?`<pre>${json(reportEvidence.get(id))}</pre>`:`<p>${text('qualityEvidenceLoad')}</p>`}</div></details>`;
}
function qualityAnalysis(a) {
  return a?`<aside class="quality-ai"><h4>${text('qualityAI')}</h4><p>${text('qualityRoot')}</p><p>${text('qualityAnalyzed')}: ${date(a.generated_at)} · ${esc(a.provider)} / ${esc(a.model)}</p><h5>${text('qualityAnalysis')}</h5><p>${esc(a.analysis_summary)}</p><h5>${text('qualityCauses')}</h5><pre>${json(a.possible_causes)}</pre><h5>${text('qualitySuggestions')}</h5><pre>${json(a.recommendations)}</pre><p>${text('qualityConfidence')}: ${esc(a.confidence)} · ${text('qualitySuggestedSeverity')}: ${esc(a.suggested_severity || '—')}</p></aside>`:'';
}
function showAnalysisDialog(refresh) {
  if(!reportSelection.size || reportSelection.size>3) throw Error(t('qualityMax'));
  const count=[...reportSelection].filter(id=>refresh || !reportData.analysis[id]).length;
  $('dialog').innerHTML=`<div class="dialog-body"><h2>${text(refresh?'qualityRefresh':'qualityAnalyze')}</h2><p>${text('qualityCost')}</p><p>${esc(t('qualitySelected',{selected:reportSelection.size,new:count}))}</p><p>${text('qualityRoot')}</p><div class="button-row">${button('cancel','close')}<button type="button" class="primary" data-action="quality-confirm-analysis" data-refresh="${refresh}">${text('qualityConfirm')}</button></div></div>`;
  $('dialog').showModal();
}
async function analyzeReport(refresh) {
  const runId=reportRunId,version=identityVersion,ids=[...reportSelection];
  if(reportAnalyzing) return;
  reportAnalyzing=true; $('dialog').close(); drawApiReport();
  try {
    const data=await api(`/api/test-runs/${runId}/analyze`,'POST',{execution_ids:ids,refresh});
    if(version===identityVersion && runId===reportRunId) { reportData=data; drawApiReport(); }
  } finally { if(version===identityVersion && runId===reportRunId) { reportAnalyzing=false; drawApiReport(); } }
}
document.addEventListener('toggle',async event=>{
  const element=event.target,id=element.dataset?.qualityExecution;
  if(!id || !element.open || reportEvidence.has(id)) return;
  const runId=reportRunId,version=identityVersion;
  try {
    const evidence=await api(`/api/test-runs/${runId}/executions/${id}`);
    if(version!==identityVersion || reportRunId!==runId || !element.isConnected) return;
    reportEvidence.set(id,evidence);
    element.querySelector('.quality-evidence-content').innerHTML=`<pre>${json(evidence)}</pre>`;
  } catch(error) { if(version===identityVersion && runId===reportRunId) toast(error.message); }
},true);
async function executeApiPlan(planId,runId=null) {
  const key=workspace+':'+planId,version=identityVersion,projectId=selectedProject;
  if(executingPlans.has(key)) return;
  executingPlans.add(key);
  try { const run=await api(runId?`/api/test-runs/${runId}/rerun`:`/api/test-plans/${planId}/execute`,'POST'); if(version===identityVersion) apiRunSelections.set(key,run.id); }
  finally { executingPlans.delete(key); if(version===identityVersion && plannerDetailId===planId) await plannerDetail(planId,projectId); }
}
async function generateApiPlan(projectId) {
  const version=identityVersion, key=workspace+':'+projectId, specId=$('planner-spec')?.value;
  if(planningProjects.has(key)) return;
  planningProjects.add(key);
  plannerFailures.delete(key);
  try {
    await apiTesting(projectId);
    const plan=await api(`/api/projects/${projectId}/test-plans/generate`,'POST',{api_spec_id:specId});
    if(version===identityVersion && apiModeProject===projectId && !plannerDetailId) location.hash=`projects/${projectId}/test-plans/${plan.id}`;
  } catch(error) {
    if(version===identityVersion && error.planner) plannerFailures.set(key,error.planner);
    throw error;
  } finally {
    planningProjects.delete(key);
    if(version===identityVersion && apiModeProject===projectId && !plannerDetailId) await apiTesting(projectId);
  }
}
function projectDialog(id = '') {
  const p = projects.find(p=>p.id===id) || {};
  $('dialog').innerHTML = `<form id="project-form" data-id="${esc(id)}"><div class="dialog-heading"><h2>${text(id?'edit':'newProject')}</h2>${button('cancel','close')}</div><div class="dialog-body">${field('name','name',p.name,'text',true)}${area('description','description',p.description)}<div class="product-form-grid"><label>${text('projectType')}<select name="project_type">${['web','api'].map(v=>`<option value="${v}" ${p.project_type===v?'selected':''}>${text(v)}</option>`).join('')}</select></label><label>${text('environment')}<select name="environment">${['development','testing','staging','custom'].map(v=>`<option value="${v}" ${p.environment===v?'selected':''}>${text(v)}</option>`).join('')}</select></label></div>${field('baseUrl','url',p.url,'url',(p.project_type || 'web')==='web')}</div><div class="dialog-footer"><button class="primary" type="submit">${text('save')}</button></div></form>`;
  $('dialog').showModal();
}
async function taskDialog(projectId) {
  if (!projects.length) { projectDialog(); return; }
  if (!model.configured) { currentView='model'; location.hash='settings/providers'; shell(); return; }
  projectId = projectId || selectedProject || projects[0].id;
  if (!projects.find(p=>p.id===projectId)?.url) {
    const environment = await api(`/api/projects/${projectId}/environment`);
    if (!environment.base_url) {
      selectedProject=projectId; currentView='projects'; location.hash='projects'; render();
      await projectDetail(projectId); toast(t('onboardingTargetPending')); return;
    }
  }
  $('dialog').innerHTML = `<form id="task-form"><div class="dialog-heading"><h2>${text('newTask')}</h2>${button('cancel','close')}</div><div class="dialog-body"><label>${text('project')}<select name="project_id" id="task-project">${projects.map(p=>`<option value="${esc(p.id)}" ${p.id===projectId?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>${field('testTarget','target_url','','url',true)}${field('title','title')}${area('goal','goal','',4)}<p>${text('goalPlaceholder')}</p><div id="task-endpoints"></div><details><summary>${text('runAccount')}</summary>${field('username','username')}${field('password','password','','password')}${jarea('runSecrets','session_secrets')}<p>${text('runSecretNote')}</p></details></div><div class="dialog-footer"><button class="primary" type="submit">${text('generatePlan')}</button></div></form>`;
  $('dialog').showModal();
  await taskEndpoints(projectId);
}
async function taskEndpoints(id) {
  const p = projects.find(p=>p.id===id);
  const env = await api(`/api/projects/${id}/environment`);
  $('task-form').elements.target_url.value = env.base_url;
  const endpoints = p.project_type==='api' ? await api(`/api/projects/${id}/endpoints`) : [];
  $('task-endpoints').innerHTML = p.project_type==='api' ? `<h3>${text('selectEndpoints')}</h3><div class="endpoint-list">${endpoints.length?endpoints.map(e=>`<label><input name="endpoint" type="checkbox" value="${esc(e.method+' '+e.path)}">${esc(e.method+' '+e.path)} · ${esc(e.summary)}</label>`).join(''):`<p>${text('noEndpoints')}</p>`}</div>` : '';
}
function formData(form) { return Object.fromEntries(new FormData(form).entries()); }
function parse(value) { try { return JSON.parse(value); } catch { throw Error(t('invalidJson')); } }
function clearPasswords() { document.querySelectorAll('input[type=password]').forEach(input=>input.value=''); }
async function download(path) {
  const version=identityVersion;
  let response = await fetch(path,{headers:{'X-Workspace-ID':workspace},credentials:'same-origin'});
  if (response.status===401) { await api('/api/auth/me'); if(version!==identityVersion) throw Error(t('requestFailed')); response=await fetch(path,{headers:{'X-Workspace-ID':workspace},credentials:'same-origin'}); }
  if (!response.ok) throw Error(t('requestFailed'));
  const blob=await response.blob();
  if(version!==identityVersion) throw Error(t('requestFailed'));
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href=blobUrl; link.download=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || (path.split('/').at(-1)==='report'?'autoqa-report.html':path.split('/').at(-1)); link.click();
  setTimeout(()=>URL.revokeObjectURL(blobUrl),10000);
}
document.addEventListener('submit', async event=>{
  event.preventDefault(); const form=event.target; const data=formData(form);
  const submit=form.querySelector('[type=submit]'); if (submit) submit.disabled=true;
  try {
    if (form.id==='auth-form') { const result=await api('/api/auth/'+authMode, 'POST',data); resetIdentity(); csrf=result.csrf_token; me=await api('/api/auth/me'); workspace=me.workspace_id; onboarding=me.onboarding; history.replaceState(null,'','#dashboard'); shell(); await refresh(); }
    else if (form.id==='onboarding-project-form') { const result=await api('/api/onboarding/project','POST',data); onboarding=result.onboarding; renderOnboarding(); }
    else if (form.id==='model-form') { await api('/api/settings/providers','POST',data); clearPasswords(); $('dialog').close(); $('dialog').innerHTML='';
      if (needsOnboarding() && onboarding.step===2) { onboarding=await api('/api/onboarding','PATCH',{action:'skip_model'}); routeView(); shell(); }
      await refresh(); toast(t('saved')); }
    else if (form.id==='project-form') { await api('/api/projects'+(form.dataset.id?'/'+form.dataset.id:''), form.dataset.id?'PATCH':'POST',data); $('dialog').close(); currentView='projects'; location.hash='projects'; await refresh(); }
    else if (form.id==='api-spec-upload-form') { const file=form.elements.file.files[0],id=form.dataset.projectId;
      if (!file || file.size>1000000) throw Error(t('apiSpecTooLarge'));
      if (!/\.(json|ya?ml)$/i.test(file.name)) throw Error(t('apiSpecFormat'));
      const upload=new FormData(); upload.append('file',file);
      const result=await api(`/api/projects/${id}/api-specs/upload`,'POST',upload);
      apiNotice={project_id:id,count:result.endpoint_count}; await apiTesting(id); toast(t('apiParsed')); }
    else if (form.id==='task-form') { const secrets=parse(data.session_secrets || '{}'); if (data.username) secrets.USERNAME=data.username; if (data.password) secrets.PASSWORD=data.password;
      const result=await api('/api/tasks','POST',{project_id:data.project_id,target_url:data.target_url,goal:data.goal,title:data.title || null,session_secrets:secrets,endpoints:new FormData(form).getAll('endpoint')});
      clearPasswords(); $('dialog').innerHTML=''; $('dialog').close(); selectedTask=result.id; currentView='workflow'; location.hash='workflow'; await refresh(); }
    else if (form.id==='openapi-form') { await api(`/api/projects/${selectedProject}/openapi`,'POST',{url:data.url}); await projectDetail(selectedProject); toast(t('saved')); }
    else if (form.id==='manual-form') { await api(`/api/projects/${selectedProject}/openapi`,'POST',{endpoints:parse(data.endpoints)}); await projectDetail(selectedProject); toast(t('saved')); }
    else if (form.id==='environment-form') { await api(`/api/projects/${selectedProject}/environment`,'PUT',{base_url:data.base_url,headers:parse(data.headers),variables:parse(data.variables),secrets:parse(data.secrets),username:data.username,password:data.password}); clearPasswords(); await projectDetail(selectedProject); toast(t('saved')); }
    else if (form.id==='member-form') { await api('/api/workspace/members','POST',data); toast(t('added')); form.reset(); }
  } catch(error) { toast(error.message); } finally { if (submit) submit.disabled=false; }
});
document.addEventListener('click', async event=>{
  const control=event.target.closest('[data-action]'); if (!control) return;
  const action=control.dataset.action,id=control.dataset.id; control.disabled=true;
  try {
    if (action==='auth-mode') { authMode=authMode==='login'?'register':'login'; shell(); }
    else if (action==='logout') { await api('/api/auth/logout','POST'); resetIdentity(); location.hash=''; shell(); }
    else if (action==='refresh') await refresh();
    else if (action==='onboarding-model' || action==='model') { location.hash='settings/providers'; routeView(); shell(); }
    else if (action==='back-onboarding') { location.hash='onboarding'; routeView(); shell(); }
    else if (['onboarding-start','onboarding-skip-model','onboarding-complete'].includes(action)) {
      const actions={'onboarding-start':'start','onboarding-skip-model':'skip_model','onboarding-complete':'complete'};
      onboarding=await api('/api/onboarding','PATCH',{action:actions[action]});
      routeView(); shell(); if (!needsOnboarding()) await refresh();
    }
    else if (action==='close') { clearPasswords(); $('dialog').close(); $('dialog').innerHTML=''; }
    else if (action==='new-project' || action==='edit-project') projectDialog(id);
    else if (action==='new-task') await taskDialog(id);
    else if (action==='add-model') modelDialog();
    else if (action==='project') { selectedProject=id; currentView='projects'; location.hash='projects'; render(); await projectDetail(id); }
    else if (action==='api-testing') { location.hash=`projects/${id}/api-testing`; }
    else if (action==='generate-api-plan') await generateApiPlan(id);
    else if (action==='view-api-plan') { location.hash=`projects/${selectedProject}/test-plans/${id}`; }
    else if (action==='approve-api-plan') await approveApiPlan(id);
    else if (action==='execute-api-plan') await executeApiPlan(id);
    else if (action==='rerun-api-run') await executeApiPlan(plannerDetailId,id);
    else if (action==='view-api-report') location.hash=`test-runs/${id}/report`;
    else if (action==='quality-back') location.hash=`projects/${reportData.metadata.project_id}/test-plans/${id}`;
    else if (action==='quality-export') await download(`/api/test-runs/${reportRunId}/report.${id}`);
    else if (action==='quality-analyze') showAnalysisDialog(false);
    else if (action==='quality-reanalyze') showAnalysisDialog(true);
    else if (action==='quality-confirm-analysis') await analyzeReport(control.dataset.refresh==='true');
    else if (action==='back-project') { selectedProject=id; location.hash='projects'; }
    else if (action==='delete-api-spec' && confirm(t('confirmDeleteApiSpec'))) { const projectId=selectedProject;
      await api(`/api/projects/${projectId}/api-specs/${encodeURIComponent(id)}`,'DELETE'); apiNotice=null; await apiTesting(projectId); toast(t('deleted')); }
    else if (action==='task') { selectedTask=id; detail=await api('/api/tasks/'+id); currentView='workflow'; location.hash='workflow'; render(); }
    else if (action==='archive-project' && confirm(t('confirmArchive'))) { await api('/api/projects/'+id,'DELETE'); selectedProject=''; await refresh(); }
    else if (action==='test-model') { const form=$('model-form'); if (!form.reportValidity()) return;
      try { await api('/api/provider/test','POST',formData(form)); toast(t('connectionOk')); } finally { clearPasswords(); } }
    else if (action==='test-saved-model') { try { await api(`/api/settings/providers/${encodeURIComponent(id)}/test`,'POST'); toast(t('connectionOk')); } finally { await refresh(); } }
    else if (action==='activate-model') { await api(`/api/settings/providers/${encodeURIComponent(id)}/activate`,'POST'); await refresh(); toast(t('saved')); }
    else if (action==='delete-model' && confirm(t('confirmDeleteModel'))) { await api(`/api/settings/providers/${encodeURIComponent(id)}`,'DELETE'); await refresh(); toast(t('deleted')); }
    else if (action==='delete-environment') { await api(`/api/projects/${id}/environment`,'DELETE'); await projectDetail(id); }
    else if (action==='save-plan') { const updated=await api(`/api/tasks/${detail.id}/plan`,'PATCH',parse($('plan-editor').value)); detail=updated; render(); toast(t('saved')); }
    else if (action==='delete-case' || action==='add-case') {
      const editor=$('plan-editor'), plan=parse(editor.value), collection=plan.cases || plan.steps;
      if (action==='delete-case') collection.splice(Number(id),1);
      else if (plan.cases) collection.push({...collection[0],case_id:'CASE-'+crypto.randomUUID().slice(0,8),name:t('addCase')});
      else collection.push({action:'assert_text',description:t('addCase'),value:''});
      editor.value=JSON.stringify(plan,null,2);
      $('case-controls').innerHTML=collection.map((c,i)=>`<div class="case-row"><strong>${esc(c.name || c.description)}</strong>${button('deleteCase','delete-case',i)}</div>`).join('');
    }
    else if (action==='approve' || action==='reject') {
      if (action==='approve' && $('plan-editor') && JSON.stringify(parse($('plan-editor').value))!==JSON.stringify(detail.plan)) throw Error(t('planChanged'));
      await api(`/api/tasks/${id}/approve`,'POST',{approved:action==='approve',plan_hash:detail.plan_hash}); await refresh();
    } else if (action==='regenerate') { const result=await api(`/api/tasks/${id}/regenerate`,'POST'); selectedTask=result.id; await refresh(); }
    else if (action==='cancel-task') { await api(`/api/tasks/${id}/cancel`,'POST'); await refresh(); }
    else if (action==='report') await download(`/api/tasks/${id}/report`);
    else if (action==='artifact') await download(`/api/tasks/${detail.id}/artifacts/${encodeURIComponent(id)}`);
  } catch(error) { toast(error.message); } finally { control.disabled=false; }
});
document.addEventListener('change', async event=>{
  if(event.target.classList.contains('quality-selection')) { if(event.target.checked && reportSelection.size>=3) { event.target.checked=false; toast(t('qualityMax')); } else if(event.target.checked) reportSelection.add(event.target.value); else reportSelection.delete(event.target.value); return; }
  if(event.target.id==='api-run-selector') { apiRunSelections.set(workspace+':'+plannerDetailId,event.target.value); await renderApiRuns(plannerDetailId).catch(e=>toast(e.message)); return; }
  try {
    if (event.target.id==='language') { await loadLocale(event.target.value); shell(); if (currentView==='projects' && selectedProject) await projectDetail(selectedProject); }
    else if (event.target.id==='workspace-selector') { identityVersion++; workspace=event.target.value; selectedProject=''; selectedTask=''; detail=null; projects=[]; tasks=[]; stats={}; model={}; providerConfigs=[]; apiModeProject=''; apiNotice=null; plannerDetailId=''; plannerSpecId=''; apiRunSelections.clear(); apiRunRenderVersion++; reportRunId=''; reportData=null; reportSelection.clear(); reportEvidence.clear(); reportRenderVersion++; reportAnalyzing=false; if(location.hash.includes('/api-testing') || location.hash.includes('/test-plans/') || location.hash.includes('test-runs/')) history.replaceState(null,'','#projects'); clearPasswords(); $('dialog').close(); $('dialog').innerHTML=''; render(); await refresh(); }
    else if (event.target.id==='task-project') await taskEndpoints(event.target.value);
    else if (event.target.id==='planner-spec') { plannerSpecId=event.target.value; $('planner-budget').textContent=plannerBudget(Number(event.target.selectedOptions[0].dataset.endpointCount)); }
    else if (event.target.name==='project_type' && event.target.form?.id==='project-form') { event.target.form.elements.url.required=event.target.value==='web'; }
    else if (event.target.id==='provider-select') { const form=$('model-form'),provider=event.target.value; form.elements.base_url.value=provider==='deepseek'?'https://api.deepseek.com':provider==='openai'?'https://api.openai.com/v1':''; form.elements.model.value=provider==='deepseek'?'deepseek-chat':''; }
    else if (event.target.id==='openapi-file') { const file=event.target.files[0]; if (!file) return; if(file.size>1000000) throw Error(t('requestFailed')); await api(`/api/projects/${selectedProject}/openapi`,'POST',{content:await file.text()}); await projectDetail(selectedProject); toast(t('saved')); }
  } catch(error) { toast(error.message); }
});
window.addEventListener('hashchange', async()=>{ clearPasswords(); $('dialog').close(); $('dialog').innerHTML=''; routeView(); shell(); if(currentView==='projects' && selectedProject) await projectDetail(selectedProject).catch(e=>toast(e.message)); });
$('dialog').addEventListener('cancel', event=>{ event.preventDefault(); clearPasswords(); $('dialog').close(); $('dialog').innerHTML=''; });
await loadLocale(preferredLocale());
currentView=views.includes(location.hash.slice(1))?location.hash.slice(1):'dashboard';
// Establish CSRF before an automatic refresh on a returning browser session.
try { const result=await fetch('/api/auth/me',{credentials:'same-origin'}); if(result.ok) { me=await result.json(); csrf=me.csrf_token; workspace=me.workspace_id; } else {
  const bootstrap=await fetch('/api/auth/csrf',{credentials:'same-origin'});
  if(bootstrap.ok) { csrf=(await bootstrap.json()).csrf_token; me=await api('/api/auth/me'); workspace=me.workspace_id; }
} } catch { resetIdentity(); }
onboarding=me?.onboarding || null;
shell();
if (me) await refresh().catch(error=>toast(error.message));
setInterval(async()=>{
  if(reportRunId) return;
  if(me && plannerDetailId && !pollBusy && !$('dialog').open) {
    pollBusy=true; try { await renderApiRuns(plannerDetailId); } catch(error) { toast(error.message); } finally { pollBusy=false; }
    return;
  }
  if(!me || pollBusy || $('dialog').open || !['dashboard','tasks','workflow','findings','reports'].includes(currentView)) return;
  if(currentView==='workflow' && (document.activeElement?.id==='plan-editor' || detail?.status==='awaiting_approval')) return;
  pollBusy=true; try { await refresh(); } catch(error) { toast(error.message); } finally { pollBusy=false; }
},2500);
