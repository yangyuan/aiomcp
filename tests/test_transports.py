import asyncio
import sys
from typing import AsyncIterator

import aiohttp
from aiohttp import web
import pytest
from pydantic import BaseModel

from aiomcp.mcp_server import McpServer
from aiomcp.mcp_client import McpClient
from aiomcp.mcp_context import McpClientContext
from aiomcp.contracts.mcp_message import (
    McpCallToolParams,
    McpCallToolRequest,
    McpInitializeParams,
    McpInitializeRequest,
    McpListToolsRequest,
    McpResponse,
)
from aiomcp.transports.base import McpClientTransport
from aiomcp.transports.direct import McpDirectTransport
from aiomcp.transports.memory import McpMemoryTransport
from aiomcp.transports.http import McpHttpClientTransport, McpHttpTransport
from aiomcp.transports.stdio import McpStdioClientTransport

HEADER_CONTENT_TYPE = "content-type"
HEADER_ACCEPT = "accept"
HEADER_MCP_SESSION_ID = "mcp-session-id"
HEADER_MCP_PROTOCOL_VERSION = "mcp-protocol-version"


async def _start_stub_http_server(
    port: int,
    *,
    headers: dict[str, str] | None = None,
    body_protocol_version: str | None = None,
) -> web.AppRunner:
    app = web.Application()
    response_headers = headers or {}

    async def _handle(request: web.Request) -> web.Response:
        payload = await request.json()
        response = {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": {
                "capabilities": {},
                "protocolVersion": body_protocol_version
                or payload.get("params", {}).get("protocolVersion", "2025-11-25"),
                "serverInfo": {"name": "stub", "version": "0.0"},
            },
        }
        return web.json_response(response, headers=response_headers)

    app.router.add_post("/aiomcp", _handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    text: str


async def echo_func(text: str) -> EchoOutput:
    return EchoOutput(text=text)


async def _client_driven_validation(
    client_transport: McpClientTransport, client: McpClient, server: McpServer
):
    try:
        await server.mcp_tools_register(
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
    finally:
        await client.close()
        await server.shutdown()


class SilentClientTransport(McpClientTransport):
    async def client_initialize(self, context: McpClientContext):
        pass

    async def client_messages(self) -> AsyncIterator:
        await asyncio.Event().wait()
        if False:
            yield

    async def client_send_message(self, message) -> bool:
        return True

    async def close(self):
        pass


class ControlledClosingClientTransport(McpClientTransport):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.sent = asyncio.Event()
        self.close_stream = asyncio.Event()

    async def client_initialize(self, context: McpClientContext):
        pass

    async def client_messages(self) -> AsyncIterator:
        self.started.set()
        await self.close_stream.wait()
        if False:
            yield

    async def client_send_message(self, message) -> bool:
        self.sent.set()
        return True

    async def close(self):
        self.close_stream.set()


@pytest.mark.asyncio
async def test_client_request_timeout_cleans_inflight():
    client = McpClient(request_timeout=0.01)
    client._transport = SilentClientTransport()
    request = McpListToolsRequest(id="timeout-test")

    with pytest.raises(TimeoutError):
        await client._process(request)

    assert "timeout-test" not in client._inflight


@pytest.mark.asyncio
async def test_client_fails_pending_request_when_message_stream_closes():
    transport = ControlledClosingClientTransport()
    client = McpClient(request_timeout=5)
    client._transport = transport
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    await transport.started.wait()
    process_task = asyncio.create_task(
        client._process(McpListToolsRequest(id="pending"))
    )
    await transport.sent.wait()
    transport.close_stream.set()

    with pytest.raises(RuntimeError, match="message stream closed"):
        await process_task

    assert client._inflight == {}
    await client.close()


@pytest.mark.asyncio
async def test_stdio_client_drains_child_stderr():
    code = (
        "import json, sys; "
        "sys.stderr.write('x' * 200000); sys.stderr.flush(); "
        "print(json.dumps({'jsonrpc': '2.0', 'id': 'ready', 'result': {}}), flush=True)"
    )
    transport = McpStdioClientTransport([sys.executable, "-c", code])
    await transport.client_initialize(McpClientContext())

    try:
        message = await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
        assert isinstance(message, McpResponse)
        assert message.id == "ready"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_client_rejects_write_after_child_exit():
    transport = McpStdioClientTransport([sys.executable, "-c", "pass"])
    await transport.client_initialize(McpClientContext())

    try:
        assert transport._process is not None
        await asyncio.wait_for(transport._process.wait(), timeout=2)
        with pytest.raises(RuntimeError, match="already exited"):
            await transport.client_send_message(McpListToolsRequest(id="dead"))
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_client_close_sends_stdin_eof_before_terminating(tmp_path):
    marker = tmp_path / "stdin-closed.txt"
    code = (
        "import pathlib, sys; "
        "sys.stdin.read(); "
        f"pathlib.Path({str(marker)!r}).write_text('closed')"
    )
    transport = McpStdioClientTransport([sys.executable, "-c", code])
    await transport.client_initialize(McpClientContext())

    await transport.close()

    assert marker.read_text() == "closed"


@pytest.mark.asyncio
async def test_direct_transport():
    mcp_server = McpServer()
    mcp_client = McpClient()
    transport = McpDirectTransport(mcp_server)
    await _client_driven_validation(transport, mcp_client, mcp_server)


@pytest.mark.asyncio
async def test_memory_transport():
    transport = McpMemoryTransport()
    mcp_server = McpServer()
    await mcp_server.create_host_task(transport)
    mcp_client = McpClient()
    await _client_driven_validation(transport, mcp_client, mcp_server)


@pytest.mark.asyncio
async def test_http_transport(unused_tcp_port):
    port = unused_tcp_port
    mcp_server = McpServer()
    await mcp_server.create_host_task(f"http://127.0.0.1:{port}/aiomcp")

    client_transport = McpHttpTransport("127.0.0.1", port, path="/aiomcp")
    mcp_client = McpClient()

    await _client_driven_validation(client_transport, mcp_client, mcp_server)


@pytest.mark.asyncio
async def test_http_transport_numeric_request_id(unused_tcp_port):
    port = unused_tcp_port
    mcp_server = McpServer()
    await mcp_server.create_host_task(f"http://127.0.0.1:{port}/aiomcp")

    client_transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    await client_transport.client_initialize(McpClientContext())

    initialize_request = McpInitializeRequest(
        id=123,
        params=McpInitializeParams(
            capabilities={},
            protocolVersion="2025-11-25",
            clientInfo={"name": "test-client", "version": "0.0.0"},
        ),
    )

    await client_transport.client_send_message(initialize_request)
    response = await asyncio.wait_for(
        client_transport._server_to_client.get(), timeout=1
    )

    assert isinstance(response, McpResponse)
    assert response.id == 123

    await client_transport.close()


@pytest.mark.asyncio
async def test_http_client_requires_session_header(unused_tcp_port):
    port = unused_tcp_port
    runner = await _start_stub_http_server(
        port, headers={HEADER_MCP_PROTOCOL_VERSION: "2025-11-25"}
    )
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    context = McpClientContext()
    context.flags.enforce_mcp_session_header = True
    await transport.client_initialize(context)

    initialize_request = McpInitializeRequest(
        id="session-test",
        params=McpInitializeParams(
            capabilities={},
            protocolVersion="2025-11-25",
            clientInfo={"name": "session-check", "version": "0.0.1"},
        ),
    )

    try:
        with pytest.raises(RuntimeError) as excinfo:
            await transport.client_send_message(initialize_request)
        assert HEADER_MCP_SESSION_ID in str(excinfo.value).lower()
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_client_requires_protocol_header(unused_tcp_port):
    port = unused_tcp_port
    runner = await _start_stub_http_server(
        port, headers={HEADER_MCP_SESSION_ID: "stub-session"}
    )
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    context = McpClientContext()
    context.flags.enforce_mcp_protocol_header = True
    await transport.client_initialize(context)

    initialize_request = McpInitializeRequest(
        id="protocol-test",
        params=McpInitializeParams(
            capabilities={},
            protocolVersion="2025-11-25",
            clientInfo={"name": "protocol-check", "version": "0.0.1"},
        ),
    )

    try:
        with pytest.raises(RuntimeError) as excinfo:
            await transport.client_send_message(initialize_request)
        assert HEADER_MCP_PROTOCOL_VERSION in str(excinfo.value).lower()
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_client_prefers_body_version_over_header(unused_tcp_port):
    port = unused_tcp_port
    runner = await _start_stub_http_server(
        port,
        headers={HEADER_MCP_PROTOCOL_VERSION: "2024-01-01"},
        body_protocol_version="2025-11-25",
    )
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    context = McpClientContext()
    await transport.client_initialize(context)

    initialize_request = McpInitializeRequest(
        id="body-wins",
        params=McpInitializeParams(
            capabilities={},
            protocolVersion="2024-01-01",
            clientInfo={"name": "body-wins", "version": "0.0.1"},
        ),
    )

    try:
        await transport.client_send_message(initialize_request)
        _ = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)
        assert context.version.version == "2025-11-25"
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_client_enforces_transport_version_consistency(unused_tcp_port):
    port = unused_tcp_port
    runner = await _start_stub_http_server(
        port,
        headers={HEADER_MCP_PROTOCOL_VERSION: "2024-01-01"},
        body_protocol_version="2025-11-25",
    )
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    context = McpClientContext()
    context.flags.enforce_mcp_transport_version_consistency = True
    await transport.client_initialize(context)

    initialize_request = McpInitializeRequest(
        id="consistency-check",
        params=McpInitializeParams(
            capabilities={},
            protocolVersion="2024-01-01",
            clientInfo={"name": "consistency", "version": "0.0.1"},
        ),
    )

    try:
        with pytest.raises(RuntimeError) as excinfo:
            await transport.client_send_message(initialize_request)
        assert "does not match transport header" in str(excinfo.value)
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_server_requires_session_header(unused_tcp_port):
    port = unused_tcp_port
    server = McpServer()
    server._context.flags.enforce_mcp_session_header = True
    await server.create_host_task(f"http://127.0.0.1:{port}/aiomcp")

    try:
        request = McpListToolsRequest(id="no-session")
        payload = request.model_dump_json(exclude_none=True)
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"http://127.0.0.1:{port}/aiomcp",
                data=payload,
                headers={
                    HEADER_CONTENT_TYPE: "application/json",
                    HEADER_ACCEPT: "application/json",
                },
            )
            await resp.read()
            assert resp.status == 400
    finally:
        await server.shutdown()


