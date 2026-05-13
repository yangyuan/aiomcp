import asyncio
import codecs
import uuid
from typing import AsyncIterator, Dict, Mapping, Optional

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
HEADER_LAST_EVENT_ID = "last-event-id"
DEFAULT_PROTOCOL_VERSION = McpVersion.LATEST
SUPPORTED_PROTOCOL_VERSIONS = set(McpVersion.SUPPORTED)

CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_STREAM = "text/event-stream"


# SSE event tuple fields: data payload, event ID, retry delay in milliseconds.
_SseEvent = tuple[Optional[bytes], Optional[str], Optional[int]]


class _HttpResponseMessages:
    def __init__(
        self,
        messages: list[McpMessage],
        last_event_id: Optional[str] = None,
        retry_ms: Optional[int] = None,
        matched_response: bool = False,
    ) -> None:
        self.messages = messages
        self.last_event_id = last_event_id
        self.retry_ms = retry_ms
        self.matched_response = matched_response


class McpHttpClientTransport(McpClientTransport):
    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        path: str = "/",
        scheme: str = "http",
        authorization: Optional[McpAuthorizationClient] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        assert path.startswith("/"), "HTTP path must start with '/'"
        self._path = path
        self._scheme = scheme
        self._endpoint = f"{scheme}://{hostname}:{port}{path}"
        self._server_to_client: asyncio.Queue[McpMessage] = asyncio.Queue()
        self._initialized: bool = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._context: Optional[McpClientContext] = None
        self._authorization: Optional[McpAuthorizationClient] = authorization
        self._headers: Dict[str, str] = dict(headers or {})
        self._initialize_request: Optional[McpInitializeRequest] = None
        self._initialized_notification: Optional[McpNotification] = None
        self._server_message_stream_task: Optional[asyncio.Task[None]] = None
        self._server_message_stream_disabled = False
        self._server_message_stream_last_event_id: Optional[str] = None
        self._server_message_stream_retry_ms = 1000

    async def client_initialize(self, context: McpClientContext):
        assert not self._initialized, "HTTP client session is already initialized"
        self._initialized = True
        self._session = aiohttp.ClientSession()
        self._context = context

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._server_to_client.get()
            yield message

    async def _iter_sse_events(
        self, response: aiohttp.ClientResponse
    ) -> AsyncIterator[_SseEvent]:
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        data_lines: list[str] = []
        event_id: Optional[str] = None
        retry_ms: Optional[int] = None

        def _process_line(line: str) -> Optional[_SseEvent]:
            nonlocal data_lines, event_id, retry_ms
            if line == "":
                if not data_lines and event_id is None and retry_ms is None:
                    return None
                payload = "\n".join(data_lines).encode("utf-8") if data_lines else None
                event = (payload, event_id, retry_ms)
                data_lines = []
                event_id = None
                retry_ms = None
                return event
            if line.startswith(":"):
                return None
            field, sep, value = line.partition(":")
            if not sep:
                return None
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field == "id":
                event_id = value
            elif field == "retry":
                try:
                    retry_ms = int(value)
                except ValueError:
                    pass
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
                event = _process_line(line)
                if event is not None:
                    yield event

        buffer += decoder.decode(b"", final=True)
        if buffer:
            if buffer.endswith("\r"):
                buffer = buffer[:-1]
            event = _process_line(buffer)
            if event is not None:
                yield event
        if data_lines or event_id is not None or retry_ms is not None:
            payload = "\n".join(data_lines).encode("utf-8") if data_lines else None
            yield (payload, event_id, retry_ms)

    async def _read_messages_from_response_body(
        self,
        response: aiohttp.ClientResponse,
        expected_response_id: Optional[int | str],
    ) -> _HttpResponseMessages:
        content_type = response.headers.get(HEADER_CONTENT_TYPE, "").lower()
        if CONTENT_TYPE_STREAM not in content_type:
            body = await response.read()
            if not body:
                return _HttpResponseMessages(messages=[])
            try:
                message = TypeAdapter(McpServerMessageUnion).validate_json(body)
                matched_response = (
                    expected_response_id is not None
                    and isinstance(message, (McpResponse, McpError))
                    and message.id == expected_response_id
                )
                return _HttpResponseMessages(
                    messages=[message], matched_response=matched_response
                )
            except Exception:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} failed to parse response: {body.decode()}"
                )

        messages: list[McpMessage] = []
        last_event_id: Optional[str] = None
        retry_ms: Optional[int] = None
        matched_response = False
        async for event_data, event_id, event_retry_ms in self._iter_sse_events(
            response
        ):
            if event_id is not None:
                last_event_id = event_id
            if event_retry_ms is not None:
                retry_ms = event_retry_ms
            if not event_data:
                continue
            try:
                message = TypeAdapter(McpServerMessageUnion).validate_json(event_data)
            except Exception:
                raise RuntimeError(
                    f"{McpHttpClientTransport.__name__} failed to parse SSE response event: {event_data.decode()}"
                )
            messages.append(message)
            if (
                expected_response_id is not None
                and isinstance(message, (McpResponse, McpError))
                and message.id == expected_response_id
            ):
                matched_response = True
                break
        return _HttpResponseMessages(
            messages=messages,
            last_event_id=last_event_id,
            retry_ms=retry_ms,
            matched_response=matched_response,
        )

    async def _build_headers(
        self,
        message: Optional[McpMessage],
        *,
        accept: str,
        include_content_type: bool = False,
        include_session: bool = True,
        last_event_id: Optional[str] = None,
    ) -> dict[str, str]:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot send messages before initialization"
            )
        context = self._context
        protocol_version = context.version.version or DEFAULT_PROTOCOL_VERSION
        headers = dict(self._headers)
        headers.update(
            {
                HEADER_ACCEPT: accept,
                HEADER_MCP_PROTOCOL_VERSION: protocol_version,
            }
        )
        if include_content_type:
            headers[HEADER_CONTENT_TYPE] = CONTENT_TYPE_JSON
        if self._authorization is not None:
            token = await self._authorization.get_access_token()
            headers["Authorization"] = f"Bearer {token}"
        if isinstance(message, McpInitializeRequest):
            headers[HEADER_MCP_PROTOCOL_VERSION] = (
                message.params.protocolVersion or protocol_version
            )
        if include_session and context.session_id:
            headers[HEADER_MCP_SESSION_ID] = context.session_id
        if last_event_id is not None:
            headers[HEADER_LAST_EVENT_ID] = last_event_id
        return headers

    async def _send_post_once(
        self,
        message: McpMessage,
        *,
        include_session: bool = True,
        recover_on_session_expiry: bool = True,
    ) -> tuple[_HttpResponseMessages, Optional[str]]:
        assert self._session is not None
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot send messages before initialization"
            )

        context = self._context
        payload = message.model_dump_json(exclude_none=True, by_alias=True)
        headers = await self._build_headers(
            message,
            accept=f"{CONTENT_TYPE_JSON}, {CONTENT_TYPE_STREAM}",
            include_content_type=True,
            include_session=include_session,
        )
        sent_session_id = headers.get(HEADER_MCP_SESSION_ID)
        is_initialize_request = (
            isinstance(message, McpRequest) and message.method == McpMethod.INITIALIZE
        )

        async with self._session.post(
            self._endpoint,
            data=payload,
            headers=headers,
        ) as response:
            if response.status == 404 and sent_session_id and recover_on_session_expiry:
                await response.read()
                await self._recover_http_session()
                return await self._send_post_once(
                    message,
                    include_session=True,
                    recover_on_session_expiry=False,
                )

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

        if (
            isinstance(message, McpRequest)
            and not response_messages.matched_response
            and response_messages.last_event_id is not None
        ):
            resumed = await self._resume_sse_until_response(
                message.id,
                response_messages.last_event_id,
                response_messages.retry_ms,
            )
            response_messages.messages.extend(resumed.messages)
            response_messages.last_event_id = resumed.last_event_id
            response_messages.retry_ms = resumed.retry_ms
            response_messages.matched_response = resumed.matched_response

        return response_messages, header_protocol_version

    async def _resume_sse_until_response(
        self,
        expected_response_id: int | str,
        last_event_id: str,
        retry_ms: Optional[int],
    ) -> _HttpResponseMessages:
        collected = _HttpResponseMessages(messages=[])
        while True:
            if retry_ms is not None:
                await asyncio.sleep(retry_ms / 1000)
            response_messages = await self._read_sse_resume_once(
                expected_response_id=expected_response_id,
                last_event_id=last_event_id,
                recover_on_session_expiry=True,
            )
            collected.messages.extend(response_messages.messages)
            collected.last_event_id = response_messages.last_event_id
            collected.retry_ms = response_messages.retry_ms
            collected.matched_response = response_messages.matched_response
            if (
                response_messages.matched_response
                or response_messages.last_event_id is None
            ):
                return collected
            last_event_id = response_messages.last_event_id
            retry_ms = response_messages.retry_ms

    async def _read_sse_resume_once(
        self,
        *,
        expected_response_id: Optional[int | str] = None,
        last_event_id: Optional[str] = None,
        recover_on_session_expiry: bool = False,
    ) -> _HttpResponseMessages:
        assert self._session is not None
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot read SSE before initialization"
            )

        headers = await self._build_headers(
            None,
            accept=CONTENT_TYPE_STREAM,
            include_session=True,
            last_event_id=last_event_id,
        )
        sent_session_id = headers.get(HEADER_MCP_SESSION_ID)
        async with self._session.get(self._endpoint, headers=headers) as response:
            if response.status == 405:
                await response.read()
                self._server_message_stream_disabled = True
                return _HttpResponseMessages(messages=[])
            if response.status == 404 and sent_session_id and recover_on_session_expiry:
                await response.read()
                await self._recover_http_session()
                return _HttpResponseMessages(messages=[])
            response.raise_for_status()
            content_type = response.headers.get(HEADER_CONTENT_TYPE, "").lower()
            if CONTENT_TYPE_STREAM not in content_type:
                await response.read()
                return _HttpResponseMessages(messages=[])
            return await self._read_messages_from_response_body(
                response, expected_response_id
            )

    async def _consume_server_message_stream_once(self) -> None:
        assert self._session is not None
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot read SSE before initialization"
            )

        headers = await self._build_headers(
            None,
            accept=CONTENT_TYPE_STREAM,
            include_session=True,
            last_event_id=self._server_message_stream_last_event_id,
        )
        sent_session_id = headers.get(HEADER_MCP_SESSION_ID)
        async with self._session.get(self._endpoint, headers=headers) as response:
            if response.status == 405:
                await response.read()
                self._server_message_stream_disabled = True
                return
            if response.status == 404 and sent_session_id:
                await response.read()
                await self._recover_http_session()
                return
            response.raise_for_status()
            content_type = response.headers.get(HEADER_CONTENT_TYPE, "").lower()
            if CONTENT_TYPE_STREAM not in content_type:
                await response.read()
                return
            async for event_data, event_id, event_retry_ms in self._iter_sse_events(
                response
            ):
                if event_id is not None:
                    self._server_message_stream_last_event_id = event_id
                if event_retry_ms is not None:
                    self._server_message_stream_retry_ms = event_retry_ms
                if not event_data:
                    continue
                try:
                    message = TypeAdapter(McpServerMessageUnion).validate_json(
                        event_data
                    )
                except Exception:
                    continue
                await self._server_to_client.put(message)

    async def _handle_client_response_messages(
        self,
        message: McpMessage,
        response_messages: _HttpResponseMessages,
        header_protocol_version: Optional[str],
        *,
        enqueue_messages: bool = True,
    ) -> None:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot handle responses before initialization"
            )
        context = self._context
        if isinstance(message, McpRequest):
            structured_response: Optional[McpMessage] = None
            for response_message in response_messages.messages:
                if enqueue_messages:
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
            is_initialize_request = message.method == McpMethod.INITIALIZE
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
        elif enqueue_messages:
            for response_message in response_messages.messages:
                await self._server_to_client.put(response_message)

    async def _recover_http_session(self) -> None:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot recover session before initialization"
            )
        if self._initialize_request is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot recover expired session before initialize"
            )
        self._context.session_id = None
        response_messages, header_protocol_version = await self._send_post_once(
            self._initialize_request,
            include_session=False,
            recover_on_session_expiry=False,
        )
        await self._handle_client_response_messages(
            self._initialize_request,
            response_messages,
            header_protocol_version,
            enqueue_messages=False,
        )
        if self._initialized_notification is not None:
            response_messages, header_protocol_version = await self._send_post_once(
                self._initialized_notification,
                include_session=True,
                recover_on_session_expiry=False,
            )
            await self._handle_client_response_messages(
                self._initialized_notification,
                response_messages,
                header_protocol_version,
                enqueue_messages=False,
            )
        self._server_message_stream_last_event_id = None
        self._server_message_stream_disabled = False

    def _ensure_server_message_stream_task(self) -> None:
        if self._server_message_stream_disabled:
            return
        if (
            self._server_message_stream_task is not None
            and not self._server_message_stream_task.done()
        ):
            return
        self._server_message_stream_task = asyncio.create_task(
            self._run_server_message_stream_loop()
        )

    async def _run_server_message_stream_loop(self) -> None:
        try:
            while (
                self._session is not None and not self._server_message_stream_disabled
            ):
                try:
                    await self._consume_server_message_stream_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                if not self._server_message_stream_disabled:
                    await asyncio.sleep(self._server_message_stream_retry_ms / 1000)
        except asyncio.CancelledError:
            raise

    async def client_send_message(self, message: McpMessage) -> bool:
        assert self._session is not None
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpClientTransport.__name__} cannot send messages before initialization"
            )
        if isinstance(message, McpInitializeRequest):
            self._initialize_request = message
        elif (
            isinstance(message, McpNotification)
            and message.method == McpMethod.NOTIFICATIONS_INITIALIZED
        ):
            self._initialized_notification = message

        response_messages, header_protocol_version = await self._send_post_once(message)
        await self._handle_client_response_messages(
            message, response_messages, header_protocol_version
        )
        if isinstance(message, McpInitializeRequest):
            self._ensure_server_message_stream_task()
        return True

    async def close(self):
        if self._server_message_stream_task is not None:
            self._server_message_stream_task.cancel()
            try:
                await self._server_message_stream_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._server_message_stream_task = None
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
        self._client_to_server: asyncio.Queue[tuple[McpMessage, Optional[str]]] = (
            asyncio.Queue()
        )
        self._inflight: Dict[
            tuple[Optional[str], str], asyncio.Future[McpResponseOrError]
        ] = {}
        self._sse_streams: Dict[
            Optional[str], list[asyncio.Queue[Optional[McpMessage]]]
        ] = {}
        self._sse_event_counter = 0
        self._context: Optional[McpServerContext] = None
        self._authorization: Optional[McpAuthorizationServer] = authorization

    async def server_initialize(self, context: McpServerContext):
        if self._runner is not None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} is already initialized"
            )
        self._context = context
        _app = web.Application()
        _app.router.add_post(self._path, self._handle_post)
        _app.router.add_get(self._path, self._handle_get)
        _app.router.add_delete(self._path, self._handle_delete)
        if self._authorization is not None:
            self._authorization.bind(self._hostname, self._port, self._path)
            self._authorization.register_routes(_app)
        self._runner = web.AppRunner(_app)
        await self._runner.setup()
        _site = web.TCPSite(self._runner, self._hostname, self._port)
        await _site.start()

    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, Optional[str]]]:
        while True:
            message_tuple = await self._client_to_server.get()
            yield message_tuple

    async def server_send_message(
        self, message: McpMessage, session_id: Optional[str] = None
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
        streams = self._sse_streams.get(session_id) or []
        if streams:
            await streams[0].put(message)
            return True
        return False

    def _next_sse_event_id(self, session_id: Optional[str]) -> str:
        self._sse_event_counter += 1
        session_key = session_id or "default"
        return f"{session_key}:{self._sse_event_counter}"

    async def _write_sse_event(
        self,
        response: web.StreamResponse,
        *,
        message: Optional[McpMessage] = None,
        event_id: Optional[str] = None,
        retry_ms: Optional[int] = None,
    ) -> None:
        lines: list[str] = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        if retry_ms is not None:
            lines.append(f"retry: {retry_ms}")
        if message is None:
            lines.append("data: ")
        else:
            payload = message.model_dump_json(exclude_none=True, by_alias=True)
            for line in payload.splitlines() or [payload]:
                lines.append(f"data: {line}")
        await response.write(("\n".join(lines) + "\n\n").encode("utf-8"))

    def _validate_session_request(self, request: web.Request) -> Optional[web.Response]:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} not initialized with context"
            )

        if self._authorization is not None:
            claims = self._authorization.validate_bearer_token(request)
            if claims is None:
                return self._authorization.create_401_response()

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
        session_id = request.headers.get(HEADER_MCP_SESSION_ID)
        if session_id and self._context.is_session_expired(session_id):
            return web.Response(status=404)
        if flags.enforce_mcp_session_header and not session_id:
            return web.Response(
                status=400,
                text=f"{HEADER_MCP_SESSION_ID} header required",
            )
        return None

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
        if session_id and self._context.is_session_expired(session_id):
            return web.Response(status=404)
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

        if isinstance(message, McpResponseOrError):
            protocol_header_value = session.version.version or DEFAULT_PROTOCOL_VERSION
            headers: Dict[str, str] = {}
            if protocol_header_value:
                headers[HEADER_MCP_PROTOCOL_VERSION] = protocol_header_value
            return web.Response(status=202, headers=headers)
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
            resp_obj = web.json_response(response.model_dump(by_alias=True))

            session = self._context.get_session(session_id)
            protocol_header_value = session.version.version or request_protocol_version
            resp_obj.headers[HEADER_MCP_PROTOCOL_VERSION] = (
                protocol_header_value or DEFAULT_PROTOCOL_VERSION
            )

            if message.method == McpMethod.INITIALIZE and session_id:
                resp_obj.headers[HEADER_MCP_SESSION_ID] = session_id
            return resp_obj

    async def _handle_get(self, request: web.Request) -> web.StreamResponse:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} not initialized with context"
            )

        error_response = self._validate_session_request(request)
        if error_response is not None:
            return error_response

        session_id = request.headers.get(HEADER_MCP_SESSION_ID)
        session = self._context.get_session(session_id)
        protocol_header_value = session.version.version or DEFAULT_PROTOCOL_VERSION
        response = web.StreamResponse(
            status=200,
            headers={
                HEADER_CONTENT_TYPE: CONTENT_TYPE_STREAM,
                HEADER_MCP_PROTOCOL_VERSION: protocol_header_value,
            },
        )
        await response.prepare(request)

        queue: asyncio.Queue[Optional[McpMessage]] = asyncio.Queue()
        streams = self._sse_streams.setdefault(session_id, [])
        streams.append(queue)
        try:
            await self._write_sse_event(
                response,
                event_id=self._next_sse_event_id(session_id),
            )
            while True:
                message = await queue.get()
                if message is None:
                    break
                await self._write_sse_event(
                    response,
                    message=message,
                    event_id=self._next_sse_event_id(session_id),
                )
        except (asyncio.CancelledError, ConnectionResetError):
            raise
        finally:
            streams = self._sse_streams.get(session_id)
            if streams is not None and queue in streams:
                streams.remove(queue)
                if not streams:
                    self._sse_streams.pop(session_id, None)
        return response

    async def _handle_delete(self, request: web.Request) -> web.Response:
        if self._context is None:
            raise RuntimeError(
                f"{McpHttpServerTransport.__name__} not initialized with context"
            )

        error_response = self._validate_session_request(request)
        if error_response is not None:
            return error_response

        session_id = request.headers.get(HEADER_MCP_SESSION_ID)
        self._context.terminate_session(session_id)
        streams = self._sse_streams.pop(session_id, [])
        for stream in streams:
            await stream.put(None)
        return web.Response(status=202)

    async def close(self):
        streams = list(self._sse_streams.values())
        self._sse_streams.clear()
        for stream_list in streams:
            for stream in stream_list:
                await stream.put(None)
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
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._client: McpHttpClientTransport = McpHttpClientTransport(
            hostname,
            port,
            path=path,
            scheme=scheme,
            authorization=authorization_client,
            headers=headers,
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

    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, Optional[str]]]:
        async for message in self._server.server_messages():
            yield message

    async def server_send_message(
        self, message: McpMessage, session_id: Optional[str] = None
    ) -> bool:
        return await self._server.server_send_message(message, session_id)

    async def close(self):
        await self._client.close()
        await self._server.close()
