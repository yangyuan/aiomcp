import asyncio
import io
import json
import os
import sys
from typing import AsyncIterator

import aiohttp
from aiohttp import web
import pytest
from pydantic import BaseModel

from aiomcp.mcp_server import McpServer
from aiomcp.mcp_client import McpClient
from aiomcp.mcp_context import McpClientContext, McpServerContext
from aiomcp.mcp_flag import McpClientFlags, McpServerFlags
from aiomcp.contracts.mcp_message import (
    McpCallToolParams,
    McpCallToolRequest,
    McpCallToolResult,
    McpCancelledNotification,
    McpCancelledNotificationParams,
    McpError,
    McpInitializeParams,
    McpInitializeRequest,
    McpInitializeResult,
    McpListToolsRequest,
    McpListToolsResult,
    McpNotification,
    McpPingRequest,
    McpResponse,
    McpServerRequest,
)
from aiomcp.contracts.mcp_tool import McpTool
from aiomcp.jsonrpc_error_codes import JsonRpcErrorCodes
from aiomcp.transports.base import McpClientTransport
from aiomcp.transports.direct import McpDirectTransport
from aiomcp.transports.memory import McpMemoryTransport
from aiomcp.transports.http import (
    McpHttpClientTransport,
    McpHttpServerTransport,
    McpHttpTransport,
)
from aiomcp.transports.stdio import McpStdioClientTransport, McpStdioServerTransport

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


async def _start_sse_http_server(port: int) -> web.AppRunner:
    app = web.Application()

    async def _handle(request: web.Request) -> web.Response:
        payload = await request.json()
        response_id = payload.get("id")
        response_payload = json.dumps(
            {"jsonrpc": "2.0", "id": response_id, "result": {"tools": []}}
        )
        body = "".join(
            [
                "event: message\n",
                'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":0.5}}\n\n',
                "event: message\n",
                f"data: {response_payload}\n\n",
            ]
        )
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                HEADER_CONTENT_TYPE: "text/event-stream",
                HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
            },
        )

    app.router.add_post("/aiomcp", _handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


async def _start_session_expiry_http_server(
    port: int, records: list[tuple[str, str | None]]
) -> web.AppRunner:
    app = web.Application()
    initialized_once = False

    async def _handle(request: web.Request) -> web.Response:
        nonlocal initialized_once
        payload = await request.json()
        method = payload.get("method")
        session_id = request.headers.get(HEADER_MCP_SESSION_ID)
        records.append((method or "response", session_id))

        if method == "initialize":
            next_session_id = (
                "expired-session" if not initialized_once else "fresh-session"
            )
            initialized_once = True
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {
                    "capabilities": {},
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "session", "version": "0.0"},
                },
            }
            return web.json_response(
                response,
                headers={
                    HEADER_MCP_SESSION_ID: next_session_id,
                    HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
                },
            )

        if method == "tools/list" and session_id == "expired-session":
            return web.Response(status=404)

        if method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {"tools": []},
            }
            return web.json_response(
                response, headers={HEADER_MCP_PROTOCOL_VERSION: "2025-11-25"}
            )

        return web.Response(
            status=202, headers={HEADER_MCP_PROTOCOL_VERSION: "2025-11-25"}
        )

    app.router.add_post("/aiomcp", _handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


async def _start_resumable_sse_http_server(
    port: int, last_event_ids: list[str | None]
) -> web.AppRunner:
    app = web.Application()
    request_id: str | int | None = None

    async def _handle_post(request: web.Request) -> web.Response:
        nonlocal request_id
        payload = await request.json()
        request_id = payload.get("id")
        body = "".join(
            [
                "id: post-1\n",
                "retry: 1\n",
                'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":0.25}}\n\n',
            ]
        )
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                HEADER_CONTENT_TYPE: "text/event-stream",
                HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
            },
        )

    async def _handle_get(request: web.Request) -> web.Response:
        last_event_ids.append(request.headers.get("last-event-id"))
        response_payload = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}}
        )
        body = f"id: get-1\ndata: {response_payload}\n\n"
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                HEADER_CONTENT_TYPE: "text/event-stream",
                HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
            },
        )

    app.router.add_post("/aiomcp", _handle_post)
    app.router.add_get("/aiomcp", _handle_get)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


