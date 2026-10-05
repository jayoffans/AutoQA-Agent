# Phase 6-2-C-1-B — AI Test Planner Agent

本阶段新增从已上传 OpenAPI 生成、保存和查看 API 测试计划的 Production 流程。
未新增 API 执行、Playwright 执行或测试报告，未合并 main。

## 修改文件

| 文件 | 变化 |
| --- | --- |
| `autoqa/test_planner.py` | TestPlannerAgent、JSON Pydantic Schema、中文测试专家 Prompt、批次/覆盖/归属验证 |
| `autoqa/test_plan_api.py` | 生成、列表、详情和用例 API、原子保存和固定失败提示 |
| `autoqa/product_models.py` | TestPlan、TestCase 和关联约束 |
| `migrations/versions/0007_test_planner.py` | 非破坏性增量迁移 |
| `autoqa/product_app.py` | 注册 Production Router，重启恢复中断计划 |
| `autoqa/api_import.py` | 拒绝删除已被计划引用的文档，返回友好 409 |
| `autoqa/static/production.js` | 启用生成按钮、选择文档、状态/数量/分类统计、详情路由 |
| `autoqa/static/production.css` | 深色计划卡片、分类统计、移动端用例布局 |
| `autoqa/static/locales/zh-CN.json`、`en-US.json` | 计划界面文案 |
| `pyproject.toml` | wheel 包含新迁移 |
| `.github/workflows/ci.yml` | MySQL 计划兼容测试、浏览器截图 artifact |
| `tests/test_test_planner.py` | Agent / API / 持久化 / 权限 / 安全测试 |
| `tests/test_test_planner_compat.py` | SQLite / MySQL 迁移、持久化、数据库隔离契约 |
| `tests/test_test_planner_browser.py` | 实际 Chromium 生成/详情/失败/刷新/移动端/XSS 验收 |
| 既有 migration head 测试 | 更新到 `0007_test_planner`，保留已有契约 |
| `tests/test_api_import_browser.py` | 原无模型测试现在验证配置模型入口可见 |
| `README.md`、本报告 | AI Test Planner Architecture、接口与限制 |

## Agent 架构

OpenAPI → ApiSpec / ApiEndpoint → TestPlannerAgent → ProviderGateway → 用户的 BYOK Provider
→ PlannerOutput / PlannedCase → TestPlan / TestCase。

Agent 只依赖 Gateway。未在业务代码中直接调用 OpenAI 或 DeepSeek。
接口批次携带解析好的参数、请求/响应 Schema、文档内 definitions/components、安全定义和关联接口 ID。
模型名称和 Provider 在开始生成时固定，使用相同加密配置完成所有批次。
配置不进入 Prompt，不使用环境密钥或 Production MockProvider 回退；测试只注入 Mock HTTP Transport。

## 数据模型与状态

新逻辑模型叫 `TestPlan` 和 `TestCase`，物理表使用 `ai_test_plans` / `ai_test_cases`，
因为已有 Demo 使用 `test_plans` / `test_cases`，不能覆盖其 task-based 契约。

- TestPlan：id、workspace_id、project_id、api_spec_id、status、provider、model、created_by、created_at、updated_at。
- TestCase：id、workspace_id、plan_id、endpoint_id、name、category、priority、description、JSON steps、expected_result、created_at。
- 计划支持 draft / generating / ready / failed / approved；生成流程使用 generating → ready / failed。
- 数据库复合外键保证计划引用相同项目/工作区的规范，Case 引用相同工作区的计划和接口。
- Agent 校验 Case 接口属于所选规范的当前批次，拒绝跨规范输出和重复用例名称。
- 所有批次校验成功后一次提交 Cases 和 ready；失败只保留 failed 计划，不保留部分结果。
- 请求取消和服务启动恢复会将中断的 generating 计划标记 failed。

## API

| 方法与路径 | 结果 |
| --- | --- |
| POST `/api/projects/{project_id}/test-plans/generate` | 201 返回已持久化计划与数量/分类统计 |
| GET `/api/projects/{project_id}/test-plans` | 项目计划历史 |
| GET `/api/test-plans/{id}` | 计划元信息与统计 |
| GET `/api/test-plans/{id}/cases` | 结构化用例列表 |

生成可省略请求体，也可传 `{"api_spec_id":"…"}`；省略文档 ID 时选择最新文档。
依次校验 Workspace / 项目权限、模型配置、规范与接口。没有模型或文档返回中文 422；
跨工作区与归档项目返回 404。失败返回 502，detail 包含固定中文 message 和 plan_id。
模型响应、异常体和 Key 均不进入错误信息或日志。Gateway 对响应中的实际 Key 回显进行遮蔽。
已被计划引用的文档禁止删除，返回中文 409。

