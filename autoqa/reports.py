import html
import json


def report_metrics(task: dict) -> dict:
    """Count explicit assertions, using the latest attempt for each planned step."""
    planned = (task.get("plan") or {}).get("steps", [])
    latest = {step["index"]: step for step in task["steps"]}
    assertions = [(index, step) for index, step in enumerate(planned) if step["action"].startswith("assert_")]
    executed = [latest[index] for index, _ in assertions if index in latest]
    metrics = {"total": len(assertions), "passed": sum(bool(step["ok"]) for step in executed),
            "failed": sum(not step["ok"] and step.get("status") != "system_error" for step in executed),
            "pending": len(assertions) - len(executed),
            "coverage": int(len(executed) / len(assertions) * 100 + 0.5) if assertions else None}
    if task.get("production"):
        metrics["errors"] = sum(step.get("status") == "system_error" for step in executed)
    return metrics


def report_issues(task: dict) -> list[dict]:
    latest = {step["index"]: step for step in task["steps"]}
    diagnosis = task.get("diagnosis") or {}
    severity = "High" if diagnosis.get("category") in {
        "network_error", "frontend_error", "task_timeout", "agent_error", "api_contract_error"
    } else "Medium"
    issues = [{"id": f'BUG-{task["id"]}-{step["index"] + 1}', "severity": severity,
               "description": step.get("error") or step["step"]["description"],
               "suggestion": diagnosis.get("suggestion") or "检查页面状态、定位器与预期结果，并复核步骤证据。"}
              for step in latest.values() if not step["ok"] and step.get("status") != "system_error"]
    if not issues and diagnosis and task["status"] == "failed" and not task.get("production"):
        issues.append({"id": f'RUN-{task["id"]}', "severity": severity,
                       "description": diagnosis.get("summary", "运行失败"),
                       "suggestion": diagnosis.get("suggestion", "检查运行证据。")})
    return issues


