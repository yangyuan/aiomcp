from enum import Enum
import base64
import json
from typing import Annotated
import pytest

from aiomcp.mcp_server import McpServer
from aiomcp.mcp_client import McpClient
from aiomcp.contracts.mcp_message import (
    McpListToolsRequest,
    McpListToolsParams,
    McpListToolsResult,
    McpCallToolRequest,
    McpCallToolParams,
    McpCallToolResult,
    McpResponse,
    McpError,
)
from pydantic import Field

input_schema_add = """
{
  "type": "object",
  "properties": {
    "a": {
      "type": "integer",
      "description": "The first number"
    },
    "b": {
      "type": "integer",
      "description": "The second number"
    }
  },
  "required": ["a", "b"]
}
"""

output_schema_add = """{ "type": "integer" }"""


async def add_async(a: int, b: int) -> int:
    return a + b


def add_sync(a: int, b: int) -> int:
    return a + b


def add(a: int, b: int) -> int:
    return a + b


class Color(Enum):
    RED = "red"
    BLUE = "blue"


async def echo_color(color: Color) -> Color:
    return color


async def sum_payload(payload: list[int]) -> int:
    return sum(payload)


async def echo_text(text: str) -> str:
    return text


def mock_get_screenshot_png() -> bytes:
    return b"png image bytes from browser or screenshot service"


async def screenshot_preview():
    image_data = base64.b64encode(mock_get_screenshot_png()).decode("ascii")

    return [
        {"type": "text", "text": "Here is the captured screenshot."},
        {"type": "image", "data": image_data, "mimeType": "image/png"},
    ]


async def annotated_add(
    a: Annotated[int, Field(description="The first number")],
    b: Annotated[int, Field(description="The second number")],
) -> int:
    return a + b


async def templated_add(
    a: Annotated[int, Field(description="The {FIRST_LABEL} number")],
    b: Annotated[int, Field(description="The {SECOND_LABEL} number")],
) -> int:
    return a + b


class AddClass:
    async def add_async(self, a: int, b: int) -> int:
        return a + b

    def add_sync(self, a: int, b: int) -> int:
        return a + b

    @staticmethod
    async def static_add_async(a: int, b: int) -> int:
        return a + b

    @staticmethod
    def static_add_sync(a: int, b: int) -> int:
        return a + b

    @classmethod
    async def class_add_async(cls, a: int, b: int) -> int:
        return a + b


