# GitHub 作品集：当前验收与公开放行索引

审核开始：2026-10-21；收尾复核：2026-10-22（Asia/Shanghai）。本页记录本轮审核；[早期验收](validation.md)和各阶段报告保留原日期、原结果，不代表当前版本的验证。

## 审核基线与范围

- 仓库：`jayoffans/AutoQA-Agent`，审核开始时为 Private；远程 main 为 `ca6c423d74dd87b9f9dddd18576c6ef4aae8da59`。
- 待处理 [PR #2](https://github.com/jayoffans/AutoQA-Agent/pull/2) 的 head 为 `2a2f63f5b46a21e7954f47c0d4788013a23b5aa3`；本轮从此版本建立独立分支 `codex/public-release-review`。
- 原主 checkout 有未提交的验收文档与 XML，本轮使用同级独立工作树，保留原内容，不将其混入交付。
- GitHub 是作品集展示入口。README 不提供公网 Demo；既有部署配置、历史报告和真实截图保留。没有调用 Railway 管理接口、删除或停止服务，也没有调用付费模型。

## 本轮修改

| 文件 | 目的 |
| --- | --- |
| `README.md` | 精简展示、组件架构图、真实截图、工程难点、验证边界和本地流程；纠正 API Planner 与保留的 LangGraph 浏览器流程的归属 |
| `start.cmd`、`scripts/start.ps1` | 修复 Windows PowerShell 5.1 编码解析、首次配置、解释器选择及失败退出码；沿用已有入口与锁定依赖 |
| `requirements.lock` | 仅定向更新 certifi `2026.10.19`、cryptography `50.0.2`、Playwright `1.64.0`，分别处理已确认的 CA 信任、捆绑 OpenSSL 和旧 Chromium 风险 |
| 本页及下方两份审查报告 | 当前验收、安全与依赖许可记录；不改写历史验收 |

未新增 Agent、API 功能、项目依赖或扫描忽略规则；没有强推或改写 Git 历史。

## 安全与依赖审查

- [全历史敏感信息审查](public-security-review.md)：扫描覆盖、成熟工具选择、误报、图像人工检查及限制。
- [依赖安全与许可证审查](public-dependency-review.md)：全部锁定 Python 包、直接依赖、重要传递依赖、可选 MCP、授权来源及已知公告。
- certifi 的 [GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2) 涉及受损中间 CA 链的 TLS 信任风险。采用官方修复版，不批量升级其他包；旧镜像不会因源码锁文件更新而自动获得新证书包。
- cryptography 采用上游同 minor 补丁版 `50.0.2`，其 wheel 搭载 OpenSSL `4.0.3`。Playwright `1.63.0` 没有 Python 补丁版，采用范围内的最小后续版本 `1.64.0`，Chromium 为 `156.0.8078.4` / revision `1248`；不使用新增 API。版本证据、兼容成本、原生/浏览器残留限制见依赖报告。

## 本次验证

本页记录审查范围、复现入口与已完成检查；**最终本地计数、跳过原因、失败初轮与最后一次 Actions 结果统一记录在[本轮 PR 的验收表](https://github.com/jayoffans/AutoQA-Agent/pulls?q=is%3Apr+head%3Acodex%2Fpublic-release-review)**，以便结果完成后更新记录而不为更新 CI 结果反复触发相同测试。命令中的 `work/` 是忽略目录，原始日志、临时凭据、数据库、浏览器 trace 不直接纳入公开提交。

| 检查 | 当前结果 / 证据 |
| --- | --- |
| PR #2 Actions | [run 39553459168](https://github.com/jayoffans/AutoQA-Agent/actions/runs/39553459168)：`tests`、`auth-mysql`、`docker-production` 全部 success；无 review 或未解决 review thread |
| Ruff | `python -m ruff check autoqa tests scripts migrations` 通过 |
| JavaScript | Node 22.16.0，现有 5 个前端入口 `node --check` 全通过 |
| 部署与配置 | `validate_deployment.py`、`validate_production.py` 通过；结构检查不等于 Docker runtime 成功 |
| 最终锁版本全量 pytest / MySQL / Docker | 独立源码副本、独立 venv，707 项：703 passed、4 errors、0 failures、0 skipped；4 errors 均为 Docker 构建 fixture 失败，不能称全量通过。真实 MySQL 已启用。原始记录 `work/final-regression/work/final-full.xml`；后续定向复验与最终 CI 结果见本轮 PR 验收表，不修改测试断言、超时或 skip 条件 |
| 本地 Docker 构建诊断 | 一次独立原样构建复现 `files.pythonhosted.org` 的 pip 读取超时，退出 1；日志保留在 `work/public-release/docker-build.log`。不把构建尝试写成 runtime 通过，最终 CI Docker 门禁另行核对 |
| 启动与 Chromium | 隔离副本 `start.cmd -NoBrowser` 真正启动、health/root 200、仅 loopback、Demo/Mock；7 组真实浏览器检查通过（6 用例人工审批、5 passed + 1 预设失败、报告刷新保持、3 种视口、0 JS errors）。最终锁版本启动 smoke 亦通过，补充脚本结果见 PR |
| 启动失败处理 | 无效配置退出 1、canary 未输出、已有 `.env` 不变；端口占用退出 3、readiness job 数为 0。首次标准 venv 安装遇下载 hash mismatch 时拒绝安装；官方文件重下与 PyPI hash 一致，正常重试安装并启动成功，未关闭校验 |
| 本轮最终 Actions | 合并前必须核对最终 head 的 `tests`、`auth-mysql`、`docker-production` 全部 success；run 链接及最终状态记在 PR，不以 PR #2 的旧 run 代替 |

全量测试在无 Demo `.env` 干扰的 checkout、Python 3.12、锁定依赖中运行。复用既有 `scripts/ci_mysql.py` 分配隔离 MySQL、随机本地端口和临时凭据，启用 `AUTOQA_REQUIRE_MYSQL=1` 与 `AUTOQA_RUN_DOCKER=1`；临时凭据不打印、不提交，结束只清理本次容器。

自动开浏览器的成功分支未直接观察，已确认代码仅在 `/health=200` 后打开页面、失败退出清理自己的等待 job；`-NoBrowser` 路径已端到端实测。终端强制关闭或系统强杀不属于已验证的正常清理路径。

## 如何解读作品集证据

| 证据 | 能说明什么 | 不能说明什么 |
| --- | --- | --- |
| Mock / fixture | 结构校验、失败处理、执行与报告边界按受控输入工作 | 真实模型生成质量与真实推理成本 |
| Chromium / HTTP / MySQL / Docker | 各自已运行的真实浏览器、请求、数据库与容器行为 | 任意第三方业务系统成功率 |
| [受控 Alpha 与历史诊断](planner-alpha-blocker.md) | 有限契约、预设失败与已记录模型响应问题 | 修复后真实 BYOK 的完整闭环通过 |
| [阶段报告](phase6-2c-1e.md)与历史截图 | 对应日期版本的受控运行事实 | 当前在线服务实时状态 |

旧 README 中的受控 Alpha / Production 数字未找到可逐项复核的原始记录，本轮不将这些数字用作放行证据，并从当前 README 移除；Git 历史仍保留原文。本轮未调用真实模型、未访问真实用户业务数据。

## Public 放行结论

**当前不批准转为 Public。仓库可见性保持 Private。** 技术审查与 PR 合并不等于公开授权；最终是否放行由负责人决定。

公开前必须完成以下明确决定：

1. 确认原创代码与截图/图标的权利来源、署名主体，并选择许可证。建议 MIT：适合求职源码展示与复用，规则简短；第三方 MPL、Apache、MITNFA 等仍遵守原许可。现阶段未擅自添加 LICENSE 或版权声明。
2. 明确接受历史 commit 中个人邮箱、报告中的私人目录元数据公开。当前文件删除不能清除 Git 历史；本轮遵守禁止改写历史的要求。
3. 核对最终 PR 的安全审查与 CI；了解依赖报告中镜像系统包等未完成扫描的范围。扫描无已知问题不构成不存在所有未知风险的证明。

负责人确认以上事项后，另行执行可见性发布；本轮没有调用修改仓库可见性的接口。
