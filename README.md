# aiomcp
A Simple Python MCP Solution

## Tutorial

**Expected Behavior for Version 0.1.0**

### A simple McpServer

```python
def func(a: int, b: int):
    return a + b

impl = ToolImpl()

mcp_server = McpServer("mcp-server-name")
await mcp_server.register_tool("tool_1", func)
await mcp_server.register_tool("tool_2", impl.method)
await mcp_server.register_tool("tool_3", ToolImpl.class_method)
await mcp_server.host("http://127.0.0.1:8000/mcp")
```

### A simple McpClient

```python
mcp_client = McpClient("mcp-client-name")
await mcp_client.initialize("http://127.0.0.1:8000/mcp")
# mcp_client = mcp_client.initialize("server.py") for stdio
# mcp_client = mcp_client.initialize(mcp_server) for quick testing without hosting server
sum_value = await mcp_client.invoke("name", {"a": 1, "b": 2})
# standard MCP functions
# await mcp_client.mcp_tools_list() -> list[McpTool]
# await mcp_client.mcp_tools_call(tool: McpTool, request: McpRequest) -> McpResponse | McpError
```

### Using McpTransports
While `McpClient` and `McpServer` handle client/server behavior, they are implemented in a platform-agnostic way by using `McpClientTransport`/`McpServerTransport` to handle message streams. Client and server transports usually come in pairs as `McpTransport` implementations.

```python
mcp_server = McpServer()
await mcp_server.host(McpHttpTransport("127.0.0.1", 8000))
```

```python
mcp_client = McpClient()
await mcp_client.initialize(McpHttpTransport("127.0.0.1", 8000))
```

Supported `McpTransport` implementations:
- `McpDirectTransport`: Simple client driven transport that invokes `McpServer` with minimal MCP protocol overhead.
- `McpMemoryTransport`: Single event loop, in-memory transport.
- `McpHttpTransport`
- `McpStdioTransport`
- `McpSocksTransport`

Sample: Using McpMemoryTransport for local debugging.
```python
transport = McpMemoryTransport()

mcp_server = McpServer()
asyncio.create_task(mcp_server.host(transport))

mcp_client = McpClient()
await mcp_client.initialize(transport)
```

