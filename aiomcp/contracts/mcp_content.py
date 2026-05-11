from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


class McpTextContent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text"] = "text"
    text: str


class McpImageContent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["image"] = "image"
    data: str
    mimeType: str


class McpAudioContent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["audio"] = "audio"
    data: str
    mimeType: str


class McpTextResourceContents(BaseModel):
    model_config = ConfigDict(extra="allow")

    uri: str
    text: str
    mimeType: Optional[str] = None


class McpBlobResourceContents(BaseModel):
    model_config = ConfigDict(extra="allow")

    uri: str
    blob: str
    mimeType: Optional[str] = None


McpResourceContents = Union[McpTextResourceContents, McpBlobResourceContents]


class McpEmbeddedResource(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["resource"] = "resource"
    resource: McpResourceContents


class McpResourceLink(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["resource_link"] = "resource_link"
    name: str
    uri: str
    title: Optional[str] = None
    description: Optional[str] = None
    mimeType: Optional[str] = None
    size: Optional[int] = None
    icons: Optional[List[Dict[str, Any]]] = None


McpContent = Union[
    McpTextContent,
    McpImageContent,
    McpAudioContent,
    McpResourceLink,
    McpEmbeddedResource,
]
