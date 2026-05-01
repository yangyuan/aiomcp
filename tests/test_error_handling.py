from aiomcp.contracts.mcp_message import McpSystemError, McpError
from aiomcp.mcp_client import McpClient
import pytest


def test_mcp_system_error_validation():
    client = McpClient()

    client._context.flags.throw_mcp_contract_errors = False
    error_msg = McpError(id=1, error=McpSystemError())
    client._validate_system_error(error_msg)

    client._context.flags.throw_mcp_contract_errors = True
    error_msg = McpError(id=2, error=McpSystemError())

    with pytest.raises(ValueError) as excinfo:
        client._validate_system_error(error_msg)
    assert "McpSystemError requires 'code'" in str(excinfo.value)

    error_msg = McpError(id=3, error=McpSystemError(code=123, message="Error"))
    client._validate_system_error(error_msg)
