import asyncio
import json
import uuid
from typing import Any, Dict, List
from pydantic import TypeAdapter
from aiomcp.contracts.mcp_content import McpContent, McpTextContent
from aiomcp.mcp_context import McpClientContext
from aiomcp.mcp_flag import McpClientFlags
from aiomcp.mcp_server import McpServer
from aiomcp.mcp_transport_resolver import McpTransportResolver
from aiomcp.mcp_authorization import McpAuthorizationClient
from aiomcp.transports.base import McpClientTransport
from aiomcp.transports.direct import McpDirectClientTransport
from aiomcp.contracts.mcp_tool import McpTool
from aiomcp.jsonrpc_error_codes import JsonRpcErrorCodes
from aiomcp.contracts.mcp_message import (
    McpCallToolRequest,
    McpInitializeRequest,
    McpInitializeParams,
    McpInitializeResult,
    McpListToolsRequest,
    McpListToolsResult,
    McpRequest,
    McpResponse,
    McpError,
    McpResponseOrError,
    McpCallToolParams,
    McpNotification,
    McpMethod,
    McpCallToolResult,
    McpListToolsParams,
    McpServerRequest,
    McpSystemError,
    McpCancelledNotification,
    McpCancelledNotificationParams,
)


class McpInvokeError(RuntimeError):
    def __init__(self, tool_name: str, result: McpCallToolResult) -> None:
        self.tool_name = tool_name
        self.result = result
        super().__init__(f"{McpClient.__name__} tool '{tool_name}' returned an error")


