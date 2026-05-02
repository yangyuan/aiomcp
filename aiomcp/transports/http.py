import asyncio
import codecs
import uuid
from typing import AsyncIterator, Dict, Optional

import aiohttp
from aiohttp import web
from pydantic import TypeAdapter

from aiomcp.contracts.mcp_message import (
    McpClientMessageUnion,
    McpError,
    McpMessage,
    McpNotification,
    McpRequest,
    McpInitializeRequest,
    McpResponse,
    McpResponseOrError,
    McpServerMessageUnion,
    McpMethod,
    McpInitializeResult,
)
from aiomcp.mcp_context import McpServerContext, McpClientContext
from aiomcp.mcp_serialization import McpSerialization
from aiomcp.mcp_version import McpVersion
from aiomcp.mcp_authorization import McpAuthorizationClient, McpAuthorizationServer
from aiomcp.transports.base import (
    McpClientTransport,
    McpServerTransport,
    McpTransport,
)

HEADER_CONTENT_TYPE = "content-type"
HEADER_ACCEPT = "accept"
HEADER_MCP_SESSION_ID = "mcp-session-id"
HEADER_MCP_PROTOCOL_VERSION = "mcp-protocol-version"
DEFAULT_PROTOCOL_VERSION = McpVersion.LATEST
SUPPORTED_PROTOCOL_VERSIONS = set(McpVersion.SUPPORTED)

CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_STREAM = "text/event-stream"


class McpHttpClientTransport(McpClientTransport):
    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        path: str = "/",
        scheme: str = "http",
        authorization: Optional[McpAuthorizationClient] = None,
    ) -> None:
        assert path.startswith("/"), "HTTP path must start with '/'"
        self._path = path
        self._scheme = scheme
        self._endpoint = f"{scheme}://{hostname}:{port}{path}"
        self._server_to_client: asyncio.Queue[McpMessage] = asyncio.Queue()
        self._initialized: bool = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._context: McpClientContext | None = None
        self._authorization: Optional[McpAuthorizationClient] = authorization

    async def client_initialize(self, context: McpClientContext):
        assert not self._initialized, "HTTP client session is already initialized"
        self._initialized = True
        self._session = aiohttp.ClientSession()
        self._context = context

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._server_to_client.get()
            yield message

    async def _iter_sse_payloads(
        self, response: aiohttp.ClientResponse
    ) -> AsyncIterator[bytes]:
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        data_lines: list[str] = []

        def _process_line(line: str) -> bytes | None:
            nonlocal data_lines
            if line == "":
                if not data_lines:
                    return None
                payload = "\n".join(data_lines).encode("utf-8")
                data_lines = []
                return payload
            if line.startswith(":"):
                return None
            field, sep, value = line.partition(":")
            if not sep:
                return None
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            return None

        async for chunk in response.content.iter_chunked(4096):
            buffer += decoder.decode(chunk)
            while True:
                newline_index = buffer.find("\n")
                if newline_index < 0:
                    break
                line = buffer[:newline_index]
                buffer = buffer[newline_index + 1 :]
                if line.endswith("\r"):
                    line = line[:-1]
                payload = _process_line(line)
                if payload is not None:
                    yield payload

        buffer += decoder.decode(b"", final=True)
        if buffer:
            if buffer.endswith("\r"):
                buffer = buffer[:-1]
            payload = _process_line(buffer)
            if payload is not None:
                yield payload
        if data_lines:
            yield "\n".join(data_lines).encode("utf-8")

    async def _read_messages_from_response_body(
        self, response: aiohttp.ClientResponse, expected_response_id: int | str | None
    ) -> list[McpMessage]:
        content_type = response.headers.get(HEADER_CONTENT_TYPE, "").lower()
        if CONTENT_TYPE_STREAM not in content_type:
            body = await response.read()
            if not body:
                return []
            try:
                return [TypeAdapter(McpServerMessageUnion).validate_json(body)]
            except Exception:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} failed to parse response: {body.decode()}"
                )

        messages: list[McpMessage] = []
        async for payload in self._iter_sse_payloads(response):
            try:
                message = TypeAdapter(McpServerMessageUnion).validate_json(payload)
            except Exception:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} failed to parse SSE response event: {payload.decode()}"
                )
            messages.append(message)
            if (
                expected_response_id is not None
                and isinstance(message, (McpResponse, McpError))
                and message.id == expected_response_id
            ):
                break
        return messages

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

        if self._authorization is not None:
            token = await self._authorization.get_access_token()
            headers["Authorization"] = f"Bearer {token}"

        if isinstance(message, McpInitializeRequest):
            headers[HEADER_MCP_PROTOCOL_VERSION] = (
                message.params.protocolVersion or protocol_version
            )

        is_initialize_request = (
            isinstance(message, McpRequest) and message.method == McpMethod.INITIALIZE
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
            if context.flags.enforce_mcp_protocol_header and not protocol_header_value:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} server response missing {HEADER_MCP_PROTOCOL_VERSION}"
                )

            header_protocol_version = (
                protocol_header_value.strip() if protocol_header_value else None
            )

            if is_initialize_request:
                server_session_id = response.headers.get(HEADER_MCP_SESSION_ID)
                if not server_session_id and context.flags.enforce_mcp_session_header:
                    raise RuntimeError(
                        f"{McpHttpClientTransport.__name__} server did not provide {HEADER_MCP_SESSION_ID} during initialize"
                    )
                if server_session_id:
                    context.session_id = server_session_id.strip()

            expected_response_id = (
                message.id if isinstance(message, McpRequest) else None
            )
            response_messages = await self._read_messages_from_response_body(
                response, expected_response_id
            )

        if isinstance(message, McpRequest):
            structured_response: McpMessage | None = None
            for response_message in response_messages:
                await self._server_to_client.put(response_message)
                if (
                    isinstance(response_message, (McpResponse, McpError))
                    and response_message.id == message.id
                ):
                    structured_response = response_message

            if structured_response is None:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} response did not include matching ID {message.id!r}"
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

        else:
            for response_message in response_messages:
                await self._server_to_client.put(response_message)
        return True

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None


