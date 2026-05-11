from typing import Annotated, List, Optional, Union, Literal, Dict, Any
from enum import StrEnum
from pydantic import BaseModel, Field

from aiomcp.contracts.mcp_tool import McpTool


class McpMethod(StrEnum):
    TOOLS_CALL = "tools/call"
    TOOLS_LIST = "tools/list"
    INITIALIZE = "initialize"
    PING = "ping"
    NOTIFICATIONS_INITIALIZED = "notifications/initialized"
    NOTIFICATIONS_TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
    NOTIFICATIONS_PROGRESS = "notifications/progress"
    NOTIFICATIONS_MESSAGE = "notifications/message"
    NOTIFICATIONS_CANCELLED = "notifications/cancelled"


class McpMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"


class McpPackage(McpMessage):
    id: Union[int, str]


class McpNotification(McpMessage):
    method: str
    params: Optional[Dict[str, Any]] = None


class McpInitializedNotification(McpNotification):
    method: Literal[McpMethod.NOTIFICATIONS_INITIALIZED] = (
        McpMethod.NOTIFICATIONS_INITIALIZED
    )


class McpCancelledNotificationParams(BaseModel):
    requestId: Union[int, str]
    reason: Optional[str] = None


class McpCancelledNotification(McpNotification):
    method: Literal[McpMethod.NOTIFICATIONS_CANCELLED] = (
        McpMethod.NOTIFICATIONS_CANCELLED
    )
    params: McpCancelledNotificationParams


class McpRequest(McpPackage):
    method: str
    params: Optional[Dict[str, Any]] = None


class McpServerRequest(McpPackage):
    method: str
    params: Optional[Dict[str, Any]] = None


class McpPingRequest(McpServerRequest):
    method: Literal[McpMethod.PING] = McpMethod.PING
    params: Optional[Dict[str, Any]] = None


class McpInitializeParams(BaseModel):
    capabilities: Dict[str, Any]
    protocolVersion: Optional[str] = None
    clientInfo: Optional[Dict[str, Any]] = None


class McpInitializeRequest(McpRequest):
    method: Literal[McpMethod.INITIALIZE] = McpMethod.INITIALIZE
    params: McpInitializeParams


class McpCallToolParams(BaseModel):
    name: str
    arguments: Optional[Any] = None


class McpCallToolRequest(McpRequest):
    method: Literal[McpMethod.TOOLS_CALL] = McpMethod.TOOLS_CALL
    params: McpCallToolParams


class McpListToolsParams(BaseModel):
    cursor: Optional[str] = None


class McpListToolsRequest(McpRequest):
    method: Literal[McpMethod.TOOLS_LIST] = McpMethod.TOOLS_LIST
    params: Optional[McpListToolsParams] = None


class McpSystemError(BaseModel):
    code: Optional[int] = None
    message: Optional[str] = None
    data: Optional[Any] = None


class McpError(McpPackage):
    error: McpSystemError


class McpResponse(McpPackage):
    result: Dict[str, Any]


class McpInitializeResult(BaseModel):
    capabilities: Dict[str, Any]
    protocolVersion: Optional[str] = None
    serverInfo: Optional[Dict[str, Any]] = None


class McpCallToolResult(BaseModel):
    content: Optional[Any] = None
    isError: Optional[bool] = None
    structuredContent: Optional[Any] = None


class McpListToolsResult(BaseModel):
    nextCursor: Optional[str] = None
    tools: list[McpTool]


McpResponseOrError = Union[McpResponse, McpError]

McpClientMessageUnion = Union[
    McpResponseOrError,
    Annotated[
        Union[
            McpInitializedNotification,
            McpCancelledNotification,
            McpPingRequest,
            McpInitializeRequest,
            McpCallToolRequest,
            McpListToolsRequest,
        ],
        Field(discriminator="method"),
    ],
]

McpServerMessageUnion = Union[
    McpResponseOrError, McpPingRequest, McpServerRequest, McpNotification
]
