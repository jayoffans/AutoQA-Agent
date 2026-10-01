# Railway 手动部署

本轮只准备代码和配置，未登录 Railway、创建服务或部署。没有在本机运行 Docker / MySQL：这里的本机验收包括结构检查、MySQL DDL 编译和 URL 转换；真实 MySQL 测试由新增 CI job 提供，必须查看实际运行结果。

## 环境变量

| 变量 | 公开 Demo 设置 |
| --- | --- |
| `AUTOQA_ENV` | `production` |
| `HOST` | `0.0.0.0` |
| `PORT` | 读取 Railway 注入的值，无须手动固定 |
| `DATABASE_URL` | `${{MySQL.MYSQL_URL}}`，服务名须与实际 MySQL 服务名一致 |
| `DEMO_MODE` | `true`，production 默认开启 |
| `DEMO_DAILY_LIMIT` | `20`，UTC 每日全局任务额度；0 禁用新建 |
| `DEMO_IP_LIMIT` | `3`，UTC 每日 socket 来源额度；0 禁用新建 |
| `DEMO_LLM_ENABLED` | `false`，强制使用 MockProvider，即使存在模型密钥 |
| `AUTOQA_DATA_DIR` | `/app/data`；SQLite / 本地检查点必须使用持久卷 |
| `AUTOQA_TRUSTED_PROXY_CIDRS` | Railway 当前实测 ingress 为 `100.64.0.0/24`；只在确认可信代理地址范围后配置 |
| `AUTOQA_ARTIFACT_DIR` | `/app/data/artifacts`；推荐把证据和数据放同一持久卷 |
| `AUTOQA_PUBLIC_BASE_URL` | 可留空，从 `RAILWAY_PUBLIC_DOMAIN` 推导 HTTPS 域名 |
| `AUTOQA_SEED_DEMO` | 可省略；公开 Demo 在空任务库中自动初始化 |
| `AUTOQA_API_TOKEN` | 非 Demo 的 production 必填；公开 Demo 无需向访客分发 token |

只有维护者希望在有限额度内开放真实规划时，才设置 `DEMO_LLM_ENABLED=true`、`LLM_PROVIDER=deepseek` 或 `openai`、`LLM_MODEL` 和对应 provider key。通过 Railway Variables 配置，不提交 `.env`，不发送到聊天或页面。每个 API 任务最多一次计划调用，输出上限 4096 tokens，失败不自动重试或切换真实 provider。

## 手动步骤

1. 把本轮 commit 推送到 GitHub，确保 `.env`、数据库和运行日志未跟踪。
2. 在 Railway 创建项目，从该 GitHub 仓库创建服务。仓库根目录的 Dockerfile 负责安装依赖和 Chromium；生产命令是 `python -m autoqa`，无需 `start.cmd`。[Railway 部署文档](https://docs.railway.com/deployments/reference)
3. 添加 MySQL 服务，把它的 `MYSQL_URL` 引用绑定为应用的 `DATABASE_URL`。配置层转为 `mysql+pymysql`，保留 URL 编码密码并设置 utf8mb4。[MySQL 变量文档](https://docs.railway.com/databases/mysql)
4. 添加持久卷挂载到 `/app/data`，设置上表变量。镜像以 uid 10001 运行，确认卷挂载后该目录可写；不要把数据库写入临时镜像层。[持久卷说明](https://docs.railway.com/guides/docker-compose)
5. 保持一个 replica / worker。生成公共域名，确认 `AUTOQA_PUBLIC_BASE_URL` 或平台域名正确，然后手动部署。
6. 配置健康检查 `/health`（railway.toml 已指定）。应用监听 `HOST` 和平台 `PORT`；健康检查不调用 LLM。[健康检查文档](https://docs.railway.com/deployments/healthchecks)
7. 访问 `/health` 确认 `{"status":"ok"}`，检查 Dashboard 的种子任务。完成一次计划审批，核对 5/1、High finding 和执行覆盖率；超额应返回 429，已有报告仍可访问。
8. 重启服务，确认种子无重复、配额未重置、历史报告仍存在。查看 CI MySQL job 是否通过，再宣布 MySQL 运行验收完成。

## 持久化与安全边界

业务表、配额、访客归属和报告支持 SQLAlchemy SQLite / MySQL。MySQL 模式的 LangGraph 检查点使用同一数据库中的图检查点表；SQLite 模式保留原有 checkpoint 文件，避免破坏已有审批状态。DateTime 字段统一写入 UTC，SQLite / MySQL 返回的无时区时间按 UTC 解读。JSON、Boolean、Text/LONGTEXT、外键通过 SQLAlchemy 方言适配。事务内的全局配额行写锁同时保护任务创建与来源计数。

来源默认取实际 socket peer，应用忽略 `X-Forwarded-For`。Railway ingress 的 socket 地址可能逐请求变化，不能直接用于访客限额。只有 socket peer 属于显式配置的 `AUTOQA_TRUSTED_PROXY_CIDRS` 时，才读取单个合法 `X-Real-IP`；Railway edge 提供该客户端地址。[平台请求头说明](https://docs.railway.com/networking/public-networking/specs-and-limits)。未知代理、重复头和非法地址仍使用 socket 来源。默认不信任任何代理；不要填写所有地址范围。使用 VPN / 出口代理的访客可能共享额度，数据库全局额度始终生效。

访客 cookie 随机、HttpOnly、生产 Secure、SameSite=Strict。只能审批或取消自身任务，系统种子只读；不支持公开项目修改、外部站点、自由步骤、shell、调试接口、文件浏览或原始 artifact 下载。报告通过数据库内容呈现 HTTP 证据。生产错误与日志只输出安全摘要，不序列化异常信息、密钥、请求正文或本地文件路径。

数据库初始化使用 `create_all` 与既有 additive bootstrap；这不是完整版本化迁移系统。已有 MySQL TEXT 表不会自动升级到 LONGTEXT，历史库应备份后单独审查 DDL 迁移。活动浏览器会话不能跨进程恢复；API 计划可以审批后继续，浏览器中途恢复需要新任务。
