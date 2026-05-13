from typing import List, Literal, Optional
from pydantic import BaseModel
from pydantic import ConfigDict

from aiomcp.contracts.mcp_common import McpIcon, McpMetadataModel
from aiomcp.contracts.mcp_schema import JsonSchema


class McpToolAnnotations(BaseModel):
    title: Optional[str] = None
    readOnlyHint: Optional[bool] = None
    destructiveHint: Optional[bool] = None
    idempotentHint: Optional[bool] = None
    openWorldHint: Optional[bool] = None


class McpToolIcon(McpIcon):
    pass


class McpToolExecution(BaseModel):
    taskSupport: Optional[Literal["forbidden", "optional", "required"]] = None


class McpTool(McpMetadataModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    inputSchema: Optional[JsonSchema] = None
    outputSchema: Optional[JsonSchema] = None
    annotations: Optional[McpToolAnnotations] = None
    icons: Optional[List[McpToolIcon]] = None
    execution: Optional[McpToolExecution] = None
