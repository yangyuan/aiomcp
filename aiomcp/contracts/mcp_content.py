from typing import List, Literal, Optional, Union

from aiomcp.contracts.mcp_common import McpAnnotations, McpIcon, McpMetadataModel


class McpContentBlock(McpMetadataModel):
    annotations: Optional[McpAnnotations] = None


class McpTextContent(McpContentBlock):
    type: Literal["text"] = "text"
    text: str


class McpImageContent(McpContentBlock):
    type: Literal["image"] = "image"
    data: str
    mimeType: str


class McpAudioContent(McpContentBlock):
    type: Literal["audio"] = "audio"
    data: str
    mimeType: str


class McpTextResourceContents(McpMetadataModel):
    uri: str
    text: str
    mimeType: Optional[str] = None


class McpBlobResourceContents(McpMetadataModel):
    uri: str
    blob: str
    mimeType: Optional[str] = None


McpResourceContents = Union[McpTextResourceContents, McpBlobResourceContents]


class McpEmbeddedResource(McpContentBlock):
    type: Literal["resource"] = "resource"
    resource: McpResourceContents


class McpResourceLink(McpContentBlock):
    type: Literal["resource_link"] = "resource_link"
    name: str
    uri: str
    title: Optional[str] = None
    description: Optional[str] = None
    mimeType: Optional[str] = None
    size: Optional[int] = None
    icons: Optional[List[McpIcon]] = None


McpContent = Union[
    McpTextContent,
    McpImageContent,
    McpAudioContent,
    McpResourceLink,
    McpEmbeddedResource,
]
