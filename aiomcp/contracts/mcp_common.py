from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class McpMetadataModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meta: Optional[Dict[str, Any]] = Field(default=None, alias="_meta")


class McpAnnotations(BaseModel):
    audience: Optional[List[Literal["user", "assistant"]]] = None
    priority: Optional[float] = None
    lastModified: Optional[str] = None


class McpIcon(BaseModel):
    src: str
    mimeType: Optional[str] = None
    sizes: Optional[List[str]] = None
    theme: Optional[Literal["light", "dark"]] = None
