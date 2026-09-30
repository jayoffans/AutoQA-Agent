# AutoQA Studio Phase 2

## Delivery

本阶段在现有 UI、FastAPI、SQLAlchemy 与浏览器工作流上增量实现。没有替换 LangGraph 浏览器图、重试/取消/审批机制或原有 22 个测试。新增 API 任务分支复用 Runner 的并发、超时、取消、审批路由与存储。

### Changed files

| 范围 | 文件 |
| --- | --- |
| 持久化与接口 | `autoqa/store.py`、`store_base.py`、`studio_models.py`、`app.py`、`schemas.py` |
| 模型与执行 | `autoqa/providers.py`、`api_workflow.py`、`planner.py`、`runner.py`、`reports.py`、`security.py` |
| 工作台组件 | `autoqa/static/index.html`、`style.css`、`app.js`、`components.js`、`studio-model.js` |
| 配置与部署 | `autoqa/config.py`、`__main__.py`、`.env.example`、`.gitignore`、`Dockerfile`、`compose.yaml`、`docker-compose.yml`、`railway.toml`、`scripts/start.ps1` |
| 验证与 CI | `tests/test_phase2.py`、`test_studio_reports.py`、`scripts/verify_phase2.py`、`verify_studio.py`、`validate_deployment.py`、`.github/workflows/ci.yml` |
| 说明与证据 | `README.md`、`docs/phase2.md`、`studio-guide.md`、`demo-guide.md`、`usage.md`、Phase 2 的 JUnit / JSON 记录与截图 |

原始 `workflow.py`、`browser.py` 和原测试文件未修改；用户已有的 `docs/validation.md` 与展示边界测试记录未覆盖。

## Database

| 表 | 用途 |
| --- | --- |
| `projects` | 项目名称、说明、默认 URL、内置标记、归档与时间戳 |
| `tasks` | 复用原有任务状态、request、plan、diagnosis、approval 与时间戳 |
| `task_metadata` | task → project 外键，持久化 title / test_type；不改写旧 tasks 表 |
| `test_plans` | 一任务一计划、脱敏 content、generated / approved / rejected 状态 |
| `test_cases` | 显式浏览器断言或 API Case、预期结果、最终尝试状态 |
| `test_runs` | 一任务一运行、状态、起止时间、passed / failed / coverage |
| `findings` | 全任务 ID 的唯一问题引用、Severity、描述、建议和脱敏证据 |
| `reports` | 汇总 JSON、HTML 快照、相对 html_path 与生成时间 |
| `workflow_steps` | 阶段/步骤的 Waiting / Running / Completed / Failed、input / output、起止时间 |
| `steps` / `events` | 复用原有执行尝试与持久化 SSE 事件 |
| `schema_versions` | 记录 Phase 2 的新增结构版本与应用时间 |

首次启动 `create_all` 创建新增表，按旧任务补齐项目、元数据、计划、用例、运行和报告；重复启动不会重复生成记录。旧 tasks / steps / events 不做 ALTER、重命名或删除。SQLite 启用外键约束，开发数据库父目录自动创建。MySQL 使用同一 SQLAlchemy 模型；报告 HTML 在 MySQL 使用 LONGTEXT。

目前是新增表的版本记录与幂等补齐，不是完整 Alembic 迁移链。未来修改已有字段、复杂数据转换或滚动多实例发布前应引入正式迁移命令；升级现有生产数据库前备份。当前进程启动会把 queued / running 标记 interrupted，避免重放业务动作。计划审批可恢复，执行中浏览器会话不能持久化。

报告终态写入数据库，HTML 同时原子落盘到 `<artifact_dir>/<task_id>/report.html`。GET 报告优先返回数据库快照，文件删除也不影响已保存的 HTML 快照；原截图、DOM、Trace 仍由证据目录提供。

项目 DELETE 是归档，保留任务、报告和外键关系；内置演示项目不可更改或归档。老浏览器项目与任务关联在首次成功连接时一次性导入并清理旧 localStorage key。当前项目、任务、统计、流程和报告均以服务端为准；sessionStorage 仅保存访问令牌与当前任务选择。

## API

| Method | Path | 行为 |
| --- | --- | --- |
| GET | `/api/projects` | 项目与数据库汇总的运行数 |
| POST | `/api/projects` | `{name, description?, url}` 创建项目 |
| GET | `/api/projects/{id}` | 项目详情 |
| PATCH | `/api/projects/{id}` | 更新非内置项目名称、说明或 URL |
| DELETE | `/api/projects/{id}` | 归档，204；历史数据保留 |
| GET | `/api/dashboard` | 对全部历史任务/Findings 的 SQL 汇总 |
| PATCH | `/api/tasks/{id}/project` | `{project_id}` 迁移旧关联或重新归组 |
| GET | `/api/tasks/{id}/workflow` | 持久化阶段与每次尝试 |
| GET | `/api/tasks/{id}/findings` | 问题与脱敏证据 |

