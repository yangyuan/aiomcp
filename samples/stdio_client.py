import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiomcp import McpClient
from aiomcp.transports.stdio import McpStdioClientTransport


async def main() -> None:
    server_path = os.path.join(os.path.dirname(__file__), "stdio_server.py")
    transport = McpStdioClientTransport(
        [sys.executable, server_path],
    )

    client = McpClient("aiomcp-stdio-client")
    await client.initialize(transport)

    total = await client.invoke("add", {"a": 2, "b": 3})
    print(f"add result: {total}")

    echoed = await client.invoke("echo", {"message": "aiomcp"})
    print(f"echo result: {echoed}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
