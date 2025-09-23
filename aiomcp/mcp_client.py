import asyncio
from typing import Any, Dict, List
import uuid
from aiomcp.contracts.mcp_schema import JsonSchemaType
from aiomcp.mcp_server import McpServer
from aiomcp.mcp_transport_resolver import McpTransportResolver
from aiomcp.transports.base import McpClientTransport
from aiomcp.transports.direct import McpDirectClientTransport
from aiomcp.contracts.mcp_tool import McpTool
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
)


class McpClient:
    def __init__(self, name: str = "aiomcp-client") -> None:
        self.name = name
        self._initialized = False
        self._transport: McpClientTransport | None = None
        self._tools: Dict[str, McpTool] = {}
        self._inflight: Dict[str, asyncio.Future[McpResponseOrError]] = {}
        self._message_loop: asyncio.Task | None = None

    def _generate_request_id(self) -> str:
        return uuid.uuid4().hex

    async def _notify_initialized(self) -> None:
        notification = McpNotification(
            method=McpMethod.NOTIFICATIONS_INITIALIZED,
            params=None,
        )
        await self._transport.client_send_message(notification)

    async def initialize(self, transport: McpClientTransport | McpServer | str) -> None:
        if self._initialized:
            # TODO: lock instead?
            raise RuntimeError(f"{McpClient.__name__} is already initialized")

        if not isinstance(transport, McpClientTransport):
            if isinstance(transport, McpServer):
                transport = McpDirectClientTransport(transport)
            elif isinstance(transport, str):
                transport = McpTransportResolver.resolve(transport)
                self._transport = transport
        self._transport = transport

        await self._transport.client_initialize()
        self._message_loop = asyncio.create_task(self._handle_message_loop())
        _ = await self._initialize_server()
        await self._notify_initialized()
        await self._refresh_tools()
        self._initialized = True

    def _check_initialized(self) -> None:
        # TODO: lazy initialize when project is stable.
        if not self._initialized:
            raise RuntimeError(f"{McpClient.__name__} not initialized")

    async def _handle_message_loop(self) -> None:
        # TODO: _inflight management, graceful shutdown, etc.
        async for msg in self._transport.client_messages():
            if isinstance(msg, McpResponseOrError):
                if msg.id in self._inflight:
                    self._inflight[msg.id].set_result(msg)
                    del self._inflight[msg.id]
                else:
                    pass
            # TODO: save responses for other ids, handle other notifications, etc.
        raise RuntimeError(f"{McpClient.__name__} message stream closed unexpectedly")

    async def _generate_response_future(
        self, request_id: str
    ) -> asyncio.Future[McpResponseOrError]:
        future = asyncio.Future()
        # TODO: what if already exists?
        self._inflight[request_id] = future
        return future

    async def _process(self, request: McpRequest) -> McpResponseOrError:
        future = await self._generate_response_future(request.id)
        await self._transport.client_send_message(request)
        return await future

    async def _initialize_server(self) -> McpInitializeParams:
        request_id = self._generate_request_id()
        request = McpInitializeRequest(
            id=request_id,
            params=McpInitializeParams(
                capabilities={},
                protocolVersion="2024-11-05",
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
        return structured

    async def _refresh_tools(self) -> List[McpTool]:
        request_id = self._generate_request_id()
        request = McpListToolsRequest(id=request_id, params=None)
        response = await self._process(request)
        if isinstance(response, McpError):
            raise RuntimeError(
                f"{McpClient.__name__} failed to list tools, {response.error.model_dump_json()}"
            )
        try:
            structured = McpListToolsResult.model_validate(response.result)
        except Exception as e:
            raise RuntimeError(f"{McpClient.__name__} failed to parse tools list: {e}")
        # Populate internal lookup for subsequent calls
        self._tools = {t.name: t for t in structured.tools}
        return structured.tools

    async def mcp_tools_list(self) -> List[McpTool]:
        self._check_initialized()
        return list(self._tools.values())

    async def mcp_tools_call(
        self,
        tool: McpTool,
        request: McpCallToolRequest,
        bypass_client_validation: bool = False,
    ) -> McpResponseOrError:
        self._check_initialized()

        if tool.inputSchema is not None and not bypass_client_validation:
            try:
                # TODO: add input schema validation (for each field)
                if tool.inputSchema.type != JsonSchemaType.OBJECT:
                    raise RuntimeError(
                        "{McpClient.__name__} Input schema must be an object"
                    )
            except Exception as e:
                raise RuntimeError(
                    f"{McpClient.__name__} input validation failed for tool '{tool.name}': {e}"
                )

        response = await self._process(request)

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

    async def invoke(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        self._check_initialized()
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"{McpClient.__name__} cannot find tool: {tool_name}")

        request_id = self._generate_request_id()
        request = McpCallToolRequest(
            id=request_id,
            params=McpCallToolParams(name=tool.name, arguments=arguments),
        )

        response = await self.mcp_tools_call(tool, request)

        if isinstance(response, McpError):
            raise RuntimeError(
                f"{McpClient.__name__} error occurred, {response.error.model_dump_json()}"
            )
        try:
            structured = McpCallToolResult.model_validate(response.result)
        except Exception as e:
            raise RuntimeError(f"{McpClient.__name__} failed to parse tools list: {e}")

        return structured.structuredContent