@pytest.mark.asyncio
async def test_http_server_requires_protocol_header(unused_tcp_port):
    port = unused_tcp_port
    server = McpServer()
    server._context.flags.enforce_mcp_protocol_header = True
    await server.create_host_task(f"http://127.0.0.1:{port}/aiomcp")

    initialize_request = McpInitializeRequest(
        id="proto-server",
        params=McpInitializeParams(
            capabilities={},
            protocolVersion="2025-11-25",
            clientInfo={"name": "server-proto", "version": "0.0.1"},
        ),
    )
    payload = initialize_request.model_dump_json(exclude_none=True)

    try:
        async with aiohttp.ClientSession() as session:
            resp_missing = await session.post(
                f"http://127.0.0.1:{port}/aiomcp",
                data=payload,
                headers={
                    HEADER_CONTENT_TYPE: "application/json",
                    HEADER_ACCEPT: "application/json",
                },
            )
            await resp_missing.read()
            assert resp_missing.status == 400

            resp_ok = await session.post(
                f"http://127.0.0.1:{port}/aiomcp",
                data=payload,
                headers={
                    HEADER_CONTENT_TYPE: "application/json",
                    HEADER_ACCEPT: "application/json",
                    HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
                },
            )
            await resp_ok.read()
            assert resp_ok.status == 200
    finally:
        await server.shutdown()
