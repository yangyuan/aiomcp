from __future__ import annotations

from mcp.server.fastmcp import FastMCP

HOST = "127.0.0.1"
PORT = 8000
PATH = "/mcp"

mcp = FastMCP(
    "mcp-http-server",
    instructions="MCP HTTP sample built with the mcp package.",
    host=HOST,
    port=PORT,
    streamable_http_path=PATH,
)


@mcp.tool(structured_output=False)
def add(a: int, b: int) -> int:
    return a + b


@mcp.tool(structured_output=False)
def echo(message: str) -> str:
    return message + "!"


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
