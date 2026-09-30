# AutoQA Studio Phase 3 验证报告

验收日期：2026-10-01（Asia/Shanghai）。项目目录由用户指定；基线 commit 为 `380599f`。本阶段完成代码、公开 Demo 限制、展示材料和部署准备，没有操作 Railway 账号、部署或推送 GitHub。

## 1. 修改文件

| 文件组 | 变化 |
| --- | --- |
| `autoqa/config.py`、`__main__.py`、`safe_logging.py` | HOST / PORT、production 与 Demo 默认值、MySQL URL 转换、安全结构化日志 |
| `autoqa/app.py`、`demo_guard.py`、`store.py` | 健康检查、公开访问边界、事务内配额与访客归属、只读种子、列表按归属过滤 |
| `autoqa/checkpoints.py`、`api_workflow.py`、`runner.py` | MySQL 图检查点、受控 ASGI 执行、安全错误、快速审批调度竞争修复 |
| `autoqa/seed.py`、`scripts/seed_demo.py` | 空任务库的幂等演示初始化和脚本入口 |
| `autoqa/browser.py`、`security.py`、`reports.py` | 生产错误去除内部文本、长文本脱敏性能修复、覆盖率含义明确化 |
| `autoqa/static/app.js`、`components.js` | 公开模式只开放 API 场景，隐藏配置和项目写入口，显示后端任务标题 |
| Dockerfile、railway.toml、compose.yaml、`.env.example`、`.dockerignore` | Linux ASGI 启动、动态端口、健康检查、Demo 环境变量、敏感文件忽略 |
| `.github/workflows/ci.yml`、`tests/`、`scripts/` | MySQL 服务 job、生产 / 公共 UI / 配置 / 密钥检查、隔离本地密钥与测试端口 |
| README、`docs/deployment.md`、`docs/demo-script.md`、`docs/assets/` | 作品首页、真实截图、60 秒流程和手动部署说明 |

用户原有未提交的 `docs/validation.md` 和 `docs/presentation-boundary-results.xml` 保留，不纳入本轮 commit。

## 2. Public Demo 安全策略

- `AUTOQA_ENV=production` 默认启用 `DEMO_MODE=true`。`DEMO_LLM_ENABLED=false` 强制 MockProvider，不论机器是否已配置真实 provider key。
- 只允许内置 Login API，规范化目标、测试需求、六条断言和执行上限；不允许外部站点、自由步骤、shell 或 provider 配置。
- UTC 每日默认全局 20 个任务、每个 socket 来源 3 个任务。事务内写锁把任务与两种配额作为一个提交，失败和取消不返还额度，重启不清空。
- 超额返回 429 和友好提示；已有示例 Workflow、Finding、Report 可继续浏览。
- 随机访客 cookie 使用 HttpOnly、生产 Secure、SameSite=Strict，数据库保存其摘要。只允许修改自身任务的审批 / 取消状态，种子任务不可操作。
- 公开项目只读，调试文档和 MCP 在生产关闭，原始 artifact 下载在 Demo 禁用；报告从数据库读取，不提供服务端文件浏览。
- 错误、验证失败和 JSON 日志不暴露 API Key、模型内部错误、堆栈或服务端路径。

应用忽略 `X-Forwarded-For`，防止访客伪造来源。反向代理若使用统一 peer，不同访客会共享来源额度；当前不能据此声称准确识别真实用户 IP。

## 3. Seed 数据设计

固定命名空间 UUID 标识 `Login API Quality Demo` 与 `Authentication Regression Test`。空任务库启动时，用 MockProvider 生成六类契约，通过 HTTPX ASGI transport 执行实际 fixture，再把计划、案例、结果、事件、阶段和时间写入同一数据库事务。报告及 Finding 由现有生成器计算，缺失报告可在后续启动补全。

默认受控缺陷得到 6 cases、5 passed、1 failed、100% Planned Assertion Execution Coverage、1 High finding。Mock / seed 来源写进证据；没有把运行结果写死在前端。关闭故障开关会得到正常契约结果，不伪造固定 5/1。非空历史库不会批量插入示例，重复启动不增加种子任务。

## 4. Railway 启动命令

```text
python -m autoqa
```

Uvicorn 单 worker，读取 `HOST=0.0.0.0` 和 `PORT`。Dockerfile 与 railway.toml 使用同一入口。健康检查 `GET /health` 精确返回 `{"status":"ok"}`，不调用 LLM。生产无需 Windows `start.cmd`。

## 5. 所需环境变量

最小公开配置：`AUTOQA_ENV=production`、`HOST=0.0.0.0`、平台 `PORT`、`DATABASE_URL`、`DEMO_MODE=true`、`DEMO_LLM_ENABLED=false`。建议明确设置 `DEMO_DAILY_LIMIT=20`、`DEMO_IP_LIMIT=3`、`AUTOQA_DATA_DIR=/app/data`、`AUTOQA_ARTIFACT_DIR=/app/data/artifacts`。

