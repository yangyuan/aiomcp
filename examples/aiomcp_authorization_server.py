import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiomcp import McpServer, McpAuthorizationServer


async def add(a: int, b: int) -> int:
    return a + b


async def echo(message: str) -> str:
    return message + "!"


async def main() -> None:
    server = McpServer("aiomcp-oauth-server")
    await server.register_tool(add, alias="add")
    await server.register_tool(echo, alias="echo")

    authorization = McpAuthorizationServer()

    transport = "http://127.0.0.1:8000/mcp"
    print("OAuth MCP server listening on http://127.0.0.1:8000/mcp", flush=True)
    print("Clients can discover and register automatically.", flush=True)
    await server.host(transport, authorization=authorization)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