## Prompt 策略

仅输出 JSON，禁止 Markdown 前言；每个接口必须覆盖 positive / negative / boundary / security。
输出包含 endpoint_id、中文名称与说明、P0–P3 优先级、描述性步骤数组、明确预期结果。
不编造文档中未定义的字段、凭据或保证；未知边界/鉴权/错误码注明需要确认的假设。
文档描述、示例和 Schema 视为不可信数据，不接受其中要求改变任务或索取 Key 的指令。
Pydantic strict + extra=forbid，限制字段长度和案例数量，并补充接口归属、四类覆盖、重复名称验证。
任何批次失败都不保存非法结果或部分 Cases。

## UI 流程

API 项目 → API 测试 → 上传文档 → 配置/启用模型 → 选择文档 → 生成测试计划。
显示生成中状态并禁止重复点击，成功后进入可刷新的计划详情页；
历史卡片显示状态、数量和四类统计。失败卡片显示零用例、友好错误，允许重新生成。
详情展示接口、分类、优先级、步骤和预期结果；所有模型内容经 HTML 转义。
采用既有深色 SaaS 样式，验证 1440 px 桌面和 390 px 移动端，无横向溢出。

## 验证

执行完整 pytest 回归，以及新增 Planner 专项和受影响的 OpenAPI Chromium 验收。
最终按测试 ID 去重、以修复后的最近一次结果为准：**382 passed、29 skipped、0 failed**。
新增 Planner 测试共 **56 项**，其中 **54 passed、2 skipped**（MySQL 参数）。

| 验证 | 结果 |
| --- | --- |
| 首次全量回归 | 375 passed、1 failed、29 skipped，755.69 秒 |
| 修复后的 Planner + OpenAPI 浏览器重测 | 57 passed、2 skipped，109.84 秒 |
| 增强后的规范/接口保留迁移测试 | 2 passed、2 skipped，10.59 秒 |
| Ruff / JS syntax / git diff check | 全部通过 |

全量运行中的唯一失败是旧 OpenAPI 浏览器断言要求没有“配置模型”入口。
本阶段增加该入口后，已更新旧断言并在最终专项中重测通过。
专项曾发现 SQLite 读取时间戳与生成响应时区格式不同，已统一输出 UTC `Z`，
最终持久化、刷新和 Chromium 验收全部通过。本表保留各次运行结果，汇总不代表另一次全量重跑。
可机读结果见 [verification.json](assets/phase6-2c-1b/verification.json)。

UI 验收截图：[桌面](assets/phase6-2c-1b/planner-desktop.png)、[移动端](assets/phase6-2c-1b/planner-mobile.png)。
截图中的 HTML/script 字符串是用于验证转义的测试数据，未执行。

```powershell
.venv/Scripts/python.exe -m pytest -q --junitxml=work/phase6-2c-1b-tests.xml
.venv/Scripts/python.exe -m pytest tests/test_test_planner.py tests/test_test_planner_compat.py tests/test_test_planner_browser.py tests/test_api_import_browser.py -q --junitxml=work/phase6-2c-1b-focused.xml
.venv/Scripts/python.exe -m ruff check autoqa tests scripts migrations
node --check autoqa/static/production.js
node --check autoqa/static/i18n.js
git diff --check
```

测试覆盖：无模型、Mock Provider HTTP 生成、非法 JSON/Schema、非法接口 ID、四类覆盖、重复输出、
大小/数量限制、选择不同文档、完整 Case 保存、跨用户/工作区隔离、异常/HTTP 401/429/500、
超时、凭据回显、数据库失败回滚、批次失败原子性、会话模型、重启恢复和浏览器完整流程。
模型调用全部是 fake transport，不访问真实 LLM；生成测试确认没有 Task 和目标接口请求。
MySQL 用相同参数化兼容测试进入 CI，本地没有提供独立 MySQL URL 时明确跳过。

## Demo 影响

Demo 路由、页面、Provider 和 task-based TestPlan / TestCase 未修改。
数据库只有附加迁移；原有 Demo 表与数据保留，Production Planner Router 仅注册在 Production App。

## 后续限制

每份计划最多 20 个接口，每批 2 个，最多 12 个案例/接口；单批上下文 200 KB，整次超时 120 秒。
目前同步生成，重试创建新计划；尚无任务队列、跨进程任务租约、批次断点恢复或自动重试。
启动恢复遵循当前单进程部署约定；不支持同时多个 worker 启动时维护活跃生成租约。
approved 是预留状态，审批 API 未实现。API 执行、Playwright 和报告均属于后续阶段。
本地仅验收 Mock LLM 和 SQLite；真实模型、MySQL 服务和 Docker 验收依赖相应运行环境。
