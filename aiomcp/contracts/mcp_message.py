from typing import Annotated, List, Optional, Union, Literal, Dict, Any
from enum import StrEnum
from pydantic import BaseModel, Field

from aiomcp.contracts.mcp_tool import McpTool


class McpMethod(StrEnum):
    TOOLS_CALL = "tools/call"
    TOOLS_LIST = "tools/list"
    INITIALIZE = "initialize"
    NOTIFICATIONS_INITIALIZED = "notifications/initialized"


class McpMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"


class McpPackage(McpMessage):
    id: Union[int, str]


class McpNotification(McpMessage):
    method: McpMethod
    params: Optional[Dict[str, Any]] = None


class McpInitializedNotification(McpNotification):
    method: Literal[McpMethod.NOTIFICATIONS_INITIALIZED] = (
        McpMethod.NOTIFICATIONS_INITIALIZED
    )


class McpRequest(McpPackage):
    method: McpMethod


class McpInitializeParams(BaseModel):
    capabilities: Dict[str, Any]
    protocolVersion: Optional[str] = None
    clientInfo: Optional[Dict[str, Any]] = None


class McpInitializeRequest(McpRequest):
    method: Literal[McpMethod.INITIALIZE] = McpMethod.INITIALIZE
    params: McpInitializeParams


class McpCallToolParams(BaseModel):
    name: str
    arguments: Optional[Dict[str, Any]] = None


class McpCallToolRequest(McpRequest):
    method: Literal[McpMethod.TOOLS_CALL] = McpMethod.TOOLS_CALL
    params: McpCallToolParams


class McpListToolsParams(BaseModel):
    cursor: Optional[str] = None


class McpListToolsRequest(McpRequest):
    method: Literal[McpMethod.TOOLS_LIST] = McpMethod.TOOLS_LIST
    params: Optional[McpListToolsParams] = None


class McpSystemError(BaseModel):
    code: Optional[int | str] = None
    message: Optional[str] = None


class McpError(McpPackage):
    error: McpSystemError


class McpResponse(McpPackage):
    result: Dict[str, Any]


class McpInitializeResult(BaseModel):
    capabilities: Dict[str, Any]
    protocolVersion: Optional[str] = None
    serverInfo: Optional[Dict[str, Any]] = None


class McpCallToolResult(BaseModel):
    content: Optional[List[Dict[str, Any]]] = None
    isError: Optional[bool] = None
    structuredContent: Optional[Any] = None


class McpListToolsResult(BaseModel):
    nextCursor: Optional[str] = None
    tools: list[McpTool]


McpResponseOrError = Union[McpResponse, McpError]


McpClientMessageAnnotated = Annotated[
    Union[
        McpInitializedNotification,
        McpInitializeRequest,
        McpCallToolRequest,
        McpListToolsRequest,
    ],
    Field(discriminator="method"),
]
