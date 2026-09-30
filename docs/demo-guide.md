# 演示与面试讲解

新版 AutoQA Studio 使用 **Dashboard → 创建测试任务 → 审核 Test Plan / Agent Workflow → Test Report → 结果与故障证据** 的产品演示流程。完整操作与指标口径见 [Studio 五步 Demo](studio-guide.md)。以下原有场景与面试说明继续适用；新版按钮名为「生成测试计划」，默认先审核再执行。

返回 [项目首页](../README.md)。本页包含短视频分镜、素材位置与面试讲解；详细配置见 [配置说明](usage.md)。

## 60–90 秒展示视频

以下是录制方案，仓库目前没有演示视频文件。录制前启动工作台，确认使用内置测试数据，并检查规划模式；无需模型密钥即可完成这些场景。

| 时间 | 画面与操作 | 建议讲解 |
| --- | --- | --- |
| 0–10 秒 | Dashboard 与 `Demo templates` | “AutoQA 将规划、执行、校验和故障诊断组织为可追踪流程。这里使用演示模板，浏览器与工作流真实运行。” |
| 10–30 秒 | 「登录与多页面」→「生成测试计划」→ 审核并执行→ 查看执行详情 | “8 个步骤覆盖登录和跨页面断言，每次尝试保留截图与 DOM。” |
| 30–50 秒 | 「接口故障」→ 查看失败任务与证据 | “这个场景故意返回 HTTP 500。系统按 Network 证据归类故障，结果符合预期。” |
| 50–75 秒 | 「表单审批」→ 展示待审批 → 批准 | “提交前由人工确认，LangGraph 保存暂停位置，批准后继续执行。” |
| 75–90 秒 | 导出报告，展示截图与 Trace 下载入口 | “报告记录结果、尝试次数和诊断依据，便于复核与回归排查。” |

总时长可通过剪掉等待缩短，保留真实操作顺序与结果。原有 18 项及新增 4 项报告测试，共 22 项，来自独立自动化测试套件，不能作为一次 Demo 的“通过步骤数”。连接真实模型后的自然语言规划应另行录制并说明模型与测试环境。

## Studio 新版素材

新版截图已生成：`studio-dashboard.png`、`studio-workflow.png`（计划审核）、`studio-report.png`、`studio-issues.png`、`studio-empty.png` 与 390 / 768 / 1440px 响应式截图，均位于 `docs/screenshots/`。图中数据来自隔离实例的真实 Demo 运行。

## 截图与架构图位置

| 素材 | 路径 | 当前状态 / 推荐画面 |
| --- | --- | --- |
| 工作台总览 | `docs/screenshots/workspace.png` | 保留的原版截图；新版 README 展示 studio-dashboard.png |
| 登录成功 | `docs/screenshots/login-success.png` | 待补充；显示 `passed`、8 个步骤与最终订单页面截图 |
| 审批暂停 | `docs/screenshots/approval-pending.png` | 待补充；显示提交步骤前的待审批状态与批准 / 拒绝操作 |
| HTML 报告 | `docs/screenshots/report.png` | 待补充；显示结果、步骤耗时、尝试次数与诊断依据 |
| 展示视频 | `docs/demo/autoqa-demo.mp4` | 待录制；完成后再添加 README 链接，较大文件可使用视频平台链接 |
| 系统架构 | README 的 Mermaid 图 | 已有；展示服务、工作流、存储与可选集成 |
| 实际状态图 | `docs/architecture.md` 的 Mermaid 图 | 已有；展示审批、重试、失败与结束分支 |
| 架构图片导出 | `docs/screenshots/architecture.svg` | 可选，尚未生成；仅在简历附件或平台无法显示 Mermaid 时补充 |

待补充的文件只列位置，不添加失效的图片链接。建议统一使用 1440px 宽桌面视口，确保截图中的关键结果和证据清晰；仅展示演示账号与测试数据。已有图中的 43% 是包含故意失败任务的通过率，与 7 / 7 场景预期匹配结果不同。

## 6 分钟演示

1. 双击 `start.cmd`，打开测试工作台。说明当前是 Demo Mode：不调用模型，但浏览器、图调度和证据全部真实执行。
2. 选择「登录与多页面」。创建任务，展示 8 个步骤、跨页面会话、最终订单截图，下载 HTML 报告和 trace。
3. 选择「接口故障」。说明站点故意返回 HTTP 500，展示 Network 证据与 `network_error`，解释 HTTP 错误不能仅依赖 requestfailed。
4. 选择「前端异常」。展示真实 JavaScript 异常与 `frontend_error`，并比较网络错误与前端错误的证据差异。
5. 选择「表单审批」。在提交前展示暂停状态，批准后继续执行。说明使用 LangGraph interrupt 和持久化检查点。
6. 选择「延迟与重试」。展示失败尝试与成功尝试。说明只读断言可重试，提交类动作不自动重放。

连接真实模型后，再在授权的测试环境展示自然语言规划。不要将离线演示说成模型真实推理，也不要以固定模板测试结果代表开放网站的成功率。

## 项目介绍

> 我实现了一个面向 Web 回归测试的 Agent 服务。它把测试需求转成受 Schema 约束的步骤，通过 LangGraph 编排规划、浏览器执行、校验与失败分析。浏览器层使用 Playwright，保存每步 DOM、截图、Console、Network 和 Trace；错误通过实际证据分类。计划和关键操作可以等待人工审批，图状态有持久化检查点，服务重启不会自动重放已提交操作。FastAPI 提供任务接口，业务数据支持 MySQL，另有 Docker Compose、评测脚本和浏览器 E2E 测试。

## 常见问题

**为什么用 LangGraph？**

测试既有线性步骤，也有重试、失败分支和人工审批。图将这些分支与状态变更写明；checkpoint + interrupt 让暂停恢复有真实执行语义。

**怎么确定失败原因？**

先区分响应错误、传输失败和前端异常，然后结合定位错误、断言和 DOM。现阶段分析器是规则驱动的证据归类，不能仅凭 HTTP 500 确定后端哪一行有 Bug。

**服务重启能接着运行吗？**

计划审批可以：图的执行点已保存，尚未发生业务动作。中途浏览器进程丢失时，检查点不能完整恢复页面，我返回明确冲突并保留证据，不自动重放提交。

**如何避免无限循环？**

计划步骤数量限制、每步超时、整体超时、只读重试次数、LangGraph recursion_limit 和并发上限共同约束。

**有多 Agent 吗？**

有四个职责明确的工作流节点，模型参与 Test Planner；Browser、Verifier 和 Failure Analyzer 是确定性工具节点。不是四个大模型互相聊天。

**Browser Use、MCP 和 Langfuse 是否都已验证？**

主执行器选用 Playwright，没有另外使用 Browser Use。MCP 和 Langfuse 属于可配置集成，验证范围以 `validation.md` 的实际记录为准。面试时明确区分已运行的能力与只完成接线的外部服务。

**为什么业务 MySQL，Checkpoint 却是 SQLite？**

任务、步骤和事件通过 SQLAlchemy 支持 MySQL；图检查点单独使用官方异步 SQLite Saver。项目当前单进程部署，这样本地开箱即可运行。若扩展成多实例，会迁移到集中式 Checkpoint 与任务队列。