async def _start_get_sse_http_server(port: int) -> web.AppRunner:
    app = web.Application()

    async def _handle_post(request: web.Request) -> web.Response:
        payload = await request.json()
        response = {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": {
                "capabilities": {},
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "get-sse", "version": "0.0"},
            },
        }
        return web.json_response(
            response,
            headers={
                HEADER_MCP_SESSION_ID: "get-session",
                HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
            },
        )

    async def _handle_get(request: web.Request) -> web.Response:
        body = "".join(
            [
                "id: get-channel-1\n",
                'data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n',
            ]
        )
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                HEADER_CONTENT_TYPE: "text/event-stream",
                HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
            },
        )

    app.router.add_post("/aiomcp", _handle_post)
    app.router.add_get("/aiomcp", _handle_get)
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
        assert resp.result["content"] == [{"type": "text", "text": '{"text": "hello"}'}]
        assert resp.result["isError"] is False

        result = await client.invoke("echo", {"text": "hello"})
        assert result == {"text": "hello"}
    finally:
        await client.close()
        await server.shutdown()


async def structured_content_only_tool():
    return McpCallToolResult(structuredContent={"value": 42})


@pytest.mark.asyncio
async def test_structured_content_only_tool_result_fills_content():
    server = McpServer()
    await server.mcp_tools_register("structured", structured_content_only_tool, {})

    response = await server.process(
        McpCallToolRequest(
            id="structured",
            params=McpCallToolParams(name="structured", arguments={}),
        )
    )

    assert isinstance(response, McpResponse)
    assert response.result == {
        "content": [{"type": "text", "text": '{"value": 42}'}],
        "isError": False,
        "structuredContent": {"value": 42},
    }


class SilentClientTransport(McpClientTransport):
    def __init__(self) -> None:
        self.sent = []

    async def client_initialize(self, context: McpClientContext):
        pass

    async def client_messages(self) -> AsyncIterator:
        await asyncio.Event().wait()
        if False:
            yield

    async def client_send_message(self, message) -> bool:
        self.sent.append(message)
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


class QueueClientTransport(McpClientTransport):
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent = []

    async def client_initialize(self, context: McpClientContext):
        pass

    async def client_messages(self) -> AsyncIterator:
        while True:
            message = await self.incoming.get()
            if message is None:
                break
            yield message

    async def client_send_message(self, message) -> bool:
        self.sent.append(message)
        return True

    async def close(self):
        await self.incoming.put(None)


class PaginatedToolsTransport(QueueClientTransport):
    def __init__(self) -> None:
        super().__init__()
        self.list_cursors = []

    async def client_send_message(self, message) -> bool:
        self.sent.append(message)
        if isinstance(message, McpInitializeRequest):
            result = McpInitializeResult(
                capabilities={"tools": {}},
                protocolVersion="2025-11-25",
                serverInfo={"name": "paged", "version": "0.0"},
            )
            await self.incoming.put(
                McpResponse(id=message.id, result=result.model_dump())
            )
        elif isinstance(message, McpListToolsRequest):
            cursor = message.params.cursor if message.params is not None else None
            self.list_cursors.append(cursor)
            if cursor is None:
                result = McpListToolsResult(
                    tools=[McpTool(name="first")], nextCursor="next-page"
                )
            else:
                result = McpListToolsResult(tools=[McpTool(name="second")])
            await self.incoming.put(
                McpResponse(id=message.id, result=result.model_dump())
            )
        return True


