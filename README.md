# AutoQA Studio

把 OpenAPI 接口描述转成可审核、可执行、可追溯的 API 测试。AI 负责生成测试计划；每次执行须经人工审核，HTTP 请求和断言按已批准配置确定性运行。

[GitHub 仓库](https://github.com/jayoffans/AutoQA-Agent) · [CI 状态](https://github.com/jayoffans/AutoQA-Agent/actions/workflows/ci.yml)

[![Browser QA CI](https://github.com/jayoffans/AutoQA-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jayoffans/AutoQA-Agent/actions/workflows/ci.yml)

**English summary:** AutoQA Studio imports OpenAPI contracts, uses the workspace owner's model key to draft test cases, requires human review, then runs deterministic HTTP requests and assertions. It stores evidence and exports HTML/Markdown quality reports.

## 产品流程与架构

~~~mermaid
flowchart LR
    UI[Web Studio] --> API[FastAPI / 工作区鉴权]
    API --> A[OpenAPI 解析与校验]
    A --> B[API Planner / Pydantic]
    B <--> GW[Provider Gateway / BYOK]
    B --> C[人工审核结构化请求与断言]
    C --> D[HTTPX / 目标网络策略]
    D --> E[执行证据 / 确定性质量报告]
    E --> F[HTML / Markdown 导出]
    E -. 用户确认后可选 .-> G[AI Analyzer]
    G <--> GW
    API --> DB[(SQLAlchemy / SQLite 或 MySQL)]
    E --> DB
~~~

AI 生成的是待审核的描述性用例，不会直接发起测试请求。人工审核结构化请求、预期状态码和断言后，HTTP 执行器按冻结配置运行。AI Analyzer 可基于已脱敏证据补充分析建议；它不会修改执行结果，建议也不代表已确认根因。仓库另保留 LangGraph + Playwright 的浏览器测试工作流与 Demo，详见[浏览器执行架构](docs/architecture.md)。

## GitHub 作品展示与本地演示

以 [GitHub 仓库](https://github.com/jayoffans/AutoQA-Agent) 作为唯一对外作品展示入口，不提供在线体验链接。源代码、自动化测试、CI 及技术验收记录均可通过仓库查阅。

![AutoQA 工作台](docs/assets/phase6-2a/dashboard.png)

![API 测试计划：Mock 规划与 XSS 转义验收](docs/assets/phase6-2c-1b/planner-desktop.png)

截图来自不同阶段的真实受控测试环境，展示 Production 工作台与 Planner。第二张使用 `mock-planner`，包含作为文本转义显示的 XSS 测试输入；不代表真实模型生成效果。默认本地 Demo 展示模板流程，界面和数据与截图不同。

### 本地体验路线

1. 依据下方“本地启动”说明运行项目。默认 Demo 模式可使用内置场景，不必配置真实模型 Key。
2. 查看测试计划、人工审批、执行证据和报告。清楚区分使用 Mock 的规划、真实 HTTP / 浏览器执行与历史受控 Alpha 结果。
3. 要运行完整的 OpenAPI → AI Planner → 人工审核 → HTTP 执行闭环，参照[部署指南](docs/production-deployment.md)建立独立 Production 环境，配置自有模型 Key，并仅测试已授权系统。
4. 参考[演示讲解](docs/demo-guide.md)、[当前验收与公开审核索引](docs/public-release-review.md)。历史报告按原日期保留。

## 能力与技术栈

- **OpenAPI 导入**：支持 OpenAPI 3.x 与 Swagger 2.0 的常用接口、参数、JSON 请求体和响应结构；仅接受文档内引用，受支持范围和限制以解析器校验为准。
- **计划与审批**：API Planner 经 Provider Gateway 调用用户配置的模型，使用 Pydantic 校验结构和接口归属；用户审核用例，批准后执行配置被冻结。
- **HTTP 执行**：基于 HTTPX 逐条发送真实 HTTP 请求，按确定性状态码和 JSON 断言判定通过或失败；网络、认证等执行异常单独报告为 execution error。
- **证据与报告**：保存请求、响应、断言、时间和诊断证据；生成 HTML 与 Markdown 质量报告。证据和日志会进行敏感信息脱敏与长度限制。
- **可选分析**：AI Analyzer 阅读经过筛选和脱敏的证据，输出假设与建议，不改写测试判定。
- **技术栈**：Python 3.11+（CI 使用 3.12）、FastAPI、Pydantic、SQLAlchemy、LangGraph、HTTPX、Alembic、SQLite、MySQL、Playwright / Chromium、Docker Compose、pytest、Ruff、GitHub Actions。

本地可使用 SQLite；仓库还包含通过 CI 验收的 MySQL 与 Docker 部署路径。

### 工程难点

- **模型输出不能直接执行**：按两个接口分批规划，严格检查结构、接口归属、四类覆盖与数量限制；所有批次通过后一次事务落库，失败不留下部分用例。见 [Planner 诊断与原子性验收](docs/planner-alpha-blocker.md)。
- **审核与执行保持一致**：冻结请求与断言；在审核、执行与建立连接时检查目标，固定已校验 IP 并保留 TLS 域名校验，限制响应大小。见 [HTTP 执行边界](docs/phase6-2c-1c.md)。
- **测试事实与分析建议分离**：断言失败和执行异常分开统计，报告保存确定性快照，AI overlay 不改写执行事实。见 [断言验收](docs/phase6-2c-1d.md)与[报告验收](docs/phase6-2c-1e.md)。

## 生产安全边界

- **BYOK**：用户自行配置 Provider 和 API Key。密钥使用 AES-256-GCM 加密保存，界面仅显示掩码；Production 不读取开发者模型 Key，也不回退到 Mock。
- **人工审批**：计划须经人审核和批准后才能执行；审批后冻结本次执行配置。
- **SSRF 防护**：Production 默认限制为经过 DNS 和公网 IP 校验的 HTTP(S) 目标，阻止私有、环回、链路本地和云 metadata 地址，并限制重定向与响应大小。
- **审计与隐私**：保存执行与审核记录；安全日志、响应和证据对已知敏感值脱敏。证据中可能仍包含目标服务返回的业务数据，使用者应选择适当的测试数据并管理报告访问权限。
- **执行范围**：执行器运行接口请求和受支持的断言，不运行模型生成的 shell、脚本或任意代码。仅测试你获准访问的目标。

## 测试证据与验证边界

| 范围 | 已验证内容与边界 |
| --- | --- |
| Mock / 自动化 | 模型使用 Mock、FakeGateway、MockTransport 或本地兼容服务；HTTP、Chromium、数据库与容器按各项记录真实运行。不能据此评价真实模型质量。 |
| 本地 Demo 种子 | Mock 生成计划，ASGITransport 调用内置应用并计算结果；种子中的审批事件是演示数据。新建浏览器任务使用真实 Chromium。 |
| 受控 Alpha | 独立受控目标的执行验收，结果限于预设接口与断言，不代表开放业务系统成功率。 |
| 真实模型 | [历史 Alpha 诊断](docs/planner-alpha-blocker.md)记载过模型响应及规划失败；修复使用静态 fixture 复验，不等于修复后真实 BYOK 完整闭环通过。本轮不调用付费模型。 |

历史记录涉及受控 Alpha Target 执行，以及 Production 切换与重启后的 HTTPS、登录、OpenAPI 和安全检查。本轮未找到可独立复核其汇总数字的原始记录，因此不将这些数字作为当前证据；历史 Alpha 也不代表 Production 的真实 BYOK 完整闭环。

[2026-10-05 的 release CI](https://github.com/jayoffans/AutoQA-Agent/actions/runs/37274547291)包含 MySQL 与 Docker 验收；历史结果不替代当前提交验收。本轮命令、结果、跳过项和公开阻塞项见[当前验收索引](docs/public-release-review.md)。

## 本地启动

Windows 安装 Python 3.11 或以上版本后，可在仓库目录运行：

~~~powershell
.\start.cmd -Setup
~~~

脚本使用项目内 `.venv`，首次启动复制 `.env.example` 为本地 `.env`，安装锁定依赖与 Chromium，并仅监听 `127.0.0.1`。默认使用 Demo / Mock 和 SQLite，不需要模型 Key；已有 `.env` 不会被覆盖。后续运行 `.\start.cmd`；不自动打开浏览器可用 `.\start.cmd -NoBrowser`，在原终端按 Ctrl+C 停止。

也可在 PowerShell 中手动安装（以下使用 Python 3.11）：

~~~powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
pip install -r requirements.lock
pip install --no-deps -e .
python -m playwright install chromium
$env:HOST = "127.0.0.1"
python -m autoqa
~~~

打开 http://127.0.0.1:8765 。本地环境变量说明见 [.env.example](.env.example)。首次迁移也会在应用启动时执行。全套测试应在没有 Demo `.env` 干扰的独立 checkout 中运行：

~~~powershell
.\.venv\Scripts\python.exe -m ruff check autoqa tests scripts migrations
.\.venv\Scripts\python.exe -m pytest -q --junitxml=work/test-results.xml
~~~

默认 pytest 中未配置的 MySQL / Docker 用例会跳过；CI 的 `auth-mysql` 与 `docker-production` 分别使用独立 MySQL 和真实容器运行这些门禁，详见 [CI 配置](.github/workflows/ci.yml)。Production 与 Demo 必须使用不同数据库，Production 浏览器登录还要求 HTTPS；默认 Demo 不包含完整 BYOK 产品闭环。

## 自托管部署

仓库提供独立的 Production Compose 配置 [compose.production.yaml](compose.production.yaml)，使用 MySQL 与独立数据、证据 Volume。部署前请准备独立数据库凭据、32 字节随机 AUTOQA_SECRET_KEY、显式 AUTOQA_MODE=production 和 AUTOQA_ENV=production，并在 HTTPS 反向代理后运行。请勿复用 Demo 数据库或 Volume，也不要把任何密钥提交到 Git。

~~~powershell
docker compose --env-file .env.production -f compose.production.yaml build
docker compose --env-file .env.production -f compose.production.yaml up -d --wait
~~~

.env.production 仅保存在部署环境中。主密钥丢失会导致已保存的 Provider 密钥无法解密；单副本运行是当前部署约束。部署前应独立配置数据卷与环境密钥。

## 许可证

本项目中维护者有权授权的原创代码和相关文档采用 [MIT License](LICENSE)，Copyright (c) 2026 jayoffans。第三方依赖、工具及其材料分别遵守原有许可证，详见[依赖及授权审查](docs/public-dependency-review.md)。测试数据、历史截图与模型效果说明仍以实际验收记录为准。

## English

AutoQA Studio turns OpenAPI contracts into reviewed API test plans. Users bring their own model credentials; a human approves each plan before deterministic HTTP execution. The product retains evidence and exports HTML/Markdown quality reports, with optional AI analysis clearly separated from test results.
