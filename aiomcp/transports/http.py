import asyncio
from typing import AsyncIterator, Dict, Optional

import aiohttp
from aiohttp import web
from pydantic import TypeAdapter

from aiomcp.contracts.mcp_message import (
    McpClientMessageAnnotated,
    McpMessage,
    McpNotification,
    McpRequest,
    McpResponseOrError,
    McpMethod,
)
from aiomcp.mcp_serialization import McpSerialization
from aiomcp.transports.base import McpClientTransport, McpServerTransport, McpTransport


HEADER_CONTENT_TYPE = "content-type"
HEADER_ACCEPT = "accept"
HEADER_MCP_SESSION_ID = "mcp-session-id"

CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_STREAM = "text/event-stream"


class McpHttpClientTransport(McpClientTransport):
    def __init__(self, hostname: str, port: int, *, path: str = "/") -> None:
        assert path.startswith("/"), "HTTP path must start with '/'"
        self._path = path
        self._endpoint = f"http://{hostname}:{port}{path}"
        self._server_to_client: asyncio.Queue[McpMessage] = asyncio.Queue()
        self._initialized: bool = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_id: Optional[str] = None

    async def client_initialize(self):
        assert not self._initialized, "HTTP client session is already initialized"
        self._initialized = True
        self._session = aiohttp.ClientSession()

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._server_to_client.get()
            yield message

    async def _extract_payload_from_response_body(
        self, response: aiohttp.ClientResponse
    ) -> bytes:
        content_type = response.headers.get(HEADER_CONTENT_TYPE, "").lower()
        if CONTENT_TYPE_STREAM not in content_type:
            return await response.read()
        body_text = await response.text()
        data_lines: list[str] = []
        for raw_line in body_text.splitlines():
            line = raw_line.rstrip("\r\n")
            if line == "":
                if data_lines:
                    break
                continue
            if line.startswith(":"):
                continue
            field, sep, value = line.partition(":")
            if not sep:
                continue
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
        return "\n".join(data_lines).encode("utf-8")

    async def client_send_message(self, message: McpMessage) -> bool:
        assert self._session is not None

        payload = message.model_dump_json(exclude_none=True)
        headers = {
            HEADER_CONTENT_TYPE: CONTENT_TYPE_JSON,
            HEADER_ACCEPT: f"{CONTENT_TYPE_JSON}, {CONTENT_TYPE_STREAM}",
        }
        if self._session_id:
            headers[HEADER_MCP_SESSION_ID] = self._session_id
        async with self._session.post(
            self._endpoint,
            data=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            # Capture session id from InitializeResult
            if (
                isinstance(message, McpRequest)
                and message.method == McpMethod.INITIALIZE
            ):
                server_session_id = response.headers.get(
                    HEADER_MCP_SESSION_ID, ""
                ).lower()
                if server_session_id:
                    self._session_id = server_session_id
            response_body = await self._extract_payload_from_response_body(response)

        # For requests, the result must be a matching response/error and is enqueued.
        # For notifications, no further processing after POST completes.
        if isinstance(message, McpRequest):
            try:
                message: McpMessage = TypeAdapter(McpResponseOrError).validate_json(
                    response_body
                )
            except Exception:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} failed to parse response: {response_body.decode()}"
                )
            await self._server_to_client.put(message)
        return True

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None


class McpHttpServerTransport(McpServerTransport):
    def __init__(self, hostname: str, port: int, *, path: str = "/") -> None:
        assert path.startswith("/"), "HTTP path must start with '/'"
        self._path = path
        self._hostname: str = hostname
        self._port: int = port
        self._runner: Optional[web.AppRunner] = None
        self._client_to_server: asyncio.Queue[McpMessage] = asyncio.Queue()
        self._inflight: Dict[str, asyncio.Future[McpResponseOrError]] = {}

    async def server_initialize(self):
        if self._runner is not None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} is already initialized"
            )
        _app = web.Application()
        _app.router.add_post(self._path, self._handle_post)
        self._runner = web.AppRunner(_app)
        await self._runner.setup()
        _site = web.TCPSite(self._runner, self._hostname, self._port)
        await _site.start()

    async def server_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._client_to_server.get()
            yield message

    async def server_send_message(self, message: McpMessage) -> bool:
        message = McpSerialization.process_server_message(message)
        if isinstance(message, McpResponseOrError):
            request_id = message.id
            if request_id not in self._inflight:
                raise RuntimeError(
                    f"{McpHttpServerTransport.__name__} has no matching request for response ID {request_id}"
                )
            future = self._inflight.pop(request_id)
            future.set_result(message)
            return True
        return False

    async def _handle_post(self, request: web.Request) -> web.StreamResponse:
        request_body = await request.read()
        try:
            message = TypeAdapter(McpClientMessageAnnotated).validate_json(request_body)
        except Exception:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} failed to parse request: {request_body.decode()}"
            )

        if isinstance(message, McpNotification):
            await self._client_to_server.put(message)
            return web.Response(status=202)
        elif isinstance(message, McpRequest):
            future: asyncio.Future[McpResponseOrError] = asyncio.Future()
            self._inflight[str(message.id)] = future
            await self._client_to_server.put(message)
            response = await future
            return web.json_response(response.model_dump())

    async def close(self):
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


class McpHttpTransport(McpTransport):
    def __init__(self, hostname: str, port: int, path: str = "/") -> None:
        self._client: McpHttpClientTransport = McpHttpClientTransport(
            hostname, port, path=path
        )
        self._server: McpHttpServerTransport = McpHttpServerTransport(
            hostname, port, path=path
        )

    async def client_initialize(self):
        await self._client.client_initialize()

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        async for message in self._client.client_messages():
            yield message

    async def client_send_message(self, message: McpMessage) -> bool:
        return await self._client.client_send_message(message)

    async def server_initialize(self):
        await self._server.server_initialize()

    async def server_messages(self) -> AsyncIterator[McpMessage]:
        async for message in self._server.server_messages():
            yield message

    async def server_send_message(self, message: McpMessage) -> bool:
        return await self._server.server_send_message(message)

    async def close(self):
        await self._client.close()
        await self._server.close()
