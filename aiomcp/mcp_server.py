import asyncio
import hashlib
import inspect
import json
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
    List,
)

from pydantic import BaseModel, TypeAdapter
from aiomcp.contracts.mcp_content import McpContent
from aiomcp.contracts.mcp_schema import JsonSchema
from aiomcp.mcp_flag import McpServerFlags
from aiomcp.mcp_context import (
    McpServerContext,
    McpSession,
    McpSessionStatus,
)
from aiomcp.mcp_authorization import McpAuthorizationServer
from aiomcp.mcp_schema_resolver import McpSchemaResolver
from aiomcp.mcp_transport_resolver import McpTransportResolver
from aiomcp.transports.base import McpServerTransport
from aiomcp.contracts.mcp_tool import McpTool, McpToolAnnotations, McpToolIcon
from aiomcp.jsonrpc_error_codes import JsonRpcErrorCodes as McpErrorCodes
from aiomcp.contracts.mcp_message import (
    McpCallToolRequest,
    McpResponseOrError,
    McpSystemError,
    McpInitializeResult,
    McpListToolsRequest,
    McpRequest,
    McpResponse,
    McpError,
    McpMethod,
    McpCallToolResult,
    McpListToolsResult,
    McpInitializeRequest,
    McpInitializedNotification,
    McpCancelledNotification,
    McpServerRequest,
)
from aiomcp.mcp_version import McpVersion


class McpCallableTool(McpTool):
    callable_async: Callable[..., Awaitable]