@pytest.mark.asyncio
async def test_tool_registration():
    server = McpServer()

    add_tools = []

    await server.register_tool(
        add_async,
    )
    add_tools.append("add_async")
    await server.register_tool(add_sync, alias="add_sync_registered")
    add_tools.append("add_sync_registered")
    await server.mcp_tools_register(
        "add_sync",
        add_sync,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_sync")

    await server.mcp_tools_register(
        "add_static_async",
        AddClass.static_add_async,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_static_async")
    await server.register_tool(AddClass.static_add_sync)
    add_tools.append("static_add_sync")
    await server.register_tool(AddClass.class_add_async)
    add_tools.append("class_add_async")

    add_instance = AddClass()
    await server.register_tool(
        add_instance.add_async,
        alias="add_instance_async",
    )
    add_tools.append("add_instance_async")
    await server.mcp_tools_register(
        "add_instance_sync",
        add_instance.add_sync,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_instance_sync")
    server_tools = await server.list_tools()
    for t_name in add_tools:
        assert any(t.name == t_name for t in server_tools)

    # Direct transport via client-driven approach
    client = McpClient()
    await client.initialize(server)

    tools = await client.mcp_tools_list()
    tools_with_output_schema = {
        tool.name for tool in tools if tool.outputSchema is not None
    }
    for t_name in add_tools:
        assert any(t.name == t_name for t in tools)
        result = await client.invoke(t_name, {"a": 1, "b": 2})
        if t_name in tools_with_output_schema:
            assert result == 3
        else:
            assert result == [{"type": "text", "text": "3"}]

        # Invalid request should raise from server-side validation
        with pytest.raises(Exception):
            await client.invoke(t_name, {"a": "nope", "b": 2})


@pytest.mark.asyncio
async def test_readme_rpc_style_usage_invokes_registered_function():
    mcp_server = McpServer()
    await mcp_server.register_tool(add)

    mcp_client = McpClient("mcp-client-name")
    await mcp_client.initialize(mcp_server)

    try:
        sum_value = await mcp_client.invoke("add", {"a": 1, "b": 2})
        assert sum_value == 3
    finally:
        await mcp_client.close()


@pytest.mark.asyncio
async def test_enum_tool_registration_and_result_serialization():
    server = McpServer()
    await server.register_tool(echo_color)

    tools = await server.list_tools()
    tool = next(tool for tool in tools if tool.name == "echo_color")
    input_schema = tool.inputSchema.model_dump(exclude_none=True)

    assert input_schema["properties"]["color"] == {
        "enum": ["red", "blue"],
        "type": "string",
    }
    assert tool.outputSchema.model_dump(exclude_none=True) == {
        "enum": ["red", "blue"],
        "type": "string",
    }

    client = McpClient()
    await client.initialize(server)

    try:
        result = await client.invoke("echo_color", {"color": "red"})
        assert result == "red"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_raw_top_level_arguments_are_tolerated_for_single_parameter_tools():
    server = McpServer()
    await server.register_tool(sum_payload)
    await server.register_tool(echo_text)

    client = McpClient()
    await client.initialize(server)

    try:
        assert await client.invoke("sum_payload", [1, 2, 3]) == 6
        assert await client.invoke("echo_text", "hello") == "hello"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_tool_can_return_llm_friendly_mixed_content_blocks():
    server = McpServer()
    await server.register_tool(screenshot_preview)

    client = McpClient()
    await client.initialize(server)

    try:
        result = await client.invoke("screenshot_preview", {})
        assert result == [
            {"type": "text", "text": "Here is the captured screenshot."},
            {
                "type": "image",
                "data": "cG5nIGltYWdlIGJ5dGVzIGZyb20gYnJvd3NlciBvciBzY3JlZW5zaG90IHNlcnZpY2U=",
                "mimeType": "image/png",
            },
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_register_tool_accepts_parameter_and_tool_annotations():
    server = McpServer()
    await server.register_tool(
        annotated_add,
        alias="annotated_add",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )

    tools = await server.list_tools()
    tool = next(tool for tool in tools if tool.name == "annotated_add")
    input_schema = tool.inputSchema.model_dump(exclude_none=True)
    annotations = tool.annotations.model_dump(exclude_none=True)

    assert input_schema["properties"]["a"] == {
        "type": "integer",
        "description": "The first number",
    }
    assert input_schema["properties"]["b"] == {
        "type": "integer",
        "description": "The second number",
    }
    assert annotations == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


@pytest.mark.asyncio
async def test_register_tool_applies_format_map_to_metadata():
    server = McpServer()
    await server.register_tool(
        templated_add,
        description="Add numbers for {AUDIENCE}",
        format_map={
            "FIRST_LABEL": "first",
            "SECOND_LABEL": "second",
            "AUDIENCE": "testing",
        },
    )

    tools = await server.list_tools()
    tool = next(tool for tool in tools if tool.name == "templated_add")
    input_schema = tool.inputSchema.model_dump(exclude_none=True)

    assert tool.description == "Add numbers for testing"
    assert tool.outputSchema.model_dump(exclude_none=True) == {"type": "integer"}
    assert input_schema["properties"]["a"]["description"] == "The first number"
    assert input_schema["properties"]["b"]["description"] == "The second number"


@pytest.mark.asyncio
async def test_register_tool_creates_output_schema_by_default():
    server = McpServer()
    await server.register_tool(echo_color)

    tools = await server.list_tools()
    tool = next(tool for tool in tools if tool.name == "echo_color")

    assert tool.outputSchema.model_dump(exclude_none=True) == {
        "enum": ["red", "blue"],
        "type": "string",
    }


@pytest.mark.asyncio
async def test_register_tool_can_skip_output_schema_by_flag():
    server = McpServer(flags={"skip_mcp_tool_output_schema": True})
    await server.register_tool(echo_color)

    tools = await server.list_tools()
    tool = next(tool for tool in tools if tool.name == "echo_color")

    assert tool.outputSchema is None


@pytest.mark.asyncio
async def test_register_tool_accepts_title_and_icons():
    server = McpServer()
    await server.register_tool(
        add_async,
        title="Add Two Numbers",
        icons=[
            {
                "src": "https://example.com/add.png",
                "mimeType": "image/png",
                "sizes": ["48x48"],
            },
            {"src": "https://example.com/add.svg"},
        ],
    )
    tool = next(t for t in await server.list_tools() if t.name == "add_async")
    assert tool.title == "Add Two Numbers"
    assert tool.icons is not None and len(tool.icons) == 2
    assert tool.icons[0].src == "https://example.com/add.png"
    assert tool.icons[0].mimeType == "image/png"
    assert tool.icons[0].sizes == ["48x48"]
    assert tool.icons[1].src == "https://example.com/add.svg"
    assert tool.icons[1].mimeType is None

    # title/icons must round-trip through tools/list
    request = McpListToolsRequest(id=1)
    response = await server.process(request)
    assert isinstance(response, McpResponse)
    structured = McpListToolsResult.model_validate(response.result)
    listed = next(t for t in structured.tools if t.name == "add_async")
    assert listed.title == "Add Two Numbers"
    assert (
        listed.icons is not None
        and listed.icons[0].src == "https://example.com/add.png"
    )


@pytest.mark.asyncio
async def test_tools_list_paginates_with_cursor(monkeypatch):
    monkeypatch.setattr(McpServer, "TOOLS_PAGE_SIZE", 2)
    server = McpServer()
    for i in range(5):
        await server.mcp_tools_register(
            f"tool_{i}", add_async, json.loads(input_schema_add)
        )

    # First page (sorted-name order)
    response = await server.process(McpListToolsRequest(id=1))
    assert isinstance(response, McpResponse)
    page1 = McpListToolsResult.model_validate(response.result)
    assert [t.name for t in page1.tools] == ["tool_0", "tool_1"]
    assert page1.nextCursor is not None

    # Second page using cursor
    response = await server.process(
        McpListToolsRequest(id=2, params=McpListToolsParams(cursor=page1.nextCursor))
    )
    assert isinstance(response, McpResponse)
    page2 = McpListToolsResult.model_validate(response.result)
    assert [t.name for t in page2.tools] == ["tool_2", "tool_3"]
    assert page2.nextCursor is not None

    # Final page; no further cursor
    response = await server.process(
        McpListToolsRequest(id=3, params=McpListToolsParams(cursor=page2.nextCursor))
    )
    assert isinstance(response, McpResponse)
    page3 = McpListToolsResult.model_validate(response.result)
    assert [t.name for t in page3.tools] == ["tool_4"]
    assert page3.nextCursor is None

    # Garbage cursor → INVALID_PARAMS
    response = await server.process(
        McpListToolsRequest(id=4, params=McpListToolsParams(cursor="not_a_cursor"))
    )
    assert isinstance(response, McpError)
    assert response.error.code == -32602


@pytest.mark.asyncio
async def test_tools_list_cursor_invalidated_on_registry_change(monkeypatch):
    """Cursor encodes a fingerprint of the tool set; any registration
    mutation invalidates outstanding cursors."""
    monkeypatch.setattr(McpServer, "TOOLS_PAGE_SIZE", 2)
    server = McpServer()
    for i in range(5):
        await server.mcp_tools_register(
            f"tool_{i}", add_async, json.loads(input_schema_add)
        )

    response = await server.process(McpListToolsRequest(id=1))
    page1 = McpListToolsResult.model_validate(response.result)
    cursor = page1.nextCursor
    assert cursor is not None

    # Register a new tool — fingerprint changes
    await server.mcp_tools_register("tool_new", add_async, json.loads(input_schema_add))

    response = await server.process(
        McpListToolsRequest(id=2, params=McpListToolsParams(cursor=cursor))
    )
    assert isinstance(response, McpError)
    assert response.error.code == -32602


@pytest.mark.asyncio
async def test_tools_list_paginates_through_client(monkeypatch):
    """Client should reassemble all tools across pages."""
    monkeypatch.setattr(McpServer, "TOOLS_PAGE_SIZE", 2)
    server = McpServer()
    for i in range(5):
        await server.mcp_tools_register(
            f"tool_{i}", add_async, json.loads(input_schema_add)
        )

    client = McpClient()
    await client.initialize(server)
    try:
        tools = await client.mcp_tools_list()
        assert sorted(t.name for t in tools) == [f"tool_{i}" for i in range(5)]
    finally:
        await client.close()
