from typing import Optional
from pydantic import BaseModel
from pydantic import ConfigDict

from aiomcp.contracts.mcp_schema import JsonSchema


class McpTool(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: Optional[str] = None
    inputSchema: Optional[JsonSchema] = None
    outputSchema: Optional[JsonSchema] = None
