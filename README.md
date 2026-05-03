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
- `enforce_mcp_tools_capability`: require the server to advertise the `tools` capability before the client sends `tools/list`.
- `enforce_mcp_tool_result_content`: reject tool results that omit `content`.
- `enforce_mcp_version_negotiation`: reject unsupported negotiated protocol versions.
- `enforce_mcp_session_header`: require HTTP session headers where applicable.
- `enforce_mcp_protocol_header`: require HTTP protocol version headers where applicable.
- `enforce_mcp_transport_version_consistency`: require HTTP header and initialize body protocol versions to match.

Available server flags
- `enforce_mcp_initialize_sequence`: reject requests sent before MCP initialization completes.
- `enforce_mcp_version_negotiation`: negotiate only supported protocol versions.
- `enforce_mcp_session_header`: require HTTP session headers where applicable.
- `enforce_mcp_protocol_header`: require HTTP protocol version headers where applicable.

