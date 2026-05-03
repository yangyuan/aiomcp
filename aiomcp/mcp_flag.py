from pydantic import BaseModel, ConfigDict


class McpClientFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # such as missing required fields
    throw_mcp_contract_errors: bool = False
    throw_mcp_parse_errors: bool = False
    enforce_mcp_tools_capability: bool = False
    enforce_mcp_tool_result_content: bool = False
    enforce_mcp_version_negotiation: bool = False
    enforce_mcp_session_header: bool = False
    enforce_mcp_protocol_header: bool = False
    enforce_mcp_transport_version_consistency: bool = False


class McpServerFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    throw_mcp_parse_errors: bool = False
    enforce_mcp_initialize_sequence: bool = False
    enforce_mcp_version_negotiation: bool = False
    enforce_mcp_session_header: bool = False
    enforce_mcp_protocol_header: bool = False
