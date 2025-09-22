from asyncio import Queue
from typing import AsyncIterator, Protocol, runtime_checkable
from aiomcp.contracts.mcp_message import (
    McpSystemError,
    McpError,
    McpMessage,
    McpNotification,
    McpRequest,
    McpResponseOrError,
)
from aiomcp.mcp_serialization import McpSerialization
from aiomcp.transports.base import McpClientTransport


# Direct transport is a transport that has dependency on server behavior.
# It breaks the purity of transport abstraction, but also necessary to exist.
# A server-like object to minimize transport's knowledge of server internals.
@runtime_checkable
class McpServerLike(Protocol):
    async def process(self, req: McpRequest) -> McpResponseOrError: ...


class McpDirectClientTransport(McpClientTransport):
    def __init__(self, server: McpServerLike) -> None:
        if not isinstance(server, McpServerLike):
            raise ValueError(
                f"{McpDirectClientTransport.__name__} can only take a McpServer like object"
            )
        self._server = server
        self._server_to_client: Queue[McpMessage] = Queue()

    async def client_initialize(self):
        pass

    async def client_messages(self) -> AsyncIterator[McpMessage]:
        while True:
            message = await self._server_to_client.get()
            yield message

    async def client_send_message(self, message: McpMessage) -> bool:
        if isinstance(message, McpRequest):
            try:
                message = McpSerialization.process_client_message(message)
                response = await self._server.process(message)
                response = McpSerialization.process_server_message(response)
                await self._server_to_client.put(response)
            except Exception as e:
                await self._server_to_client.put(
                    McpError(
                        id=message.id,
                        error=McpSystemError(
                            message=f"{McpDirectClientTransport.__name__} processing request error: {e}"
                        ),
                    )
                )
            return True
        elif isinstance(message, McpNotification):
            return True
        return False


class McpDirectTransport(McpDirectClientTransport):
    pass
