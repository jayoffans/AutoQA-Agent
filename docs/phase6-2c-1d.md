# Phase 6-2-C-1-D — API Execution 验收

日期：2026-10-02（Asia/Shanghai）。分支：`codex/phase5-real-product`，实施基线：`ab08ec7`。

## 1. 完成范围与架构

复用 Phase C 的审核快照、执行器、httpx 固定 IP transport、工作区权限、CSRF、串行 Run、逐条事务和 Production 页面，补齐：

```text
Approved TestPlan → Execution Preview → Real HTTP Execution
  → deterministic HTTP / JSON assertions → TestExecution → TestRun Summary
```

执行和断言均无模型调用。未实现 Phase E、Finding、AI 分析、Report、Web 执行扩展、队列、自动重试或部分重跑。
已有 Demo/Report 表和公开 Demo 路由沿用原流程；本阶段没有修改其页面或业务行为。

## 2. 数据与兼容

新增增量 migration `0009_execution_assertions`，旧 migration 不变：

| 对象 | 改动 |
| --- | --- |
| `TestExecution.status` | nullable String(16)；新结果明确保存 passed / failed / execution_error |
| `TestExecution.result_data` | nullable JSON；版本、用例名称、预期状态码、错误代码、逐项断言证据和展示裁剪标记 |
| Execution CHECK | 分类合法且与已有 passed 布尔值一致；历史 NULL 仍合法 |
| TestRun CHECK | 原 status 已同时承担 running / interrupted 和结果状态，最小扩展 execution_error |
| 审核配置 | 原 AES-GCM envelope 内增加可选 assertions，默认 []；无需转换旧配置 |

不新增 Run 计数字段，也不补写旧证据。已有工作区/Run/Case 复合外键、唯一活动槽和结果唯一约束继续生效。

Run API 的 `summary.lifecycle` 为 running / interrupted / completed，`summary.outcome` 只在完整完成时为
passed / failed / execution_error。未保存的 Case 为 pending；中断不会生成虚假的失败或错误 Execution。
Summary 从保存的分类派生，列表仅读取分类所需列，详情才读取请求和响应正文。

旧 Execution 的 status 为 NULL 时，只在读取时有限推断：passed=true → passed；否则有 HTTP 状态 → failed；
没有 HTTP 状态 → execution_error。返回 `classification_inferred=true`，不伪造历史逐项断言。
旧 Run 顶层计数保留原值；新的 summary 使用上述兼容推断，因此可能与旧 failed_cases 不同。

SQLite / MySQL 测试从真实 revision 7 数据升级至 8，再升级至 9 两次，逐列确认旧 Run 和旧 Execution 未改写。
Downgrade 若已有新分类证据则拒绝，要求恢复升级前备份，避免静默删除新证据。
另在隔离 SQLite 和真实 MySQL 库验证空库 downgrade/re-upgrade；新 Run outcome 和新 Execution
证据两种阻止降级的路径均验证通过，拒绝后版本仍为 0009，结果行保持存在。

## 3. 确定性断言

保留 required `expected_status`，增加可选 `assertions`，例如：

```json
{
  "expected_status": 200,
  "assertions": [
    {"type":"json","path":"data.success","operator":"equals","expected":true},
    {"type":"json","path":"data.token","operator":"exists"},
    {"type":"json","path":"users.0.id","operator":"type","expected":"integer"}
  ]
}
```

- 点路径精确取键；数字段可取数组索引。最多 256 字符、32 段，不支持 JSONPath、通配符、脚本或 eval。
- 每 Case 最多 32 条 JSON 断言；expected JSON 最多 32 层。未知字段、无效 operator/type 和非有限数被拒绝。
- exists：字段存在即通过，null 也算存在；缺失与 null 独立记录。
- equals：布尔与数字不同；1 与 1.0 相等；对象键序无关；数组顺序有意义，递归确定性比较。
- type：string / number / integer / boolean / object / array / null；number 接受 integer，integer 不接受 float。
- 无效 JSON 在 HTTP 完成且配置 JSON 断言时为 failed。合法 JSON 超出 64 层处理边界为 execution_error。
- 全部断言生成独立 expected / actual / status / reason_code；HTTP 未完成时断言为 not_evaluated。
- 原始响应完成比较后才脱敏与展示裁剪；actual_exists 区分缺失。expected/actual 超 4096 UTF-8 字节时标记裁剪。

## 4. 分类和汇总

| 事实 | Execution 分类 |
| --- | --- |
| HTTP 完成、全部断言通过 | passed |
| HTTP 完成、状态/JSON 断言不符 | failed |
| timeout / DNS / connection / TLS / transport / policy / internal error | execution_error |
| 响应大小或断言处理复杂度超限 | execution_error |
| HTTP 500 且预期 500、其他断言通过 | passed |
| 3xx 响应 | failed，始终不跟随重定向，包括预期也是 3xx |

新 Run.failed_cases 仅计业务断言失败，错误数从 Execution 派生。总体 outcome 优先 execution_error，其次 failed，否则 passed。
Case 普通执行错误保存后继续；存储错误、整个 Run 超时、取消和重启恢复为 interrupted，保留已提交证据。
异常消息使用固定文本和 error_code，不保存原始异常字符串。无隐式重试。