def report_html(task: dict) -> str:
    def esc(value):
        return html.escape(str(value if value is not None else "—"))

    metrics = report_metrics(task)
    issues = report_issues(task)
    planned = (task.get("plan") or {}).get("steps", [])
    latest = {step["index"]: step for step in task["steps"]}
    def result_label(step):
        if not task.get("production"):
            return "PASS" if step["ok"] else "FAIL"
        return "执行错误" if step.get("status") == "system_error" else "已通过" if step["ok"] else "未通过"
    # Keep every execution attempt as an auditable history; summaries deduplicate retries.
    rows = "".join(
        f'<tr><td>{step["index"] + 1}</td><td>{esc(step["step"]["description"])}</td>'
        f'<td>{result_label(step)}</td>'
        f'<td>{step["duration_ms"]} ms</td>'
        f'<td>{step["attempt"] + 1}</td><td>{esc(step.get("error") or "—")}</td></tr>'
        for step in task["steps"]
    )
    rows += "".join(
        f'<tr><td>{index + 1}</td><td>{esc(step["description"])}</td><td>NOT RUN</td>'
        '<td>—</td><td>—</td><td>未执行</td></tr>'
        for index, step in enumerate(planned) if index not in latest
    )
    bug_rows = "".join(
        f'<tr><td>{esc(issue["id"])}</td><td>{esc(issue["severity"])}</td>'
        f'<td>{esc(issue["description"])}</td><td>{esc(issue["suggestion"])}</td></tr>' for issue in issues
    ) or '<tr><td colspan="4">暂无已记录的问题；结果仅覆盖本次实际执行范围。</td></tr>'
    cards = "".join(
        f'<article><span>{label}</span><strong>{esc(value)}</strong><small>{note}</small></article>'
        for label, value, note in [
            ("测试用例", metrics["total"], "计划中的显式断言"),
            ("已通过", metrics["passed"], "按最终尝试统计"),
            ("未通过", metrics["failed"], f'{metrics.get("errors", 0)} 个执行错误 · {metrics["pending"]} 个未执行'),
            ("执行覆盖率", f'{metrics["coverage"]}%' if metrics["coverage"] is not None else "—", "计划断言执行覆盖率 · 非代码覆盖率"),
        ]
    )
    preview = ("" if task["status"] in {"passed", "failed", "cancelled", "interrupted"}
               else '<p class="notice">实时预览：任务尚未结束，结果可能更新。</p>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>AutoQA Studio · 测试报告</title>
    <style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{font:14px/1.7 system-ui,"Segoe UI",sans-serif;
    margin:0;background:#101116;color:#e7eaf2}}main{{max-width:1120px;padding:48px 28px;margin:auto}}
    .brand{{color:#a89eff;font-size:12px;letter-spacing:2px}}h1{{font-size:32px;margin:12px 0 4px}}
    h2{{font-size:17px;margin-top:34px}}p,small{{color:#9ca2b2}}.cards{{display:grid;
    grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}}article{{border:1px solid #303442;
    background:#191c24;border-radius:10px;padding:20px}}article span,article small{{display:block;font-size:11px}}
    article strong{{display:block;font-size:32px;margin:10px 0;color:#b8b0ff}}.table-wrap{{overflow:auto}}
    table{{border-collapse:collapse;width:100%;background:#191c24}}td,th{{border:1px solid #303442;
    padding:12px;text-align:left;font-size:12px;vertical-align:top}}th{{color:#a7adbc;background:#15171f}}
    pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#191c24;border:1px solid #303442;
    border-radius:10px;padding:20px;font-size:12px}}.notice{{padding:14px;background:#29233b;border-radius:8px}}
    @media(max-width:600px){{main{{padding:24px 16px}}.cards{{grid-template-columns:1fr 1fr;gap:10px}}
    article{{padding:14px}}h1{{font-size:26px}}}}@media print{{:root{{color-scheme:light}}body{{background:white;
    color:black}}article,table,pre,th{{background:white;color:black}}td,th,pre,article{{border-color:#aaa}}
    p,small,article strong,.brand{{color:#444}}main{{padding:0}}article{{break-inside:avoid}}}}</style></head><body><main>
    <div class="brand">AUTOQA STUDIO</div><h1>{"测试报告" if task.get("production") else "Test Report · 测试报告"}</h1>
    <p>任务：{esc(task["id"])} · 创建时间：{esc(task.get("created_at"))}</p>
    <p>状态：<strong>{esc(task["status"])}</strong> · 规划模式：{esc(task["planner_mode"])}</p>
    <p>需求：{esc(task["request"]["goal"])}</p><p>目标：{esc(task["request"]["target_url"])}</p>{preview}
    <div class="cards">{cards}</div><p>覆盖率 = 已执行断言 / 计划断言，不是代码覆盖率。
    流程可因操作或运行异常失败，即使 Failed 断言数为 0；任务状态优先。</p>
    <h2>问题发现（{len(issues)}）</h2><p>严重程度按故障类别规则分级，根因待确认。修复建议为 AI 分析建议。</p>
    <div class="table-wrap"><table><thead><tr><th>ID</th><th>严重程度</th><th>描述</th><th>AI 分析建议</th>
    </tr></thead><tbody>{bug_rows}</tbody></table></div>
    <h2>执行历史 · 步骤与重试记录</h2><div class="table-wrap"><table><thead><tr><th>步骤</th><th>描述</th>
    <th>结果</th><th>耗时</th><th>尝试</th><th>错误</th></tr></thead><tbody>{rows}</tbody></table></div>
    <h2>诊断依据</h2><pre>{esc(json.dumps(task["diagnosis"], ensure_ascii=False, indent=2))}</pre>
    <p>诊断为证据归类，后端根因仍需结合服务日志确认。截图、DOM 与 Playwright Trace 可在工作流页面下载。</p>
    </main></body></html>'''
