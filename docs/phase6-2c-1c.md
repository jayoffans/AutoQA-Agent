# Phase 6-2-C-1-C — Human Review + API Execution Engine

实现日期：2026-10-02（Asia/Shanghai）。分支：`codex/phase5-real-product`。

## 1. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `autoqa/api_executor.py` | OpenAPI 默认参数、结构化请求验证、httpx 执行和脱敏结果 |
| `autoqa/test_execution_api.py` | 请求预览、人工审核、执行、运行历史和结果详情 |
| `autoqa/product_models.py` | TestRun、TestExecution、审核字段和加密 Case 配置 |
| `autoqa/product_app.py` | 接入生产路由和执行器，重启恢复中断状态 |
| `migrations/versions/0008_api_execution.py` | 可升级 / 回退的独立迁移，保留旧 Demo 数据与表 |
| `autoqa/static/production.js` | 审核表单、执行按钮、计数轮询、历史和响应详情 |
| `autoqa/static/locales/zh-CN.json`、`en-US.json` | 中英文审核、执行、限制提示 |
| `tests/test_api_execution.py` | Mock HTTP、状态、权限、SSRF、错误和持久化验收 |
| `tests/test_api_execution_browser.py` | Chromium 审核 / 执行 / 刷新 / 移动端 / 转义验收 |
| `tests/test_api_execution_compat.py` | SQLite / MySQL 迁移、持久化、外键和 DDL 验收 |
| 既有 6 个数据库 / 部署测试文件 | migration head 更新至 0008，保留原有断言 |
| `pyproject.toml` | wheel 打包新增迁移 |
| `.github/workflows/ci.yml` | MySQL CI 新增兼容测试，收集 UI 截图 |
| `README.md`、本文 | Execution Architecture 和本阶段报告 |

更新的既有测试文件：`test_api_import_compat.py`、`test_docker_production.py`、`test_onboarding.py`、
`test_product.py`、`test_production_compatibility.py`、`test_test_planner_compat.py`。

## 2. 数据模型

- `TestRun` → `api_test_runs`：id、workspace_id、project_id、plan_id、status、total_cases、passed_cases、
  failed_cases、started_at、finished_at、created_by；另有唯一 `active_plan_id`，禁止同计划并发运行。
- `TestExecution` → `api_test_executions`：id、workspace_id、run_id、case_id、method、url、request_data、
  response_status、response_body、passed、error_message、duration_ms；另有 `plan_id`，通过复合外键
  保证 Run 和 Case 必须属于同一个计划及工作区。
- `TestPlan` 增加 approved_base_url、approved_by、approved_at。
- `TestCase.execution_config` 保存结构化请求及预期状态码的 AES-GCM 密文 envelope，绑定工作区、
  计划和 Case ID 的 associated data；公开预览与结果脱敏。
- 同一个 Run 的同一个 Case 只能保存一份执行记录；计数不允许为负或超过总数。

## 3. 执行架构

```text
TestPlan (ready)
  → Human Review（完整 Case 请求 JSON + expected_status）
  → TestPlan (approved，固定 Base URL / 请求 / 状态断言)
  → TestRun (running)
  → ApiExecutor（串行 httpx，不调用 LLM）
  → TestExecution（每条原子保存结果与 Run 计数）
  → TestRun (passed / failed / interrupted)
```

每条请求只执行一次，状态码按人工确认的预期比较。HTTP 错误记为该 Case 失败并继续下一条；
保存错误、整次超时、请求取消或重启记为 interrupted，保留已提交结果。
没有自动恢复 HTTP 写入；再次点击执行会创建新的 Run。

## 4. API 列表

| API | 返回与约束 |
| --- | --- |
| `GET /api/test-plans/{id}/execution-preview` | 200：OpenAPI 样例请求、审核标记、默认参数错误；不发目标请求 |
| `POST /api/test-plans/{id}/approve` | 200：ready → approved；完整审核 body 格式见 README |
| `POST /api/test-plans/{id}/execute` | 201：同步完成的 TestRun；只允许 approved；已有活动运行返回 409 |
| `GET /api/test-plans/{id}/runs` | 200：持久化历史，执行中可读取部分计数 |
| `GET /api/test-runs/{id}` | 200：Run 和逐条 TestExecution |

未登录返回 401，Cookie 请求无有效 CSRF 返回 403，外部工作区 / 未知 / 归档对象返回 404，
非法审核或公网目标校验失败返回 422，未审核 / 重复审核 / 目标变更返回 409。
执行中断返回固定中文 503 和 run_id，避免泄露原始网络或存储错误。