原有 tasks / events / approve / cancel / report / artifacts API 路径与原字段保持兼容。创建任务新增可选 `project_id`、`title`、`test_type`（regression / smoke / api）。任务详情新增 project、run、report、findings 与 workflow_steps 信息。没有传 project_id 的旧客户端按内置场景或目标来源自动关联。

Dashboard 的 Projects / Running / Passed / Failed / Issues / Success rate 来自整个数据库；最近记录仍展示最近 50 条。报告 Coverage 是已执行显式断言 / 计划断言，不是代码覆盖率。API 流程执行全部用例后汇总，不因第一条业务失败提前结束。

## LLM Provider

`LLMProvider.generate(system, context, schema)` 输出 JSON，随后由 Pydantic 验证。`OpenAIProvider` 用于 OpenAI 或原有兼容接口，`DeepSeekProvider` 使用官方 DeepSeek API，`MockProvider` 只替代计划生成；测试仍真实调用浏览器/HTTP 执行器。

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=你的实际模型ID
DEEPSEEK_API_KEY=仅放在本地.env或部署变量
```

OpenAI 使用 `LLM_PROVIDER=openai` 与 `OPENAI_API_KEY`。`compatible` 保留旧 `AUTOQA_LLM_BASE_URL / MODEL / API_KEY` 配置。`mock` 不需要 Key，用于 CI 和离线 API 演示。DeepSeek 的 `LLM_MODEL=auto` 会通过 `/models` 获取真实可用 ID，然后选择支持的非推理模型；无法解析会报错，不切换到 Mock。显式模型 ID 会直接调用。

HTTP、认证、超时、无效 JSON 和 Schema 错误使用清晰消息；不返回提供方原始错误 body 或 Key。Provider 没有重试/切换到 Mock 的隐含行为。模型输入只描述合同与演示 fixture；输出只允许受约束的浏览器步骤或六类 API 请求，不包含任意脚本/shell 操作。

Provider JSON 协议依据：[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。模型可用性以运行时 `/models` 和账户权限为准。

## Login API Demo

UI 创建任务 → 选择 **Login API Testing** → API 类型 → 生成计划 → 人工审核 → 执行 → 报告。

六类请求：Valid login、Invalid password、Missing username、Missing password、Malformed payload、Unauthorized request。预期 HTTP 状态来自合同，模型不能把实际缺陷结果改写成“预期通过”。fixtures 只使用内置测试账号。`/demo/api/login` 与 `/demo/api/protected` 是受控站点；不是平台账户系统。

`AUTOQA_DEMO_LOGIN_FAULT=true` 默认刻意保留错误密码被接受的缺陷。模型生成计划后，真实 HTTP 返回 200，而合同要求 401，得到 5 Passed / 1 Failed / 100% 执行覆盖率和 High Finding。将开关改为 false 后错误密码返回 401，正常情况下六项全过；结果由实际请求计算，未硬编码 Passed / Failed。

请求不跟随重定向，不自动重放 POST，遵循原目标来源策略。请求/响应证据保存为 `api-case-N.json`，密码/Token 脱敏；页面与报告可查看结果并下载证据。

真实验收使用 **DeepSeek / deepseek-flash**，确实生成计划并通过 HTTP 执行六类请求。已重启服务验证项目、任务、12 条阶段/步骤记录、Finding 与相同报告快照仍存在。数据来自隔离实例，见 [真实模型验收](phase2-llm-results.json)。

## Deployment

开发默认 `DATABASE_URL=sqlite:///./data/autoqa.db`；旧 `AUTOQA_DATABASE_URL` 继续支持，通用变量优先。MySQL 示例：`mysql+pymysql://USER:URL_ENCODED_PASSWORD@HOST:3306/DB?charset=utf8mb4`。账号密码不写入代码。

Docker Compose 使用 `MYSQLHOST / MYSQLPORT / MYSQLUSER / MYSQLPASSWORD / MYSQLDATABASE` 生成正确转义的 SQLAlchemy URL，并等待 MySQL healthcheck。`MYSQL_PASSWORD` 与 `MYSQL_ROOT_PASSWORD` 必須由环境提供，没有硬编码密码回退。

```bash
docker compose config --quiet
docker compose up --build -d
docker compose logs -f autoqa
```

