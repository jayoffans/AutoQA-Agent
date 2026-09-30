import {labels, terminal, reportStats, planInfo, issueRows, latestSteps} from './studio-model.js';
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const badge = (value, text = labels[value] || value) => `<span class="status ${esc(value.toLowerCase().replaceAll(' ', '-'))}"><i></i>${esc(text)}</span>`;
export function time(value) {
  if (!value) return '—';
  const date = new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z');
  return date.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
export function empty(title, message, action = true) {
  return `<div class="empty"><span class="empty-icon">▧</span><h3>${esc(title)}</h3><p>${esc(message)}</p>${action ? '<button class="secondary" data-action="new-task">＋ 创建测试任务</button>' : ''}</div>`;
}
export function metric(label, value, note, tone = '') {
  return `<article class="metric ${esc(tone)}"><div class="metric-label">${esc(label)}<span>↗</span></div><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;
}
export function taskTable(tasks, projectFor, selected, compact = false) {
  if (!tasks.length) return empty('还没有运行记录', '创建一个测试任务，开始积累可复核的质量数据。');
  return `<table class="run-table"><thead><tr><th>测试任务 / 项目</th><th>Status</th>${compact ? '' : '<th>Mode</th>'}<th>Created</th><th><span class="sr-only">打开任务</span></th></tr></thead><tbody>${tasks.map(t => `<tr class="${selected === t.id ? 'selected' : ''}"><td><button class="task-link" data-task="${esc(t.id)}"><strong title="${esc(t.goal)}">${esc(t.goal)}</strong><small>${esc(projectFor(t).name)} <span>· ${esc(t.id.slice(0,8))}</span></small></button></td><td>${badge(t.status)}</td>${compact ? '' : `<td class="mono mode-cell">${esc(t.planner_mode || 'pending')}</td>`}<td class="time-cell">${time(t.created_at)}</td><td><button class="icon-button" data-task="${esc(t.id)}" aria-label="打开任务 ${esc(t.id.slice(0,8))}">↗</button></td></tr>`).join('')}</tbody></table>`;
}
export function stepTimeline(task) {
  const latest = latestSteps(task);
  if (!task.plan) return '<div class="plan-loading"><span class="spinner"></span> 正在读取页面并规划测试…</div>';
  return task.plan.steps.map((step, index) => {
    const result = latest.get(index);
    const state = result ? (result.ok ? 'passed' : 'failed') : 'waiting';
    return `<div class="step-row ${state}"><span class="step-number">${result?.ok ? '✓' : index + 1}</span><div class="step-description">${esc(step.description)}<small>${esc(step.action)}${step.selector ? ' · '+esc(step.selector) : ''}${result ? ' · attempt '+(result.attempt+1) : ''}</small></div><span class="step-time">${result ? esc(result.duration_ms)+' ms' : '—'}</span>${badge(state, result ? labels[state] : terminal.has(task.status) ? 'Not run' : 'Waiting')}</div>`;
  }).join('');
}
export function reportView(task, project) {
  const s = reportStats(task), p = planInfo(task), issues = issueRows(task);
  const completed = terminal.has(task.status);
  return `<section class="panel report-summary"><div><span class="eyebrow">${completed ? 'RUN SUMMARY' : 'LIVE PREVIEW'}</span><h2>${esc(task.request.goal)}</h2><p>${esc(project.name)} <span>·</span> ${esc(task.id.slice(0,8))} <span>·</span> ${time(task.created_at)}</p></div>${badge(task.status)}</section>
    ${!completed ? '<div class="info-note">任务尚未结束，以下为实时预览。最终报告将在运行结束后可导出。</div>' : ''}
    <div class="metrics report-metrics">${metric('Total Cases', s.total, '计划中的显式断言')}${metric('Passed', s.passed, '最终尝试通过的断言', 'green')}${metric('Failed', s.failed, `${s.pending} 个断言未执行`, 'red')}${metric('Coverage', s.coverage === null ? '—' : s.coverage+'%', '计划断言执行覆盖率', 'violet')}</div>
    <div class="report-notes"><span>覆盖率 = 已执行断言 / 计划断言，不是代码覆盖率。</span><span>${p.count} 个操作步骤 · ${s.attempts} 次执行尝试 · 重试按最终结果统计</span></div>
    <section class="panel"><div class="panel-title"><div><h2>Issues found <span class="count">${issues.length}</span></h2><p>运行问题与建议 · Severity 为规则分级，根因待确认</p></div><span class="small-label">EVIDENCE BASED</span></div><div class="table-container">${issues.length ? `<table class="bug-table"><thead><tr><th>ID</th><th>Severity</th><th>Description</th><th>Suggestion</th></tr></thead><tbody>${issues.map(i => `<tr><td class="mono">${esc(i.id)}</td><td>${badge(i.severity)}</td><td>${esc(i.description)}<small>${esc(i.category)}</small></td><td>${esc(i.suggestion)}</td></tr>`).join('')}</tbody></table>` : empty(completed && task.status === 'passed' ? '本次运行未发现问题' : '暂无已记录的问题', '结果只覆盖本次计划与实际执行范围。', false)}</div></section>
    <section class="panel report-steps"><div class="panel-title"><div><h2>Execution results</h2><p>${esc(task.request.target_url)}</p></div><button class="text-button" data-action="workflow">查看证据与 Trace ↗</button></div>${stepTimeline(task)}</section>`;
}