AI 生成的 Case 目前是文字说明。接口不会把自然语言当成请求或断言；异常、边界和安全类别必须
提交人工确认的结构化配置。全为 positive 的计划可省略审核 body 并使用默认请求。

## 5. 安全措施

- 审核、执行前、每条请求、建立连接时均校验公网目标；整个 DNS 结果集合存在非公网 IP 即拒绝。
- 禁止 localhost、IPv4 / IPv6 回环、私网、链路本地、云元数据及非 HTTP(S)；不受旧流程私网开关影响。
- 仅允许 80 / 443 / 8080 / 8443 端口；固定已校验 IP，TLS SNI 和 Host 保留原始域名。
- 请求路径只能来自关联 OpenAPI Endpoint；拒绝路径穿越、URL override、Host 与传输 Header 覆盖。
- 不跟随重定向、不读环境代理、不重试。每用例 10 秒、Run 120 秒、请求 64 KB、响应 256 KB、
  最多 240 Case，递归 Schema 和默认数组 / 字符串也有界限。
- 复用登录、工作区成员权限、归档隔离和 CSRF；数据库复合外键阻止跨计划 / 工作区结果混入。
- 审核配置加密；按整次运行的请求敏感值处理响应回显，已保存请求及响应脱敏；前端转义全部内容。

## 6. UI 流程

项目 → API 测试 → 生成 / 查看测试计划 → 设置项目公网 Base URL → 逐条编辑请求及预期状态码 →
审核通过 → 执行 API 测试 → 查看运行状态、通过 / 失败数和响应详情。

未审核时执行按钮禁用，审核后配置只读；运行时按钮禁用并轮询计数。刷新保存审核与历史；
workspace 切换不展示上一工作区的运行结果。支持中文 / 英文和 390px 视口。
默认请求仅是样例，页面明确提醒异常、边界、安全请求需人工调整；只比较状态码。

## 7. 测试结果

专项：**74 passed，1 skipped**（未配置真实 MySQL）；Chromium：**2 passed**。
新增共 77 个参数化测试实例，使用 HTTP MockTransport / ASGI Mock Server 与假凭据，
不调用真实外部 API 或模型。包含审核原子性、非法状态、完整 Case 集、权限、CSRF、
持久化 / 刷新、并发、DNS 重绑定、目标变更、私网 / 元数据 / 重定向、路径与 Header 注入、
默认参数、响应超限、超时、错误继续、跨用例脱敏、超时 / 重启恢复和浏览器内容转义。

完整回归：**458 passed，30 skipped，0 failed**，耗时 898.13 秒；原有 382 passed
保留，本阶段增加 76 项通过测试和 1 项 MySQL 跳过实例。30 项跳过包括 25 项真实 MySQL
验收、4 项 Docker 验收及 1 项 Windows 缺少符号链接权限的测试。相关验收明确跳过，
不将 SQL 编译检查当作真实 MySQL 验收。

Ruff、JavaScript 语法、Git whitespace 检查通过；离线构建 wheel 成功，并包含新增迁移。
全仓历史密钥扫描返回非零：既有且未修改的 	ests/test_provider_center_compat.py 中假密钥常量
命中了长度模式；它带 fixture 标记并用于本地兼容测试。本阶段修改 / 新增文件无该模式匹配，
未发现禁止提交的数据库 / 日志文件；没有修改历史或扩大修复范围。

本地记录：`work/phase6-2c-1c-unit.xml`、`work/phase6-2c-1c-browser.xml`、
`work/phase6-2c-1c-full.xml`；截图：`work/phase6-2c-1c-ui/`。

## 8. Demo 影响

新模型表、路由与执行器只接入 Production。旧 Demo 的任务、计划审核、浏览器、API 场景、
报告和 seed 不修改。历史迁移文件不改动；迁移增加数据表 / 字段，保留旧数据。
新增执行链不会产生旧任务记录、报告或自动调用 AI 分析。

## 9. Git

commit message：`feat: add api execution engine`。
继续 `codex/phase5-real-product`，未 merge main；没有推送或部署。
本阶段单个提交的 hash 由最终答复提供。

## 10. 后续限制

本阶段只实现结构化 API 请求与 HTTP 状态码断言。尚未接入项目环境变量 / 凭据替换、
Cookie / 表单 / 文件上传、响应 Schema / 内容断言、复杂参数序列化、用例间数据依赖、
自动构造负向测试、AI 结果分析、Web 测试或报告系统。
执行在请求生命周期内串行完成，没有分布式队列、多 worker 租约、自动续跑、重试或业务事务回滚。
单进程启动恢复会把遗留 running 标记为 interrupted；主动重跑可能重复产生业务写入。
