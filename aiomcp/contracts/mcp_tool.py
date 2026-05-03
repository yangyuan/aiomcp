from typing import List, Optional
from pydantic import BaseModel
from pydantic import ConfigDict

from aiomcp.contracts.mcp_schema import JsonSchema


class McpToolAnnotations(BaseModel):
    title: Optional[str] = None
    readOnlyHint: Optional[bool] = None
    destructiveHint: Optional[bool] = None
    idempotentHint: Optional[bool] = None
    openWorldHint: Optional[bool] = None


class McpToolIcon(BaseModel):
    src: str
    mimeType: Optional[str] = None
    sizes: Optional[List[str]] = None


class McpTool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    inputSchema: Optional[JsonSchema] = None
    outputSchema: Optional[JsonSchema] = None
    annotations: Optional[McpToolAnnotations] = None
    icons: Optional[List[McpToolIcon]] = None
