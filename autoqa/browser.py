import asyncio
import json
import socket
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from .config import Settings
from .mcp_bridge import DevToolsBridge
from .schemas import RETRYABLE, Step
from .security import TargetPolicy, redact, safe_url

DOM_SCRIPT = """() => {
  const clone = document.documentElement.cloneNode(true);
  clone.querySelectorAll('script,style').forEach(e => e.remove());
  clone.querySelectorAll('input,textarea').forEach(e => {
    e.removeAttribute('value'); if(e.tagName==='TEXTAREA') e.textContent='[REDACTED]';
  });
  return {
    title: document.title,
    text: document.body.innerText.slice(0,14000),
    elements: [...document.querySelectorAll('a,button,input,select,textarea,[role]')].slice(0,100).map(e => ({
      tag:e.tagName.toLowerCase(), id:e.id, testid:e.getAttribute('data-testid'),
      type:e.getAttribute('type'), name:e.getAttribute('name'),
      text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,200),
      href:e.tagName==='A' ? e.pathname : null
    })), html:clone.outerHTML.slice(0,150000)
  };
}"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BrowserSession:
    def __init__(self, settings: Settings, policy: TargetPolicy, task_id: str, timeout: int):
        self.settings, self.policy, self.task_id, self.timeout = settings, policy, task_id, timeout
        self.directory: Path = settings.artifact_dir / task_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.console, self.network, self.blocked = [], [], []
        self.playwright = None
        self.browser_url = ""
        self.mcp = None
        self.mcp_error = ""

    async def start(self, target_url: str):
        self.policy.validate(target_url)
        self.playwright = await async_playwright().start()
        args = []
        if self.settings.mcp_enabled:
            port = free_port()
            self.browser_url = f"http://127.0.0.1:{port}"
            args = [f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"]
        self.browser = await self.playwright.chromium.launch(headless=self.settings.headless, args=args)
        self.context = await self.browser.new_context(viewport={"width": 1365, "height": 900},
                                                       service_workers="block", accept_downloads=False)
        await self.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        await self.context.route("**/*", self.route)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout)
        self.page.on("console", lambda msg: self.console.append({"type": msg.type, "text": msg.text[:2000]}))
        self.page.on("pageerror", lambda err: self.console.append({"type": "pageerror", "text": str(err)[:2000]}))
        self.page.on("response", lambda r: self.network.append({"url": safe_url(r.url), "status": r.status,
                                                                "method": r.request.method}))
        self.page.on("requestfailed", lambda r: self.network.append({"url": safe_url(r.url),
                                                                     "error": r.failure, "method": r.method}))
        self.page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        if self.settings.mcp_enabled:
            await self.page.goto("about:blank#autoqa-" + self.task_id)
            self.mcp = DevToolsBridge(self.settings, self.browser_url, self.page.url)
            try:
                await self.mcp.start()
            except Exception as error:
                self.mcp_error = str(error)[:1000]
                await self.mcp.close()
                self.mcp = None
        await self.page.goto(target_url, wait_until="domcontentloaded")

    async def route(self, route):
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        try:
            self.policy.validate(url, resource=not route.request.is_navigation_request())
        except ValueError:
            self.blocked.append({"url": safe_url(url), "reason": "origin_policy"})
            await route.abort("blockedbyclient")
            return
        # Do not let an allowed endpoint redirect the browser to another origin
        # before the redirect destination has been checked.
        try:
            response = await route.fetch(max_redirects=0, timeout=self.timeout)
            location = response.headers.get("location")
            if 300 <= response.status < 400 and location:
                self.policy.validate(urljoin(url, location), resource=not route.request.is_navigation_request())
            await route.fulfill(response=response)
        except ValueError:
            self.blocked.append({"url": safe_url(url), "reason": "redirect_origin_policy"})
            await route.abort("blockedbyclient")
        except Exception:
            await route.abort("failed")

    async def snapshot(self):
        dom = await self.page.evaluate(DOM_SCRIPT)
        return {"url": safe_url(self.page.url), "title": dom["title"], "text": dom["text"],
                "elements": dom["elements"]}

    async def execute(self, step: Step) -> dict:
        start = time.monotonic()
        result = {"step": step.model_dump(), "ok": True, "error": None, "retryable": step.action in RETRYABLE}
        try:
            locator = self.page.locator(step.selector) if step.selector else None
            match step.action:
                case "navigate":
                    self.policy.validate(step.value)
                    await self.page.goto(step.value, wait_until="domcontentloaded")
                case "click":
                    await locator.click()
                case "fill":
                    await locator.fill(step.value)
                case "select":
                    await locator.select_option(step.value)
                case "press":
                    await locator.press(step.value)
                case "wait":
                    if locator:
                        await locator.wait_for(state="visible")
                    else:
                        await self.page.wait_for_load_state("domcontentloaded")
                case "assert_text":
                    await expect(locator or self.page.locator("body")).to_contain_text(step.value,
                                                                                       timeout=self.timeout)
                case "assert_visible":
                    await expect(locator).to_be_visible(timeout=self.timeout)
                case "assert_url":
                    await expect(self.page).to_have_url(re_compile(step.value), timeout=self.timeout)
                case "screenshot":
                    pass
        except Exception as error:
            result.update(ok=False, error="Browser operation failed; review the evidence." if
                          self.settings.env == "production" else str(error)[:3500], error_type=type(error).__name__)
        result["duration_ms"] = round((time.monotonic() - start) * 1000)
        return result

    async def capture(self, index: int, attempt: int):
        prefix = f"step-{index + 1:02d}-attempt-{attempt + 1}"
        names = {"screenshot": f"{prefix}.png", "dom": f"{prefix}.html", "telemetry": f"{prefix}.json"}
        try:
            await self.page.screenshot(path=str(self.directory / names["screenshot"]), full_page=True,
                                       mask=[self.page.locator('input[type="password"]')], timeout=5000)
            dom = await self.page.evaluate(DOM_SCRIPT)
            (self.directory / names["dom"]).write_text(dom["html"], encoding="utf-8")
        except Exception as error:
            names = {"capture_error": "Evidence capture failed" if self.settings.env == "production" else str(error)[:300]}
            dom = {"text": "", "title": ""}
        evidence = redact({"console": self.console[-100:], "network": self.network[-150:],
                           "blocked": self.blocked[-50:], "url": safe_url(self.page.url),
                           "title": dom["title"], "text": dom["text"]})
        telemetry_name = f"{prefix}.json"
        (self.directory / telemetry_name).write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")
        names["telemetry"] = telemetry_name
        return {"artifacts": names, "evidence": evidence}

    async def mcp_capture(self):
        if not self.settings.mcp_enabled:
            return {"enabled": False}
        try:
            if not self.mcp:
                raise ValueError(self.mcp_error or "MCP was not attached")
            data = await self.mcp.capture()
            data = redact(data)
            (self.directory / "devtools-mcp.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                                             encoding="utf-8")
            return {"enabled": True, "ok": True, "artifact": "devtools-mcp.json"}
        except Exception as error:
            return {"enabled": True, "ok": False, "error": str(error)[:1000]}

    async def close(self):
        try:
            if self.mcp:
                await self.mcp.close()
            if self.context:
                trace_temp = self.directory / ".trace.tmp.zip"
                await self.context.tracing.stop(path=str(trace_temp))
                trace_temp.replace(self.directory / "trace.zip")
        finally:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()


def re_compile(value: str):
    import re
    return re.compile(re.escape(value))
