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


class McpServerTransport(ABC):
    @abstractmethod
    async def server_initialize(self):
        pass

    @abstractmethod
    async def server_messages(self) -> AsyncIterator[McpMessage]:
        yield

    @abstractmethod
    async def server_send_message(self, message: McpMessage) -> bool:
        pass


class McpTransport(McpClientTransport, McpServerTransport):
    pass
