from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Dict, Optional, Union

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
        self.request_tasks: Dict[Union[int, str], asyncio.Task] = {}
        self.version: McpVersion = McpVersion()


class McpServerContext:
    def __init__(self, flags: Optional[McpServerFlags] = None) -> None:
        self.flags = flags or McpServerFlags()
        self._sessions: Dict[str, McpSession] = {}
        # Tombstones prevent terminated HTTP sessions from being recreated
        self._expired_sessions: set[str] = set()

    @staticmethod
    def _normalize_session_id(session_id: Optional[str]) -> str:
        return session_id or "default"

    def get_session(self, session_id: Optional[str]) -> McpSession:
        key = self._normalize_session_id(session_id)
        if key not in self._sessions:
            self._sessions[key] = McpSession(key)
        return self._sessions[key]

    def terminate_session(self, session_id: Optional[str]) -> None:
        key = self._normalize_session_id(session_id)
        self._sessions.pop(key, None)
        self._expired_sessions.add(key)

    def is_session_expired(self, session_id: Optional[str]) -> bool:
        key = self._normalize_session_id(session_id)
        return key in self._expired_sessions

    def iter_sessions(self):
        return self._sessions.values()


class McpClientContext:
    def __init__(self, flags: Optional[McpClientFlags] = None) -> None:
        self.flags = flags or McpClientFlags()
        self.session_id: Optional[str] = None
        self.version: McpVersion = McpVersion()
