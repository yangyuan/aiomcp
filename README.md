# aiomcp
A Simple Python MCP Solution

## Mission of aiomcp
Start with a smooth experience, end with a compliant solution.


## Quick Start

### A Simple STDIO Example

MCP server `server.py`

```python
import asyncio

from aiomcp import McpServer, McpStdioServerTransport


async def add(a: int, b: int) -> int:
    return a + b


async def main() -> None:
    mcp_server = McpServer("mcp-server-name")
    await mcp_server.register_tool(add)
    await mcp_server.host(McpStdioServerTransport())


if __name__ == "__main__":
    asyncio.run(main())
```

MCP client `client.py`

```python
import asyncio
import sys

from aiomcp import McpClient, McpStdioClientTransport


async def main() -> None:
    mcp_client = McpClient("mcp-client-name")
    await mcp_client.initialize(
        McpStdioClientTransport([sys.executable, "server.py"])
    )

    try:
        sum_value = await mcp_client.invoke("add", {"a": 1, "b": 2})
        print(sum_value)
        assert sum_value == 3
    finally:
        await mcp_client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Run the client from the same directory as `server.py`:

```bash
python client.py
```

### A Simple HTTP Example

MCP server `server.py`

```python
import asyncio

from aiomcp import McpServer


async def add(a: int, b: int) -> int:
    return a + b


async def main() -> None:
    mcp_server = McpServer("mcp-server-name")
    await mcp_server.register_tool(add)
    await mcp_server.host("http://127.0.0.1:8000/mcp")


if __name__ == "__main__":
    asyncio.run(main())
```

Run the server, it will host on `http://127.0.0.1:8000/mcp`

```bash
python server.py
```

MCP client `client.py`

```python
import asyncio

from aiomcp import McpClient


async def main() -> None:
    mcp_client = McpClient("mcp-client-name")
    await mcp_client.initialize("http://127.0.0.1:8000/mcp")

    try:
        sum_value = await mcp_client.invoke("add", {"a": 1, "b": 2})
        assert sum_value == 3
    finally:
        await mcp_client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Run the client from anywhere in the same machine:

```bash
python client.py
```

### A Complete LLM-Friendly HTTP MCP Server

For best compatibility with LLM clients, tools can return an array of MCP content blocks. Each item in the array is one content block, such as text, image, audio, resource link, or embedded resource.

```python
import asyncio

from aiomcp import McpServer


async def screenshot():
    image_data = get_screenshot_image_data()

    return [
        {"type": "text", "text": "Here is the captured screenshot."},
        {"type": "image", "data": image_data, "mimeType": "image/png"},
    ]


async def main() -> None:
    mcp_server = McpServer("computer-use-server")
    await mcp_server.register_tool(screenshot)
    await mcp_server.host("http://127.0.0.1:8000/mcp")


if __name__ == "__main__":
    asyncio.run(main())
```

### Register Any Python Callable

`register_tool` accepts ordinary Python callables. Use a function, async function, bound method, class method, or static method; aiomcp reads the type hints and exposes the callable as an MCP tool.

```python
def add(a: int, b: int) -> int:
    return a + b


async def fetch_status(service: str) -> str:
    return f"{service}: ok"


class Calculator:
    def scale(self, value: float, factor: float = 1.0) -> float:
        return value * factor

    @classmethod
    async def add_offset(cls, value: int, offset: int) -> int:
        return value + offset

    @staticmethod
    async def total(items: list[int]) -> int:
        return sum(items)


calculator = Calculator()

await mcp_server.register_tool(add)
await mcp_server.register_tool(fetch_status)
await mcp_server.register_tool(calculator.scale, alias="calculator_scale")
await mcp_server.register_tool(Calculator.add_offset)
await mcp_server.register_tool(Calculator.total)
```

Clients invoke registered tools by name with a dict of arguments, similar to Python keyword arguments.

```python
assert await mcp_client.invoke("add", {"a": 1, "b": 2}) == 3
assert await mcp_client.invoke("calculator_scale", {"value": 3, "factor": 2}) == 6.0
assert await mcp_client.invoke("total", [1, 2, 3]) == 6
```

### Customize Parameters and Schemas

Use `Annotated` with Pydantic `Field` for parameter descriptions. Use Pydantic models when a tool takes a structured object.

```python
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


class Tone(StrEnum):
    WARM = "warm"
    PRECISE = "precise"


async def search(
    request: SearchRequest,
    tone: Annotated[Tone, Field(description="Response tone")] = Tone.PRECISE,
) -> list[str]:
    return [f"{tone.value}: {request.query}"][: request.limit]


await mcp_server.register_tool(
    search,
    description="Search documents for the current workspace.",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
```

### Advanced Tool Registration

Use `mcp_tools_register` when you already have JSON schemas or want exact control over the tool contract.

```python
def add(a: int, b: int) -> int:
    return a + b


await mcp_server.mcp_tools_register(
    "add",
    add,
    input_schema={
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "The first number"},
            "b": {"type": "integer", "description": "The second number"},
        },
        "required": ["a", "b"],
    },
    output_schema={"type": "integer"},
    title="Add Numbers",
    icons=[{"src": "https://example.com/add.png", "mimeType": "image/png"}],
)
```

### Using McpTransports
While `McpClient` and `McpServer` handle client/server behavior, they are implemented in a platform-agnostic way by using `McpClientTransport`/`McpServerTransport` to handle message streams. Client and server transports usually come in pairs as `McpTransport` implementations.

```python
mcp_server = McpServer()
await mcp_server.host(McpHttpTransport("127.0.0.1", 8000, "/mcp"))
# await mcp_server.host("http://127.0.0.1:8000/mcp")
```

```python
mcp_client = McpClient()
await mcp_client.initialize(McpHttpTransport("127.0.0.1", 8000, "/mcp"))
# await mcp_client.initialize("http://127.0.0.1:8000/mcp")
```

Supported `McpTransport` implementations:
- `McpDirectTransport`: Simple client driven transport that invokes `McpServer` with minimal MCP protocol overhead.
- `McpMemoryTransport`: Single event loop, in-memory transport.
- `McpHttpTransport`
- `McpStdioClientTransport` / `McpStdioServerTransport`: stdio based transport

Sample: Using McpMemoryTransport for local debugging.
```python
transport = McpMemoryTransport()

