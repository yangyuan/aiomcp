# aiomcp
A Simple Python MCP Solution

## Mission of aiomcp
Start with a smooth experience, end with a compliant solution.


## Tutorial

### A simple McpServer

```python
def func(a: int, b: int):
    return a + b

impl = ToolImpl()

mcp_server = McpServer("mcp-server-name")
await mcp_server.register_tool(func)
await mcp_server.register_tool(impl.method, alias="tool_alias")
await mcp_server.register_tool(ToolImpl.class_method)
await mcp_server.host("http://127.0.0.1:8000/mcp")
```

### A simple McpClient

```python
mcp_client = McpClient("mcp-client-name")
await mcp_client.initialize("http://127.0.0.1:8000/mcp")
# await mcp_client.initialize(McpHttpTransport("127.0.0.1", 8000, "/mcp"))
# await mcp_client.initialize(McpStdioClientTransport([sys.executable, "server.py"]))
# await mcp_client.initialize(mcp_server) for quick testing without hosting a server
sum_value = await mcp_client.invoke("name", {"a": 1, "b": 2})
# standard MCP functions
# await mcp_client.mcp_tools_list() -> list[McpTool]
# await mcp_client.mcp_tools_call(tool: McpTool, request: McpCallToolRequest) -> McpResponse | McpError
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

### Tool metadata

Use `Annotated` with Pydantic `Field` for parameter descriptions, and pass MCP tool annotations when registering tools.

```python
from typing import Annotated

from pydantic import Field


async def get_current_time(
    timezone: Annotated[
        str,
        Field(
            description="IANA timezone name, e.g. 'Europe/London'. Use '{CURRENT_TIMEZONE}' as local timezone."
        ),
    ],
) -> dict[str, str]:
    return {"timezone": timezone}


await mcp_server.register_tool(
    get_current_time,
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    format_map={"CURRENT_TIMEZONE": "America/Los_Angeles"},
)
```

### Tool Results and LLM Compatibility

The MCP specification defines tool results with both `content` and `structuredContent`. The `content` field should be a list of text, image, audio, resource link, or embedded resource blocks. The optional `structuredContent` field is a structured object that follows the tool's output schema when one exists.

As an MCP client, `aiomcp` follows the MCP specification and can optionally convert and merge `content` and `structuredContent` into a standard LLM-friendly content list. If an MCP server does not produce standard output, `aiomcp` still follows the MCP specification while tolerating non-standard behavior on a best-effort basis for maximum compatibility.

As an MCP server, `aiomcp` does not generate or enforce output schemas by default unless you opt in. For best LLM compatibility, tools should consider returning [a list of MCP content blocks](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#tool-result). A simple text response can be a dict like `{"type": "text", "text": "..."}` or a `McpTextContent`; richer responses can use `McpImageContent`, `McpAudioContent`, `McpResourceLink`, or `McpEmbeddedResource`. For advanced control, tools can return `McpCallToolResult` directly, and aiomcp will pass it through as-is. This is the best option when you want full control over what the MCP server returns.

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
- `auto_mcp_tool_output_schema`: create output schemas from registered tool.
- `enforce_mcp_tool_result_content_format`: validate tool result content against MCP content block.
- `allow_mcp_tool_result_empty_content`: allow tool results to omit the content.

