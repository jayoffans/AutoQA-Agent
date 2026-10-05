# AutoQA Production：独立部署指南

本阶段没有更改现有公开 Demo、Railway 变量或数据库。正式版需要一个新服务和新数据库，由维护者决定部署时间。

## Railway 新环境

1. 保留当前公开 Demo 服务和域名。新建 `AutoQA Production` 环境，选择 **Empty Environment**，避免复制旧变量与数据库引用；也可以创建独立 Railway Project。
2. 在新环境添加独立 MySQL 服务，以及连接 `jayoffans/AutoQA-Agent` 的 Web 服务。Web 服务的部署分支选择经过验收的 Phase 5 分支，后续由维护者决定合并到哪条发布分支。
3. Web 服务采用仓库 Dockerfile，启动命令 `python -m autoqa`，健康检查 `/health`。先配置下面的变量，再由维护者应用部署。
4. 给新 Web 服务创建独立 Volume，挂载 `/app/persistent`，用于执行证据；若使用 SQLite，该目录同时保存数据库。不要引用 Demo 的数据库或 Volume。
5. 为正式版生成独立 HTTPS 域名。`AUTOQA_PUBLIC_BASE_URL` 设置为该域名；未设置时使用 Railway 的 `RAILWAY_PUBLIC_DOMAIN`。浏览器认证 Cookie 在生产环境为 Secure。
6. 数据库第一次启动执行 `0001_legacy → 0002_product → 0003_auth`，只建业务结构，不创建任何 Demo 项目、任务、用户或 Finding。先备份任何已有库，迁移没有自动破坏性 downgrade。Phase 5 用户及业务数据保留；原 cookie 会话需要重新登录以获得 JWT。
7. 注册一个真实用户，检查工作台全为 0。在“模型服务”配置自己的 Provider，并以授权的测试环境做小规模验收。

Railway 的环境、变量、数据库和 Volume 隔离及分支配置参见：[Environments](https://docs.railway.com/environments)、[Services](https://docs.railway.com/services)、[隔离环境指南](https://docs.railway.com/guides/isolate-staging-production)。请人工检查新服务的数据库引用，不能只依赖环境名称。

## 必需变量

| 变量 | Production 值 / 用途 |
| --- | --- |
| `AUTOQA_MODE` | `production`，必须明确设置 |
| `AUTOQA_ENV` | `production` |
| `AUTOQA_SECRET_KEY` | 随机 32 字节的 URL-safe base64；用于 AES-GCM，并派生独立用途的 JWT 签名密钥，**不是模型 Key** |
| `DATABASE_URL` | 新 MySQL 的 `mysql+pymysql://...` 或独立 SQLite 路径 |
| `AUTOQA_ARTIFACT_DIR` | `/app/persistent/artifacts` |
| `AUTOQA_DATA_DIR` | `/app/persistent/data` |
| `HOST` | `0.0.0.0` |
| `PORT` | Railway 分配值，应用自动读取 |

可选：`AUTOQA_PUBLIC_BASE_URL`、`AUTOQA_CONCURRENCY=2`、`AUTOQA_TRUSTED_PROXY_CIDRS`（仅实际可信代理地址）、`AUTOQA_ALLOW_PRIVATE_TARGETS=false`（保持默认）。自托管访问内网测试站点必须显式设置最后一个变量为 `true`；metadata IP 始终禁止。

MySQL 也可以用 `DATABASE_URL=""` 加 `MYSQLHOST`、`MYSQLPORT`、`MYSQLUSER`、`MYSQLPASSWORD`、`MYSQLDATABASE` 生成安全编码的连接串。数据库密码只设在部署变量中。Docker Compose 会自动设置这种方式；Railway 推荐明确引用新数据库的连接变量。

主密钥生成：

```bash
python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

将生成值存入部署 Secret，并另外安全备份。更换或丢失主密钥会导致已有 Provider 和环境密钥无法解密；本阶段没有自动轮换工具。

正式版不要设置 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`AUTOQA_LLM_API_KEY` 或 `LLM_PROVIDER=mock`。即使误设，这些全局配置也不会成为 Production 模型来源。`AUTOQA_API_TOKEN` 仅保留旧开发模式兼容，正式版使用用户 JWT 认证。

access token 为 15 分钟，refresh 为绝对 7 天，数据库只保存 refresh 哈希并在刷新时轮换。退出撤销当前会话；其他设备的会话保留。Production Cookie 要求 HTTPS，浏览器写请求要求 CSRF。新变量不是必需；继续保留原主密钥，否则已有 AES 密文无法解密且 JWT 验证会失败。限流与会话级模型配置仍依赖单副本运行。详细 API 和隔离方案见 [Authentication Architecture](../README.md#authentication-architecture)。

Demo 保持 `AUTOQA_MODE=demo`、`DEMO_LLM_ENABLED=false`，使用独立数据库。未明确设置 `AUTOQA_MODE` 的旧安装保持原有行为，避免自动改变已部署 Demo；新的正式部署不能省略 `AUTOQA_MODE`。

## Self-hosting

```bash
python -m venv .venv
# 激活虚拟环境
pip install -r requirements.lock
pip install --no-deps -e .
python -m playwright install chromium
# 从 .env.example 创建 .env，设置 production 模式、主密钥、新数据库和 HTTPS 公共域名
python -m alembic upgrade head
python -m autoqa
```

Docker / Compose：设置 `.env` 的 `AUTOQA_MODE=production`、主密钥及 MySQL 密码，然后 `docker compose up --build`。正式使用需在前方设置 HTTPS 反向代理。首次数据库迁移也会在应用启动时执行。旧 Demo Compose 请用独立 Compose project / Volume，不能直接复用原数据目录。

## 当前运行约束

保持 **1 个进程、1 个副本**：运行队列、会话级模型配置和临时运行账号存在当前进程内存中。MySQL 持久化用户会话、工作区、工作区配置、计划、结果和报告。服务重启后临时配置丢失；运行中的任务标记中断，浏览器不会重放已经提交的操作。

本次在本地完成 fixture HTTP / Chromium 验收；新 Railway Production、真实收费模型、外部真实项目和 Docker 容器运行均未实际验收。正式公网开放前还需要用户找回密码、邮件验证、资源限额、数据保留与密钥轮换等运营能力，详见 [Phase 5 验收](phase5.md)。
