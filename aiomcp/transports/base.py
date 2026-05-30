from abc import ABC, abstractmethod
from typing import AsyncIterator
from aiomcp.contracts.mcp_message import (
    McpMessage,
)
from aiomcp.mcp_context import McpServerContext, McpClientContext


class McpServerTransportEnvelope:
    def __init__(
        self,
        message: McpMessage,
        session_id: str | None = None,
        # Per-request transport-level correlation token. Created by the transport
        # for one in-flight request so the matching response can be routed back to
        # the exact pending caller. Decoupled from the MCP session_id and
        # JSON-RPC id.
        transport_correlation_id: str | None = None,
    ) -> None:
        self.message = message
        self.session_id = session_id
        self.transport_correlation_id = transport_correlation_id

    def __iter__(self):
        yield self.message
        yield self.session_id


class McpClientTransport(ABC):
    @abstractmethod
    async def client_initialize(self, context: McpClientContext):
        pass

    @abstractmethod
    async def client_messages(self) -> AsyncIterator[McpMessage]:
        yield

    @abstractmethod
    async def client_send_message(self, message: McpMessage) -> bool:
        pass

    @abstractmethod
    async def close(self):
        pass


class McpServerTransport(ABC):
    @abstractmethod
    async def server_initialize(self, context: McpServerContext):
        pass

    @abstractmethod
    async def server_messages(self) -> AsyncIterator[McpServerTransportEnvelope]:
        yield

    @abstractmethod
    async def server_send_message(
        self,
        message: McpMessage,
        session_id: str | None = None,
        transport_correlation_id: str | None = None,
    ) -> bool:
        pass

    @abstractmethod
    async def close(self):
        pass


class McpTransport(McpClientTransport, McpServerTransport):
    pass
