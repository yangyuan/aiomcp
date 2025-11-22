import pytest
from aiomcp.contracts.mcp_message import McpSystemError, McpError
from aiomcp.mcp_client import McpClient
from aiomcp.transports.memory import McpMemoryTransport

@pytest.mark.asyncio
async def test_mcp_system_error_validation():
    # Setup client and transport
    client = McpClient()
    
    # We need to mock the transport to yield messages
    # But McpClient.initialize starts the loop.
    # Let's manually trigger the validation logic by simulating message processing
    # or by using a real client-server interaction if possible, but unit test is better.
    
    # Since we modified _handle_message_loop, we can test that method.
    # But it's an infinite loop.
    
    # Let's create a mock transport that yields one message then stops
    class MockTransport(McpMemoryTransport):
        def __init__(self, messages):
            super().__init__()
            self._mock_messages = messages
            
        async def client_messages(self):
            for msg in self._mock_messages:
                yield msg
        
        async def client_initialize(self):
            pass
            
        async def client_send_message(self, message):
            pass

    # Case 1: Default behavior (tolerate missing code)
    client.flags.throw_mcp_contract_errors = False
    error_msg = McpError(id=1, error=McpSystemError())
    
    client._transport = MockTransport([error_msg])
    # This should not raise
    try:
        await client._handle_message_loop()
    except RuntimeError as e:
        # _handle_message_loop raises RuntimeError when stream closes
        assert "message stream closed unexpectedly" in str(e)

    # Case 2: Strict behavior (raise error)
    client.flags.throw_mcp_contract_errors = True
    error_msg = McpError(id=2, error=McpSystemError())
    
    client._transport = MockTransport([error_msg])
    
    with pytest.raises(ValueError) as excinfo:
        await client._handle_message_loop()
    assert "McpSystemError requires 'code'" in str(excinfo.value)

    # Case 3: Strict behavior with valid error
    error_msg = McpError(id=3, error=McpSystemError(code=123, message="Error"))
    client._transport = MockTransport([error_msg])
    
    try:
        await client._handle_message_loop()
    except RuntimeError as e:
        assert "message stream closed unexpectedly" in str(e)