class McpServer:
    TOOLS_PAGE_SIZE: int = 128
    STRUCTURED_CONTENT_HINT: str = (
        "Tool returned structured content. See structuredContent."
    )

    def __init__(
        self,
        name: str = "aiomcp-server",
        flags: Dict[str, bool] | None = None,
    ) -> None:
        self._context = McpServerContext(McpServerFlags.model_validate(flags or {}))
        self.name = name
        self._tools: Dict[str, McpCallableTool] = {}
        self._hosting: bool = False  # TODO: remove once enable multiple hosting.
        self._message_loop: Optional[asyncio.Task] = None
        self._server_transport: Optional[McpServerTransport] = None

    def _get_session(self, session_id: Optional[str]) -> McpSession:
        return self._context.get_session(session_id)

    async def register_tool(
        self,
        func: Callable,
        alias: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        annotations: Optional[Dict[str, Any] | McpToolAnnotations] = None,
        icons: Optional[List[Dict[str, Any] | McpToolIcon]] = None,
        format_map: Optional[Dict[str, Any]] = None,
    ) -> None:
        func_name, input_schema, output_schema = McpSchemaResolver.resolve(
            func,
            format_map=format_map,
            auto_mcp_tool_output_schema=self._context.flags.auto_mcp_tool_output_schema,
        )
        if description is not None and format_map is not None:
            description = description.format_map(format_map)

        await self.mcp_tools_register(
            name=alias or func_name,
            func=func,
            input_schema=input_schema,
            output_schema=output_schema,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
        )

    async def mcp_tools_register(
        self,
        name: str,
        func: Callable,
        input_schema: Dict[str, Any],
        output_schema: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        annotations: Optional[Dict[str, Any] | McpToolAnnotations] = None,
        icons: Optional[List[Dict[str, Any] | McpToolIcon]] = None,
    ) -> None:
        validated_input_schema: Optional[JsonSchema] = None
        validated_output_schema: Optional[JsonSchema] = None
        tool_annotations: Optional[McpToolAnnotations] = None
        tool_icons: Optional[List[McpToolIcon]] = None
        try:
            validated_input_schema = JsonSchema.model_validate(input_schema)
        except Exception:
            validated_input_schema = None
        if output_schema is not None:
            try:
                validated_output_schema = JsonSchema.model_validate(output_schema)
            except Exception:
                validated_output_schema = None
        if annotations is not None:
            try:
                tool_annotations = McpToolAnnotations.model_validate(annotations)
            except Exception:
                tool_annotations = None
        if icons is not None:
            try:
                tool_icons = [
                    (
                        icon
                        if isinstance(icon, McpToolIcon)
                        else McpToolIcon.model_validate(icon)
                    )
                    for icon in icons
                ]
            except Exception:
                tool_icons = None

        if not inspect.iscoroutinefunction(func):
            # tolerate sync functions by wrapping with async
            async def _async_wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            callable_async = _async_wrapper
        else:
            callable_async = func

        _tool = McpCallableTool(
            name=name,
            title=title,
            description=description,
            inputSchema=validated_input_schema,
            outputSchema=validated_output_schema,
            annotations=tool_annotations,
            icons=tool_icons,
            callable_async=callable_async,
        )
        self._tools[name] = _tool

    async def list_tools(self) -> List[McpTool]:
        return list(self._tools.values())

    def _tools_fingerprint(self, sorted_names: List[str]) -> str:
        digest = hashlib.sha256("\0".join(sorted_names).encode("utf-8")).hexdigest()
        return digest[:16]

    def _paginate_tools(
        self, cursor: Optional[str]
    ) -> tuple[Optional[List[McpCallableTool]], Optional[str]]:
        names = sorted(self._tools.keys())
        fingerprint = self._tools_fingerprint(names)
        start = 0
        if cursor is not None:
            try:
                cursor_fingerprint, index_str = cursor.split(":", 1)
                start = int(index_str)
            except (ValueError, AttributeError):
                return None, None
            if cursor_fingerprint != fingerprint or start < 0 or start > len(names):
                return None, None
        end = start + self.TOOLS_PAGE_SIZE
        page = [self._tools[n] for n in names[start:end]]
        next_cursor = f"{fingerprint}:{end}" if end < len(names) else None
        return page, next_cursor

    def _server_capabilities(self) -> Dict[str, Any]:
        return {"tools": {}}

    async def _create_host_task(
        self,
        transport: McpServerTransport | str,
        authorization: Optional[McpAuthorizationServer] = None,
    ) -> asyncio.Task:
        if self._hosting:
            raise RuntimeError(f"{McpServer.__name__} is already hosting")
        self._hosting = True

        if isinstance(transport, str):
            transport = McpTransportResolver.resolve(
                transport,
                authorization_server=authorization,
            )
        self._server_transport = transport

        await transport.server_initialize(self._context)

        if self._message_loop is None:
            self._message_loop = asyncio.create_task(
                self._handle_message_loop(transport)
            )
        return self._message_loop

    async def host(
        self,
        transport: McpServerTransport | str,
        authorization: Optional[McpAuthorizationServer] = None,
    ) -> None:
        task = await self._create_host_task(transport, authorization=authorization)
        await task

    async def shutdown(self) -> None:
        self._hosting = False
        task = self._message_loop
        self._message_loop = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        for session in self._context.iter_sessions():
            tasks = list(session.request_tasks.values())
            session.request_tasks.clear()
            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

        transport = self._server_transport
        self._server_transport = None
        if transport is not None:
            await transport.close()

    def _to_kwargs(
        self, func: Callable[..., Awaitable], arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return arguments
        _kwargs: Dict[str, Any] = dict(arguments)
        for name, parameter in signature.parameters.items():
            if name in _kwargs:
                annotation = parameter.annotation
                try:
                    _kwargs[name] = TypeAdapter(annotation).validate_python(
                        _kwargs[name]
                    )
                except Exception:
                    pass
        return _kwargs

    async def _call_tool(self, tool: McpCallableTool, arguments: Any) -> Any:
        if arguments is None:
            return await tool.callable_async()
        if isinstance(arguments, dict):
            _kwargs = self._to_kwargs(tool.callable_async, arguments)
            return await tool.callable_async(**_kwargs)
        return await tool.callable_async(arguments)

    @staticmethod
    def _to_jsonable(value: Any) -> Any:
        if isinstance(value, Enum):
            return McpServer._to_jsonable(value.value)
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {
                McpServer._to_jsonable(key): McpServer._to_jsonable(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [McpServer._to_jsonable(item) for item in value]
        try:
            return TypeAdapter(Any).dump_python(value, mode="json")
        except Exception:
            return value

    @staticmethod
    def _as_text_content(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except TypeError:
                text = str(value)
        return [{"type": "text", "text": text}]

    @staticmethod
    def _structured_content_hint() -> List[Dict[str, Any]]:
        return McpServer._as_text_content(McpServer.STRUCTURED_CONTENT_HINT)

    @staticmethod
    def _as_content_blocks(value: Any) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(value, list):
            return None
        try:
            TypeAdapter(List[McpContent]).validate_python(value)
        except Exception:
            return None
        return value

    @staticmethod
    def _validate_tool_result_content(content: Any) -> None:
        try:
            TypeAdapter(List[McpContent]).validate_python(content)
        except Exception as exc:
            raise ValueError(
                f"{McpCallToolResult.__name__}.content must be a valid list of MCP content blocks"
            ) from exc

    def _to_tool_result(
        self, tool: McpCallableTool, value: Any, *, is_error: bool = False
    ) -> McpCallToolResult:
        if isinstance(value, McpCallToolResult):
            result = value.model_copy(deep=True)
            # TODO: gate repair code with a flag
            # This is when user's code specifically return a strong typed McpCallToolResult.
            # In here we repair the result to ensure that isError and content are set correctly.

            # TODO: if outputSchema, validate
            if self._context.flags.enforce_mcp_tool_result_content_format:
                self._validate_tool_result_content(result.content)
            if result.isError is None:
                result.isError = is_error
            if result.content is None and result.structuredContent is not None:
                result.content = self._as_text_content(result.structuredContent)
            return result

        value = self._to_jsonable(value)

        if tool.outputSchema is not None and not is_error:
            content = (
                []
                if self._context.flags.allow_mcp_tool_result_empty_content
                else self._structured_content_hint()
            )
            return McpCallToolResult(
                content=content,
                isError=is_error,
                structuredContent=value,
            )

        content_blocks = None if is_error else self._as_content_blocks(value)
        if content_blocks is not None:
            return McpCallToolResult(content=content_blocks, isError=is_error)

        return McpCallToolResult(
            content=self._as_text_content(value),
            isError=is_error,
        )

    async def process(
        self, request: McpRequest | McpServerRequest, session_id: str | None = None
    ) -> McpResponseOrError:
        """Process a single MCP request and return a response."""
        try:
            method = request.method

            if self._context.flags.enforce_mcp_initialize_sequence:
                session = self._get_session(session_id)
                if session.status == McpSessionStatus.UNINITIALIZED and method not in (
                    McpMethod.INITIALIZE,
                    McpMethod.PING,
                ):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.INVALID_REQUEST,
                            message=f"{McpServer.__name__} not initialized",
                        ),
                    )
                if (
                    session.status == McpSessionStatus.INITIALIZING
                    and method != McpMethod.PING
                ):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.INVALID_REQUEST,
                            message=f"{McpServer.__name__} initializing, waiting for initialized notification",
                        ),
                    )

            if method == McpMethod.PING:
                return McpResponse(id=request.id, result={})
            elif method == McpMethod.TOOLS_LIST:
                if not isinstance(request, McpListToolsRequest):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.INVALID_REQUEST,
                            message=f"{McpServer.__name__} request is not a {McpListToolsRequest.__name__}",
                        ),
                    )
                cursor = request.params.cursor if request.params else None
                tools, next_cursor = self._paginate_tools(cursor)
                if cursor is not None and tools is None:
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.INVALID_PARAMS,
                            message=f"{McpServer.__name__} invalid tools/list cursor",
                        ),
                    )
                result = McpListToolsResult(tools=tools or [], nextCursor=next_cursor)
                return McpResponse(
                    id=request.id,
                    result=result.model_dump(exclude_none=True),
                )
            elif method == McpMethod.TOOLS_CALL:
                if not isinstance(request, McpCallToolRequest):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.INVALID_REQUEST,
                            message=f"{McpServer.__name__} request is not a {McpCallToolRequest.__name__}",
                        ),
                    )
                name = request.params.name
                arguments = request.params.arguments
                tool = self._tools.get(name)
                if tool is None:
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.METHOD_NOT_FOUND,
                            message=f"{McpServer.__name__} tool '{name}' not found",
                        ),
                    )
                try:
                    call_result = await self._call_tool(tool, arguments)
                except Exception as ex:
                    error_result = self._to_tool_result(tool, str(ex), is_error=True)
                    return McpResponse(
                        id=request.id,
                        result=error_result.model_dump(exclude_none=True),
                    )
                result = self._to_tool_result(tool, call_result)
                return McpResponse(
                    id=request.id,
                    result=result.model_dump(exclude_none=True),
                )
            elif method == McpMethod.INITIALIZE:
                if not isinstance(request, McpInitializeRequest):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            code=McpErrorCodes.INVALID_REQUEST,
                            message=f"{McpServer.__name__} request is not a {McpInitializeRequest.__name__}",
                        ),
                    )

                session = self._get_session(session_id)
                protocol_version = session.version.default_version
                if self._context.flags.enforce_mcp_version_negotiation:
                    client_version = request.params.protocolVersion
                    if client_version:
                        protocol_version = session.version.negotiate(client_version)

                session.status = McpSessionStatus.INITIALIZING
                session.version.negotiate(protocol_version)
                result = McpInitializeResult(
                    capabilities=self._server_capabilities(),
                    protocolVersion=protocol_version,
                    serverInfo={"name": self.name, "version": "0.0.0"},
                )
                return McpResponse(id=request.id, result=result.model_dump())
            else:
                return McpError(
                    id=request.id,
                    error=McpSystemError(
                        code=McpErrorCodes.METHOD_NOT_FOUND,
                        message=f"{McpServer.__name__} unknown method {method}",
                    ),
                )
        except Exception as e:
            return McpError(
                id=request.id,
                error=McpSystemError(
                    code=McpErrorCodes.INTERNAL_ERROR,
                    message=f"{McpServer.__name__} exception during process: {e}",
                ),
            )

    async def _handle_message_loop(self, transport: McpServerTransport) -> None:
        async def _handle_request(
            request: McpRequest | McpServerRequest, session_id: str | None
        ) -> None:
            session = self._get_session(session_id)
            task = asyncio.current_task()
            if task:
                session.request_tasks[request.id] = task
            try:
                message = await self.process(request, session_id)
                await transport.server_send_message(message, session_id)
            finally:
                if task and session.request_tasks.get(request.id) == task:
                    session.request_tasks.pop(request.id, None)

        def _handle_cancelled(
            notification: McpCancelledNotification, session_id: str | None
        ) -> None:
            session = self._get_session(session_id)
            task = session.request_tasks.get(notification.params.requestId)
            if task is not None and not task.done():
                task.cancel()

        async for message, session_id in transport.server_messages():
            if isinstance(message, (McpRequest, McpServerRequest)):
                asyncio.create_task(_handle_request(message, session_id))
            elif isinstance(message, McpCancelledNotification):
                _handle_cancelled(message, session_id)
            elif isinstance(message, McpInitializedNotification):
                session = self._get_session(session_id)
                if self._context.flags.enforce_mcp_initialize_sequence:
                    if session.status == McpSessionStatus.INITIALIZING:
                        session.status = McpSessionStatus.INITIALIZED
                else:
                    session.status = McpSessionStatus.INITIALIZED