class McpClient:
    def __init__(
        self,
        name: str = "aiomcp-client",
        request_timeout: float | None = 60.0,
        flags: Dict[str, bool] | None = None,
    ) -> None:
        self._context = McpClientContext(McpClientFlags.model_validate(flags or {}))
        self.name = name
        self.request_timeout = request_timeout
        self._initialized = False
        self._transport: McpClientTransport | None = None
        self._tools: Dict[str, McpTool] = {}
        self._server_capabilities: Dict[str, Any] = {}
        self._inflight: Dict[int | str, asyncio.Future[McpResponseOrError]] = {}
        self._message_loop: asyncio.Task | None = None

    def _generate_request_id(self) -> str:
        return uuid.uuid4().hex

    async def _notify_initialized(self) -> None:
        notification = McpNotification(
            method=McpMethod.NOTIFICATIONS_INITIALIZED,
            params=None,
        )
        await self._transport.client_send_message(notification)

    async def initialize(
        self,
        transport: McpClientTransport | McpServer | str,
        authorization: McpAuthorizationClient | None = None,
    ) -> None:
        if self._initialized:
            # TODO: lock instead?
            raise RuntimeError(f"{McpClient.__name__} is already initialized")

        if not isinstance(transport, McpClientTransport):
            if isinstance(transport, McpServer):
                transport = McpDirectClientTransport(transport)
            elif isinstance(transport, str):
                transport = McpTransportResolver.resolve(
                    transport, authorization_client=authorization
                )
                self._transport = transport
        self._transport = transport

        await self._transport.client_initialize(self._context)
        self._message_loop = asyncio.create_task(self._handle_message_loop())
        _ = await self._initialize_server()
        await self._notify_initialized()
        if (
            "tools" in self._server_capabilities
            or not self._context.flags.enforce_mcp_tools_capability
        ):
            await self._refresh_tools()
        else:
            self._tools = {}
        self._initialized = True

    async def close(self) -> None:
        loop_task = self._message_loop
        self._message_loop = None
        if loop_task is not None:
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        transport = self._transport
        self._transport = None
        if transport is not None:
            await transport.close()
        self._initialized = False

    def _check_initialized(self) -> None:
        # TODO: lazy initialize when project is stable.
        if not self._initialized:
            raise RuntimeError(f"{McpClient.__name__} not initialized")

    def _validate_system_error(self, msg: McpResponseOrError) -> None:
        if isinstance(msg, McpError) and self._context.flags.throw_mcp_contract_errors:
            if msg.error.code is None:
                raise ValueError(
                    "McpSystemError requires 'code', bypass by flag throw_mcp_contract_errors."
                )
            if msg.error.message is None:
                raise ValueError(
                    "McpSystemError requires 'message', bypass by flag throw_mcp_contract_errors."
                )

    async def _handle_message_loop(self) -> None:
        transport = self._transport
        if transport is None:
            self._fail_inflight(
                RuntimeError(f"{McpClient.__name__} transport is closed")
            )
            return

        try:
            async for msg in transport.client_messages():
                if isinstance(msg, McpResponseOrError):
                    self._validate_system_error(msg)
                    future = self._inflight.pop(msg.id, None)
                    if future is not None and not future.done():
                        future.set_result(msg)
                elif isinstance(msg, McpServerRequest):
                    await self._handle_server_request(msg)
                elif isinstance(msg, McpNotification):
                    self._handle_server_notification(msg)
        except asyncio.CancelledError:
            self._fail_inflight(
                RuntimeError(f"{McpClient.__name__} message loop was cancelled")
            )
            raise
        except Exception as exc:
            self._fail_inflight(
                RuntimeError(f"{McpClient.__name__} message loop failed: {exc}")
            )
        else:
            self._fail_inflight(
                RuntimeError(f"{McpClient.__name__} message stream closed unexpectedly")
            )

    async def _handle_server_request(self, request: McpServerRequest) -> None:
        transport = self._transport
        if transport is None:
            return
        if request.method == McpMethod.PING:
            response = McpResponse(id=request.id, result={})
        else:
            response = McpError(
                id=request.id,
                error=McpSystemError(
                    code=JsonRpcErrorCodes.METHOD_NOT_FOUND,
                    message=f"{McpClient.__name__} does not support server request method {request.method}",
                ),
            )
        await transport.client_send_message(response)

    def _handle_server_notification(self, notification: McpNotification) -> None:
        return

    def _fail_inflight(self, exc: BaseException) -> None:
        pending = list(self._inflight.values())
        self._inflight.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _notify_cancelled(
        self, request: McpRequest, request_timeout: float | None
    ) -> None:
        transport = self._transport
        if transport is None or request.method == McpMethod.INITIALIZE:
            return
        reason = (
            f"Request timed out after {request_timeout} seconds"
            if request_timeout is not None
            else "Request cancelled"
        )
        notification = McpCancelledNotification(
            params=McpCancelledNotificationParams(requestId=request.id, reason=reason)
        )
        try:
            await transport.client_send_message(notification)
        except Exception:
            pass

    async def _generate_response_future(
        self, request_id: int | str
    ) -> asyncio.Future[McpResponseOrError]:
        existing = self._inflight.get(request_id)
        if existing is not None and not existing.done():
            raise RuntimeError(
                f"{McpClient.__name__} already has an inflight request with ID {request_id!r}"
            )
        future = asyncio.get_running_loop().create_future()
        self._inflight[request_id] = future
        return future

    async def _process(
        self,
        request: McpRequest,
        timeout: float | None = None,
    ) -> McpResponseOrError:
        transport = self._transport
        if transport is None:
            raise RuntimeError(f"{McpClient.__name__} transport is closed")
        if self._message_loop is not None and self._message_loop.done():
            raise RuntimeError(f"{McpClient.__name__} message loop is not running")

        future = await self._generate_response_future(request.id)
        request_timeout = self.request_timeout if timeout is None else timeout
        try:
            await transport.client_send_message(request)
            if request_timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=request_timeout)
        except asyncio.TimeoutError as exc:
            self._inflight.pop(request.id, None)
            await self._notify_cancelled(request, request_timeout)
            raise TimeoutError(
                f"{McpClient.__name__} request {request.method!s} with ID {request.id!r} timed out after {request_timeout} seconds"
            ) from exc
        except Exception:
            self._inflight.pop(request.id, None)
            raise

    async def _initialize_server(self) -> McpInitializeResult:
        request_id = self._generate_request_id()
        request = McpInitializeRequest(
            id=request_id,
            params=McpInitializeParams(
                capabilities={},
                protocolVersion=self._context.version.default_version,
                clientInfo={"name": self.name, "version": "0.0.0"},
            ),
        )
        response = await self._process(request)
        if isinstance(response, McpError):
            raise RuntimeError(
                f"{McpClient.__name__} failed to initialize, {response.error.model_dump_json()}"
            )
        try:
            structured = McpInitializeResult.model_validate(response.result)
        except Exception as e:
            raise RuntimeError(
                f"{McpClient.__name__} failed to parse initialize result: {e}"
            )

        self._context.version.sync_as_client(
            structured.protocolVersion,
            enforce_negotiation=self._context.flags.enforce_mcp_version_negotiation,
            enforce_consistency=self._context.flags.enforce_mcp_transport_version_consistency,
        )
        self._server_capabilities = structured.capabilities or {}

        return structured

    async def _refresh_tools(self) -> List[McpTool]:
        tools: List[McpTool] = []
        cursor: str | None = None
        while True:
            request_id = self._generate_request_id()
            request = McpListToolsRequest(
                id=request_id,
                params=(
                    McpListToolsParams(cursor=cursor) if cursor is not None else None
                ),
            )
            response = await self._process(request)
            if isinstance(response, McpError):
                raise RuntimeError(
                    f"{McpClient.__name__} failed to list tools, {response.error.model_dump_json()}"
                )
            try:
                structured = McpListToolsResult.model_validate(response.result)
            except Exception as e:
                raise RuntimeError(
                    f"{McpClient.__name__} failed to parse tools list: {e}"
                )
            tools.extend(structured.tools)
            cursor = structured.nextCursor
            if cursor is None:
                break
        # Populate internal lookup for subsequent calls
        self._tools = {t.name: t for t in tools}
        return tools

    async def mcp_tools_list(self) -> List[McpTool]:
        self._check_initialized()
        return list(self._tools.values())

    async def mcp_tools_call(
        self,
        tool: McpTool,
        request: McpCallToolRequest,
        bypass_client_validation: bool = False,
        timeout: float | None = None,
    ) -> McpResponseOrError:
        self._check_initialized()

        response = await self._process(request, timeout=timeout)

        if (
            isinstance(response, McpResponse)
            and tool.outputSchema is not None
            and not bypass_client_validation
        ):
            try:
                # TODO: add output schema validation (as a whole)
                pass
            except Exception as e:
                raise RuntimeError(
                    f"{McpClient.__name__} output validation failed for tool '{tool.name}': {e}"
                )

        return response

    async def invoke(
        self,
        tool_name: str,
        arguments: Any,
        timeout: float | None = None,
        *,
        use_structured_content: bool = False,
        convert_mcp_tool_result_content_format: bool = False,
    ) -> Any:
        tool = self._tools.get(tool_name)
        result = await self.invoke_result(tool_name, arguments, timeout=timeout)
        if result.isError:
            raise McpInvokeError(tool_name, result)
        coerced_result = self._coerce_tool_result(
            result,
            output_schema_enabled=tool is not None and tool.outputSchema is not None,
            use_structured_content=use_structured_content,
            convert_mcp_tool_result_content_format=convert_mcp_tool_result_content_format,
        )
        return coerced_result

    async def invoke_result(
        self, tool_name: str, arguments: Any, timeout: float | None = None
    ) -> McpCallToolResult:
        self._check_initialized()
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"{McpClient.__name__} cannot find tool: {tool_name}")

        request_id = self._generate_request_id()
        request = McpCallToolRequest(
            id=request_id,
            params=McpCallToolParams(name=tool.name, arguments=arguments),
        )

        response = await self.mcp_tools_call(tool, request, timeout=timeout)

        if isinstance(response, McpError):
            raise RuntimeError(
                f"{McpClient.__name__} error occurred, {response.error.model_dump_json()}"
            )
        try:
            result = McpCallToolResult.model_validate(response.result)
        except Exception as e:
            raise RuntimeError(f"{McpClient.__name__} failed to parse tool result: {e}")
        if (
            self._context.flags.enforce_mcp_tool_result_content
            and result.content is None
        ):
            raise RuntimeError(
                f"{McpClient.__name__} tool result missing required content"
            )
        return result

    @staticmethod
    def _as_text_content(value: Any) -> List[McpContent]:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except TypeError:
                text = str(value)
        return [McpTextContent(text=text)]

    @staticmethod
    def _as_content_blocks(value: Any) -> List[McpContent]:
        if isinstance(value, list):
            result: List[McpContent] = []
            for item in value:
                try:
                    content: McpContent = TypeAdapter(McpContent).validate_python(item)
                    result.append(content)
                except Exception:
                    result.extend(McpClient._as_text_content(item))
            return result

        try:
            content: McpContent = TypeAdapter(McpContent).validate_python(value)
            return [content]
        except Exception:
            return McpClient._as_text_content(value)

    def _coerce_tool_result(
        self,
        structured: McpCallToolResult,
        *,
        output_schema_enabled: bool = False,
        use_structured_content: bool = False,
        convert_mcp_tool_result_content_format: bool = False,
    ) -> Any:
        if output_schema_enabled or use_structured_content:
            return structured.structuredContent

        content = structured.content
        structured_content = structured.structuredContent

        if convert_mcp_tool_result_content_format:
            result = [] if content is None else self._as_content_blocks(content)
            if structured_content is not None:
                result.extend(self._as_content_blocks(structured_content))
            return result or None

        if content is None and structured_content is None:
            return None
        if content is None:
            return structured_content
        if structured_content is None:
            return content

        result = self._as_content_blocks(content)
        result.extend(self._as_text_content(structured_content))
        return result
