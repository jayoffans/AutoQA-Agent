"""Start AutoQA on port 8765, then run this independent same-browser MCP smoke test."""
import asyncio
import json
from pathlib import Path

from autoqa.browser import BrowserSession
from autoqa.config import Settings
from autoqa.schemas import Step
from autoqa.security import TargetPolicy


async def main():
    settings = Settings(mcp_enabled=True)
    session = BrowserSession(settings, TargetPolicy(settings), "mcp-verification", 8000)
    try:
        await session.start("http://127.0.0.1:8765/demo/broken.html")
        await session.execute(Step(action="click", selector="#submit-order", description="Trigger demo HTTP 500"))
        await session.page.get_by_text("提交失败：库存服务不可用").wait_for()
        result = await session.mcp_capture()
        assert result.get("ok"), result
        evidence = json.loads((session.directory / "devtools-mcp.json").read_text(encoding="utf-8"))
        assert all(not data["is_error"] for data in evidence.values())
        assert "500" in str(evidence), "MCP must capture the actual failed request"
        assert "/demo/broken.html" in str(evidence), "MCP must inspect the same tested page"
        result["http_500_verified"] = True
        result["same_page_verified"] = True
        result["tools"] = list(evidence)
        Path("docs/mcp-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
