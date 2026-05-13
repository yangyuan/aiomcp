import asyncio

import pytest

from aiomcp.mcp_client import McpClient, McpInvokeError
from aiomcp.contracts.mcp_content import McpImageContent, McpTextContent
from aiomcp.contracts.mcp_schema import JsonSchema
from aiomcp.contracts.mcp_message import McpCallToolResult, McpResponse
from aiomcp.contracts.mcp_tool import McpTool
from aiomcp.transports.base import McpClientTransport


class ResultTransport(McpClientTransport):
    def __init__(self, result):
        self._result = result
        self._messages = asyncio.Queue()

    async def client_initialize(self, context):
        pass

    async def client_messages(self):
        while True:
            message = await self._messages.get()
            yield message

    async def client_send_message(self, message) -> bool:
        await self._messages.put(McpResponse(id=message.id, result=self._result))
        return True

    async def close(self):
        pass


def test_coerce_prefers_structured_content():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(structuredContent={"value": 42}),
        output_schema_enabled=True,
    )
    assert result == {"value": 42}


def test_coerce_returns_only_content_as_is_by_default():
    client = McpClient()
    content = [{"type": "text", "text": "5"}]
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(content=content)
    )
    assert result == content


def test_coerce_returns_only_structured_content_as_is_by_default():
    client = McpClient()
    payload = {"foo": "bar"}
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(structuredContent=payload)
    )
    assert result == payload


def test_coerce_combines_content_and_structured_content_by_default():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            content={"type": "text", "text": "done"},
            structuredContent={"value": 42},
        )
    )
    assert result == [
        McpTextContent(text="done"),
        McpTextContent(text='{"value": 42}'),
    ]


def test_coerce_can_convert_content_format_for_single_content_block():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(content={"type": "text", "text": "done"}),
        convert_mcp_tool_result_content_format=True,
    )
    assert result == [McpTextContent(text="done")]


def test_coerce_can_convert_content_format_for_raw_content():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(content={"value": 42}),
        convert_mcp_tool_result_content_format=True,
    )
    assert result == [McpTextContent(text='{"value": 42}')]


def test_coerce_converted_content_format_combines_structured_content():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            content=[{"type": "image", "data": "abc123", "mimeType": "image/png"}],
            structuredContent={"value": 42},
        ),
        convert_mcp_tool_result_content_format=True,
    )
    assert result == [
        McpImageContent(data="abc123", mimeType="image/png"),
        McpTextContent(text='{"value": 42}'),
    ]


def test_coerce_converted_content_format_parses_structured_content_block():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            content=[{"type": "text", "text": "content"}],
            structuredContent={"type": "text", "text": "structured"},
        ),
        convert_mcp_tool_result_content_format=True,
    )
    assert result == [
        McpTextContent(text="content"),
        McpTextContent(text="structured"),
    ]


def test_coerce_converted_content_format_parses_structured_content_list():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            structuredContent=[
                {"type": "text", "text": "one"},
                {"type": "image", "data": "abc123", "mimeType": "image/png"},
            ]
        ),
        convert_mcp_tool_result_content_format=True,
    )
    assert result == [
        McpTextContent(text="one"),
        McpImageContent(data="abc123", mimeType="image/png"),
    ]


def test_coerce_converted_content_format_preserves_valid_list_items():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            structuredContent=[
                {"type": "text", "text": "one"},
                {"value": 42},
                {"type": "image", "data": "abc123", "mimeType": "image/png"},
            ]
        ),
        convert_mcp_tool_result_content_format=True,
    )
    assert result == [
        McpTextContent(text="one"),
        McpTextContent(text='{"value": 42}'),
        McpImageContent(data="abc123", mimeType="image/png"),
    ]


def test_coerce_output_schema_uses_structured_content_only():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            content=[{"type": "text", "text": "ignore"}],
            structuredContent={"value": 42},
        ),
        output_schema_enabled=True,
        convert_mcp_tool_result_content_format=True,
    )
    assert result == {"value": 42}


def test_coerce_can_force_structured_content_only():
    client = McpClient()
    result = client._coerce_tool_result(  # type: ignore[attr-defined]
        McpCallToolResult(
            content=[{"type": "text", "text": "ignore"}],
            structuredContent={"value": 42},
        ),
        use_structured_content=True,
        convert_mcp_tool_result_content_format=True,
    )
    assert result == {"value": 42}


