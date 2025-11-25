import asyncio

import pytest
from aiomcp.mcp_server import McpServer
from aiomcp.mcp_context import McpSessionStatus
from aiomcp.mcp_client import McpClient
from aiomcp.transports.memory import McpMemoryTransport
from aiomcp.contracts.mcp_message import (
    McpInitializeRequest,
    McpInitializeParams,
    McpListToolsRequest,
    McpInitializedNotification,
    McpResponse,
    McpInitializeResult,
    McpListToolsResult,
)
from aiomcp.jsonrpc_error_codes import JsonRpcErrorCodes

@pytest.mark.asyncio
async def test_server_enforce_initialize_sequence():
    async def _run():
        server = McpServer()
        server._context.flags.enforce_mcp_initialize_sequence = True
        transport = McpMemoryTransport()
        
        # Start server hosting
        await server.create_host_task(transport)
        
        # 1. Send non-initialize request (tools/list)
        req_id = 1
        req = McpListToolsRequest(id=req_id)
        await transport.client_send_message(req)
        
        # Read response
        response = await transport._server_to_client.get()
        assert response.error.code == JsonRpcErrorCodes.INVALID_REQUEST.value
        assert "not initialized" in response.error.message
        
        # 2. Send initialize request
        req_id = 2
        init_req = McpInitializeRequest(
            id=req_id,
            params=McpInitializeParams(
                capabilities={},
                protocolVersion="2025-06-18",
                clientInfo={"name": "test", "version": "1.0"}
            )
        )
        await transport.client_send_message(init_req)
        
        response = await transport._server_to_client.get()
        assert response.id == req_id
        assert response.result is not None
        
        # Server status should be INITIALIZING
        assert server._get_session(None).status == McpSessionStatus.INITIALIZING
        
        # 3. Send tools/list before initialized notification
        req_id = 3
        req = McpListToolsRequest(id=req_id)
        await transport.client_send_message(req)
        
        response = await transport._server_to_client.get()
        assert response.error.code == JsonRpcErrorCodes.INVALID_REQUEST.value
        assert "waiting for initialized notification" in response.error.message
        
        # 4. Send initialized notification
        notif = McpInitializedNotification()
        await transport.client_send_message(notif)
        
        # Give server a moment to process notification (it's async)
        await asyncio.sleep(0.1)
        assert server._get_session(None).status == McpSessionStatus.INITIALIZED
        
        # 5. Send tools/list again
        req_id = 4
        req = McpListToolsRequest(id=req_id)
        await transport.client_send_message(req)
        
        response = await transport._server_to_client.get()
        assert response.id == req_id
        assert response.result is not None
        
        await server.shutdown()

    await asyncio.wait_for(_run(), timeout=5)

@pytest.mark.asyncio
async def test_server_version_negotiation():
    async def _run():
        server = McpServer()
        server._context.flags.enforce_mcp_version_negotiation = True
        transport = McpMemoryTransport()
        await server.create_host_task(transport)
        
        # 1. Supported version
        req_id = 1
        init_req = McpInitializeRequest(
            id=req_id,
            params=McpInitializeParams(
                capabilities={},
                protocolVersion="2025-06-18",
                clientInfo={"name": "test", "version": "1.0"}
            )
        )
        await transport.client_send_message(init_req)
        response = await transport._server_to_client.get()
        assert response.result["protocolVersion"] == "2025-06-18"
        
        # Reset server for next test
        await server.shutdown()
        
        server = McpServer()
        server._context.flags.enforce_mcp_version_negotiation = True
        transport = McpMemoryTransport()
        host_task = await server.create_host_task(transport)

        # 2. Unsupported version
        req_id = 2
        init_req = McpInitializeRequest(
            id=req_id,
            params=McpInitializeParams(
                capabilities={},
                protocolVersion="1.0.0",
                clientInfo={"name": "test", "version": "1.0"}
            )
        )
        await transport.client_send_message(init_req)
        response = await transport._server_to_client.get()
        # Should fall back to latest supported
        assert response.result["protocolVersion"] == "2025-06-18"
        
        await server.shutdown()

    await asyncio.wait_for(_run(), timeout=5)

@pytest.mark.asyncio
async def test_client_version_negotiation():
    async def _run():
        # We need a mock server that returns a specific version
        class MockServerTransport(McpMemoryTransport):
            def __init__(self, version_to_return):
                super().__init__()
                self.version_to_return = version_to_return
                
            async def run_server_loop(self):
                try:
                    while True:
                        msg = await self._client_to_server.get()
                        if isinstance(msg, McpInitializeRequest):
                            # Respond with configured version
                            result = McpInitializeResult(
                                capabilities={},
                                protocolVersion=self.version_to_return,
                                serverInfo={"name": "mock", "version": "0.0"}
                            )
                            resp = McpResponse(id=msg.id, result=result.model_dump())
                            await self._server_to_client.put(resp)
                        elif isinstance(msg, McpListToolsRequest):
                            result = McpListToolsResult(tools=[])
                            resp = McpResponse(id=msg.id, result=result.model_dump())
                            await self._server_to_client.put(resp)
                except asyncio.CancelledError:
                    raise

        # 1. Client enforces negotiation, server returns unsupported version
        client = McpClient()
        client._context.flags.enforce_mcp_version_negotiation = True
        transport = MockServerTransport("1.0.0") # Unsupported
        
        server_task = asyncio.create_task(transport.run_server_loop())
        
        try:
            with pytest.raises(RuntimeError) as excinfo:
                await client.initialize(transport)
            assert "protocol version '1.0.0' not supported" in str(excinfo.value)
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            await client.close()

        # 2. Client enforces negotiation, server returns supported version
        client = McpClient()
        client._context.flags.enforce_mcp_version_negotiation = True
        transport = MockServerTransport("2025-06-18") # Supported
        
        server_task = asyncio.create_task(transport.run_server_loop())
        
        try:
            # Should not raise
            await client.initialize(transport)
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            await client.close()

    await asyncio.wait_for(_run(), timeout=5)
