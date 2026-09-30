import html
import json


def report_html(task: dict) -> str:
    def esc(v):
        return html.escape(str(v))
    rows = "".join(
        f'<tr><td>{s["index"] + 1}</td><td>{esc(s["step"]["description"])}</td>'
        f'<td>{"PASS" if s["ok"] else "FAIL"}</td><td>{s["duration_ms"]} ms</td>'
        f'<td>{s["attempt"] + 1}</td><td>{esc(s.get("error") or "—")}</td></tr>' for s in task["steps"]
    )
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>AutoQA 测试报告</title><style>body{{font:15px system-ui;margin:40px;color:#162b39}}
    table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:12px;text-align:left}}
    pre{{white-space:pre-wrap;background:#f2f6f8;padding:20px}}h1{{color:#087e74}}</style>
    <h1>AutoQA Agent · 测试报告</h1><p>任务：{esc(task['id'])}</p>
    <p>状态：{esc(task['status'])} · 规划模式：{esc(task['planner_mode'])}</p>
    <p>需求：{esc(task['request']['goal'])}</p><p>目标：{esc(task['request']['target_url'])}</p>
    <table><thead><tr><th>步骤</th><th>描述</th><th>结果</th><th>耗时</th><th>尝试</th><th>错误</th></tr></thead>
    <tbody>{rows}</tbody></table><h2>诊断依据</h2>
    <pre>{esc(json.dumps(task['diagnosis'], ensure_ascii=False, indent=2))}</pre>
    <p>诊断为证据归类，后端根因仍需结合服务日志确认。截图和 trace 可在任务详情下载。</p></html>"""
