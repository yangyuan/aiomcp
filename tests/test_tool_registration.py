from enum import Enum
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


class Color(Enum):
    RED = "red"
    BLUE = "blue"


async def echo_color(color: Color) -> Color:
    return color


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


@pytest.mark.asyncio
async def test_tool_registration():
    server = McpServer()

    add_tools = []

    await server.register_tool(
        add_async,
    )
    add_tools.append("add_async")
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
    for t_name in add_tools:
        assert any(t.name == t_name for t in tools)
        result = await client.invoke(t_name, {"a": 1, "b": 2})
        assert result == 3

        # Invalid request should raise from server-side validation
        with pytest.raises(Exception):
            await client.invoke(t_name, {"a": "nope", "b": 2})


@pytest.mark.asyncio
async def test_enum_tool_registration_and_result_serialization():
    server = McpServer()
    await server.register_tool(echo_color)

    tools = await server.list_tools()
    tool = next(tool for tool in tools if tool.name == "echo_color")
    input_schema = tool.inputSchema.model_dump(exclude_none=True)
    output_schema = tool.outputSchema.model_dump(exclude_none=True)

    assert input_schema["properties"]["color"] == {
        "enum": ["red", "blue"],
        "type": "string",
    }
    assert output_schema == {"enum": ["red", "blue"], "type": "string"}

    client = McpClient()
    await client.initialize(server)

    try:
        result = await client.invoke("echo_color", {"color": "red"})
        assert result == "red"
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
