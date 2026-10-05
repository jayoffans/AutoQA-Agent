# Phase 6-2-C-1-A — OpenAPI Import Foundation

建立稳定、按 Workspace 隔离的 API 测试输入。流程为：创建 API 项目 → 上传规范 → 校验 / 解析 → 持久化 → 接口列表。
本阶段不调用 DeepSeek 或其他模型，不生成测试、不执行接口、不生成报告、不新增 Web 测试能力。

## 修改文件

| 文件 | 改动 |
| --- | --- |
| `autoqa/api_import.py` | 受限离线解析、上传、规范 / 接口列表、删除与项目权限检查 |
| `autoqa/product_models.py` | ApiSpecRow / ApiEndpointRow |
| `autoqa/studio_models.py` | Project 的 id / workspace 复合唯一约束 |
| `autoqa/product_app.py` | 仅在 Production 注册新 API；API 项目的 URL 可暂空 |
| `autoqa/product_schemas.py` | API 项目可先导入文档，Web 项目仍要求 URL |
| `migrations/versions/0006_api_import.py` | 新增迁移，不改历史迁移 |
| `autoqa/static/production.js` | multipart、API 测试入口 / 页面、文档删除、禁用计划生成 |
| `autoqa/static/production.css` | 上传、规范列表与响应式接口表 |
| `autoqa/static/locales/zh-CN.json` / `en-US.json` | 中英文文案 |
| `pyproject.toml` / `requirements.lock` | 解析依赖、锁定版本、wheel 中包含新 migration |
| `.github/workflows/ci.yml` | MySQL job 包含导入兼容测试；保留 UI 截图 |
| `tests/test_api_import.py` | 解析、上传、安全、隔离、事务、持久化和删除 |
| `tests/test_api_import_compat.py` | SQLite / MySQL 迁移、旧数据、长文本和复合外键 |
| `tests/test_api_import_browser.py` | Chromium JSON / YAML、错误、XSS、刷新、删除、手机和双语 |
| `tests/test_product.py` / `test_onboarding.py` / `test_docker_production.py` / `test_production_compatibility.py` | 更新最新迁移版本断言与完整链 |
| `README.md` / `docs/phase6-2c-1a.md` | API Testing Workflow 与阶段报告 |

## 数据模型与 migration

- **ApiSpec** (`api_specs`)：id、workspace_id、project_id、name、version、format、raw_content、created_at、updated_at。
- **ApiEndpoint** (`api_endpoints`)：id、workspace_id、project_id、spec_id、method、path、summary、parameters、request_schema、response_schema、created_at。
- UUID 主键；Workspace 必填且有外键。参数为 JSON 数组，请求 / 响应结构为 JSON 对象。
- `version` 保存规范的 `info.version`；`name` 保存 `info.title`；`format` 为 `json` 或 `yaml`。
- MySQL 原始文档使用 LONGTEXT；SQLite 使用 Text。UTC 时间由现有 `now()` 生成。
- 新 migration：`0005_provider_center → 0006_api_import`。新增 Project 的 `(id, workspace_id)` 唯一约束，
  ApiSpec 的 `(id, project_id, workspace_id)` 唯一约束和 ApiEndpoint 到 ApiSpec 的复合外键。
  数据库拒绝跨 Workspace / Project 的关联；删除规范有 `ON DELETE CASCADE`，HTTP 删除也显式清理关联接口。
- 规范和所有接口在同一事务中创建，接口写入失败会回滚规范。历史 migration 内容保持不变。

## API 列表

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `POST /api/projects/{project_id}/api-specs/upload` | multipart `file`；201 返回 `{spec, endpoint_count}`，spec 不含 raw_content |
| `GET /api/projects/{project_id}/api-specs` | 规范元信息数组，含各规范的 endpoint_count |
| `GET /api/projects/{project_id}/api-endpoints` | 接口数组，含 method、path、summary、parameters、request_schema、response_schema |
| `DELETE /api/projects/{project_id}/api-specs/{id}` | 204 删除规范及其接口；外部 Workspace / 错误 Project / 不存在 ID 返回 404 |

延用认证、工作区成员检查、Cookie CSRF 和跨站限制。每个查询 / 删除同时约束 project_id 与 workspace_id；
归档项目和非 API 项目不能导入。没有原始内容下载接口，不把上传文件名作为磁盘路径。

## 支持规范与安全边界

