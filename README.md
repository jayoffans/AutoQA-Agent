# AutoQA Studio

面向真实 Web / API 项目的 AI 软件测试工作区：解析需求、生成计划、人工审核、受控执行、保留证据与报告。

[![Browser QA CI](https://github.com/jayoffans/AutoQA-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jayoffans/AutoQA-Agent/actions/workflows/ci.yml)

**AutoQA 不提供模型额度。真实模型请求由用户自行配置 Provider API Key，费用计入用户自己的 Provider 账号。**

[Production 部署](docs/production-deployment.md) · [Phase 6-1 认证与隔离验收](docs/phase6-1.md) · [Phase 5 验收与限制](docs/phase5.md) · [旧版使用文档](docs/usage.md)

## Live Demo

[公开体验 AutoQA Studio](https://autoqa-studio-production.up.railway.app/)

Demo 使用 `AUTOQA_MODE=demo`，MockProvider 生成测试计划，执行内置 Login API 真实 HTTP fixture。示例故意接受错误密码，得到 6 个用例、5 个通过、1 个失败。示例 Finding 与报告仅用于 Demo，不代表正式用户的测试结果。

现有线上 Demo 本轮没有部署、清空或修改。Git 历史和测试 fixture 保留。

## Production Use

正式部署必须明确设置 `AUTOQA_MODE=production`，并连接**新的独立数据库**。第一次打开为登录 / 注册入口；注册自动创建个人工作区，项目、任务、问题和执行数全部为 0。

用户流程：

1. 创建 Web 或 API 项目，设置测试环境及 Base URL。
2. 在“模型服务”选择 DeepSeek、OpenAI 或 OpenAI-compatible Provider，测试连接并保存用户自己的密钥。
3. API 项目导入 OpenAPI URL / JSON / YAML，或手动配置接口并选择操作；Web 项目输入目标 URL 和自然语言目标。
4. 检查 AI 计划的测试目标、范围、前置条件、输入和预期结果；修改、增删用例或重新生成。
5. 人工批准后执行，查看每个用例的预期 / 实际、状态、耗时、证据、问题与历史报告。

默认生产操作界面为简体中文，支持 English 切换；文案集中在 `autoqa/static/locales/zh-CN.json` 与 `en-US.json`。服务端错误和导出 HTML 报告目前主要使用中文。旧公开 Demo 界面保持兼容。

用户、Workspace 与 Owner / Member 关系持久化；每个项目属于一个工作区，任务及 Workflow / Finding / Report / Artifact 通过项目继承访问范围。修改 URL / ID 或 `X-Workspace-ID` 不能跨工作区访问数据。Owner 管理模型配置和成员，Member 可以协作测试。

没有模型配置时显示“尚未配置模型服务”。Production 不读取全局开发者模型 Key，不回退 Mock。模型认证 / 额度 / 网络错误作为执行错误保留，不伪装成业务断言失败。

## Authentication Architecture

```mermaid
flowchart TD
    User[User: active / disabled] --> Membership[Workspace Member: Owner / Member]
    Membership --> Workspace[Workspace: owner_id]
    Workspace --> Project[Project: workspace_id]
    Project --> Task[Task]
    Task --> Workflow[Workflow / Findings / Reports / Artifacts]
```

注册接受 `email`、`password`、`nickname`，同一事务创建 User、个人 Workspace、Owner 成员关系和登录会话。邮箱统一小写且唯一；密码采用带随机盐的 scrypt（N=16384、r=8、p=5），符合 [OWASP 的 16 MiB scrypt 配置](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。保留 Phase 5 密码验证，旧哈希在成功登录时升级。用户响应显式列出基本信息，不包含 password_hash。

| API | 行为 |
| --- | --- |
| `POST /api/auth/register` | 注册并自动登录，返回 user、access_token、refresh_token、csrf_token |
| `POST /api/auth/login` | 邮箱 / 密码认证，返回同样的令牌及用户信息 |
| `POST /api/auth/refresh` | JSON refresh_token 或浏览器 refresh cookie，轮换为新的令牌对 |
| `POST /api/auth/logout` | 撤销当前会话，已签发 access 和 refresh 均失效，清除 cookie 和会话模型配置 |
| `GET /api/auth/me` | 当前用户及所属 Workspace；需要有效 access token |
| `GET /api/auth/csrf` | 验证 refresh cookie 后返回 CSRF，供页面重新打开时恢复登录 |

JWT 使用 PyJWT，固定 HS256，验证签名、issuer、audience、exp、iat、nbf 与 token type；必需 claims 采用 [PyJWT 的 require 配置](https://pyjwt.readthedocs.io/en/stable/api.html)。access 有效期 15 分钟；refresh 最长 7 天，刷新不会延长当前会话的绝对过期时间。每次刷新使用数据库 compare-and-swap 更新 refresh_token_hash，同一令牌并发刷新仅一个成功。数据库保存 SHA-256 refresh 哈希和会话标识哈希，不保存原始令牌。每次生产请求还验证会话、用户 active 状态和成员关系，因此退出、禁用或移除成员后访问立即失效。

JWT 签名密钥通过 HMAC-SHA256 从现有 AUTOQA_SECRET_KEY 按独立用途派生；不增加部署必需变量，AES-GCM 信封格式保持兼容。不要将 Token 放在 URL 或日志中。API 客户端使用 `Authorization: Bearer <access_token>`；浏览器使用 HttpOnly、SameSite=Strict cookie，在 Production HTTPS 上设 Secure，写操作校验 Origin / CSRF。refresh cookie 仅发送到 `/api/auth`。浏览器不将令牌写入 localStorage，access 过期时自动刷新。

Production `/api` 业务入口统一生成 CurrentUser；`X-Workspace-ID` 只能选择当前用户已加入的 Workspace，不能用作授权证明。项目按 workspace_id 查询，任务通过 TaskMetadata → Project 检查归属；Workflow、Finding、Report、SSE 与 Artifact 共用任务归属检查。跨工作区返回 404，不披露资源名称或内容。Artifact 没有公开文件目录，文件名经过校验，路径必须位于当前任务目录；拒绝路径遍历及符号链接。退出或切换工作区时前端清除数据并丢弃旧请求结果。

Owner 可配置模型及添加已注册的 Member；本阶段不实现所有权转移、额外角色、邮件、支付、企业 RBAC 或新的 Onboarding 流程。Demo 保持公开体验和 Seed，使用原有独立入口。

## BYOK Architecture

```mermaid
flowchart TD
    User[User] --> Workspace[Workspace]
    Workspace --> Config[Encrypted Provider Config]
    Config --> Gateway[Provider Gateway]
    Gateway --> Model[User's Model Account]
```

`AUTOQA_SECRET_KEY` 是随机 32 字节的服务端主密钥。用户模型 Key 用 **AES-256-GCM** 加密后存入 `encrypted_api_key`，随机 nonce 和绑定工作区的 associated data 防止密文交换。页面只收到 `sk-••••••••1234`，无法取回完整 Key。更换 Key 时提交新密钥；删除配置可以清除密文。

支持两种模型配置保存方式：

- **仅本次会话**：加密配置只存在当前服务进程内存，绑定用户登录会话与工作区，退出 / 过期 / 重启后不可使用。
- **保存到工作区**：数据库保存加密配置，工作区成员共享模型服务，费用计入该工作区配置的 Provider 账号。

密钥解密只在 Provider 请求局部使用，完成后清除对象中的引用；这不是对 Python 内存的物理擦除保证。Provider Gateway 使用用户提供的 Base URL / Model / Key，不配置免费真实模型额度。

环境认证 Header、Token、Password、自定义敏感变量及保存的测试账号也通过 AES-GCM 加密。计划只保存 `{{TOKEN}}` / `{{USERNAME}}` / `{{PASSWORD}}` 等占位符。临时账号仅本次任务使用，任务完成后清除内存密文。已知秘密会从输出、日志、DOM 与响应证据中移除。Production 禁用 Playwright Trace；配置运行变量时不保存截图，避免目标页面把密码回显到图像中。

## Testing Architecture

保留 FastAPI、SQLAlchemy、LangGraph 和 Playwright。Production 复用既有浏览器工作流、执行器、Store 和报告系统，为 API 项目加入通用接口执行器。

API 支持 OpenAPI 3.x / Swagger 2.0 的 path、method、parameters、JSON request body、response schema 和本地 `$ref`，不再限定 Login API。远程 / 循环引用需预先打包；当前不是完整的 OpenAPI 兼容性实现，不支持 multipart、OAuth 自动取令牌、递归 schema 和任意复杂 JSON Schema 方言。

Web 测试观察用户真实页面、规划可审核动作、执行断言并保存证据。任务不会默认指向 AutoQA 页面。生产模式所有计划，包括只读断言，都必须人工批准；关键步骤可以继续单独审批。多用例 Web Suite、自动浏览器登录发现与复杂跨域业务流程尚未完善。

业务失败来自实际执行断言。系统 / Agent / 网络异常记为 `system_error`。Finding 显示受影响目标、预期、实际和证据；修复提示标注“AI 分析建议”，`root_cause_confirmed=false`。当前故障归类和提示仍包含规则逻辑，不能视为已证实的后端根因。

公网目标采用 HTTP(S) 检查、DNS 解析、公网 IP 校验和连接 IP 固定，校验重定向并限制响应大小。默认禁止 localhost、私有 IP、link-local、metadata、file URL、任意 shell / 脚本动作与服务器文件访问。自托管允许内网需要显式 opt-in；metadata IP 始终禁止。运行测试须使用已获授权的环境和测试账号。

## Database Migrations

Alembic 替代仅依赖 `create_all` 的建表方式：

- `0001_legacy`：识别并补齐旧结构，不删除旧行。
- `0002_product`：users、workspaces、workspace_members、user_sessions、provider_configs、environment_configs、api_contracts、deployment mode marker，以及项目 ownership / 类型 / 环境字段。
- `0003_auth`：用户 nickname / is_active / updated_at、工作区 owner_id / updated_at、成员 id / created_at、会话 refresh_token_hash / created_at。回填旧成员及 Owner，保留已有密码、工作区名称和项目。历史无成员工作区允许 owner_id=NULL，历史 Demo 项目继续保持未归属；新注册与项目创建必有有效归属。

旧项目保持 `workspace_id=NULL`，不会自动分配给新用户。数据库模式标记阻止 Demo / Production 复用数据库；正式模式拒绝接管旧 Demo 业务库。迁移前备份；没有自动破坏性 downgrade。

Phase 5 的旧 cookie 会话保留记录，但不能用于新 JWT 认证，升级后用户需要重新登录；业务数据不受影响。启动使用 Alembic，不调用 create_all。账号禁用目前通过维护者更新 is_active，没有新增管理后台。

## Self-hosting

```bash
pip install -r requirements.lock
pip install --no-deps -e .
python -m playwright install chromium
# 设置 .env：production 模式、主密钥、独立数据库、HTTPS 域名
python -m alembic upgrade head
python -m autoqa
```

Docker 与 Railway 环境变量、Volume、数据库及 HTTPS 配置见 [正式版部署指南](docs/production-deployment.md)。不要复制开发者模型 Key。当前要求单进程 / 单副本；临时配置和运行队列不能跨进程共享。

## Verification

```bash
ruff check autoqa tests scripts migrations
pytest -q --junitxml=work/test-results.xml
node --check autoqa/static/production.js
node --check autoqa/static/i18n.js
```

保留旧浏览器、Demo、报告、代理限额与数据库测试。新增空 Production、用户 / 工作区 / 对象隔离、BYOK、加密、掩码、无回退、OpenAPI、环境秘密、Alembic 和真实 Chromium 验收。CI 仅使用 fixture Key 和 Mock HTTP，不产生真实模型费用；MySQL 使用独立的 Demo 与 Production 测试数据库。

认证、刷新轮换、并发刷新、跨用户资源隔离及真实 Chromium 双用户流程的本轮结果见 [Phase 6-1 报告](docs/phase6-1.md)。MySQL CI 还验证带 Phase 5 用户、工作区和项目的升级及重新登录。没有真实模型依赖。

完整结果、未实际验收项目和上线前缺项见 [Phase 5 报告](docs/phase5.md) 和 [Phase 6-1 报告](docs/phase6-1.md)。
