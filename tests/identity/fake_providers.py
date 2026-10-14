"""Google (OIDC) e GitHub (OAuth 2.0) falsos para os testes do login.

Um servidor local em processo, servido por `httpx2.MockTransport` nos mesmos endereços dos
provedores reais (os do código de produção, que não usa discovery). Ele se comporta como um
servidor de autorização exigente: o `code` vale uma vez, o `redirect_uri` da troca tem de ser
idêntico ao da autorização e ao cadastrado, o PKCE S256 é conferido e o cliente se autentica.
O Google emite ID tokens RS256 assinados com uma chave gerada no teste e publica a JWKS; cada
teste pode trocar os claims, o cabeçalho ou o token inteiro para simular ataques.
"""

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2 as httpx
from joserfc import jwt
from joserfc.jwk import RSAKey

from goblinshift.identity.providers import (
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_ISSUER,
    GOOGLE_JWKS_URL,
    GOOGLE_TOKEN_URL,
)
from goblinshift.platform.settings import OAuthClient

# Credenciais inventadas, só do provedor falso.
GOOGLE_CLIENT = OAuthClient("google-client.apps.test", "google-secret-de-teste")
GITHUB_CLIENT = OAuthClient("github-client", "github-secret-de-teste")
GOOGLE_SUBJECT = "1092837465"
GITHUB_USER: dict[str, object] = {
    "id": 583231,
    "login": "octocat",
    "email": "mesma@example.com",
}
KEY_ID = "chave-de-teste"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unsigned_jwt(header: dict[str, Any], claims: dict[str, Any], signature: bytes = b"") -> str:
    """JWT montado à mão, para os ataques que a biblioteca de JOSE se recusa a gerar."""
    head = b64url(json.dumps(header).encode())
    body = b64url(json.dumps(claims).encode())
    return f"{head}.{body}.{b64url(signature)}"


@dataclass
class Grant:
    provider: str
    client_id: str
    redirect_uri: str
    code_challenge: str | None
    code_challenge_method: str | None
    nonce: str | None


IdTokenFactory = Callable[[dict[str, Any], RSAKey], str]


@dataclass
class FakeProviders:
    key: RSAKey = field(
        default_factory=lambda: RSAKey.generate_key(2048, parameters={"kid": KEY_ID})
    )
    grants: dict[str, Grant] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    # Ajustes por teste.
    claims_patch: dict[str, Any] = field(default_factory=dict)
    id_token_factory: IdTokenFactory | None = None
    omit_id_token: bool = False
    google_subject: str = GOOGLE_SUBJECT
    github_user_body: bytes = field(default_factory=lambda: json.dumps(GITHUB_USER).encode())
    github_user_status: int = 200
    token_failure: httpx.Response | None = None
    network_failure: Exception | None = None
    issued_access_tokens: list[str] = field(default_factory=list)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def consent(self, authorize_location: str) -> str:
        """O usuário aprova no provedor: devolve o caminho do callback com `code` e `state`."""
        url = urlsplit(authorize_location)
        query = {name: values[0] for name, values in parse_qs(url.query).items()}
        provider = "google" if url.netloc == urlsplit(GOOGLE_AUTHORIZE_URL).netloc else "github"
        code = secrets.token_urlsafe(16)
        self.grants[code] = Grant(
            provider=provider,
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            code_challenge=query.get("code_challenge"),
            code_challenge_method=query.get("code_challenge_method"),
            nonce=query.get("nonce"),
        )
        callback = urlsplit(query["redirect_uri"])
        return f"{callback.path}?{urlencode({'code': code, 'state': query['state']})}"

    def id_token_claims(self, grant: Grant) -> dict[str, Any]:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": GOOGLE_ISSUER,
            "sub": self.google_subject,
            "aud": grant.client_id,
            "iat": now,
            "exp": now + 300,
            "email": GITHUB_USER["email"],
        }
        if grant.nonce is not None:
            claims["nonce"] = grant.nonce
        return claims | self.claims_patch

    def sign(self, claims: dict[str, Any], key: RSAKey | None = None) -> str:
        signer = key or self.key
        return jwt.encode({"alg": "RS256", "kid": KEY_ID}, claims, signer)

    def requests_to(self, url: str) -> list[httpx.Request]:
        return [request for request in self.requests if str(request.url).startswith(url)]

    # --- Servidor --------------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.network_failure is not None:
            raise self.network_failure
        url = str(request.url).split("?")[0]
        if url == GOOGLE_JWKS_URL:
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url in {GOOGLE_TOKEN_URL, GITHUB_TOKEN_URL}:
            return self._token(request, "google" if url == GOOGLE_TOKEN_URL else "github")
        if url == GITHUB_USER_URL:
            return self._github_user(request)
        return httpx.Response(404, text="endereço desconhecido no provedor falso")

    def _token(self, request: httpx.Request, provider: str) -> httpx.Response:
        if self.token_failure is not None:
            return self.token_failure
        form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
        grant = self.grants.pop(form.get("code", ""), None)
        client = GOOGLE_CLIENT if provider == "google" else GITHUB_CLIENT
        if not self._client_authenticated(request, client):
            return httpx.Response(401, json={"error": "invalid_client"})
        if (
            grant is None
            or grant.provider != provider
            or form.get("grant_type") != "authorization_code"
            or form.get("redirect_uri") != grant.redirect_uri
            or not _pkce_matches(grant, form.get("code_verifier"))
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        access_token = f"gho_{secrets.token_urlsafe(24)}"
        self.issued_access_tokens.append(access_token)
        body: dict[str, Any] = {"access_token": access_token, "token_type": "bearer"}
        if provider == "google":
            body |= {"expires_in": 3599, "scope": "openid"}
            if not self.omit_id_token:
                claims = self.id_token_claims(grant)
                factory = self.id_token_factory
                body["id_token"] = factory(claims, self.key) if factory else self.sign(claims)
        return httpx.Response(200, json=body)

    def _github_user(self, request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("authorization", "")
        if authorization.removeprefix("Bearer ") not in self.issued_access_tokens:
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(self.github_user_status, content=self.github_user_body)

    @staticmethod
    def _client_authenticated(request: httpx.Request, client: OAuthClient) -> bool:
        expected = base64.b64encode(f"{client.client_id}:{client.client_secret}".encode())
        return request.headers.get("authorization") == f"Basic {expected.decode()}"


def _pkce_matches(grant: Grant, verifier: str | None) -> bool:
    if grant.code_challenge is None or grant.code_challenge_method != "S256" or not verifier:
        return False
    return b64url(hashlib.sha256(verifier.encode()).digest()) == grant.code_challenge
