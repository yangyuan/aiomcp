import asyncio
import pytest
from pydantic import BaseModel

from aiomcp.mcp_server import McpServer
from aiomcp.mcp_client import McpClient
from aiomcp.contracts.mcp_message import (
    McpCallToolParams,
    McpCallToolRequest,
    McpResponse,
)
from aiomcp.mcp_transport import McpClientTransport
from aiomcp.transports.direct import DirectMcpTransport
from aiomcp.transports.memory import MemoryMcpTransport


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    text: str


async def echo_func(text: str) -> EchoOutput:
    return EchoOutput(text=text)


async def _client_driven_validation(
    client_transport: McpClientTransport, client: McpClient, server: McpServer
):
    await server.register_tool(
        "echo",
        echo_func,
        EchoInput.model_json_schema(),
        EchoOutput.model_json_schema(),
    )
    await client.initialize(client_transport)
    tools = await client.mcp_tools_list()
    assert any(t.name == "echo" for t in tools)

    echo_tool_def = next(t for t in tools if t.name == "echo")
    request = McpCallToolRequest(
        id="test",
        params=McpCallToolParams(name="echo", arguments={"text": "hello"}),
    )
    resp = await client.mcp_tools_call(echo_tool_def, request)
    assert isinstance(resp, McpResponse)
    assert resp.result["structuredContent"] == {"text": "hello"}

    result = await client.invoke("echo", {"text": "hello"})
    assert result == {"text": "hello"}


@pytest.mark.asyncio
async def test_direct_transport():
    mcp_server = McpServer()
    mcp_client = McpClient()
    transport = DirectMcpTransport(mcp_server)
    await _client_driven_validation(transport, mcp_client, mcp_server)


@pytest.mark.asyncio
async def test_memory_transport():
    transport = MemoryMcpTransport()
    mcp_server = McpServer()
    await mcp_server.create_host_task(transport)
    mcp_client = McpClient()
    await _client_driven_validation(transport, mcp_client, mcp_server)
