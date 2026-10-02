# Phase 6-2-C-1-E — API Quality Report

分支：`codex/phase5-real-product`。施工基线：`3b021c6`。
本阶段完成 API Run → Deterministic Quality Report → Finding → Evidence → HTML / Markdown Export → Optional AI Analysis。
不部署 Railway，不 merge main，不 push，不进入下一阶段。

## 1. 事实与复用

报告只读取已保存的 TestRun、TestExecution 和 Assertion Result，不重新执行 HTTP 或 assertion evaluator。
分类和计数复用 Phase D 的 `execution_status()` / `run_summary()`；通过率为 passed / total × 100，
执行错误与 pending 计入分母，total=0 返回 null，UI 显示 `—`。
Run 耗时使用已保存的起止时间。Case 耗时、HTTP status、assertion expected/actual 均来自执行证据。
Planner 的描述性 expected_result 不覆盖人工审核后的执行预期。

旧 `ReportRow`、`FindingRow`、Task Report API、旧 metrics/issues/html、Store.finalize 和 Demo seed 保持兼容。
API Finding 不写入旧 FindingRow；API Run 不调用旧 finalizer，也不改写旧 Run/Execution。

## 2. 数据与生命周期

新增 `0010_api_reports`，不修改 0001–0009：

| 变更 | 行为 |
| --- | --- |
| reports.run_id | nullable，保留旧 UNIQUE / FK |
| reports.api_run_id | nullable，UNIQUE / FK 到 api_test_runs |
| ck_report_source | 两种来源恰好一个非空 |
| reports.analysis | nullable JSON，独立保存最近成功 AI overlay |
| reports.summary | API 来源保存 schema_version=1 的确定性快照 |

API Report 的 HTML 字段为空，按快照与 overlay 安全渲染导出；旧 Report 的已保存 HTML 不变。
存在 API Report 时拒绝 downgrade；空库可 downgrade / re-upgrade。
MySQL downgrade 必须先删除 API 外键再删除其索引，已通过真实实例验证。

首次 GET report / findings / export 确定性物化；每个终态 Run 唯一一个报告。
并发首次创建由 UNIQUE 收敛；重复读取、重启保持同一 ID、created_at、metadata 和 Finding。
Running 返回 409。Interrupted 可生成 partial report，明确保留 pending / incomplete。
Rerun 创建独立 Run 和 Report。

metadata 展示名、Scope 在首次物化时快照，不声称是执行开始时存储的名称。
Run 没有独立 created_at，报告 created_at 是报告物化时间。
历史分类推断保留 classification_inferred；缺失断言证据显示未保存 / 不可恢复，禁止补造。
已保存 HTTP evidence 没有 response headers，本阶段不虚构该部分证据。

## 3. Finding 与 Severity

每个 Failed Execution 生成一个聚合 Failure Finding，独立 Execution Error Finding 只用于执行异常。
Passed / Pending 无 Finding；稳定 ID 由 Run / Execution ID 派生，含 source execution、project、run、
category、expected/actual 摘录、evidence reference、保守建议、root_cause_confirmed=false。
完整断言仍保存在原 Execution，摘要保留裁剪标记与证据可用性。

| 事实 | Severity |
| --- | --- |
| execution_error | Low，执行异常 |
| Failed + security + 预期401/403 + 实际2xx | High，审核拒绝预期未满足 |
| Failed + 非预期500–599 | High |
| 其他 Failed / 信息不足 | Medium |

Phase E 无 Critical，不根据 Endpoint 名称、Planner P0 或 AI 文本升级。
预期500且 Execution Passed 时无 Finding；非标准600响应不归入服务端5xx规则。AI suggested_severity 不覆盖上述分级。

## 4. API 与导出

| API | 行为 |
| --- | --- |
| GET /api/test-runs/{id}/report | 确定性快照 + optional overlay |
| GET /api/test-runs/{id}/findings | 同一快照的 Finding + optional analysis |
| GET /api/test-runs/{id}/executions/{execution_id} | 单项已保存的脱敏详细证据 |
| POST /api/test-runs/{id}/analyze | 显式选择1–3项，refresh默认false |
| GET /api/test-runs/{id}/report.html | HTML attachment |
| GET /api/test-runs/{id}/report.md | Markdown attachment |

所有入口先 owned_run → owned_plan，再使用授权 Run 查询；跨 Workspace / 归档项目沿用404。
无 Provider 仅 analyze 返回422，报告/证据/导出正常。无效选项422，并发分析409，Provider/结构化输出失败502。
报告读取、Finding、证据和导出不触发模型。

导出使用稳定快照、最新成功 overlay 和不可变 Execution 证据，按项流式渲染，不批量复制完整正文。
request/response 摘录最多4096 UTF-8 bytes，assertion expected/actual 摘录各最多512 bytes，
保留断言状态、reason_code 和裁剪标记。产品内仍可查看完整已保存的脱敏单项证据。
文件名使用已验证 Run UUID；非 UUID 历史 ID 使用新生成的 Report UUID，避免 header injection。
attachment / UTF-8 / no-store / nosniff；HTML 全部 escaping，无脚本或外部资源，使用限制性 CSP。
Markdown 不可信内容放入安全代码块，围栏长于内容中反引号序列，不直接拼接模型 Markdown。
不实现 PDF，不解密执行配置或环境 Secret，不重新读取目标 HTTP 响应。

