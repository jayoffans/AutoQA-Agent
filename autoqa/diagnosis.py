def diagnose(result: dict, evidence: dict) -> dict:
    console = [v for v in evidence.get("console", []) if v["type"] in {"error", "pageerror"}]
    network = [v for v in evidence.get("network", []) if v.get("status", 0) >= 400 or v.get("error")]
    action = result.get("step", {}).get("action", "")
    if evidence.get("blocked"):
        category, summary, suggestion = "origin_policy", "测试访问了未授权的来源", "核对跳转和资源来源，再调整允许列表。"
        proof = evidence["blocked"]
    elif network:
        category, summary, suggestion = "network_error", "页面出现 HTTP 错误或网络请求失败", "检查对应 API 的服务日志、路由与响应状态。"
        proof = network
    elif console:
        category, summary, suggestion = "frontend_error", "页面发生前端运行异常", "依据 Console 报错检查事件处理器、变量与组件状态。"
        proof = console
    elif not result.get("ok") and action in {"click", "fill", "select", "press", "assert_visible"}:
        category, summary, suggestion = "element_error", "目标元素不可见、缺失或定位不唯一", "检查选择器、iframe、异步渲染与页面实际状态。"
        proof = [{"selector": result.get("step", {}).get("selector"), "error": result.get("error")}]
    elif action.startswith("assert_"):
        category, summary, suggestion = "assertion_error", "页面实际状态未达到预期", "比较截图和 DOM，确认业务行为与断言条件。"
        proof = [{"error": result.get("error"), "url": evidence.get("url")}]
    else:
        category, summary, suggestion = "execution_error", "浏览器步骤执行失败", "检查步骤错误和 Playwright trace。"
        proof = [{"error": result.get("error")}]
    return {"category": category, "summary": summary, "suggestion": suggestion,
            "evidence": proof, "confidence": "evidence_based", "root_cause_confirmed": False}


def runtime_errors(evidence: dict) -> bool:
    return (any(v["type"] in {"error", "pageerror"} for v in evidence.get("console", []))
            or any(v.get("status", 0) >= 400 or v.get("error") for v in evidence.get("network", []))
            or bool(evidence.get("blocked")))
