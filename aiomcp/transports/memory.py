from asyncio import Queue
from typing import AsyncIterator
from aiomcp.contracts.mcp_message import McpMessage
from aiomcp.mcp_serialization import McpSerialization
from aiomcp.transports.base import McpTransport


class McpMemoryTransport(McpTransport):
    def __init__(self) -> None:
        self._server_to_client: Queue[McpMessage] = Queue()
        self._client_to_server: Queue[McpMessage] = Queue()

    async def client_initialize(self):
        pass

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._server_to_client.get()
            yield message

    async def client_send_message(self, message: McpMessage) -> bool:
        message = McpSerialization.process_client_message(message)
        await self._client_to_server.put(message)
        return True

    async def server_initialize(self):
        pass

    async def server_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._client_to_server.get()
            yield message

    async def server_send_message(self, message: McpMessage) -> bool:
        message = McpSerialization.process_server_message(message)
        await self._server_to_client.put(message)
        return True
