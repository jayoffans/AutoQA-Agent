# 验收记录

验收日期：2026-09-30（Asia/Shanghai）。本记录依据实际执行结果，不从简历中继承任何效果指标。

## 自动化验收

**18 / 18 项通过。** 本地 Windows、Python 3.12.14、Playwright 1.63.0、LangGraph 1.2.12。原始 JUnit 记录：[test-results.xml](test-results.xml)。

覆盖内容：

- 真实 Chromium 登录与跨页面会话，8 个步骤全部通过。
- HTTP 500、JavaScript 异常、错误断言、元素缺失的真实故障诊断与证据。
- 延迟渲染中只读断言失败后重试通过；提交动作不自动重试。
- 表单提交前暂停，批准后只执行一次点击；拒绝后取消。
- 完整计划审批 Checkpoint 在进程终止并重启后仍可恢复执行。
- 已执行部分步骤的浏览器丢失后拒绝自动恢复，保留证据并返回 409。
- 任务取消与整体超时。
- SSE 事件、脱敏、HTML / JSON 报告、PNG 与有效 trace ZIP 下载。
- API Token、来源约束、计划步骤限制、显式断言要求、禁用任意脚本。
- 从已允许来源的重定向在请求未允许目标前被阻止，目标服务器实际请求数为零。
- 模型服务协议适配：真实 HTTP 客户端通过 MockTransport 校验请求和返回计划。
- 真实 Langfuse SDK 将 generation / 嵌套 span 导出到本地 OTLP 测试接收器，并验证输入脱敏与父子关系。

Ruff 检查通过；工作台 JavaScript 语法检查通过。

## 演示场景评测

**7 / 7 场景的状态与诊断类别符合预期。** 数据文件：[evaluation-results.json](evaluation-results.json)。全部使用真实 Chromium 和演示模板，不使用模型推理。

| 场景 | 预期与实际 |
| --- | --- |
| 登录与多页面 | passed |
| 订单接口 HTTP 500 | failed / network_error |
| 前端 JavaScript 异常 | failed / frontend_error |
| 业务断言不匹配 | failed / assertion_error |
| 元素缺失 | failed / element_error |
| 延迟渲染重试 | passed，发生真实重试 |
| 人工审批提交 | 审批前暂停，批准后 passed |

此指标用于检查执行器和诊断链路是否符合受控场景预期，不表示开放网站测试成功率。工作台显示的任务通过率包含故意失败的测试，所以不会显示为 100%。

## MCP 与界面验证

Chrome DevTools MCP 1.10.1 实际通过 stdio 启动，并通过 CDP 连接到同一个 Chromium 任务浏览器。按唯一页面 URL 找到 pageId，向 `list_console_messages`、`list_network_requests`、`take_snapshot` 显式传入 pageId。从首次导航前开始监听，避免错过故障请求。三项读取工具均无错误，采集结果包含演示目标与真实 HTTP 500。运行摘要：[mcp-verification.json](mcp-verification.json)。Windows 验证使用 `AUTOQA_MCP_COMMAND=npx.cmd`。

工作台在 1440px 桌面与 390px 手机视口验证：任务列表、实时详情、失败证据、页面导航正常，无 JavaScript 异常或横向溢出。桌面截图位于 `screenshots/workspace.png`。

## 尚未在本机验证的外部环境

- 本机未安装 Docker daemon，也未运行 MySQL。Dockerfile、Compose 与 SQLAlchemy MySQL 适配已实现，Compose 结构已解析检查；镜像构建和 MySQL 运行验收尚未执行。
- 未提供真实模型密钥。OpenAI 兼容规划接口已实现并通过协议测试，但没有调用真实模型服务。
- 未提供真实 Langfuse 云凭证。SDK 的本地 OTLP 导出已验证，但未验证云端鉴权和看板展示。
- 没有对真实第三方业务系统进行开放场景评测。
