import asyncio
import uuid
from typing import AsyncIterator, Dict, Optional

import aiohttp
from aiohttp import web
from pydantic import TypeAdapter

from aiomcp.contracts.mcp_message import (
    McpClientMessageAnnotated,
    McpMessage,
    McpNotification,
    McpRequest,
    McpInitializeRequest,
    McpResponse,
    McpResponseOrError,
    McpMethod,
    McpInitializeResult,
)
from aiomcp.mcp_context import McpServerContext, McpClientContext
from aiomcp.mcp_serialization import McpSerialization
from aiomcp.mcp_version import McpVersion
from aiomcp.transports.base import McpClientTransport, McpServerTransport, McpTransport


HEADER_CONTENT_TYPE = "content-type"
HEADER_ACCEPT = "accept"
HEADER_MCP_SESSION_ID = "mcp-session-id"
HEADER_MCP_PROTOCOL_VERSION = "mcp-protocol-version"
DEFAULT_PROTOCOL_VERSION = McpVersion.LATEST
SUPPORTED_PROTOCOL_VERSIONS = set(McpVersion.SUPPORTED)

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
        self._context: McpClientContext | None = None

    async def client_initialize(self, context: McpClientContext):
        assert not self._initialized, "HTTP client session is already initialized"
        self._initialized = True
        self._session = aiohttp.ClientSession()
        self._context = context

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
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot send messages before initialization"
            )

        context = self._context
        protocol_version = context.version.version or DEFAULT_PROTOCOL_VERSION

        payload = message.model_dump_json(exclude_none=True)
        headers = {
            HEADER_CONTENT_TYPE: CONTENT_TYPE_JSON,
            HEADER_ACCEPT: f"{CONTENT_TYPE_JSON}, {CONTENT_TYPE_STREAM}",
            HEADER_MCP_PROTOCOL_VERSION: protocol_version,
        }

        if isinstance(message, McpInitializeRequest):
            headers[HEADER_MCP_PROTOCOL_VERSION] = (
                message.params.protocolVersion or protocol_version
            )

        is_initialize_request = (
            isinstance(message, McpRequest)
            and message.method == McpMethod.INITIALIZE
        )

        if context.session_id:
            headers[HEADER_MCP_SESSION_ID] = context.session_id

        async with self._session.post(
            self._endpoint,
            data=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            protocol_header_value = response.headers.get(HEADER_MCP_PROTOCOL_VERSION)
            if (
                context.flags.enforce_mcp_protocol_header
                and not protocol_header_value
            ):
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} server response missing {HEADER_MCP_PROTOCOL_VERSION}"
                )

            header_protocol_version = (
                protocol_header_value.strip() if protocol_header_value else None
            )

            if is_initialize_request:
                server_session_id = response.headers.get(HEADER_MCP_SESSION_ID)
                if (
                    not server_session_id
                    and context.flags.enforce_mcp_session_header
                ):
                    raise RuntimeError(
                        f"{McpHttpClientTransport.__name__} server did not provide {HEADER_MCP_SESSION_ID} during initialize"
                    )
                if server_session_id:
                    context.session_id = server_session_id.strip()

            response_body = await self._extract_payload_from_response_body(response)

        if isinstance(message, McpRequest):
            try:
                structured_response: McpMessage = TypeAdapter(
                    McpResponseOrError
                ).validate_json(response_body)
            except Exception:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} failed to parse response: {response_body.decode()}"
                )

            body_protocol_version: Optional[str] = None
            if is_initialize_request and isinstance(structured_response, McpResponse):
                result_payload = structured_response.result
                if isinstance(result_payload, dict):
                    raw_version = result_payload.get("protocolVersion")
                    if isinstance(raw_version, str):
                        body_protocol_version = raw_version

            context.version.sync_as_client(
                body_protocol_version,
                header_protocol_version,
                enforce_negotiation=context.flags.enforce_mcp_version_negotiation,
                enforce_consistency=context.flags.enforce_mcp_transport_version_consistency,
            )

            await self._server_to_client.put(structured_response)
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
        self._client_to_server: asyncio.Queue[tuple[McpMessage, str | None]] = asyncio.Queue()
        self._inflight: Dict[tuple[str | None, str], asyncio.Future[McpResponseOrError]] = {}
        self._context: McpServerContext | None = None

    async def server_initialize(self, context: McpServerContext):
        if self._runner is not None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} is already initialized"
            )
        self._context = context
        _app = web.Application()
        _app.router.add_post(self._path, self._handle_post)
        self._runner = web.AppRunner(_app)
        await self._runner.setup()
        _site = web.TCPSite(self._runner, self._hostname, self._port)
        await _site.start()

    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, str | None]]:
        while True:
            message_tuple = await self._client_to_server.get()
            yield message_tuple

    async def server_send_message(self, message: McpMessage, session_id: str | None = None) -> bool:
        message = McpSerialization.process_server_message(message)
        if isinstance(message, McpResponseOrError):
            request_id = str(message.id)
            key = (session_id, request_id)
            if key not in self._inflight:
                raise RuntimeError(
                    f"{McpHttpServerTransport.__name__} has no matching request for response ID {request_id} in session {session_id}"
                )
            future = self._inflight.pop(key)
            future.set_result(message)
            return True
        return False

    async def _handle_post(self, request: web.Request) -> web.StreamResponse:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} not initialized with context"
            )
        request_body = await request.read()
        try:
            message = TypeAdapter(McpClientMessageAnnotated).validate_json(request_body)
        except Exception:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} failed to parse request: {request_body.decode()}"
            )

        protocol_version_header = request.headers.get(HEADER_MCP_PROTOCOL_VERSION)
        protocol_version_header = protocol_version_header.strip() if protocol_version_header else ""

        flags = self._context.flags

        if flags.enforce_mcp_protocol_header and not protocol_version_header:
            return web.Response(
                status=400,
                text=f"{HEADER_MCP_PROTOCOL_VERSION} header required",
            )

        if protocol_version_header and protocol_version_header not in SUPPORTED_PROTOCOL_VERSIONS:
            return web.Response(
                status=400,
                text=f"Unsupported {HEADER_MCP_PROTOCOL_VERSION}: {protocol_version_header}",
            )

        request_protocol_version = protocol_version_header or DEFAULT_PROTOCOL_VERSION

        session_id = request.headers.get(HEADER_MCP_SESSION_ID)
        if (
            isinstance(message, McpRequest)
            and message.method == McpMethod.INITIALIZE
            and not session_id
        ):
            session_id = uuid.uuid4().hex
        elif flags.enforce_mcp_session_header and not session_id:
            return web.Response(
                status=400,
                text=f"{HEADER_MCP_SESSION_ID} header required",
            )

        session = self._context.get_session(session_id)

        if isinstance(message, McpNotification):
            await self._client_to_server.put((message, session_id))
            protocol_header_value = session.version.version or DEFAULT_PROTOCOL_VERSION
            headers: Dict[str, str] = {}
            if protocol_header_value:
                headers[HEADER_MCP_PROTOCOL_VERSION] = protocol_header_value
            return web.Response(status=202, headers=headers)
        elif isinstance(message, McpRequest):
            future: asyncio.Future[McpResponseOrError] = asyncio.Future()
            self._inflight[(session_id, str(message.id))] = future
            await self._client_to_server.put((message, session_id))
            response = await future
            resp_obj = web.json_response(response.model_dump())

            session = self._context.get_session(session_id)
            protocol_header_value = session.version.version or request_protocol_version
            resp_obj.headers[HEADER_MCP_PROTOCOL_VERSION] = (
                protocol_header_value or DEFAULT_PROTOCOL_VERSION
            )

            if message.method == McpMethod.INITIALIZE and session_id:
                resp_obj.headers[HEADER_MCP_SESSION_ID] = session_id
            return resp_obj

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

    async def client_initialize(self, context: McpClientContext):
        await self._client.client_initialize(context)

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        async for message in self._client.client_messages():
            yield message

    async def client_send_message(self, message: McpMessage) -> bool:
        return await self._client.client_send_message(message)

    async def server_initialize(self, context: McpServerContext):
        await self._server.server_initialize(context)

    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, str | None]]:
        async for message in self._server.server_messages():
            yield message

    async def server_send_message(self, message: McpMessage, session_id: str | None = None) -> bool:
        return await self._server.server_send_message(message, session_id)

    async def close(self):
        await self._client.close()
        await self._server.close()
