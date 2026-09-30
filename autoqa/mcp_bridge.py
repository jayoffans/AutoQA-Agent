import asyncio
import re

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import Settings


class DevToolsBridge:
    """Own the stdio transport in one task; attach before navigation to collect events."""

    def __init__(self, settings: Settings, browser_url: str, page_url: str):
        self.settings, self.browser_url, self.page_url = settings, browser_url, page_url
        self.commands = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.worker = None

    async def start(self):
        self.worker = asyncio.create_task(self.serve())
        await asyncio.wait_for(asyncio.shield(self.ready), timeout=45)

    async def call(self, session, name, arguments):
        response = await session.call_tool(name, arguments)
        if response.isError:
            raise ValueError(f"DevTools MCP tool {name} returned an error")
        return [c.text[:24000] for c in response.content if hasattr(c, "text")]

    async def serve(self):
        params = StdioServerParameters(command=self.settings.mcp_command,
                                      args=[*self.settings.mcp_args, f"--browserUrl={self.browser_url}"])
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    text = "\n".join(await self.call(session, "list_pages", {}))
                    pages = [int(m.group(1)) for m in re.finditer(r"(?m)^(\d+):\s*(.*)$", text)
                             if self.page_url in m.group(2)]
                    if len(pages) != 1:
                        raise ValueError("MCP could not uniquely identify the task page")
                    page_id = pages[0]
                    arguments = {"pageId": page_id}
                    # Initialize CDP collection before any tested request.
                    await self.call(session, "list_network_requests", arguments)
                    await self.call(session, "list_console_messages", arguments)
                    self.ready.set_result(True)
                    while True:
                        command, answer = await self.commands.get()
                        if command == "close":
                            answer.set_result(True)
                            break
                        try:
                            result = {}
                            for name in ("list_console_messages", "list_network_requests", "take_snapshot"):
                                result[name] = {"is_error": False, "content": await self.call(session, name, arguments)}
                            if not answer.done():
                                answer.set_result(result)
                        except Exception as error:
                            if not answer.done():
                                answer.set_exception(error)
        except Exception as error:
            if not self.ready.done():
                self.ready.set_exception(error)
            while not self.commands.empty():
                _, answer = self.commands.get_nowait()
                if not answer.done():
                    answer.set_exception(error)

    async def capture(self):
        if not self.worker or self.worker.done():
            raise ValueError("DevTools MCP transport is unavailable")
        answer = asyncio.get_running_loop().create_future()
        await self.commands.put(("capture", answer))
        return await asyncio.wait_for(answer, timeout=35)

    async def close(self):
        if self.worker and not self.worker.done():
            answer = asyncio.get_running_loop().create_future()
            await self.commands.put(("close", answer))
            try:
                await asyncio.wait_for(answer, timeout=10)
                await asyncio.wait_for(self.worker, timeout=10)
            except Exception:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
