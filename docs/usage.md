# 配置、API 与部署说明

返回 [项目首页](../README.md)。本页保留浏览器 Agent 的兼容配置与接口说明；Phase 2 的 Provider、项目 API、Docker 和 Railway 请优先参阅 [新工程说明](phase2.md)，它替代本页旧部署示例。

## 接入自己的测试站点

复制 `.env.example` 为 `.env`，填写授权测试站点和模型信息，重启服务。

```dotenv
AUTOQA_ALLOWED_ORIGINS=https://your-staging.example
AUTOQA_RESOURCE_ORIGINS=https://your-static-cdn.example
AUTOQA_LLM_BASE_URL=https://your-model-provider.example/v1
AUTOQA_LLM_MODEL=your-model-id
AUTOQA_LLM_API_KEY=your-key
AUTOQA_API_TOKEN=your-local-access-token
```

`ALLOWED_ORIGINS` 是可导航来源；`RESOURCE_ORIGINS` 只允许加载资源，不扩大导航范围。按 scheme + host + port 精确匹配。内置工作台的本地来源只能访问 `/demo/`，不能通过浏览器 Agent 访问任务管理 API。重定向目的地也会在请求前检查。跨域登录、iframe 和资源需要明确配置。

计划包含输入、点击、选择或按键时，在内置演示以外默认先审批完整计划；单个 `approval_required=true` 的步骤还会独立暂停。允许列表是来源约束，不是通用的 SSRF 隔离方案；共享部署应在网络层限制出口并启用 API Token。

不要在测试需求中填写真实密码。输入值在 API 的步骤展示与事件中脱敏，密码输入在截图中遮盖；**原始 Playwright Trace、页面正文和站点自行输出的 Console 仍可能包含测试数据**。证据仅保存在本地运行目录，`.gitignore` 排除全部数据库、密钥和原始证据。

## API 示例

接口文档：<http://127.0.0.1:8765/docs>。

```http
POST /api/tasks
Content-Type: application/json

{
  "target_url": "http://127.0.0.1:8765/demo/login.html",
  "goal": "登录并完成多页面测试",
  "auto_approve_demo": true,
  "max_steps": 25,
  "max_retries": 1
}
```

接口返回 `202` 和任务 ID。其他接口：

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| GET | `/api/tasks` | 最近任务 |
| GET | `/api/tasks/{id}` | 计划、步骤、诊断与证据列表 |
| GET | `/api/tasks/{id}/events` | SSE；支持 `Last-Event-ID` / `after` 续读 |
| POST | `/api/tasks/{id}/approve` | `{"approved": true}` 批准 / `false` 拒绝 |
| POST | `/api/tasks/{id}/cancel` | 取消执行或审批等待 |
| GET | `/api/tasks/{id}/report` | HTML 报告；`?format=json` 输出 JSON |
| GET | `/api/tasks/{id}/artifacts/{name}` | 截图、DOM、运行信息、trace |

设置 API Token 后使用 `Authorization: Bearer ...`。工作台「连接与配置」可保存当前浏览器会话的访问令牌。

结构化步骤示例：

```json
{
  "target_url": "http://127.0.0.1:8765/demo/form.html",
  "goal": "检查报名页标题",
  "steps": [
    {"action": "assert_text", "value": "测试报名表", "description": "检查页面标题"}
  ]
}
```

所有计划必须有显式断言，不能只执行操作就判定通过。完整行为约束见 [架构说明](architecture.md)。

## Docker + MySQL

```bash
docker compose up --build -d
docker compose logs -f app
```

工作台仍为 <http://127.0.0.1:8765>。业务任务、步骤与事件写入 MySQL 8.4；LangGraph Checkpoint 独立保存到 SQLite 数据卷。数据库、检查点和证据分别使用持久化卷。

Compose 的默认密码只用于本地演示，可通过 `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` 覆盖。容器以普通用户运行。默认容器未安装 Node.js，MCP 适合在 Windows 本地启用；在容器中启用需自行加入 Node.js LTS 和 MCP 依赖。当前版本必须使用 **单个 Uvicorn worker**，因为活动浏览器与调度器在进程内。

## Chrome DevTools MCP 与 Langfuse

Chrome DevTools MCP 需要 Node.js LTS，固定版本见 `.env.example`：

```dotenv
AUTOQA_MCP_ENABLED=true
AUTOQA_MCP_COMMAND=npx.cmd
```

Linux / macOS 使用 `AUTOQA_MCP_COMMAND=npx`。启用后每个任务启动独立 Chromium 的本地 CDP 端口。MCP 在首次导航前使用 `--browserUrl` 连接同一实例，按唯一 URL 找到任务页 pageId，开始监听 Network / Console；失败时读取运行记录与 DOM。连接或工具读取失败会在诊断的 `mcp` 字段中记录。

服务运行时可执行 `python scripts/verify_mcp.py`，独立验证同一页面与真实 HTTP 500 的 MCP 采集。

Langfuse 配置标准环境变量 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL` 后启动。模型调用用 generation observation；Planner、Browser、Verifier、Analyzer 使用嵌套 span。无凭证时完全关闭远端上报，本地事件和 trace 仍可使用。

集成依据：[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Playwright Request](https://playwright.dev/python/docs/api/class-request)、[Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)、[Langfuse SDK](https://langfuse.com/docs/observability/sdk/instrumentation)。
