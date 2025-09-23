import asyncio
import inspect
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional,
    List,
    Set,
)

from pydantic import BaseModel, TypeAdapter
from aiomcp.contracts.mcp_schema import JsonSchema
from aiomcp.mcp_schema_resolver import McpSchemaResolver
from aiomcp.mcp_transport_resolver import McpTransportResolver
from aiomcp.transports.base import McpServerTransport
from aiomcp.contracts.mcp_tool import McpTool
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
)


class McpCallableTool(McpTool):
    callable_async: Callable[..., Awaitable]


class McpServer:
    def __init__(self, name: str = "aiomcp-server") -> None:
        self.name = name
        self._tools: Dict[str, McpCallableTool] = {}
        self._hosting: bool = False  # TODO: remove once enable multiple hosting.
        self._message_loop: Optional[asyncio.Task] = None
        self._inflight: Set[asyncio.Task] = set()

    async def register_tool(
        self,
        func: Callable,
        alias: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        func_name, input_schema, output_schema = McpSchemaResolver.resolve(func)

        await self.mcp_tools_register(
            name=alias or func_name,
            func=func,
            input_schema=input_schema,
            output_schema=output_schema,
            description=description,
        )

    async def mcp_tools_register(
        self,
        name: str,
        func: Callable,
        input_schema: Dict[str, Any],
        output_schema: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> None:
        inputSchema: Optional[JsonSchema] = None
        outputSchema: Optional[JsonSchema] = None
        try:
            inputSchema = JsonSchema.model_validate(input_schema)
        except Exception:
            inputSchema = None
        if output_schema is not None:
            try:
                outputSchema = JsonSchema.model_validate(output_schema)
            except Exception:
                outputSchema = None

        if not asyncio.iscoroutinefunction(func):
            # tolerate sync functions by wrapping with async
            async def _async_wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            callable_async = _async_wrapper
        else:
            callable_async = func

        _tool = McpCallableTool(
            name=name,
            description=description,
            inputSchema=inputSchema,
            outputSchema=outputSchema,
            callable_async=callable_async,
        )
        self._tools[name] = _tool

    async def list_tools(self) -> List[McpTool]:
        return list(self._tools.values())

    async def create_host_task(
        self, transport: McpServerTransport | str
    ) -> asyncio.Task:
        if self._hosting:
            raise RuntimeError(f"{McpServer.__name__} is already hosting")
        self._hosting = True

        if isinstance(transport, str):
            transport = McpTransportResolver.resolve(transport)

        await transport.server_initialize()

        if self._message_loop is None:
            self._message_loop = asyncio.create_task(
                self._handle_message_loop(transport)
            )
        return self._message_loop

    async def host(self, transport: McpServerTransport | str) -> None:
        await self.create_host_task(transport)

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

    async def process(self, request: McpRequest) -> McpResponseOrError:
        """Process a single MCP request and return a response."""
        try:
            method = request.method
            if method == McpMethod.TOOLS_LIST:
                if not isinstance(request, McpListToolsRequest):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            message=f"{McpServer.__name__} request is not a {McpListToolsRequest.__name__}"
                        ),
                    )
                result = McpListToolsResult(tools=self._tools.values())
                return McpResponse(
                    id=request.id,
                    result=result.model_dump(),
                )
            elif method == McpMethod.TOOLS_CALL:
                if not isinstance(request, McpCallToolRequest):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            message=f"{McpServer.__name__} request is not a {McpCallToolRequest.__name__}"
                        ),
                    )
                name = request.params.name
                arguments = request.params.arguments or {}
                tool = self._tools.get(name)
                if tool is None:
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            message=f"{McpServer.__name__} tool '{name}' not found"
                        ),
                    )
                try:
                    _kwargs = self._to_kwargs(tool.callable_async, arguments)
                    call_result = await tool.callable_async(**_kwargs)
                    # only support pydantic models and built-in types (dict, list, str, int, etc)
                    if isinstance(call_result, BaseModel):
                        call_result = call_result.model_dump()
                except Exception as ex:
                    return McpError(
                        id=request.id, error=McpSystemError(message=str(ex))
                    )
                return McpResponse(
                    id=request.id,
                    result=McpCallToolResult(
                        structuredContent=call_result
                    ).model_dump(),
                )
            elif method == McpMethod.INITIALIZE:
                if not isinstance(request, McpInitializeRequest):
                    return McpError(
                        id=request.id,
                        error=McpSystemError(
                            message=f"{McpServer.__name__} request is not a {McpInitializeRequest.__name__}"
                        ),
                    )
                result = McpInitializeResult(
                    capabilities={},
                    protocolVersion="2024-11-05",
                    serverInfo={"name": self.name, "version": "0.0.0"},
                )
                return McpResponse(id=request.id, result=result.model_dump())
            else:
                return McpError(
                    id=request.id,
                    error=McpSystemError(
                        message=f"{McpServer.__name__} unknown method {method}"
                    ),
                )
        except Exception as e:
            return McpError(
                id=request.id,
                error=McpSystemError(
                    message=f"{McpServer.__name__} exception during process: {e}"
                ),
            )

    async def _handle_message_loop(self, transport: McpServerTransport) -> None:
        async def _handle_request(request: McpRequest) -> None:
            message = await self.process(request)
            await transport.server_send_message(message)

        # TODO: _inflight management, graceful shutdown, etc.
        # TODO: handle notifications
        async for message in transport.server_messages():
            if isinstance(message, McpRequest):
                task = asyncio.create_task(_handle_request(message))
                self._inflight.add(task)
                task.add_done_callback(lambda t: self._inflight.discard(t))
