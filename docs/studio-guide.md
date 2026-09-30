# AutoQA Studio

AI-powered Software Quality Engineering Platform

用户管理测试流程：定义目标、审核计划、追踪执行、复核报告。AI 规划是系统能力；工作空间不使用聊天气泡、机器人头像或对话历史作为主交互。

## 启动

Windows：进入 `D:\agent 项目\AutoQA-Agent`，双击 `start.cmd`。首次安装运行 `start.cmd -Setup`。

```powershell
cd "D:\agent 项目\AutoQA-Agent"
.\.venv\Scripts\python.exe -m uvicorn autoqa.app:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
```

访问 http://127.0.0.1:8765。无需 Node.js、前端构建或模型密钥；模型与站点配置沿用 [配置说明](usage.md)。本地内置场景使用 8765 端口。

## 五步 Demo

1. **Dashboard**：展示 Projects、Running Tests、Passed、Issues Found、成功率、最近运行和风险提醒。首次启动为空状态，点击「开始完整 Demo」。
2. **创建测试任务**：选择 Demo Commerce、Regression、登录与多页面。保留默认的计划审核，点击「生成测试计划」。
3. **Agent Workflow**：展示 Requirement Analyzer → Test Planner → Execution Agent → Report Generator；前两个阶段完成，Execution 等待审核。Test Plan 列出 3 个断言、8 个操作步骤、目标路径与 Medium 风险。点击「批准并执行」，观察运行状态、事件和截图。
4. **Test Report**：任务 Passed 后点击「查看测试报告」。查看 Total Cases 3、Passed 3、Failed 0、Coverage 100%，再导出可离线打开的 HTML 报告。
5. **测试结果与问题**：创建「接口故障」任务，批准后观察 Failed，查看 High 问题、network_error、建议和运行证据。该场景故意返回 HTTP 500；尚未执行的断言不计入通过或失败，覆盖率为 0%。返回 Dashboard 展示数据更新。

补充演示：选择「表单审批」，展示计划审核后在提交步骤再次暂停；选择「延迟与重试」，展示最终通过与历史失败尝试；选择 Smoke，通过结构化步骤验证页面主体可见和运行异常。

无模型时，Regression 只支持内置中文目标模板。Smoke 使用导航 + body 可见断言，不验证目标文本描述中的完整业务流程。连接模型后可在允许的站点使用自然语言业务回归。

## 页面与视觉

- Dashboard：真实任务聚合、成功率环形指标、近期记录、审批和失败/中断风险。
- Projects：内置项目、自定义项目、历史目标来源归组与项目运行数量。
- Test tasks：状态筛选、目标/ID/地址/项目搜索、创建任务弹窗。
- Agent workflow：四阶段状态、计划摘要、断言数量、目标路径、规则风险、审批、取消、步骤、SSE 事件、截图与证据下载。
- Test reports：断言汇总、执行覆盖率、Bug ID / Severity / Description / Suggestion、每步结果、HTML 导出。运行期间显示实时预览。
- Settings：服务状态、规划模式、数据库、MCP、API 认证和会话令牌。

