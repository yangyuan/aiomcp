class McpClientFlags:
    # such as missing required fields
    throw_mcp_contract_errors = False
    enforce_mcp_version_negotiation = False
    enforce_mcp_session_header = False
    enforce_mcp_protocol_header = False
    enforce_mcp_transport_version_consistency = False


class McpServerFlags:
    enforce_mcp_initialize_sequence = False
    enforce_mcp_version_negotiation = False
    enforce_mcp_session_header = False
    enforce_mcp_protocol_header = False