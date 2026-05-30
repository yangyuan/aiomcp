from __future__ import annotations

from collections import OrderedDict
from enum import StrEnum
from typing import Callable, Optional

from aiomcp.mcp_flag import McpServerFlags, McpClientFlags
from aiomcp.mcp_version import McpVersion

DEFAULT_SESSION_ID = "default"


class McpSessionStatus(StrEnum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"


class McpSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.status: McpSessionStatus = McpSessionStatus.UNINITIALIZED
        self.version: McpVersion = McpVersion()


class McpSessionManager:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, McpSession] = OrderedDict()
        self._expired_sessions: OrderedDict[str, None] = OrderedDict()
        self._release_callbacks: list[Callable[[str], None]] = []
        self.max_sessions = 1000
        self.max_expired_sessions = 1000

    @staticmethod
    def normalize_session_id(session_id: Optional[str]) -> str:
        return session_id or DEFAULT_SESSION_ID

    def configure_limits(self, max_sessions: int, max_expired_sessions: int) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if max_expired_sessions < 0:
            raise ValueError("max_expired_sessions must be at least 0")
        self.max_sessions = max_sessions
        self.max_expired_sessions = max_expired_sessions

    def add_release_callback(self, callback: Callable[[str], None]) -> None:
        self._release_callbacks.append(callback)

    def _release_session_id(self, session_id: str) -> None:
        for callback in self._release_callbacks:
            callback(session_id)

    def get_session(self, session_id: Optional[str]) -> McpSession:
        key = self.normalize_session_id(session_id)
        session = self._sessions.get(key)
        if session is not None:
            self._sessions.move_to_end(key)
            return session

        if len(self._sessions) >= self.max_sessions:
            _, released_session = self._sessions.popitem(last=False)
            self._add_expired_session_id(released_session.id)
            self._release_session_id(released_session.id)

        session = McpSession(key)
        self._expired_sessions.pop(key, None)
        self._sessions[key] = session
        return session

    def terminate_session(self, session_id: Optional[str]) -> None:
        key = self.normalize_session_id(session_id)
        session = self._sessions.pop(key, None)
        if session is not None:
            self._release_session_id(session.id)
        self._add_expired_session_id(key)

    def is_session_expired(self, session_id: Optional[str]) -> bool:
        key = self.normalize_session_id(session_id)
        return key in self._expired_sessions

    def _add_expired_session_id(self, session_id: str) -> None:
        if self.max_expired_sessions == 0:
            return
        self._expired_sessions.pop(session_id, None)
        self._expired_sessions[session_id] = None
        while len(self._expired_sessions) > self.max_expired_sessions:
            self._expired_sessions.popitem(last=False)

    def iter_sessions(self):
        return self._sessions.values()


class McpServerContext:
    def __init__(
        self,
        flags: Optional[McpServerFlags] = None,
        *,
        max_sessions: int = 1000,
        max_expired_sessions: int = 1000,
    ) -> None:
        self.flags = flags or McpServerFlags()
        self.sessions = McpSessionManager()
        self.sessions.configure_limits(max_sessions, max_expired_sessions)

    def add_session_release_callback(self, callback: Callable[[str], None]) -> None:
        self.sessions.add_release_callback(callback)

    def get_session(self, session_id: Optional[str]) -> McpSession:
        return self.sessions.get_session(session_id)

    def normalize_session_id(self, session_id: Optional[str]) -> str:
        return self.sessions.normalize_session_id(session_id)

    def terminate_session(self, session_id: Optional[str]) -> None:
        self.sessions.terminate_session(session_id)

    def is_session_expired(self, session_id: Optional[str]) -> bool:
        return self.sessions.is_session_expired(session_id)

    def iter_sessions(self):
        return self.sessions.iter_sessions()


class McpClientContext:
    def __init__(self, flags: Optional[McpClientFlags] = None) -> None:
        self.flags = flags or McpClientFlags()
        self.session_id: Optional[str] = None
        self.version: McpVersion = McpVersion()