设计参考 GitHub [Primer Product UI](https://primer.style/product/) 与 Vercel [Geist Colors](https://vercel.com/geist/colors)：统一间距、字体层级、边框、卡片与状态组件。黑灰背景使用少量蓝紫强调；通过与风险分别使用绿色、红色和琥珀色。字体优先系统字体，无远端字体依赖。原生 dialog 支持键盘关闭、焦点限制与返回；界面提供加载、空、错误、重试、焦点样式、跳转主内容与减少动效偏好。

## 指标定义

| 指标 | 统计口径 |
| --- | --- |
| Projects | 当前浏览器登记项目与最近任务发现的目标来源；内置 Demo Commerce 计作一个项目 |
| Running Tests | 最近 50 条记录中 queued / running 任务 |
| Passed | 最近 50 条记录中 passed 任务数，不是通过断言数 |
| Issues Found（Dashboard） | failed 任务数；具体问题条目在每个报告中显示 |
| Success rate | passed / (passed + failed)，不含待审批、取消与中断 |
| Total Cases | 当前计划中 `assert_*` 显式断言数量；导航、点击、输入单独计作操作步骤 |
| Passed / Failed（Report） | 每个断言最终尝试的结果；未执行断言单独统计 |
| Coverage | 已执行断言 / 计划断言；不是代码、需求或业务模块覆盖率；无计划显示未知 |
| Covered Modules | 目标页面、计划导航地址、URL 断言体现的路径；不是代码模块覆盖证明 |
| Risk Level | 审批步骤 High，输入/点击等写操作 Medium，只读 Low；是计划规则分级 |
| Bug Severity | 按现有诊断类别规则分级，仅用于初步排查，不声称已确认根因 |

故障任务可能在点击、导航或运行校验时失败，此时失败断言数量可为 0。任务状态优先；报告保留操作失败和诊断。重试汇总采用最终尝试，导出报告保留每次尝试记录。

## 工程结构与边界

本次修改文件：`autoqa/static/index.html`、`style.css`、`app.js`；新增同目录的 `components.js` 与 `studio-model.js`；修改 `autoqa/reports.py`、`autoqa/app.py`（仅产品名称与描述）、`scripts/start.ps1`、`README.md`、`docs/demo-guide.md`；新增 `scripts/verify_studio.py`、`tests/test_studio_reports.py` 和本文档。

验收产物：`docs/studio-test-results.xml`、`docs/studio-ui-results.json`、`docs/screenshots/studio-dashboard.png`、`studio-workflow.png`、`studio-report.png`、`studio-issues.png`、`studio-empty.png`、`studio-390.png`、`studio-768.png`、`studio-1440.png`。原有测试和 Agent 核心文件未修改，原文档与截图保留。

```text
autoqa/static/
  index.html        页面结构、导航、创建任务与项目弹窗
  style.css         设计变量、卡片/表格/状态/弹窗、响应式与微动画
  studio-model.js   计划/断言/问题/业务阶段的展示投影
  components.js     安全转义、表格、指标、空状态、步骤与报告组件
  app.js            API、SSE、路由、轮询、会话认证与交互
autoqa/reports.py    可离线查看的 HTML 报告与断言/问题投影
scripts/verify_studio.py  真实浏览器 UI 验证及截图
tests/test_studio_reports.py  重试、未执行断言、取消、规划失败与转义测试
```

保持零前端构建方式，ES modules 分离状态投影、公共组件与交互；包数据配置 `static/*` 自动包含新增模块。没有引入框架、聊天 SDK 或装饰性 mock 指标。所有运行统计来自现有 API。

四个 UI 阶段是对原有 LangGraph 流程的业务映射：需求观察与计划生成仍属于原 Test Planner；Execution 包含浏览器、审批、Verifier 与 Failure Analyzer；Report 表示现有报告可供生成与导出，没有引入独立 Report Agent 或额外模型调用。

Phase 2 已将自定义项目、任务关联与测试类型迁移到服务端关系数据库。项目跨浏览器共享；sessionStorage 仅保存访问令牌和当前任务选择，旧 localStorage 数据会一次性导入。当前列表限最近 50 条，Dashboard 汇总覆盖全部历史。具体模型、API 与迁移边界见 [Phase 2 工程说明](phase2.md)。

产品化界面与服务端项目管理已完成，多租户、RBAC 和分布式队列尚未实现。展示时应描述为具备企业测试工作台体验的本地平台。

## 验证

```powershell
.\.venv\Scripts\python.exe -m ruff check autoqa tests scripts
.\.venv\Scripts\python.exe -m pytest -q
node --check autoqa/static/app.js
```

运行 pytest 前先停止占用 8765 的本地服务。原有测试文件没有修改；新增报告测试验证重试不重复计算、运行失败不伪造断言通过、无计划覆盖率未知、取消保留未执行断言和 HTML 转义。

在空的隔离本地实例上运行以下命令，脚本会创建真实测试任务并生成截图与 UI 验证 JSON：

```powershell
.\.venv\Scripts\python.exe scripts/verify_studio.py --output work/studio-qa
```

该脚本验证登录回归的计划审核/执行/报告下载、网络故障报告、项目跨浏览器共享、结构化 Smoke、Login API 六用例、刷新后持久化、搜索空状态、401 错误恢复与 390 / 768 / 1440px 六页布局。401 场景由浏览器路由注入，其余任务通过真实 API 与 Chromium 执行。

## 下一步

1. 在现有关系数据库上增加 Test Suite、任务分页与历史趋势。
2. 用户、团队、RBAC、环境凭据与审批审计，支持团队共享工作空间。
3. 用例版本、批量回归、CI 触发、历史趋势与需求覆盖矩阵，建立跨运行的质量指标。
4. 独立任务队列、集中式 Checkpoint 与多实例执行，随后再扩展并发与多浏览器矩阵。
5. 扩展已验收的真实模型 Login API 流程，评测更多任务的规划成功率、成本与延迟。