@pytest.mark.asyncio
async def test_client_request_timeout_cleans_inflight():
    transport = SilentClientTransport()
    client = McpClient(request_timeout=0.01)
    client._transport = transport
    request = McpListToolsRequest(id="timeout-test")

    with pytest.raises(TimeoutError):
        await client._process(request)

    assert "timeout-test" not in client._inflight
    assert isinstance(transport.sent[-1], McpCancelledNotification)
    assert transport.sent[-1].params.requestId == "timeout-test"


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
async def test_client_refresh_tools_reads_all_pages():
    transport = PaginatedToolsTransport()
    client = McpClient()

    try:
        await client.initialize(transport)

        assert transport.list_cursors == [None, "next-page"]
        assert [tool.name for tool in await client.mcp_tools_list()] == [
            "first",
            "second",
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_lists_tools_without_capability_by_default():
    class NoToolsCapabilityTransport(QueueClientTransport):
        async def client_send_message(self, message) -> bool:
            self.sent.append(message)
            if isinstance(message, McpInitializeRequest):
                result = McpInitializeResult(
                    capabilities={},
                    protocolVersion="2025-11-25",
                    serverInfo={"name": "no-tools", "version": "0.0"},
                )
                await self.incoming.put(
                    McpResponse(id=message.id, result=result.model_dump())
                )
            elif isinstance(message, McpListToolsRequest):
                result = McpListToolsResult(tools=[])
                await self.incoming.put(
                    McpResponse(id=message.id, result=result.model_dump())
                )
            return True

    transport = NoToolsCapabilityTransport()
    client = McpClient()

    try:
        await client.initialize(transport)

        assert await client.mcp_tools_list() == []
        assert any(
            isinstance(message, McpListToolsRequest) for message in transport.sent
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_can_skip_tools_list_without_capability():
    class NoToolsCapabilityTransport(QueueClientTransport):
        async def client_send_message(self, message) -> bool:
            self.sent.append(message)
            if isinstance(message, McpInitializeRequest):
                result = McpInitializeResult(
                    capabilities={},
                    protocolVersion="2025-11-25",
                    serverInfo={"name": "no-tools", "version": "0.0"},
                )
                await self.incoming.put(
                    McpResponse(id=message.id, result=result.model_dump())
                )
            elif isinstance(message, McpListToolsRequest):
                raise AssertionError("client should not list tools without capability")
            return True

    transport = NoToolsCapabilityTransport()
    client = McpClient(flags={"enforce_mcp_tools_capability": True})

    try:
        await client.initialize(transport)

        assert await client.mcp_tools_list() == []
        assert not any(
            isinstance(message, McpListToolsRequest) for message in transport.sent
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_server_cancels_inflight_request():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_tool() -> str:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    server = McpServer()
    await server.register_tool(slow_tool, alias="slow")
    transport = McpMemoryTransport()
    await server._create_host_task(transport)

    try:
        request = McpCallToolRequest(
            id="slow-1", params=McpCallToolParams(name="slow", arguments={})
        )
        await transport.client_send_message(request)
        await asyncio.wait_for(started.wait(), timeout=1)

        await transport.client_send_message(
            McpCancelledNotification(
                params=McpCancelledNotificationParams(requestId="slow-1", reason="test")
            )
        )

        await asyncio.wait_for(cancelled.wait(), timeout=1)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(transport._server_to_client.get(), timeout=0.05)
    finally:
        await server.shutdown()


@pytest.mark.asyncio
async def test_client_responds_to_server_ping_request():
    transport = QueueClientTransport()
    client = McpClient()
    client._transport = transport
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    await transport.incoming.put(McpPingRequest(id="ping-1"))
    await asyncio.sleep(0)

    response = transport.sent[-1]
    assert isinstance(response, McpResponse)
    assert response.id == "ping-1"
    assert response.result == {}
    await client.close()


@pytest.mark.asyncio
async def test_server_responds_to_client_ping_request():
    server = McpServer()
    transport = McpMemoryTransport()
    await server._create_host_task(transport)

    try:
        await transport.client_send_message(McpPingRequest(id="server-ping-1"))
        response = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)

        assert isinstance(response, McpResponse)
        assert response.id == "server-ping-1"
        assert response.result == {}
    finally:
        await server.shutdown()


@pytest.mark.asyncio
async def test_client_returns_method_not_found_for_unsupported_server_request():
    transport = QueueClientTransport()
    client = McpClient()
    client._transport = transport
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    await transport.incoming.put(McpServerRequest(id="roots-1", method="roots/list"))
    await asyncio.sleep(0)

    response = transport.sent[-1]
    assert isinstance(response, McpError)
    assert response.id == "roots-1"
    assert response.error.code == JsonRpcErrorCodes.METHOD_NOT_FOUND
    await client.close()


@pytest.mark.asyncio
async def test_client_ignores_server_notifications_without_response():
    transport = QueueClientTransport()
    client = McpClient()
    client._transport = transport
    client._message_loop = asyncio.create_task(client._handle_message_loop())

    await transport.incoming.put(
        McpNotification(method="notifications/tools/list_changed")
    )
    await asyncio.sleep(0)

    assert transport.sent == []
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
async def test_stdio_client_accepts_large_stdout_message():
    code = (
        "import json; "
        "print(json.dumps({"
        "'jsonrpc': '2.0', "
        "'id': 'large', "
        "'result': {'text': 'x' * 200000}"
        "}), flush=True)"
    )

    transport = McpStdioClientTransport([sys.executable, "-c", code])
    await transport.client_initialize(McpClientContext())
    try:
        message = await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
        assert isinstance(message, McpResponse)
        assert message.id == "large"
        assert message.result["text"] == "x" * 200000
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_client_max_frame_size_is_configurable():
    code = (
        "import json; "
        "print(json.dumps({"
        "'jsonrpc': '2.0', "
        "'id': 'large', "
        "'result': {'text': 'x' * 5000}"
        "}), flush=True)"
    )

    transport = McpStdioClientTransport(
        [sys.executable, "-c", code], max_frame_size=1024
    )
    await transport.client_initialize(McpClientContext())
    try:
        with pytest.raises(RuntimeError, match="max_frame_size=1024"):
            await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
    finally:
        await transport.close()

    transport = McpStdioClientTransport(
        [sys.executable, "-c", code], max_frame_size=16 * 1024
    )
    await transport.client_initialize(McpClientContext())
    try:
        message = await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
        assert isinstance(message, McpResponse)
        assert message.id == "large"
        assert message.result["text"] == "x" * 5000
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_client_parse_errors_are_ignored_by_default():
    code = (
        "import json; "
        "print('not-json', flush=True); "
        "print(json.dumps({'jsonrpc': '2.0', 'id': 'ok', 'result': {}}), flush=True)"
    )
    transport = McpStdioClientTransport([sys.executable, "-c", code])
    await transport.client_initialize(McpClientContext())

    try:
        message = await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
        assert isinstance(message, McpResponse)
        assert message.id == "ok"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_client_can_throw_parse_errors():
    code = "print('not-json', flush=True)"
    transport = McpStdioClientTransport([sys.executable, "-c", code])
    await transport.client_initialize(
        McpClientContext(McpClientFlags(throw_mcp_parse_errors=True))
    )

    try:
        with pytest.raises(RuntimeError, match="failed to parse stdio message"):
            await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_server_accepts_large_stdin_message(monkeypatch):
    payload = (
        McpListToolsRequest(id="large")
        .model_dump_json(exclude_none=True)
        .encode("utf-8")
    )
    line = payload[:-1] + b',"params":{"cursor":"' + (b"x" * 200000) + b'"}}\n'

    class FakeStdin:
        buffer = io.BytesIO(line)

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    transport = McpStdioServerTransport()
    await transport.server_initialize(McpServerContext())

    try:
        message, session_id = await asyncio.wait_for(
            anext(transport.server_messages()), timeout=2
        )
        assert isinstance(message, McpListToolsRequest)
        assert message.id == "large"
        assert message.params is not None
        assert message.params.cursor == "x" * 200000
        assert session_id is None
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_server_max_frame_size_is_configurable(monkeypatch):
    payload = (
        McpListToolsRequest(id="large")
        .model_dump_json(exclude_none=True)
        .encode("utf-8")
    )
    line = payload[:-1] + b',"params":{"cursor":"' + (b"x" * 5000) + b'"}}\n'

    class FakeStdin:
        buffer = io.BytesIO(line)

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    transport = McpStdioServerTransport(max_frame_size=1024)
    await transport.server_initialize(McpServerContext())

    try:
        with pytest.raises(RuntimeError, match="max_frame_size=1024"):
            await asyncio.wait_for(anext(transport.server_messages()), timeout=2)
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_server_parse_errors_are_ignored_by_default(monkeypatch):
    valid = (
        McpListToolsRequest(id="ok").model_dump_json(exclude_none=True).encode("utf-8")
    )

    class FakeStdin:
        buffer = io.BytesIO(b"not-json\n" + valid + b"\n")

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    transport = McpStdioServerTransport()
    await transport.server_initialize(McpServerContext())

    try:
        message, session_id = await asyncio.wait_for(
            anext(transport.server_messages()), timeout=2
        )
        assert isinstance(message, McpListToolsRequest)
        assert message.id == "ok"
        assert session_id is None
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_stdio_server_can_throw_parse_errors(monkeypatch):
    class FakeStdin:
        buffer = io.BytesIO(b"not-json\n")

    monkeypatch.setattr(sys, "stdin", FakeStdin())
    transport = McpStdioServerTransport()
    await transport.server_initialize(
        McpServerContext(McpServerFlags(throw_mcp_parse_errors=True))
    )

    try:
        with pytest.raises(RuntimeError, match="failed to parse stdio message"):
            await asyncio.wait_for(anext(transport.server_messages()), timeout=2)
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
async def test_stdio_client_passes_custom_environment():
    code = (
        "import json, os; "
        "print(json.dumps({'jsonrpc': '2.0', 'id': os.environ['AIOMCP_TEST_ENV'], 'result': {}}), flush=True)"
    )
    env = dict(os.environ)
    env["AIOMCP_TEST_ENV"] = "custom-env"
    transport = McpStdioClientTransport([sys.executable, "-c", code], env=env)
    await transport.client_initialize(McpClientContext())

    try:
        message = await asyncio.wait_for(anext(transport.client_messages()), timeout=2)
        assert isinstance(message, McpResponse)
        assert message.id == "custom-env"
    finally:
        await transport.close()


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
    await mcp_server._create_host_task(transport)
    mcp_client = McpClient()
    await _client_driven_validation(transport, mcp_client, mcp_server)


@pytest.mark.asyncio
async def test_http_transport(unused_tcp_port):
    port = unused_tcp_port
    mcp_server = McpServer()
    await mcp_server._create_host_task(f"http://127.0.0.1:{port}/aiomcp")

    client_transport = McpHttpTransport("127.0.0.1", port, path="/aiomcp")
    mcp_client = McpClient()

    await _client_driven_validation(client_transport, mcp_client, mcp_server)


@pytest.mark.asyncio
async def test_http_transport_uses_custom_client_headers(unused_tcp_port):
    port = unused_tcp_port
    transport = McpHttpTransport(
        "127.0.0.1",
        port,
        path="/aiomcp",
        headers={"x-aiomcp-test": "custom-header"},
    )
    await transport.client_initialize(McpClientContext())

    try:
        headers = await transport._client._build_headers(
            McpListToolsRequest(id="headers"),
            accept="application/json",
            include_content_type=True,
        )

        assert headers["x-aiomcp-test"] == "custom-header"
        assert headers[HEADER_ACCEPT] == "application/json"
        assert headers[HEADER_CONTENT_TYPE] == "application/json"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_http_transport_numeric_request_id(unused_tcp_port):
    port = unused_tcp_port
    mcp_server = McpServer()
    await mcp_server._create_host_task(f"http://127.0.0.1:{port}/aiomcp")

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
async def test_http_client_reads_sse_until_matching_response(unused_tcp_port):
    port = unused_tcp_port
    runner = await _start_sse_http_server(port)
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    await transport.client_initialize(McpClientContext())

    try:
        request = McpListToolsRequest(id="sse-list")
        await transport.client_send_message(request)

        notification = await asyncio.wait_for(
            transport._server_to_client.get(), timeout=1
        )
        response = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)

        assert isinstance(notification, McpNotification)
        assert notification.method == "notifications/progress"
        assert isinstance(response, McpResponse)
        assert response.id == "sse-list"
        assert response.result == {"tools": []}
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_client_recovers_expired_session(unused_tcp_port):
    port = unused_tcp_port
    records: list[tuple[str, str | None]] = []
    runner = await _start_session_expiry_http_server(port, records)
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    context = McpClientContext()
    await transport.client_initialize(context)

    try:
        initialize_request = McpInitializeRequest(
            id="init-expiry",
            params=McpInitializeParams(
                capabilities={},
                protocolVersion="2025-11-25",
                clientInfo={"name": "expiry", "version": "0.0"},
            ),
        )
        await transport.client_send_message(initialize_request)
        _ = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)

        await transport.client_send_message(
            McpNotification(method="notifications/initialized")
        )
        await transport.client_send_message(McpListToolsRequest(id="list-retry"))
        response = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)

        assert isinstance(response, McpResponse)
        assert response.id == "list-retry"
        assert context.session_id == "fresh-session"
        assert ("initialize", None) in records
        assert ("tools/list", "expired-session") in records
        assert ("tools/list", "fresh-session") in records
        assert ("notifications/initialized", "fresh-session") in records
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_client_resumes_sse_with_last_event_id(unused_tcp_port):
    port = unused_tcp_port
    last_event_ids: list[str | None] = []
    runner = await _start_resumable_sse_http_server(port, last_event_ids)
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    await transport.client_initialize(McpClientContext())

    try:
        await transport.client_send_message(McpListToolsRequest(id="resume-list"))

        notification = await asyncio.wait_for(
            transport._server_to_client.get(), timeout=1
        )
        response = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)

        assert last_event_ids == ["post-1"]
        assert isinstance(notification, McpNotification)
        assert notification.method == "notifications/progress"
        assert isinstance(response, McpResponse)
        assert response.id == "resume-list"
        assert response.result == {"tools": []}
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_client_reads_get_sse_channel(unused_tcp_port):
    port = unused_tcp_port
    runner = await _start_get_sse_http_server(port)
    transport = McpHttpClientTransport("127.0.0.1", port, path="/aiomcp")
    await transport.client_initialize(McpClientContext())

    try:
        initialize_request = McpInitializeRequest(
            id="init-get-sse",
            params=McpInitializeParams(
                capabilities={},
                protocolVersion="2025-11-25",
                clientInfo={"name": "get-sse", "version": "0.0"},
            ),
        )
        await transport.client_send_message(initialize_request)
        _ = await asyncio.wait_for(transport._server_to_client.get(), timeout=1)
        notification = await asyncio.wait_for(
            transport._server_to_client.get(), timeout=1
        )

        assert isinstance(notification, McpNotification)
        assert notification.method == "notifications/tools/list_changed"
    finally:
        await transport.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_server_get_sse_channel_sends_one_stream(unused_tcp_port):
    port = unused_tcp_port
    transport = McpHttpServerTransport("127.0.0.1", port, path="/aiomcp")
    await transport.server_initialize(McpServerContext())

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/aiomcp",
                headers={
                    HEADER_ACCEPT: "text/event-stream",
                    HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
                },
            ) as response:
                assert response.status == 200
                sent = await transport.server_send_message(
                    McpNotification(method="notifications/tools/list_changed")
                )
                assert sent is True

                lines = []
                while len(lines) < 6:
                    line = await asyncio.wait_for(
                        response.content.readline(), timeout=1
                    )
                    if not line:
                        break
                    lines.append(line.decode("utf-8").strip())

        assert any("notifications/tools/list_changed" in line for line in lines)
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_http_server_returns_404_for_terminated_session(unused_tcp_port):
    port = unused_tcp_port
    transport = McpHttpServerTransport("127.0.0.1", port, path="/aiomcp")
    await transport.server_initialize(McpServerContext())

    try:
        async with aiohttp.ClientSession() as session:
            delete_response = await session.delete(
                f"http://127.0.0.1:{port}/aiomcp",
                headers={
                    HEADER_MCP_SESSION_ID: "gone-session",
                    HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
                },
            )
            await delete_response.read()
            assert delete_response.status == 202

            notification = McpNotification(method="notifications/initialized")
            post_response = await session.post(
                f"http://127.0.0.1:{port}/aiomcp",
                data=notification.model_dump_json(exclude_none=True),
                headers={
                    HEADER_CONTENT_TYPE: "application/json",
                    HEADER_ACCEPT: "application/json, text/event-stream",
                    HEADER_MCP_SESSION_ID: "gone-session",
                    HEADER_MCP_PROTOCOL_VERSION: "2025-11-25",
                },
            )
            await post_response.read()
            assert post_response.status == 404
    finally:
        await transport.close()


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
    await server._create_host_task(f"http://127.0.0.1:{port}/aiomcp")

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
    await server._create_host_task(f"http://127.0.0.1:{port}/aiomcp")

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
