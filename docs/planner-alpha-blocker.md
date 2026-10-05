# Planner Alpha Blocker 修复

日期：2026-10-03；最终验收：2026-10-04（Asia/Shanghai）。基线：`codex/phase5-real-product`、
`6afe0e98e24dd0ab3d7806a2dad20bfc056876e4`。本轮不调用真实 LLM 或连接测试。

## 原 Alpha 证据更正

第二次 Alpha 的安全诊断记录两批 Gateway 成功返回，然后在基线
`autoqa/test_planner.py:87` 抛出 ValueError。该行是每接口总 Case 数超过 12 的拒绝分支；
缺少四类覆盖的拒绝在第 85 行。基线源码、导入路径、字节码行号和纯内存 FakeGateway
复现均吻合。历史 Alpha 报告将第 87 行解释为缺 coverage，需按此记录更正。

第二批是 `/protected`、`/search`，已通过 JSON、Pydantic、当前批 endpoint 归属、
重复名称及每接口四类检查，随后触发数量上限。没有保存原始/parsed 模型响应，不能确定
是哪一个接口超限、精确 Case 数或类别计数，也不能评估语义准确性。首次失败原因仍为 Unknown。
两份计划 failed，持久化 Case/Run/Execution 均为零。

第二次已知 usage：input 4,621、output 5,288、total 9,909；这是两批小计，
不是全轮 Alpha 总量。首次请求数仅推断为 2，首次 usage 和费用 Unknown。

## Prompt 与严格验证

每个 endpoint 对应 path＋HTTP method 的 operation，必须包含 positive、negative、boundary、
security 各至少一条；每接口所有类别合计最多 12 条。同接口 name 唯一，只能引用当前批
已导入的 endpoint ID。每批最多两个接口，整个输出数组最多 24 条。

新 Prompt 优先每接口正好 4 条、每类一条；双接口批次目标 8 条，单接口目标 4 条。
明确 12 是所有类别合计上限，不是每类别上限。不因无输入参数编造字段；必要假设必须
标注“待人工确认”。目标数量不是新 validator 规则：合法的 5、6、8、12 条仍接受，13 条仍拒绝。

四类政策没有无参数或 `/health` 豁免。对于契约未定义的参数边界、鉴权或错误码，
保留待人工确认的假设；程序不补造、截断、复制或重新分类 Case。

所有批次通过后，Cases 与 ready 一次事务提交。任何生成/业务校验失败，Plan failed、
Persisted Case Count = 0；存储事务失败时回滚 Cases。不保存 partial/draft Case，不自动 approve。
Human Review、执行断言、SSRF 和 Analyzer 语义不变，没有数据库迁移或新增状态。

## 错误码与安全诊断

502 detail 保留 message、plan_id，新增 code、diagnostics。201 及计划 GET 契约不变。

| code | 原因 |
| --- | --- |
| json_parse | 响应/模型内容不能解析为所需 JSON 对象 |
| schema_validation | Pydantic 结构/枚举/数组上限未满足 |
| unknown_endpoint | 引用当前批之外的 endpoint |
| duplicate_case | 同接口重复名称 |
| missing_category_coverage | 每接口缺少必需类别 |
| endpoint_case_limit | 每接口所有类别合计超过 12 |
| context_too_large | 本地批次 context 超限，零模型请求 |
| provider_timeout | Provider HTTP 超时 |
| provider_failure | 受控的通用 Provider 错误 |
| planner_timeout | 整个 action 超时 |
| persistence_failure | Case/ready 事务失败 |
| planner_failure | 未分类的受控 Planner 失败 |

诊断只有计划 ID、批次、可信 endpoint ID、候选计数、每 endpoint 的分类计数/缺口、
12 条上限、模型请求/已收到响应计数、持久化数量和白名单数值 usage。候选不是可审核 Case。
完成结构与批次 ID 归属验证前，不记录模型的 Case 数/endpoint 统计；未知 ID 不回显。
不记录 Case 内容、Prompt、请求 body、Authorization、Key、raw response、traceback 或 Pydantic input。

安全日志事件为 planner_generation，SafeFormatter 只接受严格诊断模型，并在输出前重新校验；
不序列化任意日志 extra。Provider 元数据 callback 是可选项，默认 Gateway 返回仍为 dict，
ProviderError 的可选 code 不改变既有异常类型。OpenAI、DeepSeek、compatible 共用同一逻辑。

详细诊断仅在即时错误及安全日志中，刷新后的 failed 计划保留通用提示，可用 plan_id 对应日志；
没有 DB 诊断字段。前端诊断文字使用固定中英文模板并转义，不显示模型文本。

## 调用次数、usage 和预检

一次 Generate 最多 ceil(endpoint_count / 2) 次模型 POST：6 接口最多 3 次，20 接口最多 10 次。
第二批失败后不调用第三批，无自动 retry/repair/fallback。手动再次 Generate 创建新计划，
不复用之前候选。Provider 可选 model=auto 的 discovery GET 不算模型生成 POST。
生成前页面展示所选文档的接口数、每批两个、最多调用次数和无自动模型重试。

全部批次 context（含重复 definitions/security）先检查 200 KB 上限，再发第一条模型请求。
保持串行两接口 batching、45 秒 Provider HTTP timeout、120 秒 action timeout、4096 output token 上限。

