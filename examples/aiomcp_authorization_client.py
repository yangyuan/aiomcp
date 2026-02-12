import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiomcp import McpClient, McpAuthorizationClient


async def main() -> None:
    server_url = "http://127.0.0.1:8000/mcp"

    authorization = await McpAuthorizationClient.discover(server_url)

    client = McpClient("aiomcp-authorization-client")
    await client.initialize(server_url, authorization=authorization)

    tools = await client.mcp_tools_list()
    print(f"Available tools: {[t.name for t in tools]}")

    for tool in tools:
        print(f"  {tool.name}: {tool.inputSchema.model_dump_json()}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
