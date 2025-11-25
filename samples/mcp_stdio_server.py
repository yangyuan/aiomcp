from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "mcp-stdio-server",
    instructions="MCP stdio sample built with the mcp package.",
)


@mcp.tool(structured_output=False)
def add(a: int, b: int) -> int:
    return a + b


@mcp.tool(structured_output=False)
def echo(message: str) -> str:
    return message + "!"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
