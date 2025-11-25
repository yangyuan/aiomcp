from aiomcp.mcp_client import McpClient
from aiomcp.contracts.mcp_message import McpCallToolResult


def test_coerce_prefers_structured_content():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(structuredContent={"value": 42})
    )
    assert result == {"value": 42}


def test_coerce_uses_text_block():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(content=[{"type": "text", "text": "5"}])
    )
    assert result == 5


def test_coerce_falls_back_to_object_block():
    client = McpClient()
    payload = {"foo": "bar"}
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(content=[{"type": "object", "data": payload}])
    )
    assert result == payload
