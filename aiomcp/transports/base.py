from abc import ABC, abstractmethod
from typing import AsyncIterator
from aiomcp.contracts.mcp_message import (
    McpMessage,
)


class McpClientTransport(ABC):
    @abstractmethod
    async def client_initialize(self):
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
    async def server_initialize(self):
        pass

    @abstractmethod
    async def server_messages(self) -> AsyncIterator[tuple[McpMessage, str | None]]:
        yield

    @abstractmethod
    async def server_send_message(
        self, message: McpMessage, session_id: str | None = None
    ) -> bool:
        pass

    @abstractmethod
    async def close(self):
        pass


class McpTransport(McpClientTransport, McpServerTransport):
    pass