## 5. Optional AI Analysis

ResultAnalyzerAgent 仅通过 Workspace → Provider Config → Provider Gateway。
没有 developer Key fallback，Production 继续禁止 MockProvider 配置；CI 使用注入假 Gateway / transport。
只有用户确认“AI 分析失败原因”才调用；UI 在默认分析和重新分析前都说明对应模型费用。

每次选1–3项；已成功项默认复用。模型只接收本次未缓存项，返回 IDs 必须与该实际输入集合完全匹配、无重复。
refresh=true 重新分析所有本次选中项，并重新提示费用。单次最多一次分析生成，无自动重试/分批收费。
进程内 per-run guard 拒绝并发收费；只保存成功结果，取消/重启不会持久化阻塞状态。
失败 refresh 保留旧成功 overlay；不创建分析历史表或复杂版本系统。
网络调用后重新检查用户、会话、Membership 和 Run 权限，再原子替换 JSON overlay。

输入只含 endpoint / method / case / category / deterministic severity / expected / actual / HTTP status / error_code /
相关断言及脱敏 response excerpt，不含请求 headers/body/credentials、API Key、OpenAPI、数据库、源码或日志。
每项最多16000 bytes、整个context最多64000 bytes；response最多4096 bytes，expected/actual各512 bytes，
最多8条相关断言，超限按固定顺序裁剪并标记省略数量。Analyzer 总超时60秒。

严格 Pydantic schema：execution_id、analysis_summary≤240字符、possible_causes最多2项×120字符、
recommendations最多2项×120字符、有限 confidence 0–1、建议Severity High/Medium/Low/null。
额外字段、错误IDs、事实覆盖、确认根因字段、明确根因确认语句及非法编码均拒绝，不落库。
固定展示 AI Analysis / AI Suggestion / Root Cause Not Confirmed；root_cause_confirmed 服务端保持false。
System prompt 忽略证据中的指令，不得虚构源码、日志、数据库状态或 stack trace。
结构/语句校验不能证明自由文本假设真实；AI 内容始终是未核实建议，不能代替根因调查。

## 6. Production 与安全

终态执行结果提供“查看测试报告”，路由 #test-runs/{run_id}/report。
展示 Summary、Pass Rate、Scope、所有用例、独立 Failure / Execution Error Findings、按项 Evidence、AI Suggestions 和导出。
复用深色样式、details/pre、i18n 和响应式布局；没有 chart library 或复杂 Dashboard。
终态报告不轮询。身份/Workspace/Run 切换清除状态；迟到的数据和导出响应不能覆盖当前身份或创建旧报告下载。

脱敏复用 Phase D execution_privacy、scrub、safe_text，无第二套 Secret 规则。
报告不解密审核/环境密钥；Provider Key 仅在 Gateway 内处理，不进入 prompt / Report / Export。
HTML / Frontend escaping、Markdown fences、输入/展示裁剪、CSRF、Workspace隔离、安全文件名均有测试。
Provider错误与Pydantic异常不返回原始内容；成功输出的Key回显也由既有Gateway/脱敏处理隐藏。

## 7. 验证结果

最终全量：**609 passed / 5 skipped / 0 failed**，614个用例，耗时**788.44秒**。
最终JUnit：`work/phase6-2c-1e-final-acceptance.xml`。其中27个真实MySQL运行用例均通过，无MySQL跳过；
这27项不含DDL编译/URL格式测试。新增Phase E报告、AI、浏览器和迁移用例共48项均通过。
上一轮全量：608 passed / 5 skipped / 0 failed；之后收紧了5xx Severity范围并增加600响应回归，上一轮不替代最终结果。

专项结果：报告/AI41 passed；Production报告Chromium3 passed；SQLite/真实MySQL报告、执行与Production兼容38 passed，0 skipped。
对应JUnit：`work/phase6-2c-1e-final-severity.xml`、`work/phase6-2c-1e-browser-final.xml`、`work/phase6-2c-1e-real-mysql-final.xml`。
新增测试覆盖事实前后完全相等、Evaluator禁止调用、并发物化、历史缺失证据、AI费用确认与无效输出、
权限撤销、工作区迟到下载、安全导出和真实旧库升级。

Studio浏览器12项、HTTPS Public Demo浏览器8项通过，JavaScript errors均为0。
产物在 ignored work/phase6-2c-1e-studio、work/phase6-2c-1e-public-demo、work/phase6-2c-1e-ui。
Public Demo仍为六Case、5 passed/1 failed、100% execution coverage，保留旧报告与Finding语义。

使用已有官方MySQL 8.4.11 Windows二进制，创建新的隔离数据目录、随机loopback端口与凭据；
每轮测试新分配独立数据库，启用AUTOQA_REQUIRE_MYSQL。没有访问真实业务API或真实模型。
本地运行CI同组，不代表远端GitHub Actions已运行。

