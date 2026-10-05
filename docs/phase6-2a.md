# Phase 6-2-A：首次使用引导

日期：2026-10-01（Asia/Shanghai）。分支：`codex/phase5-real-product`。

本阶段实现新注册用户的首次使用体验。模型配置页仍沿用现有实现；引导中的模型按钮仅为预留入口，不请求 Provider，不新增密钥表单。未进入 Phase 6-2-B。

## 1. 修改文件

| 文件 | 作用 |
| --- | --- |
| `autoqa/onboarding.py` | 当前用户的状态读取、里程碑观察和单向步骤推进 |
| `autoqa/product_models.py` | `OnboardingRow`，一位用户一条记录 |
| `autoqa/product_schemas.py` | 步骤动作与首个项目输入校验 |
| `autoqa/product_app.py` | 注册事务初始化进度、认证返回状态、引导 API、已有模型保存后的里程碑记录 |
| `migrations/versions/0004_onboarding.py` | 仅新增首次使用表，保留已有用户和业务数据 |
| `autoqa/static/production.js` | 四步引导、路由恢复、工作台进度、待添加目标的项目提示 |
| `autoqa/static/production.css` | 深色 SaaS 引导布局、手机布局和进度卡 |
| `autoqa/static/locales/zh-CN.json`、`en-US.json` | 新文案使用现有 i18n，默认中文 |
| `pyproject.toml` | Wheel 包含新增 Alembic 迁移 |
| `tests/test_onboarding.py`、`test_onboarding_browser.py`、`onboarding_support.py` | 后端测试、真实 Chromium 验收及公共引导步骤 |
| `tests/test_auth_browser.py`、`test_product_browser.py` | 适配新注册行为，保留老用户空工作台验收并等待异步完成 |
| `tests/test_product.py`、`test_production_compatibility.py`、`test_docker_production.py` | 更新迁移 head 和既有注册验收流程 |
| `docs/assets/phase6-2a/*.png`、本报告、测试 XML、`README.md` | 截图、验证证据与入口说明 |

## 2. 数据库变化

新增 `user_onboarding`，不改动已有用户字段，不给老用户回填记录：

| 字段 | 含义 |
| --- | --- |
| `user_id` | 主键及 users 外键，一位用户一条记录 |
| `workspace_id` | 首次注册自动创建的 Workspace 外键及索引 |
| `project_id` | 首个引导项目外键，可空，用于重复请求返回同一个项目 |
| `step` | 1–4，默认 1，服务端只允许顺序推进 |
| `model_configured` | 曾完成模型配置，默认 false |
| `project_created` | 已创建项目，默认 false |
| `first_test_completed` | 工作空间存在已结束的 passed/failed 测试，默认 false |
| `onboarding_completed` | 已从完成页进入工作台，默认 false |
| `created_at`、`updated_at` | 进度创建与更新时间 |

迁移链：`0003_auth → 0004_onboarding`。注册时 User、Workspace、Member、Onboarding、Session 同事务保存。项目创建与第三步完成同事务保存；重复或并发请求最多创建一个引导项目。SQLite 迁移重复执行与外键完整性已验证，Wheel 已验证包含新迁移。

所有状态 API 使用认证中的当前 User，输入不接受 `user_id` / `workspace_id`。共享工作空间成员只能读取自己的进度；引导写入还要求当前 Workspace 为本人首次注册的工作空间。GET 在切换工作空间后仍读取本人的原始进度，里程碑只观察该原始工作空间。

## 3. Onboarding 流程

1. 注册成功 → 自动创建个人 Workspace 和进度记录 → `#onboarding` 欢迎页：欢迎使用 AutoQA Studio / AI 软件质量工程平台 → 开始设置。
2. 配置你的 AI 模型 → 配置模型按钮展示预留入口提示 → 稍后设置。跳过时 `model_configured` 保持 false。
3. 创建第一个测试项目 → 项目名称和 API测试 / Web测试 → 保存 → 第四步。名称去除首尾空格，空名称及非法类型被拒绝。
4. 准备完成 → 进入工作台 → `onboarding_completed=true`，保留未完成的模型及首次测试里程碑。

刷新、重登和服务重启恢复数据库中的当前步骤。未完成的用户打开工作台等深链接时返回引导。原有用户没有进度记录，直接进入工作台；已完成引导的用户也直接进入工作台。

首个项目保存 `url=""`，不填虚构地址。后续启动测试时，如果项目尚无测试环境目标，现有界面会引导到项目环境配置。该项目沿用已有项目和测试数据模型。

