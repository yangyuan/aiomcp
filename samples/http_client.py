import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiomcp import McpClient


async def main() -> None:
    client = McpClient("aiomcp-http-client")
    await client.initialize("http://127.0.0.1:8000/mcp")

    total = await client.invoke("add", {"a": 2, "b": 3})
    print(f"add result: {total}")

    echoed = await client.invoke("echo", {"message": "aiomcp"})
    print(f"echo result: {echoed}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
