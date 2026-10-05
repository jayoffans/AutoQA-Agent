# Phase 6-1.5 — Production Compatibility Verification

验收范围仅为 Phase 6-1 的用户、Workspace、成员、JWT、会话和权限隔离在
SQLite / MySQL / Alembic / Docker / CI 下的兼容性。基线为 `24aa9fd`，继续
`codex/phase5-real-product`。不进入 Phase 6-2，不新增业务功能。

## 修改文件

| 文件 | 用途 |
| --- | --- |
| `autoqa/config.py` | 正式生产环境必须显式提供数据库地址，保留开发 / Demo 默认值 |
| `autoqa/store.py` | MySQL 生产连接显式使用 UTC，重连时仍生效 |
| `autoqa/product_runner.py` | 退出只等待未完成任务，释放任务引用 |
| `migrations/env.py` | MySQL 迁移连接使用 UTC，避免宿主机时区影响时间回填 |
| `compose.production.yaml` | 独立生产容器入口，必需配置无密码 / 数据库地址回退，保留三个命名卷 |
| `scripts/ci_mysql.py` | CI 启动独立 MySQL 容器，生成随机凭据并掩码，隔离测试数据库 |
| `tests/production_support.py` | 独立 HTTP Mock Provider、API 目标、临时 TLS 和真实生产服务进程 |
| `tests/test_production_compatibility.py` | SQLite / MySQL 成对迁移、认证、权限、重启、日志及浏览器测试 |
| `tests/test_production_settings.py` | 环境变量、生产无默认地址、Demo 默认、卷配置及退出回归 |
| `tests/test_docker_production.py` | Docker build、Compose up、健康检查、变量注入、卷重建及浏览器验收 |
| `tests/conftest.py` | 原有 Checkpoint 故障重启测试统一 Windows / Linux 进程退出语义，保留正常清理 |
| `.github/workflows/ci.yml` | `auth-mysql` 与 `docker-production` jobs，保留完整旧测试与 Demo 验收 |
| `README.md` | Production Compatibility 支持说明、独立容器入口与验收链接 |
| `docs/phase6-1.5.md` | 本报告 |

## Alembic / MySQL

本机使用独立临时 **MySQL 8.4.11**，只监听本机，随机数据库密码。清空并创建
`test_autoqa` 后真实执行 `python -m alembic upgrade head`，确认当前版本为
**`0003_auth`**。测试另在每个独立空库按顺序执行
`0001_legacy → 0002_product → 0003_auth`，检查版本、表、主键、外键和唯一约束。
重复 `upgrade head` 不删除现有账号；保留 Phase 5 账号 / Workspace / 项目升级测试。

全新 MySQL 的 26 张表：

```text
alembic_version, api_contracts, demo_owners, demo_quotas, environment_configs,
events, findings, graph_checkpoints, graph_writes, installation_mode, projects,
provider_configs, reports, schema_versions, steps, task_metadata, tasks,
test_cases, test_plans, test_runs, user_sessions, users, workflow_steps,
workspace_members, workspaces
```

现有 Session 模型对应 **`user_sessions`**。Artifact 是
`AUTOQA_ARTIFACT_DIR/<task_id>/` 内的任务文件，Report 同时保存数据库内容与 HTML 文件；
本阶段没有新增 `sessions` / `artifacts` 业务表或迁移版本。

SQLite 和 MySQL 使用相同验收场景：注册、事务创建 Owner Workspace / Member、登录、刷新
轮换、旧令牌重放失败、创建 Project / Task、审批、Mock HTTP 执行、读取 Report / Artifact、
退出及 access / refresh 失效。另验证并发刷新单次成功、过期会话、跨工作区项目 / 任务 /
报告 / Finding / Artifact 隔离、移除成员即时失效与 Secure Cookie / CSRF / Origin。

真实 HTTPS 服务进程在创建 User、Workspace、Project、Task、Report 和 Artifact 后停止、
重启，保持数据库、主密钥和目录，确认原会话、项目、报告和文件仍存在。

## Docker / Volume / Browser

本机没有 Docker Engine 或可用 WSL，因此 Docker 运行验收按用户授权在 GitHub Actions
执行，使用真实 Linux Docker Engine 和 MySQL 容器；本机结构检查不作为 Docker 验收证据。

`docker-production` 构建现有 Dockerfile，执行独立 Production Compose，检查 UID 10001、
容器 healthy、`GET /health` 返回 `{"status":"ok"}` 及 Alembic head。检查所有六个要求的环境变量
真正进入容器 Settings，输出仅含布尔值，避免泄露主密钥或数据库 URL。

三个命名卷分别保存 MySQL、应用数据和 Artifact。卷测试创建用户、工作区、项目、已执行
任务、报告及数据卷标记；执行 `compose down` 再 `up --no-build --wait`，确认应用和 MySQL
容器 ID 均改变，并验证账号 / 会话、项目、报告内容、Artifact 字节和数据卷标记保持一致。
清理只作用于随机命名的验收 Compose project。