## 5. API、重跑和 UI

| API | 本阶段行为 |
| --- | --- |
| `GET /api/test-plans/{id}/execution-preview` | 复用；增加脱敏 JSON 断言，仍为离线预览 |
| `POST /api/test-plans/{id}/approve` | 复用；原子校验全部请求与断言，加密不可变快照 |
| `POST /api/test-plans/{id}/execute` | 复用；新分类与 summary，201 创建 Run |
| `GET /api/test-plans/{id}/runs` | 复用；历史列表包含三类计数、pending、生命周期与总体结果 |
| `GET /api/test-runs/{id}` | 复用；全部 Execution 明确分类、推断标记、请求/响应、耗时、断言与错误 |
| `POST /api/test-runs/{id}/rerun` | 新增；从已审核配置完整执行，创建全新 Run/Execution，201 |

重跑不读取历史脱敏请求重建凭据，不修改源 Run。活动 Run、源 Run 正在执行或目标变化返回 409。
权限/归档隔离和 Cookie CSRF 与已有接口相同。重复业务写入风险在 UI 提示；不实现 rerun-failed。

Production 增加审核阶段 JSON assertion 编辑、三类结果、pending/interrupted、起止时间、历史选择、
完整逐条证据和重跑按钮。审核后只读。轮询保留选定历史 Run 与展开详情，工作区/身份切换清除选择，
异步旧结果不能覆盖新选择。中文/英文、390px 移动视口、内容转义和刷新均有浏览器回归。
没有修改 Report UI 或引入新的 Playwright 产品功能。

## 6. 安全复验与修复

保留公网全 DNS 集合检查及连接时固定 IP、Host/SNI 保留、禁 redirect/proxy/retry。API 执行始终
allow_private=False，旧私网设置不能放行；未新增 Production 私网开关、环境逃生门或 localhost 白名单。

覆盖 localhost、IPv4/IPv6 loopback、RFC1918、link-local、metadata、混合 DNS、DNS rebinding、
私网 redirect、Host/transport header override、userinfo 和非 HTTP(S)。允许端口仍为 80/443/8080/8443。

本阶段定点修复：

- Base URL 和 endpoint/组合路径均拒绝 dot traversal、双重编码、控制字符和反斜杠。
- URL 空 userinfo（如 `http://@host`）也拒绝；共享 origin 的这一安全修复通过旧流程回归。
- 请求/响应敏感字段继续按字段名屏蔽；有 Secret 特征的长字符串用于回显脱敏。
- 不对短值、纯数字或常见占位值全局替换；这类值仍在敏感字段屏蔽，非敏感字段不做危险替换。
- 非 JSON 敏感 key:value 片段保守隐藏正文；expected/actual 的敏感路径也屏蔽。
- httpx/httpcore 日志在执行 ContextVar 范围内去除 URL/参数/异常内容，范围外日志正常。
- 先对原值断言，再脱敏；响应脱敏序列化膨胀后再次限制保存长度，并标记展示裁剪。

既有界限保持：240 Case / Run；审核配置 64,000 字节；解压后响应 256,000 字节；
Case 10 秒、Run 120 秒。响应超限不保存正文，gzip 扩张和精确边界均有真实 socket 测试。

## 7. 真实 HTTP 与真实数据库

`tests/api_execution_support.py` 在随机 localhost 端口启动受控 ThreadingHTTPServer，finally 停止服务。
目标调用使用原 ApiExecutor → PinnedTransport → httpx.AsyncHTTPTransport → socket，未使用
MockTransport 或 ASGITransport；应用路由的 TestClient 与目标 transport 分离。

测试例外仅 pytest helper 的精确 scheme/127.0.0.1/随机端口 resolver monkeypatch，要求 env=test，
其他目标交回原策略；helper 不在 wheel 内。未安装 fixture 时，test 与 production 均拒绝 localhost。
受控 `/health`、`/v1/login`、`/v1/users/1`、500、超时、redirect、原始/压缩响应超限及 TLS 校验失败均覆盖。
TLS 验证未关闭，拒绝不可信证书后目标 HTTP handler 未收到请求。

本机使用官方 MySQL 8.4.11 Windows 二进制，在 ignored work 下创建隔离实例及测试数据库，
随机端口/凭据、仅 loopback、无 Windows 服务。每次完整回归分配全新的隔离数据库，避免旧 Auth
迁移验收复用已升级的库。凭据不进入文档、代码或提交。
全量测试启用 AUTOQA_REQUIRE_MYSQL，运行 CI auth-mysql 组内真实数据库兼容用例。
这是本地执行同一 CI 测试组，不代表已经触发或通过远端 GitHub Actions；没有部署 Railway。

## 8. 验证结果

最终全量 pytest：**561 passed / 5 skipped / 0 failed**，耗时 **681.35 秒**；566 个用例中
25 个真实 MySQL 运行用例均通过，无 MySQL 跳过。JUnit：`work/phase6-2c-1d-full.xml`。