Ruff、五个JS syntax、git diff --check、部署结构和Production配置验证通过。
当前源码的isolated wheel构建成功；Zip内容核对新增模块/0010 migration，并确认测试helper和本地MySQL helper未打包。
在独立venv安装wheel后，复用既有已验收依赖并初始化Windows依赖site路径；确认autoqa模块均从安装venv导入、
源码SHA256与当前工作树一致，再使用wheel内migration对新的SQLite数据库连续upgrade两次，head为0010_api_reports。
这不是无依赖运行验收，依赖与产品包导入来源已分别核对。

最终wheel：`work/phase6-2c-1e-final-acceptance-wheel/autoqa_agent-2.0.0-py3-none-any.whl`。
SHA256：`385ed8800ab1f1d5ab9a84cf3c429c4dbb304fb23364f84eb0734cf4a7e81432`。
检查记录：`work/phase6-2c-1e-final-checks.json`、`work/phase6-2c-1e-final-wheel-verification.json`。
本机无Docker CLI/engine，四个Docker runtime用例跳过；Windows无symlink privilege，一个artifact symlink用例跳过。
pytest的四个Docker原始skip提示为“Set AUTOQA_RUN_DOCKER=1 to build and run disposable production containers”；
已确认本机缺少Docker CLI/engine，因此未启用该runtime验收。Symlink原始skip为“Windows symlink privilege unavailable; this test runs in Linux CI”。
不把Docker structural validation视为runtime成功。

## 8. 28项完成条件

28项均满足；逐项证据如下。第28项由最终提交后的git status空输出确认并在最终回复记录。

| # | 完成条件 | 状态 / 证据 |
| --- | --- | --- |
| 1 | 无Provider完整报告/Finding/Evidence/Export | 通过；no-provider API与browser测试 |
| 2 | Summary复用已保存事实 | 通过；snapshot、no-evaluator、fact-equality测试 |
| 3 | passed/total通过率 | 通过；mixed / interrupted / zero测试 |
| 4 | Failed与Execution Error分离 | 通过；report分类、Finding及browser区域 |
| 5 | Report读取零模型调用 | 通过；no-provider/model-call count测试 |
| 6 | 一个Run一个稳定Report | 通过；UNIQUE、并发、restart测试 |
| 7 | 重复读取无重复Finding | 通过；deterministic IDs、snapshot与旧Finding表计数 |
| 8 | Rerun独立Report | 通过；新旧快照独立测试 |
| 9 | Finding可追溯Execution | 通过；source_execution_id及授权证据API |
| 10 | Expected/Actual来自执行 | 通过；assertions、null/missing及历史兼容测试 |
| 11 | AI最多3项 | 通过；schema、selection及browser测试 |
| 12 | AI调用前费用提示 | 通过；confirm dialog/cache/refresh browser测试 |
| 13 | 无developer Key fallback | 通过；既有Gateway、假Developer Key fixture与fake transport |
| 14 | AI不能修改事实 | 通过；拒绝覆盖字段、前后Run/Execution/Assertion完整相等 |
| 15 | 根因未确认 | 通过；server-owned false、确认字段/语句拒绝及固定标签 |
| 16 | invalid AI不落库 | 通过；JSON/schema/IDs/语句/timeout测试 |
| 17 | failed refresh保留成功结果 | 通过；invalid/provider failure refresh测试 |
| 18 | 安全HTML/Markdown导出 | 通过；escaping/fences/redaction/headers/byte-limit测试 |
| 19 | Workspace隔离 | 通过；Report/Finding/Execution/Analyze/Export IDOR、权限撤销与迟到导出 |
| 20 | real MySQL compatibility | 通过；isolated MySQL full/compat JUnit |
| 21 | 旧库升级 | 通过；populated revision9→10、repeat upgrade及数据相等 |
| 22 | Production/Studio/Demo回归 | 通过；full browser suites、Studio12/Public8 |
| 23 | full pytest零失败 | 通过；最终final-acceptance JUnit |
| 24 | Ruff/JS/wheel/migration | 通过；最终质量检查及installed-wheel验证 |
| 25 | 如实记录skip/限制 | 通过；4 Docker + 1 symlink，远端CI未运行 |
| 26 | 无范围外功能 | 通过；diff范围审查；无PDF/queue/HA/billing等 |
| 27 | 本验收文档 | 通过；docs/phase6-2c-1e.md |
| 28 | 最终工作区干净 | 通过；commit后git status --porcelain；最终回复给出检查 |

## 9. 限制与交付

沿用单进程和进程内同步请求，没有多worker协调、队列、HA、复杂版本、自动修复或issue创建。
模型费用无法撤销；超时/断网时Provider可能已收费，系统不自动重试。
不恢复历史未保存证据，完整证据仍受Phase D保存边界限制。
API Report快照与AI overlay分开，数据库管理员直接修改源事实不属于产品提供的编辑流程。

最终提交：`feat: add api quality reports`；commit hash、最终git状态和验收汇总见最终回复。
