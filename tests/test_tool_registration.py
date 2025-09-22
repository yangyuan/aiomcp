import json
import pytest

from aiomcp.mcp_server import McpServer
from aiomcp.mcp_client import McpClient

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
        "add_async",
        add_async,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_async")
    await server.register_tool(
        "add_sync",
        add_sync,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_sync")

    await server.register_tool(
        "add_static_async",
        AddClass.static_add_async,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_static_async")
    await server.register_tool(
        "add_static_sync",
        AddClass.static_add_sync,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_static_sync")

    add_instance = AddClass()
    await server.register_tool(
        "add_instance_async",
        add_instance.add_async,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    add_tools.append("add_instance_async")
    await server.register_tool(
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