[Prance](https://prance.readthedocs.io/en/latest/api/prance.html) 负责标准解析与规范校验，后端为
[openapi-spec-validator](https://github.com/python-openapi/openapi-spec-validator)；SchemaPath 负责文档内对象引用解析。
已覆盖 **OpenAPI 3.0 / 3.1 / 3.2、Swagger 2.0** 和 JSON / YAML / YML。
提取 paths、HTTP methods、summary、parameters、requestBody、responses。操作参数按 `(name, in)` 覆盖路径参数；
保留状态码、媒体类型、描述和 Schema。Swagger 的 body / formData / consumes / produces 归一化为请求响应 content 结构。

上限：文件 **1,000,000 bytes**、multipart 请求现有上限 **1,200,000 bytes**、每份文档 **1000 个接口**、
**4 MB 解析结构**、**64 层结构深度 / 50,000 节点**。UTF-8 可含 BOM。限制后缀、非法文件名、重复字段和非 JSON YAML 值。
拒绝外部 URL / 文件 / anchor 引用、schema `$id` / `$dynamicRef`、YAML 锚点 / 别名和不安全 YAML 标签；
不执行文档内容、不访问规范中服务器或接口地址、不把库异常中的上传内容回显给用户。

文档内参数、路径对象、requestBody 和 response 的 JSON Pointer 引用通过成熟库解析；
Schema 内的 `$ref` 保留，递归 Schema 不无限展开。原始规范在数据库中与解析输入一起持久化，供后续阶段使用。

## UI 流程

1. 注册 / 登录完成引导，创建 API 测试项目，可暂不填写 Base URL。
2. 项目详情点击 **API 测试**，进入可刷新恢复的项目 API 页面。
3. 选择 JSON / YAML 文件，点击 **上传接口文档**。
4. 显示 **解析完成 · 接口数量: N**、规范列表和接口列表；展开查看参数 / 请求 / 响应结构。
5. **生成测试计划**保持 disabled；删除文档后其接口同步消失。

浏览器测试覆盖桌面与 390 px 手机宽度，并验证恶意 HTML 只显示为文本。
验收截图保存在 `work/phase6-2c-1a-ui/`，由 CI 归档；不加入公开 Demo 页面。

## 验证结果

2026-10-02 本地完整回归：**328 passed、27 skipped、0 failed**，共 355 个测试实例，耗时 664.19 秒。
Phase 6-2-B 基线为 270 passed / 25 skipped，本阶段新增 **60 个实例：58 passed、2 MySQL skipped**。
新增测试分布：53 个后端 / 解析 / 安全实例、3 个 Chromium 实例、4 个 SQLite / MySQL 兼容实例。
全量 JUnit：`work/phase6-2c-1a-full-results.xml`；统计：`work/phase6-2c-1a-summary.json`。

专项覆盖 JSON / YAML / YML、OpenAPI 3.0 / 3.1 / 3.2、Swagger 2.0、参数覆盖与引用、请求 / 响应、
无效规范、外部引用、重复字段、大小 / 数量 / 深度限制、路径穿越、YAML 执行防护、数据库关联约束、
双用户 API 隔离、CSRF、原子回滚、应用重开持久化、删除、XSS、手机和中英文页面。
回归期间补齐 Swagger response 扩展字段过滤，对该用例单独复测：1 passed。

Ruff、production.js / i18n.js 语法、双语 JSON、生产设置与部署结构检查通过。
wheel 构建成功，确认包含当前最终 `autoqa/api_import.py` 源码与 `0006_api_import` migration，新增依赖闭包已锁定且版本兼容。
本地没有 MySQL / Docker，27 个跳过项目全部保留为 skipped；新增 MySQL 验收已加入 CI job，
本轮未推送或执行远程 CI，不将 MySQL / Docker runtime 计为通过。

复现命令：

```bash
pytest -q --junitxml=work/phase6-2c-1a-full-results.xml
pytest tests/test_api_import.py tests/test_api_import_compat.py tests/test_api_import_browser.py -q
ruff check autoqa tests scripts migrations
node --check autoqa/static/production.js
node --check autoqa/static/i18n.js
python -m alembic upgrade head
```

## Git

继续 `codex/phase5-real-product`，基线为 `b971f94`，在已有
`C:/Users/13432/.codex/worktrees/d187/AutoQA-Agent` 工作目录完成修改。
提交说明为 `feat: add OpenAPI import foundation`；具体 hash 见最终验收回复。
没有切换或修改其他工作目录的 main，没有合并、推送或部署。

## Demo 影响与剩余限制

不修改 Demo 路由、页面、种子、Planner、Runner 或部署，不合并 main。共享数据库只增加表 / 约束；
新导入 API 只挂载到 Production。较早 `/openapi` / `/endpoints` 契约配置和执行能力继续保留，
新 ApiSpec / ApiEndpoint 不自动同步到该流程。

同一项目可以保留多份规范，重复上传会新增规范，不做覆盖、去重、版本 diff、分页或跨规范接口合并。
原始内容不提供用户下载，Schema 引用不完全展开。文件上传只接受已打包的单文档；
外部 / 动态引用、YAML 锚点 / 别名、Webhooks / callbacks / 自定义 additionalOperations 不导入为顶层接口。
没有 AI 测试生成、执行或报告，计划按钮保持禁用。本轮不声称已完成 MySQL / Docker / 线上部署验收。
