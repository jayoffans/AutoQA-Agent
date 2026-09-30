// Presentation-only projections. API contracts and workflow execution stay on the server.
export const terminal = new Set(['passed', 'failed', 'cancelled', 'interrupted']);
export const labels = {queued:'Queued', running:'Running', awaiting_approval:'Awaiting approval', passed:'Passed', failed:'Failed', cancelled:'Cancelled', interrupted:'Interrupted'};
export const templates = {
  api:{name:'Login API Testing', page:'api/login', goal:'验证 Login API 六类契约：有效登录、错误密码、缺少用户名、缺少密码、格式错误请求、未授权访问。'},
  login:{name:'登录与多页面', page:'login.html', goal:'登录并完成多页面测试，验证欢迎信息和订单列表。'},
  network:{name:'接口故障', page:'broken.html', goal:'诊断网络接口故障，检查订单提交接口的 HTTP 500。'},
  console:{name:'前端异常', page:'console.html', goal:'检查前端异常和控制台错误，验证操作反馈。'},
  form:{name:'表单审批', page:'form.html', goal:'填写表单并审批提交，校验提交成功提示。'},
  retry:{name:'延迟与重试', page:'delayed.html', goal:'测试延迟渲染与只读步骤重试，校验加载完成。'},
  assertion:{name:'断言失败', page:'form.html', goal:'运行错误断言，检查页面状态未达预期时的诊断。'}
};
export function latestSteps(task) {
  return new Map((task.steps || []).map(step => [step.index, step]));
}
export function reportStats(task) {
  const plan = task.plan?.steps || [];
  const latest = latestSteps(task);
  const cases = plan.map((step, index) => ({step, index, result:latest.get(index)})).filter(c => c.step.action.startsWith('assert_'));
  const executed = cases.filter(c => c.result).length;
  return {total:cases.length, passed:cases.filter(c => c.result?.ok).length,
    failed:cases.filter(c => c.result && !c.result.ok).length, pending:cases.length - executed,
    coverage:cases.length ? Math.round(executed / cases.length * 100) : null,
    cases, latest, attempts:(task.steps || []).length};
}
export function planInfo(task) {
  const steps = task.plan?.steps || [];
  const paths = new Set();
  if (task.request?.target_url) paths.add(new URL(task.request.target_url).pathname);
  for (const step of steps) if (step.action === 'navigate' && step.value) {
    try { paths.add(new URL(step.value, task.request.target_url).pathname); } catch { /* invalid evidence stays in raw API */ }
  }
  // Planned assertions give additional evidence of a destination without inventing modules.
  for (const step of steps) if (step.action === 'assert_url' && step.value) paths.add(step.value);
  for (const step of steps) if (step.action === 'assert_api' && step.path) paths.add(step.path);
  const submissions = steps.some(s => s.approval_required);
  const mutations = steps.some(s => ['click','fill','press','select'].includes(s.action) || s.action === 'assert_api' && s.method === 'POST');
  return {cases:steps.filter(s => s.action.startsWith('assert_')).length, count:steps.length,
    modules:[...paths], risk:steps.length ? (submissions ? 'High' : mutations ? 'Medium' : 'Low') : 'Pending'};
}
export function issueRows(task) {
  if (task.findings?.length) return task.findings.map(f => ({...f, category:f.evidence?.diagnosis?.category || 'run_finding'}));
  const d = task.diagnosis;
  const severity = ['network_error','frontend_error','task_timeout','agent_error','api_contract_error'].includes(d?.category) ? 'High' : 'Medium';
  const rows = [...latestSteps(task).values()].filter(s => !s.ok).map(s => ({
    id:`BUG-${task.id}-${s.index + 1}`, severity,
    description:s.error || s.step.description, suggestion:d?.suggestion || '检查页面状态、定位器与预期结果，并复核步骤证据。',
    category:d?.category || s.step.action
  }));
  if (!rows.length && d && task.status === 'failed') rows.push({id:`RUN-${task.id}`, severity,
    description:d.summary, suggestion:d.suggestion, category:d.category});
  return rows;
}
export function workflowStages(task, events = []) {
  const hasPlan = Boolean(task.plan);
  const ended = terminal.has(task.status);
  const planning = task.status === 'running' && !hasPlan;
  const executing = task.status === 'running' && hasPlan;
  const last = events.at(-1);
  const analysisFailed = task.status === 'failed' && !hasPlan;
  const executionDone = ['passed','failed'].includes(task.status) && hasPlan;
  return [
    {name:'Requirement Analyzer', caption:'需求读取与页面观察', state:hasPlan ? 'Completed' : planning ? 'Running' : analysisFailed ? 'Failed' : ended ? 'Stopped' : 'Waiting'},
    {name:'Test Planner', caption:'生成并校验结构化计划', state:hasPlan ? 'Completed' : planning && last?.kind === 'planner_started' ? 'Running' : analysisFailed ? 'Failed' : ended ? 'Stopped' : 'Waiting'},
    {name:'Execution Agent', caption:task.status === 'awaiting_approval' ? '等待人工审批' : task.test_type === 'api' ? 'HTTP 请求执行与合同校验' : '浏览器执行与结果校验', state:executionDone ? (task.status === 'failed' ? 'Failed' : 'Completed') : executing ? 'Running' : ended ? 'Stopped' : 'Waiting'},
    {name:'Report Generator', caption:ended ? '运行结果与证据可供导出' : '汇总结果、问题与证据', state:ended ? 'Completed' : 'Waiting'}
  ];
}
