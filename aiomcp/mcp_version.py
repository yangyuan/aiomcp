from typing import List, Optional

class McpVersion:
    LATEST = "2025-06-18"
    SUPPORTED = [LATEST]

    def __init__(self) -> None:
        self._negotiated_version: str = self.LATEST

    @property
    def default_version(self) -> str:
        return self.LATEST

    @property
    def supported_versions(self) -> List[str]:
        return self.SUPPORTED

    @property
    def version(self) -> str:
        return self._negotiated_version

    def negotiate(self, other_version: str) -> str:
        if other_version in self.SUPPORTED:
            self._negotiated_version = other_version
        else:
            self._negotiated_version = self.LATEST
        return self._negotiated_version

    def sync_as_client(
        self,
        body_version: Optional[str],
        header_version: Optional[str] = None,
        *,
        enforce_negotiation: bool = False,
        enforce_consistency: bool = False,
    ) -> str:
        """Align negotiated version using MCP payload + transport headers."""

        def _normalize(value: Optional[str]) -> Optional[str]:
            return value.strip() if isinstance(value, str) and value.strip() else None

        body = _normalize(body_version)
        header = _normalize(header_version)

        if (
            enforce_consistency
            and body is not None
            and header is not None
            and body != header
        ):
            raise RuntimeError(
                f"{McpVersion.__name__} server protocol version '{body}' does not match transport header '{header}'"
            )

        target = body or header or self.default_version

        if enforce_negotiation and target not in self.supported_versions:
            raise RuntimeError(
                f"{McpVersion.__name__} server protocol version '{target}' not supported"
            )

        return self.negotiate(target)
