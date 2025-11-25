from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Dict, Optional

from aiomcp.mcp_flag import McpServerFlags, McpClientFlags
from aiomcp.mcp_version import McpVersion


class McpSessionStatus(StrEnum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"


class McpSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.status: McpSessionStatus = McpSessionStatus.UNINITIALIZED
        self.inflight: Optional[asyncio.Task] = None
        self.version: McpVersion = McpVersion()


class McpServerContext:
    def __init__(self, flags: Optional[McpServerFlags] = None) -> None:
        self.flags = flags or McpServerFlags()
        self._sessions: Dict[str, McpSession] = {}

    @staticmethod
    def _normalize_session_id(session_id: Optional[str]) -> str:
        return session_id or "default"

    def get_session(self, session_id: Optional[str]) -> McpSession:
        key = self._normalize_session_id(session_id)
        if key not in self._sessions:
            self._sessions[key] = McpSession(key)
        return self._sessions[key]

    def iter_sessions(self):
        return self._sessions.values()


class McpClientContext:
    def __init__(self, flags: Optional[McpClientFlags] = None) -> None:
        self.flags = flags or McpClientFlags()
        self.session_id: Optional[str] = None
        self.version: McpVersion = McpVersion()
