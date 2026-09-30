# 60 秒 Demo

公开演示默认使用 MockProvider 规划，随后真实执行受控 Login API。种子数据是启动时脚本执行 ASGI fixture 生成的结果；它不是本次真实模型调用。Phase 2 的 DeepSeek 验证记录单独保存在 `docs/phase2-llm-results.json`。

| 时间 | 操作 | 讲解重点 |
| --- | --- | --- |
| 0–5s | 打开 Dashboard | 项目、任务和 Finding 来自服务端数据库；展示 Login API Quality Demo。 |
| 5–15s | Create Test Task | 选择 Login API 场景，创建受每日全局和来源额度限制的任务。 |
| 15–30s | Review Test Plan | 查看六类认证契约，确认后批准执行；Mock 只负责规划。 |
| 30–45s | Agent Workflow | 查看 Requirement Analyzer、Test Planner、Human Review、Execution Agent 的持久化输入输出和时间。 |
| 45–60s | Finding + Report | 展示错误密码被接受的 HTTP 证据：6 cases、5 passed、1 failed、1 High finding。100% 是 Planned Assertion Execution Coverage，表示六条计划断言全部执行。 |

若现场额度已耗尽，直接打开种子任务的 Workflow 和 Report。系统种子任务不可取消、重新审批或删除。公开 Demo 只允许内置认证场景，不能填写外部站点或执行 shell。