只接受 Provider 提供的非负整数 usage：prompt_tokens、completion_tokens、total_tokens、
prompt_cache_hit_tokens、prompt_cache_miss_tokens。拒绝布尔值、负数、非整数和未知字段。
known_usage 是有证据的字段小计；usage_status 为 known/partial/unknown。
缺失 usage 显示 Unknown，部分已知明确全轮总量 Unknown，不填零、估 token 或推算人民币费用。
没有提供请求元数据的自定义 FakeGateway，其请求计数亦为 Unknown；首次预检失败可确定为零。

## 静态 fixture 与验证

`tests/fixtures/planner/deepseek-compatible-envelope.json` 明确标记 reconstructed fixture。
使用兼容 choices/message/content 字符串 envelope，Case 是人工合成脱敏内容；数字 usage 为
已有观测的静态回放，不是新的模型 usage。它不是第二次 Alpha 的原始输出。

新增测试覆盖数量边界、缺类别/接口、JSON/schema/归属/重复、安全日志、usage、三批调用上限、
后续 context 超限零请求、失败原子性、手动重新生成、SQLite/MySQL/重启/隔离及中英文 UI。
所有模型测试使用 FakeGateway/MockTransport/fixture。当前已完成的验收：

| 验收 | 结果 |
| --- | --- |
| Planner 专项（含真实 MySQL、Production Chromium） | 100 passed，0 skipped；恢复后从当前全量/MySQL 记录去重提取，先前独立专项亦为 100 passed |
| 全量 pytest，Linux Python 3.12 / 锁定依赖 | 624 passed、34 skipped、0 failed、0 errors；34 个可选用例均另有真实门禁通过记录 |
| CI auth-mysql 全范围，独立真实 MySQL 8.4 | 172 passed、0 skipped，包含 SQLite/MySQL、重启、隔离、HTTPS 浏览器 |
| 英文分类提示、预算、Unknown usage、390 px/XSS 补充验收 | 2 passed |
| 真实 Docker runtime | 4 passed：实际 Dockerfile、健康/non-root、配置注入、卷重建、HTTPS 浏览器 |
| Studio / public Demo | 12 / 8 passed，JavaScript errors 均为空 |
| Ruff / Node 22 JS syntax | 全部通过 / 5 项通过 |
| Deployment / production 配置 | 通过，真实 Compose config 校验通过 |
| Wheel | 构建、安装、仓库外导入通过；64 个包内源文件逐字节一致，包含新诊断模块；独立环境安装完整锁定依赖后再次确认导入 |
| 已安装 wheel 的空库迁移 | SQLite、真实 MySQL 均两次 upgrade head 成功，current 为 0010_api_reports |

Windows 首轮全量为 654 passed、3 failed、1 skipped：失败均为旧 Demo E2E 初始页面
导航 800/500 ms 超时；Linux 上同一组原测试无需预热通过。Windows 在只读测试页 readiness
预热后，整组 12 项通过，原断言/阈值及产品代码没有变动。Windows 跳过的一项是符号链接
权限测试，由 Linux 原测试覆盖。此冷启动现象保留为环境验收说明，不扩大本轮修复范围。

恢复后首次容器全量继承了 Dockerfile 的 `AUTOQA_ENV=production`，产生 10 个既有测试失败。
清理验收 worker 的生产环境默认值后，相关 5 项复验通过，完整重跑得到上述零失败结果；
未修改产品代码、原测试断言或超时阈值。英文补充检查首次借用的本地环境缺少
`jsonschema_path`；改用工作树外的独立环境、安装完整 `requirements.lock` 后，
Studio、Demo 和英文补充检查均重新通过。全量有一项既有 SQLite datetime adapter 弃用警告。

三个 CI 门禁（默认全量、auth-mysql、docker-production）均在本地等价运行通过。
全量默认跳过的 30 个 MySQL 用例和 4 个 Docker 用例逐项匹配到真实门禁的 passed 记录，
没有用跳过结果替代这些验收。CI 配置没有改动；本轮未推送，没有触发新的 GitHub Actions run。

工作树曾被外部 `startup-cleanup` 归档，源码已从父提交为本基线的快照恢复。
先前忽略目录中的验收文件已丢失，未完成的旧运行不计为通过。最终可复查记录保存在
本机 `Documents/Codex/2026-10-04/planner-fix-acceptance/`（工作树之外、不提交）：
`full.xml`、`auth-mysql.xml`、`docker.xml`、`english.xml`、`focused.xml`、
`acceptance-result.json`、`quality.json`、`wheel-result.json`、SQLite/MySQL wheel 迁移记录，
以及 Studio/Demo 结果 JSON 和浏览器截图。初次环境失败记录亦保留。

## 下一次真实 Alpha

本修复不自动触发真实验证。离线、数据库、Docker、浏览器及工程检查通过后，仍需单独授权：
先确认实际服务加载本次修复版本；已运行的 Alpha 服务须在授权范围内受控重启并核对版本。
本轮没有重启或调用该真实 Alpha 服务。
源码恢复不代表忽略目录中的 Alpha 数据库、配置或密钥已经恢复；真实验证前须核对
BYOK 配置和原六接口文档的可用性。本轮未为此执行真实连接测试。
同一受控六接口文档，通过 UI 仅 Generate 一次，最多 3 次真实模型请求，无自动追加或重生成。
任一失败立即停止，记录 code、批次、接口计数、调用数和实际已知 usage。
ready 后人工检查契约与假设，再确认全部请求/断言；不得自动 approve。
Execution、Report、Analyzer 分别按授权继续，Planner ready 不代表完整 Alpha 闭环通过。
