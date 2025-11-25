import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiomcp import McpServer
from aiomcp.transports.stdio import McpStdioServerTransport


async def add(a: int, b: int) -> int:
    return a + b


async def echo(message: str) -> str:
    return message + "!"


async def main() -> None:
    server = McpServer("aiomcp-stdio-server")
    await server.register_tool(add, alias="add")
    await server.register_tool(echo, alias="echo")

    transport = McpStdioServerTransport()
    server_task = await server.create_host_task(transport)
    await server_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