Chromium 使用临时 HTTPS 入口，覆盖登录页面 → 注册 → Workspace → 创建项目 →
刷新 → 退出 → 重新登录 → 项目仍存在。本机分别使用 SQLite 和 MySQL，并额外重启服务
后刷新；Docker 使用同样的界面流程。临时 TLS 证书仅用于验收，浏览器忽略该测试证书。

生产 Provider 的 BYOK / 无 Mock 回退行为保持原样。验收使用本地模拟的兼容模型 HTTP
服务和 API 目标，不使用真实模型 API Key，也不访问付费模型服务。

## 测试与 CI

新增 **40 个收集到的测试实例**：15 个场景分别在 SQLite / MySQL 执行，共 30 个；
6 个环境 / 配置 / 退出回归测试；4 个 Docker 实测。各测试包含独立的行为断言，
迁移、权限与持久化均通过真实数据库或进程 / 容器验证。

本机最终完整测试：**190 collected，185 passed，5 skipped，0 failed**。
5 个跳过为当前主机无 Docker 的 4 个容器实测，以及 Windows 无符号链接权限的
1 个原有 Artifact 测试；不是 MySQL 跳过。真实 MySQL 已在本机参与全部成对测试和
原有 Demo / Production / Phase 5 升级验收。退出期限调整另重跑 HTTPS 重启与浏览器场景。

GitHub Actions 已在验收代码提交 `009e159` 完成全部检查：
[Browser QA / run 36849091174](https://github.com/jayoffans/AutoQA-Agent/actions/runs/36849091174)。

| Job | 实际结果 |
| --- | --- |
| `tests` | 168 passed，22 skipped；Ruff、JavaScript、Studio 浏览器及既有 Production Demo 验收全部通过 |
| `auth-mysql` | 80 passed；真实 MySQL 空库 CLI 迁移、认证、权限、原有数据库回归、HTTPS 重启和浏览器全部通过 |
| `docker-production` | 4 passed；真实 Docker build、Compose up、非 root / health / 注入变量 / Volume / 浏览器全部通过 |

主 job 的 22 个跳过是分配给独立 jobs 的 15 个 MySQL 成对用例、3 个原有 MySQL 用例和
4 个 Docker 用例；独立 jobs 已实际执行，不能将这几个 jobs 的通过数相加当作独立测试总数。
Linux CI 还执行了本机因权限跳过的符号链接隔离测试。

本机退出期限调整后另验收 4 个 HTTPS 场景，4 passed；Checkpoint 故障重启平台语义统一后
另验收 2 个原有恢复场景，2 passed。Git 审计未发现令牌模式 / 已知秘密匹配或禁止跟踪的文件，
另对全部可达 Git blobs 检查本次生成的 MySQL 凭据，没有匹配。

## Demo 与安全

`compose.yaml`、`docker-compose.yml`、Dockerfile、Railway 配置、Demo 页面、现有前端和
Seed 代码与 `24aa9fd` 相比无改动。完整旧 Demo / Seed / 浏览器 / 报告 / 限额回归继续执行。
没有部署或修改线上公开 Demo，没有合并或修改 main。

`.env` / `.env.*`、数据库文件、日志、临时 TLS 文件与运行凭据不进入 Git 或 Docker build
context。CI 为 MySQL 生成随机密码，掩码后注入环境；不保存固定数据库密码。
认证和容器测试检查密码、JWT、主密钥、数据库密码、Mock Key 不出现在应用日志。
Git 安全审计检查当前跟踪文件及全部可达历史，不输出秘密值。

## 剩余限制

- 验收限定 MySQL 8.4 与 SQLite；未覆盖其他 MySQL 主版本、托管数据库 TLS 策略或真实公网流量。
- Docker 验收在 CI Linux 主机完成；当前 Windows 主机无法运行 Docker。
- 保留单进程 / 单副本要求；运行队列和会话临时模型配置不跨进程共享。
- 不涵盖真实模型质量、负载 / 高可用、备份恢复或安全渗透测试。
- Volume 验收证明保留卷重建的数据持久性，不保证 `down --volumes` / 手动删卷后的恢复。
- 保持现有迁移数据保留策略；没有自动破坏性 downgrade。
- Python 3.12 CI 的原有 SQLite 迁移 fixture 仍产生一个 datetime 适配器弃用警告，不影响验收。
- 原有 Demo 的 Checkpoint 测试统一为进程故障退出恢复，Demo 实现不改动；未完成任务正在
  收尾时的优雅停机竞态不属于本阶段认证 / 兼容性验收的修复范围。

本报告仅证明既有功能在以上受测环境中的兼容性，不宣称 enterprise ready 或 production complete。