## 4. UI 截图说明

截图来自隔离本地数据库上的真实 Chromium，均为测试账号；桌面 1440px，手机 390px，无页面脚本错误或手机横向溢出。

| 截图 | 内容 |
| --- | --- |
| [欢迎页](assets/phase6-2a/welcome.png) | 左侧四步进度、平台定位、开始设置 |
| [模型提示](assets/phase6-2a/model.png) | 自带模型说明、预留入口提示、稍后设置 |
| [API 项目](assets/phase6-2a/project-api.png) / [Web 项目](assets/phase6-2a/project-web.png) | 项目名称和两种测试类型 |
| [完成页](assets/phase6-2a/ready.png) | 项目已保存和进入工作台 |
| [工作台进度](assets/phase6-2a/dashboard.png) | 账号、Workspace、项目已完成；模型、首次测试待完成 |
| [手机欢迎页](assets/phase6-2a/mobile.png) | 390px 响应式布局、语言切换和退出登录 |

新用户没有数据时展示首次使用进度；账号和工作空间显示完成，模型、项目和首次测试显示真实进度，不再只呈现八个零。完成引导后保留进度卡直到首次测试完成。

## 5. 新增测试

新增 **30 个测试实例：22 个 Backend + 8 个真实 Chromium Frontend**。

后端覆盖指定的 `test_new_user_onboarding_created`、`test_existing_user_skip_onboarding`、`test_onboarding_workspace_isolation`，以及共享 Workspace 用户隔离、跳过模型、API/Web 项目保存、完成条件、非法输入、身份覆盖拒绝、顺序推进、并发幂等、认证/CSRF、注册回滚、重启与重登、已有模型配置里程碑、真实首次测试结束和老数据迁移。

前端覆盖注册进入中文引导、深链接恢复、模型预留入口、API/Web 完成流程、Dashboard 进度、逐步刷新状态保持、老用户直接 Dashboard、手机端与中英文切换、切换账号后的状态清理。

验证命令：

```text
python -m pytest tests/test_onboarding.py tests/test_onboarding_browser.py -q
python -m pytest -q --junitxml=work/phase6-2a-test-results.xml
python -m ruff check autoqa tests scripts migrations
node --check autoqa/static/production.js
node --check autoqa/static/i18n.js
uv build --wheel --out-dir work/phase6-2a-wheel
```

## 6. 总测试结果

新增测试：**30 passed**（22 Backend + 8 Chromium Frontend）。

最终全量：**197 passed，23 skipped，0 failed**，共 220 个实例（原有 190 + 新增 30），耗时 246.55 秒。跳过项：18 个独立 MySQL 验收、4 个需显式启用的 Docker 验收，以及 1 个缺少 Windows 符号链接权限的用例；不计为通过。

完整 JUnit：[phase6-2a-test-results.xml](phase6-2a-test-results.xml)。包括 Production 认证、隔离、HTTPS 重启/重登、Agent 执行和 Demo 回归。新增引导测试全部通过；Ruff、全部五个既有/Production JavaScript 文件的语法检查通过。

Ruff、JavaScript 语法、Wheel 构建及新迁移打包检查已通过。

## 7. Demo 影响

Demo 的页面、前端 JS、Agent 核心、Provider Gateway、种子数据和运行配置均未修改。引导仅接入 Production 的认证和页面。共用迁移只增加空进度表，不给 Demo 用户或老用户写入记录；Demo 的独立数据及模式隔离保持原有行为。

所有测试使用隔离临时数据库、测试 HTTP 服务及 MockTransport；未连接或修改 Production 数据、真实 Demo 环境或 Railway 配置，也未部署。

## 8. Git

继续 `codex/phase5-real-product`，提交消息：`feat: add first user onboarding flow`。`main` 不修改，不推送或部署。

## 9. 剩余限制

- 模型步骤只提供入口说明，完整模型配置不属于本阶段；后续使用现有模型服务页面。
- 引导只创建项目名称和类型，实际测试前仍需配置目标、模型及已有测试输入。
- 里程碑记录首次完成事实；删除模型配置、归档项目不会重置已完成里程碑。模型当前是否可用仍由既有模型状态提示判断。
- 首次测试里程碑观察本人首次工作空间的真实测试记录；没有新增个人测试归属体系。
- 本机没有可用独立 MySQL 或 Docker 环境，相关 runtime 验收跳过，不能记为通过；既有 CI 兼容性检查已适配新迁移。