mcp_server = McpServer()
asyncio.create_task(mcp_server.host(transport))

mcp_client = McpClient()
await mcp_client.initialize(transport)
```

### Tool Results and LLM Compatibility

The MCP specification defines tool results with both `content` and `structuredContent`. The `content` field should be a list of text, image, audio, resource link, or embedded resource blocks. The optional `structuredContent` field is a structured object that follows the tool's output schema when one exists.

As an MCP client, `aiomcp` follows the MCP specification and can optionally convert and merge `content` and `structuredContent` into a standard LLM-friendly content list. If an MCP server does not produce standard output, `aiomcp` still follows the MCP specification while tolerating non-standard behavior on a best-effort basis for maximum compatibility.

As an MCP server, `aiomcp` generates output schemas from return type hints by default. For content-first LLM responses, omit the return annotation or return `McpCallToolResult` directly so the tool can provide MCP content blocks. A simple text response can be a dict like `{"type": "text", "text": "..."}` or a `McpTextContent`; richer responses can use `McpImageContent`, `McpAudioContent`, `McpResourceLink`, or `McpEmbeddedResource`.

```python
from aiomcp import McpCallToolResult, McpImageContent, McpTextContent


async def use_dict_content():
    return [
        # This follows the MCP content contract, not any provider-specific LLM API shape.
        {"type": "text", "text": "Chart generated."},
        {"type": "image", "data": "base64-image-data", "mimeType": "image/png"},
    ]


async def use_mcp_contracts():
    return [
        McpTextContent(text="Chart generated."),
        McpImageContent(data="base64-image-data", mimeType="image/png"),
    ]


async def use_call_tool_result():
    return McpCallToolResult(
        content=[McpTextContent(text="Chart generated.")],
        structuredContent={"chartId": "revenue-q1"},
    )
```

### Authorization

Connect to an OAuth 2.1 protected remote MCP server.

```python
from aiomcp import McpClient, McpAuthorizationClient

authorization = await McpAuthorizationClient.discover("http://remote-server/mcp")

# Or use a bearer token
# authorization = McpAuthorizationClient("your-access-token")

client = McpClient("mcp-client-name")
await client.initialize("http://remote-server/mcp", authorization=authorization)
```

Use GitHub as the OAuth provider to connect to GitHub's MCP server.

```python
from aiomcp import McpClient, McpAuthorizationClient

server_url = "https://api.githubcopilot.com/mcp/"

authorization = await McpAuthorizationClient.discover(
    server_url,
    client_id="{YOUR_CLIENT_ID}",
    client_secret="{YOUR_CLIENT_SECRET}",
)

client = McpClient("mcp-client-name")
await client.initialize(server_url, authorization=authorization)
```

Protect your MCP server with built-in OAuth 2.1 + Dynamic Client Registration.

```python
from aiomcp import McpServer, McpAuthorizationServer

server = McpServer("mcp-server-name")
await server.register_tool(add, alias="add")

authorization = McpAuthorizationServer()
await server.host("http://127.0.0.1:8000/mcp", authorization=authorization)
```

### Compatibility flags

`aiomcp` defaults to broad compatibility, so it can connect to a wider range of MCP clients and servers. Compatibility flags let you opt into stricter protocol checks when you want closer MCP enforcement.

```python
from aiomcp import McpClient, McpServer

mcp_client = McpClient(
    flags={"enforce_mcp_tool_result_content": True}
)

mcp_server = McpServer(
    flags={"enforce_mcp_initialize_sequence": True}
)
```

Available client flags
- `throw_mcp_contract_errors`: raise error when incoming MCP messages miss required contract fields.
- `throw_mcp_parse_errors`: raise when a transport receives malformed MCP JSON-RPC.
- `enforce_mcp_tools_capability`: require the server to advertise the `tools` capability before the client sends `tools/list`.
- `enforce_mcp_tool_result_content`: reject tool results that omit `content`.
- `convert_mcp_tool_result_content_format`: convert tool result content in MCP defined format.
- `enforce_mcp_version_negotiation`: reject unsupported negotiated protocol versions.
- `enforce_mcp_session_header`: require HTTP session headers where applicable.
- `enforce_mcp_protocol_header`: require HTTP protocol version headers where applicable.
- `enforce_mcp_transport_version_consistency`: require HTTP header and initialize body protocol versions to match.

Available server flags
- `throw_mcp_parse_errors`: raise when a transport receives malformed MCP JSON-RPC instead of ignoring it.
- `enforce_mcp_initialize_sequence`: reject requests sent before MCP initialization completes.
- `enforce_mcp_version_negotiation`: negotiate only supported protocol versions.
- `enforce_mcp_session_header`: require HTTP session headers where applicable.
- `enforce_mcp_protocol_header`: require HTTP protocol version headers where applicable.
- `skip_mcp_tool_output_schema`: skip output schema generation for registered tools.
- `enforce_mcp_tool_result_content_format`: validate tool result content against MCP content block.
- `allow_mcp_tool_result_empty_content`: allow tool results to omit the content.