使用 Railway MySQL 时，`DATABASE_URL` 引用 MySQL 服务的 `MYSQL_URL`。域名从平台变量推导，也可设置 `AUTOQA_PUBLIC_BASE_URL`。真实 provider key 只由维护者通过环境变量管理，公开 Mock Demo 不需要密钥。完整变量表见 [部署指南](deployment.md)。

## 6. 测试结果

| 检查 | 结果 |
| --- | --- |
| pytest 全套 | 50 passed，1 skipped；原有 37 项全部通过 |
| Ruff | 通过 |
| JavaScript 语法 | app.js / components.js / studio-model.js 全部通过 |
| 原有浏览器 UI | 12/12 通过 |
| HTTPS 公开 Demo 浏览器 UI | 8/8 通过，含配额提示与三种视口 |
| Production config | 动态端口、默认 Mock、禁用调试、MySQL URL 转换通过 |
| Docker / Railway config | 结构检查通过；本机 Docker Compose CLI 和容器未运行 |
| Demo / rate limit | 访客归属、拒绝危险输入、限额并发、重启、次日重置、额度为零通过 |
| Seed | 反复启动唯一种子、6/5/1/100%/High、后端读取通过 |
| Health | 独立 `/health` 与公开无密钥访问通过 |
| SQLite runtime | JSON、Boolean、UTC 时间、长文本、FK、rollback、并发事务与图审批恢复通过 |
| MySQL 本机 | URL 转换及全部 DDL 编译通过；实际 MySQL 服务检查跳过 |
| MySQL CI | 已配置 MySQL 8.4 服务及同一运行套件，尚未推送 / 运行，不记为通过 |

原始记录：[pytest JUnit](phase3-test-results.xml)、[原有 UI 验证](phase3-studio-ui-results.json)、[公开 UI 验证](assets/public-ui-results.json)、[配置检查](phase3-config-results.json)、[密钥检查](phase3-secret-audit.json)。

截图位于 `docs/assets/dashboard.png`、`projects.png`、`workflow.png`、`report.png`。验证脚本使用一次性数据库、动态测试端口和临时 TLS 证书，不读取本地 `.env` 密钥，不消耗真实 LLM。

## 7. Linux / 生产兼容状态

服务入口无 Windows 绝对路径，静态资源从包目录解析；报告、截图和 evidence 使用相对任务路径，临时报告文件放在配置的 artifact 目录内并原子替换。数据 / 证据目录通过环境变量配置，MCP 本地命令按 OS 选择，生产禁用 MCP。

保留的 loopback 地址用于容器健康检查、本地测试与本地浏览器调试，不是生产公共域名。8765 是开发默认端口和 Docker EXPOSE 元数据，服务运行使用实际 PORT。MySQL 表通过方言生成外键、Boolean、JSON、DateTime 和 LONGTEXT；不使用 SQLite SQL 向 MySQL 写入。SQLite PRAGMA 与本地 checkpoint 只在 SQLite 分支执行。

Linux Dockerfile / CI 定义已检查，但此 Windows 主机没有执行 Linux 容器。不能把这项描述为实际 Linux 镜像运行验收完成。

## 8. Git / 密钥检查

执行 git status、git diff、git diff --check、git grep，以及本地已知 secret / 常见 token 特征扫描。检查当前受跟踪文件与所有可达 Git 历史 blob；未检测到真实密钥匹配，没有受跟踪的 `.env`、数据库或运行日志。变量名 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`MYSQLPASSWORD` 和 `Authorization` 出现在配置、示例与测试中，不表示包含真实密钥。

扫描不保证检测到所有未知格式凭据或已删除的远程历史。未重写 Git 历史，未访问 Railway 凭据或请求用户发送密钥。

## 9. Railway 手动部署步骤

推送本轮 commit → 从 GitHub 创建 Railway 服务 → 添加 MySQL → 引用 DATABASE_URL → 配置公开 Demo 变量 → 挂载可写持久卷 → 保持单 worker / replica → 生成 HTTPS 域名 → 手动部署 → 检查 `/health`、种子、计划审批、429、报告和重启幂等。具体操作与官方来源见 [Railway 部署指南](deployment.md)。

## 10. 当前剩余限制

1. 本机没有 Docker / MySQL 服务，新增真实 MySQL CI job 还未运行。
2. Railway 尚未部署，卷的 uid 10001 写权限、服务域名、代理来源行为和平台重启持久化需部署后验收。
3. 单进程调度器与活动浏览器不支持多 replica / worker；浏览器中途状态不可重放恢复。
4. 公共体验仅开放受控 Login API，不代表任意网站测试能力；默认 Mock 不能证明本轮真实模型调用。
5. `create_all` / additive bootstrap 不是完整迁移系统；历史 MySQL TEXT 表不会自动升级到 LONGTEXT。
6. 当前有任务额度和来源限制，没有用户账户体系、长期数据清理或抗 DDoS 能力。统一反向代理来源可能导致共享额度。