`compose.yaml` 为主配置，`docker-compose.yml` 使用 Compose include 保持兼容入口（需要 Compose 2.20+）。默认只把本机 8765 映射到容器；容器内部监听 `0.0.0.0:$PORT`，使用非 root 用户。数据库、Checkpoints 和证据分别使用持久化卷。

Railway 使用仓库 Dockerfile 与 `railway.toml`，只保留一个实例。创建 AutoQA Service 与 MySQL Service，在应用服务设置：

```dotenv
AUTOQA_ENV=production
AUTOQA_API_TOKEN=设置独立随机令牌
DATABASE_URL=使用MySQL服务的连接URL并采用mysql+pymysql方案
LLM_PROVIDER=deepseek
LLM_MODEL=实际模型ID
DEEPSEEK_API_KEY=在Railway变量中配置
AUTOQA_DATA_DIR=/app/data/checkpoints
AUTOQA_ARTIFACT_DIR=/app/data/artifacts
```

给应用挂载持久化卷 `/app/data`。平台的 `PORT` 由 `python -m autoqa` 读取；程序监听 0.0.0.0，healthcheck `/api/health`。代码读取 `RAILWAY_PUBLIC_DOMAIN` 构建平台自身来源，也可显式设置 `AUTOQA_PUBLIC_BASE_URL=https://你的服务域名`。外部测试站点仍须配置允许列表；模型密钥只在服务端读取。

如果使用独立 MySQL 变量，把 `DATABASE_URL` 设为空，并配置 MySQL 服务的 `MYSQLHOST / MYSQLPORT / MYSQLUSER / MYSQLPASSWORD / MYSQLDATABASE` 引用。代码会处理密码中的特殊字符。生产未设置 API Token 时拒绝启动。当前是团队共享令牌，没有用户身份或租户隔离。

没有操作用户的 Railway 账号，也没有实际部署公网服务。配置依据：[Railway config as code](https://docs.railway.com/config-as-code/reference)、[Healthchecks](https://docs.railway.com/deployments/healthchecks)、[MySQL](https://docs.railway.com/databases/mysql)。

## Validation

本次新增 15 项有效测试，合计 37 项；CI 不要求模型 Key 或 MySQL。`tests/test_phase2.py` 使用临时 SQLite 和 MockProvider；API 合同集成测试通过 ASGITransport 执行真实 FastAPI handler，完整 TCP / 真模型执行另由隔离本地实例验收。

2026-09-30 最终本地验收：pytest **37 / 37 通过**，Ruff 与三模块 JS 语法检查通过；真实 Chromium 的 **12 项 UI 验证通过**，覆盖六页在 390 / 768 / 1440px 的布局、跨浏览器项目共享、审批与报告刷新恢复，无 JavaScript 错误。CI 配置已扩展相同检查，本次没有推送或触发远程 CI。

证据：[JUnit](phase2-test-results.xml)、[UI 结果](phase2-ui-results.json)、[真实模型结果](phase2-llm-results.json)、[部署检查](phase2-deployment-results.json)。真实模型的 [Workflow 截图](screenshots/phase2-api-workflow.png) 与 [Report 截图](screenshots/phase2-api-report.png) 来自实际运行数据。

```bash
pytest -q
ruff check autoqa tests scripts
node --check autoqa/static/app.js
node --check autoqa/static/components.js
node --check autoqa/static/studio-model.js
python scripts/verify_studio.py
python scripts/validate_deployment.py
```

UI 验证需要空的隔离实例；API 演示使用 `LLM_PROVIDER=mock` 即可。真实模型验收需要启动配置好 Provider 的实例，然后执行 `python scripts/verify_phase2.py`。这些脚本会创建真实项目与测试任务。

本机未安装 Docker CLI/引擎，无法运行 `docker compose config` 或 `docker compose up`；已经执行 YAML / TOML / Dockerfile 结构与凭据注入检查。**没有真实运行 MySQL、Docker 容器或 Railway 部署**。不能把 MySQL URI 编码测试、模型编译或配置检查当作 MySQL 运行验收。

## Limits / Next

单进程、一个运行对应一个任务、团队共享 API Token；列表限最近 50 条，但汇总包括全部历史。API 测试目前限定内置 Login 合同，不是通用 OpenAPI 生成器。Schema 版本是新增结构记录，未来变更已有结构时需要完整迁移工具。数据库之外的 Checkpoint / Artifact 仍需持久化卷；没有卷会丢失证据与暂停恢复位置。

下一阶段：真实 MySQL / Docker / Railway 验收，RBAC 与审计、分页和历史趋势、通用 OpenAPI 用例生成、正式 Alembic 迁移、集中式队列与 Checkpoint。当前 UI 和接口为这些扩展保留了项目、任务、运行与报告实体。
