import json
from pydantic import BaseModel
import pytest

from aiomcp.mcp_server import McpServer
from aiomcp.mcp_client import McpClient

input_schema_add = """
{
  "type": "object",
  "properties": {
    "items": {
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
  },
  "required": ["input"]
}
"""

output_schema_add = """{
  "type": "object",
  "properties": {
    "sum": {
      "type": "integer",
      "description": "The sum"
    }
  },
  "required": ["sum"]
}"""


class SumResponseOutput(BaseModel):
    sum: int


class SumRequestInput(BaseModel):
    a: int
    b: int


async def add_with_pydantic(input: SumRequestInput) -> SumResponseOutput:
    return SumResponseOutput(sum=input.a + input.b)


@pytest.mark.asyncio
async def test_complex_message():
    server = McpServer()

    await server.register_tool(
        "add",
        add_with_pydantic,
        json.loads(input_schema_add),
        json.loads(output_schema_add),
    )
    # Direct transport via client-driven approach
    client = McpClient()
    await client.initialize(server)

    result = await client.invoke("add", {"input": {"a": 1, "b": 2}})
    assert result == {"sum": 3}
