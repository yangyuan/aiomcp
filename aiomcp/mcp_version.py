from typing import List

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