定向最终实现复验：`test_api_execution.py` + `test_api_execution_compat.py`：96 passed，含真实 MySQL。
第一轮全量曾为 555 passed / 5 skipped / 0 failed；之后补充日志和复杂度回归，第一轮数量不作最终证据。

质量检查：Ruff（autoqa/tests/scripts/migrations）、五个现有 JS 语法检查、isolated wheel 构建通过；
最终包核对 migration 0009 和新模块，并确认测试放行 helper 未打包。部署结构、Production 配置、
动态端口、MySQL URL 规范化和安全 Demo 默认值检查通过。原有 Studio 浏览器 12 项、HTTPS Public Demo
浏览器 8 项检查均通过，JavaScript errors 为 0，产物写入 ignored work，不覆盖既有公开截图。

跳过原因：本机无 Docker CLI/engine，四个 Docker Production runtime 用例未执行；
Windows 无 symlink privilege，一个 artifact symlink 用例跳过，Linux CI 保留该检查。
不能把 Docker structural validation 当作容器运行验收。远端 CI 未运行。

浏览器截图位于 ignored `work/phase6-2c-1d-ui/`：execution-desktop/mobile、history-assertions-desktop、history-errors-mobile。
测试和日志均使用隔离库、受控目标和假凭据，不访问真实业务 API 或调用模型。

## 9. 21 项验收对应表

全部 21 项满足；第 15 项为本地真实 MySQL 执行 CI 同组，未触发远端 Actions。
第 21 项由最终提交后的 `git status --porcelain` 空输出确认。

| # | 完成条件 | 状态 / 证据 |
| --- | --- | --- |
| 1 | 旧审核配置执行和重跑 | 通过；legacy config 回归，无转换 |
| 2 | status / exists / equals / type | 通过；纯断言、API 和 socket 回归 |
| 3 | Execution / Assertion 模型调用数 0 | 通过；API/socket 对调用数前后检查，执行模块无模型依赖 |
| 4 | DB/API/Summary/UI 三类结果 | 通过；CHECK、混合结果及浏览器回归 |
| 5 | 独立分类和脱敏证据 | 通过；status/result_data 及原值先比较测试 |
| 6 | Summary 计数 | 通过；混合、历史、中断和运行中用例 |
| 7 | 未完成 Case 不伪造结果 | 通过；pending、取消/超时/存储错误/重启回归 |
| 8 | 新 Run，旧证据不变 | 通过；legacy/API/socket/browser 前后相等 |
| 9 | 稳定查看历史 Run | 通过；浏览器两次轮询后选择与展开仍保留 |
| 10 | 真实 socket，目标无 mock | 通过；原 pinned/httpx transport 与服务器记录 |
| 11 | SSRF 默认不放宽 | 通过；test/production 默认拒绝及私网设置无效测试 |
| 12 | 已识别安全缺口回归 | 通过；base/endpoint path、empty userinfo、echo、fragment、assertion 脱敏 |
| 13 | 响应限制与敏感保护 | 通过；大小/gzip/边界/裁剪、日志和原值比较 |
| 14 | SQLite migration / compatibility | 通过；真实旧库增量升级，无历史改写 |
| 15 | 真实 CI MySQL compatibility | 本地真实 MySQL 8.4.11 执行 CI 同组；远端 Actions 未运行 |
| 16 | 全量 pytest 零失败 | 通过；561 passed / 5 skipped / 0 failed |
| 17 | 现有质量检查 | 通过；Ruff / JS / wheel / migration / 配置检查；Docker runtime 明确未执行 |
| 18 | Production / Demo 回归 | 通过；全量回归及 Studio 12 / HTTPS Public Demo 8 项浏览器检查 |
| 19 | 未实现 Phase E | 通过；范围审查 |
| 20 | 本报告真实记录 | 满足；最终计数、5 个跳过项和限制已记录 |
| 21 | 最终工作区干净 | 满足；提交后 git status --porcelain 空输出 |

## 10. 文件和剩余限制

核心：`api_assertions.py`、`execution_privacy.py`、`api_executor.py`、`test_execution_api.py`、
`product_models.py`、`security.py`、`target_network.py`、`safe_logging.py`。
UI：`production.js/css` 与两份 locales。迁移：0009 和 pyproject wheel 清单。
测试：断言、socket helper/验收、既有 execution/API/browser/compat，以及 7 处 migration head/链兼容期望。
CI：主测试超时 15→25 分钟、Phase D 截图 artifact；MySQL job 沿用既有执行兼容组。
文档：README 与本文。未更改 0001–0008 migration。

仍为单进程、进程内串行执行；无分布式租约/队列、业务回滚、响应变量串联、凭据插值、JSON Schema/
复杂 JSONPath 或失败子集重跑。大历史列表未分页，但汇总不加载响应正文。
历史推断只能反映已有字段，不能恢复旧断言预期。降级需按新证据保护规则恢复备份。
Docker runtime 与远端 GitHub Actions 未验证，未合并 main、未部署 Railway、未推送公开 Demo。
