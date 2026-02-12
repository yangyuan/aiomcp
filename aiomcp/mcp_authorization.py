import asyncio
import re
import secrets
import time
import urllib.parse
import webbrowser
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

import aiohttp
from aiohttp import web
from authlib.common.security import generate_token
from authlib.jose import jwt as authlib_jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge


@runtime_checkable
class AsyncOAuth2ClientLike(Protocol):
    def create_authorization_url(
        self,
        url: str,
        state: Optional[str] = None,
        code_verifier: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[str, str]: ...

    async def fetch_token(
        self,
        url: str,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        **kwargs: Any,
    ) -> Dict[str, Any]: ...

    @property
    def token(self) -> Optional[Dict[str, Any]]: ...


class McpAsyncOAuth2Client:
    def __init__(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        *,
        scope: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        code_challenge_method: str = "S256",
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.redirect_uri = redirect_uri
        self.code_challenge_method = code_challenge_method
        self._token: Optional[Dict[str, Any]] = None
        self._session = session
        self._owns_session = session is None

    @property
    def token(self) -> Optional[Dict[str, Any]]:
        return self._token

    @token.setter
    def token(self, value: Optional[Dict[str, Any]]) -> None:
        self._token = value

    def create_authorization_url(
        self,
        url: str,
        state: Optional[str] = None,
        code_verifier: Optional[str] = None,
        **kwargs: Any,
    ) -> Tuple[str, str]:
        if state is None:
            state = generate_token()

        params: Dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "state": state,
        }
        if self.redirect_uri:
            params["redirect_uri"] = self.redirect_uri
        if self.scope:
            params["scope"] = self.scope
        if code_verifier and self.code_challenge_method == "S256":
            params["code_challenge"] = create_s256_code_challenge(code_verifier)
            params["code_challenge_method"] = "S256"

        params.update(kwargs)
        uri = f"{url}?{urllib.parse.urlencode(params)}"
        return uri, state

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def fetch_token(
        self,
        url: str,
        *,
        code: str,
        redirect_uri: Optional[str] = None,
        code_verifier: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        session = await self._ensure_session()

        data: Dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
        }
        if redirect_uri or self.redirect_uri:
            data["redirect_uri"] = redirect_uri or self.redirect_uri  # type: ignore[assignment]
        if code_verifier:
            data["code_verifier"] = code_verifier
        if self.client_secret:
            data["client_secret"] = self.client_secret

        async with session.post(
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        ) as resp:
            resp.raise_for_status()
            token_data: Dict[str, Any] = await resp.json(content_type=None)

        if "error" in token_data:
            error = token_data["error"]
            desc = token_data.get("error_description", "")
            raise RuntimeError(f"Token exchange failed: {error} — {desc}")

        if "access_token" not in token_data:
            raise RuntimeError(f"Token response missing access_token: {token_data}")

        self._token = token_data
        return token_data

    async def close(self) -> None:
        if self._session and self._owns_session:
            await self._session.close()
            self._session = None


class McpAuthorizationClient:
    def __init__(
        self,
        access_token: str,
        *,
        refresh_token: Optional[str] = None,
        token_endpoint: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        expires_at: Optional[float] = None,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._expires_at = expires_at

    @property
    def access_token(self) -> str:
        return self._access_token

    async def get_access_token(self) -> str:
        if self._should_refresh():
            await self._refresh()
        return self._access_token

    def _should_refresh(self) -> bool:
        if self._expires_at is None:
            return False
        if self._refresh_token is None or self._token_endpoint is None:
            return False
        return time.time() >= self._expires_at - 30

    async def _refresh(self) -> None:
        assert self._token_endpoint is not None
        assert self._refresh_token is not None

        data: Dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        if self._client_id:
            data["client_id"] = self._client_id
        if self._client_secret:
            data["client_secret"] = self._client_secret

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._token_endpoint,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            ) as resp:
                resp.raise_for_status()
                token_data = await resp.json(content_type=None)

        self._access_token = token_data["access_token"]
        if "refresh_token" in token_data:
            self._refresh_token = token_data["refresh_token"]
        if "expires_in" in token_data:
            self._expires_at = time.time() + token_data["expires_in"]

    @staticmethod
    async def discover(
        server_url: str,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_host: str = "localhost",
        redirect_port: int = 8400,
        scopes: Optional[List[str]] = None,
        open_browser: bool = True,
        oauth2_client: Optional[AsyncOAuth2ClientLike] = None,
    ) -> "McpAuthorizationClient":
        redirect_uri = f"http://{redirect_host}:{redirect_port}/callback"
        scopes = list(scopes) if scopes else []

        async with aiohttp.ClientSession() as http:
            protected_resource_metadata_url = (
                await McpAuthorizationClient._discover_protected_resource_metadata_url(
                    http, server_url
                )
            )
            protected_resource_metadata = await McpAuthorizationClient._fetch_json(
                http, protected_resource_metadata_url
            )

            auth_servers: List[str] = protected_resource_metadata.get(
                "authorization_servers", []
            )
            if not auth_servers:
                raise RuntimeError(
                    "Protected Resource Metadata has no authorization_servers"
                )
            resource_scopes: List[str] = protected_resource_metadata.get(
                "scopes_supported", []
            )
            if not scopes:
                scopes = resource_scopes

            auth_server_url = auth_servers[0]
            as_metadata = await McpAuthorizationClient._discover_auth_server_metadata(
                http, auth_server_url
            )
            authorization_endpoint: str = as_metadata["authorization_endpoint"]
            token_endpoint: str = as_metadata["token_endpoint"]
            registration_endpoint: Optional[str] = as_metadata.get(
                "registration_endpoint"
            )

            if client_id is None:
                if registration_endpoint is None:
                    raise RuntimeError(
                        "No client_id provided and authorization server does not "
                        "support Dynamic Client Registration (no registration_endpoint)"
                    )
                reg_result = await McpAuthorizationClient._dynamic_client_registration(
                    http, registration_endpoint, redirect_uri
                )
                client_id = reg_result["client_id"]
                client_secret = reg_result.get("client_secret", client_secret)

            owns_client = False
            if oauth2_client is None:
                oauth2_client = McpAsyncOAuth2Client(
                    client_id,
                    client_secret,
                    scope=" ".join(scopes) if scopes else None,
                    redirect_uri=redirect_uri,
                    session=http,
                )
                owns_client = True

            code_verifier = generate_token(48)
            auth_url, state = oauth2_client.create_authorization_url(
                authorization_endpoint,
                code_verifier=code_verifier,
            )

            authorization_code = (
                await McpAuthorizationClient._wait_for_authorization_code(
                    host=redirect_host,
                    port=redirect_port,
                    auth_url=auth_url,
                    expected_state=state,
                    open_browser=open_browser,
                )
            )

            token_data = await oauth2_client.fetch_token(
                token_endpoint,
                code=authorization_code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )

            if owns_client and isinstance(oauth2_client, McpAsyncOAuth2Client):
                oauth2_client._session = None

        expires_at: Optional[float] = None
        if "expires_in" in token_data:
            expires_at = time.time() + token_data["expires_in"]

        return McpAuthorizationClient(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_endpoint=token_endpoint,
            client_id=client_id,
            client_secret=client_secret,
            expires_at=expires_at,
        )

    @staticmethod
    async def _discover_protected_resource_metadata_url(
        session: aiohttp.ClientSession, server_url: str
    ) -> str:
        async with session.post(
            server_url,
            data="{}",
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status == 401:
                www_auth = resp.headers.get("WWW-Authenticate", "")
                protected_resource_metadata_url = (
                    McpAuthorizationClient._parse_resource_metadata_url(www_auth)
                )
                if protected_resource_metadata_url:
                    return protected_resource_metadata_url
                return McpAuthorizationClient._derive_protected_resource_metadata_url(
                    server_url
                )
            raise RuntimeError(
                f"Server at {server_url} did not return 401 (got {resp.status}). "
                "OAuth authorization may not be required."
            )

    @staticmethod
    def _parse_resource_metadata_url(www_authenticate: str) -> Optional[str]:
        match = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _derive_protected_resource_metadata_url(server_url: str) -> str:
        parsed = urllib.parse.urlparse(server_url)
        return (
            f"{parsed.scheme}://{parsed.netloc}"
            f"/.well-known/oauth-protected-resource{parsed.path}"
        )

    @staticmethod
    async def _discover_auth_server_metadata(
        session: aiohttp.ClientSession, auth_server_url: str
    ) -> Dict[str, Any]:
        base = auth_server_url.rstrip("/")
        parsed = urllib.parse.urlparse(base)

        candidates = [
            f"{parsed.scheme}://{parsed.netloc}/.well-known/openid-configuration{parsed.path}",
            f"{base}/.well-known/openid-configuration",
            f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server{parsed.path}",
            f"{base}/.well-known/oauth-authorization-server",
        ]

        for url in candidates:
            try:
                data = await McpAuthorizationClient._fetch_json(session, url)
                if "authorization_endpoint" in data and "token_endpoint" in data:
                    return data
            except Exception:
                continue

        raise RuntimeError(
            f"Could not discover authorization server metadata for {auth_server_url}"
        )

    @staticmethod
    async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    @staticmethod
    async def _dynamic_client_registration(
        session: aiohttp.ClientSession,
        registration_endpoint: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        payload = {
            "client_name": "aiomcp-client",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        async with session.post(
            registration_endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    @staticmethod
    async def _wait_for_authorization_code(
        host: str,
        port: int,
        auth_url: str,
        expected_state: str,
        open_browser: bool,
    ) -> str:
        code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        async def _callback_handler(request: web.Request) -> web.Response:
            error = request.query.get("error")
            if error:
                desc = request.query.get("error_description", "")
                code_future.set_exception(
                    RuntimeError(f"Authorization failed: {error} — {desc}")
                )
                return web.Response(
                    text="Authorization failed. You can close this window.",
                    content_type="text/plain",
                )

            state = request.query.get("state", "")
            if state != expected_state:
                code_future.set_exception(
                    RuntimeError("State mismatch in OAuth callback")
                )
                return web.Response(
                    text="State mismatch. You can close this window.",
                    content_type="text/plain",
                )

            code = request.query.get("code")
            if not code:
                code_future.set_exception(
                    RuntimeError("No authorization code in callback")
                )
                return web.Response(
                    text="No code received. You can close this window.",
                    content_type="text/plain",
                )

            code_future.set_result(code)
            return web.Response(
                text="Authorization successful! You can close this window.",
                content_type="text/plain",
            )

        app = web.Application()
        app.router.add_get("/callback", _callback_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        try:
            if open_browser:
                webbrowser.open(auth_url)
            else:
                print(f"Open this URL in your browser to authorize:\n{auth_url}")
            code = await code_future
        finally:
            await runner.cleanup()

        return code


_AUTHORIZE_HTML = """<!DOCTYPE html>
<html><body style="font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0">
<div style="text-align:center">
<h2>Authorize {client_name}</h2>
<p>Scopes: <code>{scope}</code></p>
<form method="GET" action="{approve_action}">
<input type="hidden" name="client_id" value="{client_id}">
<input type="hidden" name="redirect_uri" value="{redirect_uri}">
<input type="hidden" name="state" value="{state}">
<input type="hidden" name="scope" value="{scope}">
<input type="hidden" name="code_challenge" value="{code_challenge}">
<input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
<button type="submit" style="font-size:1.2em;padding:12px 32px;cursor:pointer">Approve</button>
</form>
</div></body></html>"""


class McpAuthorizationServer:
    def __init__(
        self,
        *,
        scopes: Optional[List[str]] = None,
        token_expiry: int = 3600,
    ) -> None:
        self._scopes = scopes or ["mcp:tools"]
        self._token_expiry = token_expiry
        self._signing_key = secrets.token_hex(32)
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._auth_codes: Dict[str, Dict[str, Any]] = {}
        self._base_url: Optional[str] = None
        self._mcp_path: Optional[str] = None

    def _ensure_base_url(self) -> str:
        assert (
            self._base_url is not None
        ), "McpAuthorizationServer not bound to a server"
        return self._base_url

    def bind(self, hostname: str, port: int, mcp_path: str) -> None:
        self._base_url = f"http://{hostname}:{port}"
        self._mcp_path = mcp_path

    def register_routes(self, app: web.Application) -> None:
        base = self._ensure_base_url()
        mcp_path = self._mcp_path or "/"
        protected_resource_metadata_path = (
            f"/.well-known/oauth-protected-resource{mcp_path}"
        )

        app.router.add_get(
            protected_resource_metadata_path, self._handle_protected_resource_metadata
        )
        app.router.add_get(
            "/.well-known/oauth-authorization-server", self._handle_as_metadata
        )
        app.router.add_post("/oauth/register", self._handle_register)
        app.router.add_get("/oauth/authorize", self._handle_authorize)
        app.router.add_get("/oauth/approve", self._handle_approve)
        app.router.add_post("/oauth/token", self._handle_token)

    def validate_bearer_token(self, request: web.Request) -> Optional[Dict[str, Any]]:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]
        try:
            claims = authlib_jwt.decode(token, self._signing_key)
            claims.validate()
            return dict(claims)
        except Exception:
            return None

    def create_401_response(self) -> web.Response:
        base = self._ensure_base_url()
        mcp_path = self._mcp_path or "/"
        protected_resource_metadata_url = (
            f"{base}/.well-known/oauth-protected-resource{mcp_path}"
        )
        return web.Response(
            status=401,
            headers={
                "WWW-Authenticate": (
                    f'Bearer realm="mcp", '
                    f'resource_metadata="{protected_resource_metadata_url}"'
                ),
            },
        )

    async def _handle_protected_resource_metadata(
        self, request: web.Request
    ) -> web.Response:
        base = self._ensure_base_url()
        mcp_path = self._mcp_path or "/"
        return web.json_response(
            {
                "resource": f"{base}{mcp_path}",
                "authorization_servers": [base],
                "scopes_supported": self._scopes,
            }
        )

    async def _handle_as_metadata(self, request: web.Request) -> web.Response:
        base = self._ensure_base_url()
        return web.json_response(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/oauth/authorize",
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": self._scopes,
            }
        )

    async def _handle_register(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_request"}, status=400)

        client_id = generate_token(24)
        client_info: Dict[str, Any] = {
            "client_id": client_id,
            "client_name": body.get("client_name", "unknown"),
            "redirect_uris": body.get("redirect_uris", []),
            "grant_types": body.get("grant_types", ["authorization_code"]),
            "response_types": body.get("response_types", ["code"]),
            "token_endpoint_auth_method": body.get(
                "token_endpoint_auth_method", "none"
            ),
        }
        self._clients[client_id] = client_info
        return web.json_response(client_info, status=201)

    async def _handle_authorize(self, request: web.Request) -> web.Response:
        client_id = request.query.get("client_id", "")
        redirect_uri = request.query.get("redirect_uri", "")
        state = request.query.get("state", "")
        scope = request.query.get("scope", "")
        code_challenge = request.query.get("code_challenge", "")
        code_challenge_method = request.query.get("code_challenge_method", "")

        if client_id not in self._clients:
            return web.json_response({"error": "invalid_client"}, status=400)

        client = self._clients[client_id]
        client_name = client.get("client_name", client_id)

        base = self._ensure_base_url()
        html = _AUTHORIZE_HTML.format(
            client_name=client_name,
            scope=scope or "(none)",
            approve_action=f"{base}/oauth/approve",
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_approve(self, request: web.Request) -> web.Response:
        client_id = request.query.get("client_id", "")
        redirect_uri = request.query.get("redirect_uri", "")
        state = request.query.get("state", "")
        scope = request.query.get("scope", "")
        code_challenge = request.query.get("code_challenge", "")
        code_challenge_method = request.query.get("code_challenge_method", "")

        if client_id not in self._clients:
            return web.json_response({"error": "invalid_client"}, status=400)

        code = generate_token(48)
        self._auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "created_at": time.time(),
        }

        params = {"code": code}
        if state:
            params["state"] = state
        location = f"{redirect_uri}?{urllib.parse.urlencode(params)}"
        raise web.HTTPFound(location)

    async def _handle_token(self, request: web.Request) -> web.Response:
        body = await request.post()
        grant_type = body.get("grant_type", "")

        if grant_type == "authorization_code":
            return await self._handle_token_authorization_code(body)
        elif grant_type == "refresh_token":
            return await self._handle_token_refresh(body)
        else:
            return web.json_response({"error": "unsupported_grant_type"}, status=400)

    async def _handle_token_authorization_code(self, body: Any) -> web.Response:
        code = body.get("code", "")
        client_id = body.get("client_id", "")
        code_verifier = body.get("code_verifier", "")

        auth_code = self._auth_codes.pop(code, None)
        if auth_code is None:
            return web.json_response({"error": "invalid_grant"}, status=400)

        if auth_code["client_id"] != client_id:
            return web.json_response({"error": "invalid_grant"}, status=400)

        if auth_code.get("code_challenge"):
            expected = create_s256_code_challenge(code_verifier)
            if expected != auth_code["code_challenge"]:
                return web.json_response(
                    {
                        "error": "invalid_grant",
                        "error_description": "PKCE verification failed",
                    },
                    status=400,
                )

        if time.time() - auth_code["created_at"] > 600:
            return web.json_response(
                {
                    "error": "invalid_grant",
                    "error_description": "Authorization code expired",
                },
                status=400,
            )

        return self._issue_tokens(client_id, auth_code.get("scope", ""))

    async def _handle_token_refresh(self, body: Any) -> web.Response:
        refresh_token = body.get("refresh_token", "")
        try:
            claims = authlib_jwt.decode(refresh_token, self._signing_key)
            claims.validate()
        except Exception:
            return web.json_response({"error": "invalid_grant"}, status=400)

        client_id = claims.get("sub", "")
        scope = claims.get("scope", "")
        return self._issue_tokens(client_id, scope)

    def _issue_tokens(self, client_id: str, scope: str) -> web.Response:
        base = self._ensure_base_url()
        now = int(time.time())

        access_payload = {
            "iss": base,
            "sub": client_id,
            "aud": f"{base}{self._mcp_path or '/'}",
            "exp": now + self._token_expiry,
            "iat": now,
            "scope": scope,
            "type": "access",
        }
        access_token = authlib_jwt.encode(
            {"alg": "HS256"}, access_payload, self._signing_key
        ).decode("utf-8")

        refresh_payload = {
            "iss": base,
            "sub": client_id,
            "exp": now + self._token_expiry * 24,
            "iat": now,
            "scope": scope,
            "type": "refresh",
        }
        refresh_token = authlib_jwt.encode(
            {"alg": "HS256"}, refresh_payload, self._signing_key
        ).decode("utf-8")

        return web.json_response(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": self._token_expiry,
                "refresh_token": refresh_token,
                "scope": scope,
            }
        )