class McpHttpServerTransport(McpServerTransport):
    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        path: str = "/",
        authorization: Optional[McpAuthorizationServer] = None,
    ) -> None:
        assert path.startswith("/"), "HTTP path must start with '/'"
        self._path = path
        self._hostname: str = hostname
        self._port: int = port
        self._runner: Optional[web.AppRunner] = None
        self._client_to_server: asyncio.Queue[tuple[McpMessage, str | None]] = (
            asyncio.Queue()
        )
        self._inflight: Dict[
            tuple[str | None, str], asyncio.Future[McpResponseOrError]
        ] = {}
        self._context: McpServerContext | None = None
        self._authorization: Optional[McpAuthorizationServer] = authorization

    async def server_initialize(self, context: McpServerContext):
        if self._runner is not None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} is already initialized"
            )
        self._context = context
        _app = web.Application()
        _app.router.add_post(self._path, self._handle_post)
        if self._authorization is not None:
            self._authorization.bind(self._hostname, self._port, self._path)
            self._authorization.register_routes(_app)
        self._runner = web.AppRunner(_app)
        await self._runner.setup()
        _site = web.TCPSite(self._runner, self._hostname, self._port)
        await _site.start()

    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, str | None]]:
        while True:
            message_tuple = await self._client_to_server.get()
            yield message_tuple

    async def server_send_message(
        self, message: McpMessage, session_id: str | None = None
    ) -> bool:
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

        if self._authorization is not None:
            claims = self._authorization.validate_bearer_token(request)
            if claims is None:
                return self._authorization.create_401_response()

        request_body = await request.read()
        try:
            message = TypeAdapter(McpClientMessageUnion).validate_json(request_body)
        except Exception:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} failed to parse request: {request_body.decode()}"
            )

        protocol_version_header = request.headers.get(HEADER_MCP_PROTOCOL_VERSION)
        protocol_version_header = (
            protocol_version_header.strip() if protocol_version_header else ""
        )

        flags = self._context.flags

        if flags.enforce_mcp_protocol_header and not protocol_version_header:
            return web.Response(
                status=400,
                text=f"{HEADER_MCP_PROTOCOL_VERSION} header required",
            )

        if (
            protocol_version_header
            and protocol_version_header not in SUPPORTED_PROTOCOL_VERSIONS
        ):
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
    def __init__(
        self,
        hostname: str,
        port: int,
        path: str = "/",
        *,
        scheme: str = "http",
        authorization_client: Optional[McpAuthorizationClient] = None,
        authorization_server: Optional[McpAuthorizationServer] = None,
    ) -> None:
        self._client: McpHttpClientTransport = McpHttpClientTransport(
            hostname, port, path=path, scheme=scheme, authorization=authorization_client
        )
        self._server: McpHttpServerTransport = McpHttpServerTransport(
            hostname, port, path=path, authorization=authorization_server
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

    async def server_send_message(
        self, message: McpMessage, session_id: str | None = None
    ) -> bool:
        return await self._server.server_send_message(message, session_id)

    async def close(self):
        await self._client.close()
        await self._server.close()