def test_client_accepts_flags_dict():
    client = McpClient(
        flags={
            "enforce_mcp_tool_result_content": True,
        }
    )

    assert client._context.flags.enforce_mcp_tool_result_content is True


def test_client_rejects_unknown_flag():
    with pytest.raises(ValueError):
        McpClient(flags={"unknown_flag": True})


def test_client_rejects_invoke_level_conversion_flag():
    with pytest.raises(ValueError):
        McpClient(flags={"convert_mcp_tool_result_content_format": True})


def test_client_rejects_non_dict_flags():
    with pytest.raises(ValueError):
        McpClient(flags=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_invoke_result_returns_full_tool_result():
    raw_result = {
        "content": [
            {"type": "text", "text": "done"},
            {"type": "image", "data": "abc123", "mimeType": "image/png"},
        ],
        "structuredContent": {"ok": True},
        "isError": False,
    }
    client = McpClient()
    client._initialized = True
    client._tools = {"rich": McpTool(name="rich")}
    client._transport = ResultTransport(raw_result)
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        result = await client.invoke_result("rich", {}, timeout=1)

        assert result.structuredContent == {"ok": True}
        assert result.content == raw_result["content"]
        assert result.isError is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoke_result_tolerates_missing_content_by_default():
    client = McpClient()
    client._initialized = True
    client._tools = {"rich": McpTool(name="rich")}
    client._transport = ResultTransport({"structuredContent": {"ok": True}})
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        result = await client.invoke_result("rich", {}, timeout=1)

        assert result.content is None
        assert result.structuredContent == {"ok": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoke_result_can_enforce_content():
    client = McpClient(flags={"enforce_mcp_tool_result_content": True})
    client._initialized = True
    client._tools = {"rich": McpTool(name="rich")}
    client._transport = ResultTransport({"structuredContent": {"ok": True}})
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        with pytest.raises(RuntimeError, match="tool result missing required content"):
            await client.invoke_result("rich", {}, timeout=1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoke_uses_structured_content_when_tool_has_output_schema():
    client = McpClient()
    client._initialized = True
    client._tools = {
        "structured": McpTool(
            name="structured",
            outputSchema=JsonSchema(type="object"),
        )
    }
    client._transport = ResultTransport(
        {
            "content": [{"type": "text", "text": "ignore"}],
            "structuredContent": {"value": 42},
        }
    )
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        assert await client.invoke("structured", {}, timeout=1) == {"value": 42}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoke_can_force_structured_content_without_output_schema():
    client = McpClient()
    client._initialized = True
    client._tools = {"structured": McpTool(name="structured")}
    client._transport = ResultTransport(
        {
            "content": [{"type": "text", "text": "ignore"}],
            "structuredContent": {"value": 42},
        }
    )
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        assert await client.invoke(
            "structured", {}, timeout=1, use_structured_content=True
        ) == {"value": 42}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoke_can_convert_content_format_per_call():
    client = McpClient()
    client._initialized = True
    client._tools = {"rich": McpTool(name="rich")}
    client._transport = ResultTransport(
        {
            "content": {"type": "text", "text": "content"},
            "structuredContent": {"type": "text", "text": "structured"},
        }
    )
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        assert await client.invoke(
            "rich",
            {},
            timeout=1,
            convert_mcp_tool_result_content_format=True,
        ) == [
            McpTextContent(text="content"),
            McpTextContent(text="structured"),
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoke_raises_specific_exception_for_tool_error():
    client = McpClient()
    client._initialized = True
    client._tools = {"broken": McpTool(name="broken")}
    client._transport = ResultTransport(
        {
            "content": [{"type": "text", "text": "failed"}],
            "structuredContent": {"reason": "failed"},
            "isError": True,
        }
    )
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    try:
        with pytest.raises(McpInvokeError) as error_info:
            await client.invoke("broken", {}, timeout=1, use_structured_content=True)
        assert error_info.value.tool_name == "broken"
        assert error_info.value.result.isError is True
        assert error_info.value.result.content == [{"type": "text", "text": "failed"}]
        assert error_info.value.result.structuredContent == {"reason": "failed"}
    finally:
        await client.close()
