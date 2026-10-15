"""Normalização das identidades do Google e do GitHub (ADR 008; 3.6, terceiro malicioso)."""

import json

import pytest

from goblinshift.identity import ExternalIdentity
from goblinshift.identity.providers import (
    GITHUB_ISSUER,
    GOOGLE_DISPLAY_NAME,
    GOOGLE_ISSUER,
    MAX_GITHUB_USER_BYTES,
    InvalidProviderIdentityError,
    github_identity,
    google_identity,
)

GITHUB_USER = {"id": 583231, "login": "octocat", "email": "octocat@example.com", "name": "Mona"}


def github_body(**changes: object) -> bytes:
    return json.dumps(GITHUB_USER | changes).encode()


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
def test_google_normaliza_o_emissor(issuer: str) -> None:
    identity = google_identity({"iss": issuer, "sub": "1092837465"})

    assert identity == ExternalIdentity(GOOGLE_ISSUER, "1092837465", GOOGLE_DISPLAY_NAME)


def test_google_nome_de_exibicao_e_fixo_mesmo_com_claims_de_perfil() -> None:
    identity = google_identity(
        {"iss": GOOGLE_ISSUER, "sub": "1", "name": "Fulano", "email": "f@example.com"}
    )

    assert identity.display_name == "Conta Google"


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example", "sub": "1"},
        {"iss": GOOGLE_ISSUER},
        {"iss": GOOGLE_ISSUER, "sub": ""},
        {"iss": GOOGLE_ISSUER, "sub": 1092837465},
        {"iss": GOOGLE_ISSUER, "sub": "1" * 256},
        {"iss": GOOGLE_ISSUER, "sub": "12\x0034"},
        {"iss": GOOGLE_ISSUER, "sub": "conta com espaço"},
    ],
    ids=["emissor", "sem-sub", "sub-vazio", "sub-numero", "sub-longo", "sub-nul", "sub-espaco"],
)
def test_google_claims_invalidas_sao_recusadas(claims: dict[str, object]) -> None:
    with pytest.raises(InvalidProviderIdentityError):
        google_identity(claims)


def test_github_identidade_pelo_id_numerico_e_nome_pelo_login() -> None:
    identity = github_identity(github_body())

    assert identity == ExternalIdentity(GITHUB_ISSUER, "583231", "octocat")


@pytest.mark.parametrize("login", ["a", "a" * 39, "octo-cat-9"])
def test_github_login_valido(login: str) -> None:
    assert github_identity(github_body(login=login)).display_name == login


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "583231"},
        {"id": -1},
        {"id": 0},
        {"id": True},
        {"id": 1.5},
        {"id": 2**63},
        {"id": None},
        {"login": ""},
        {"login": "a" * 40},
        {"login": "octo\x00cat"},
        {"login": "octo cat"},
        {"login": 42},
    ],
    ids=[
        "id-texto",
        "id-negativo",
        "id-zero",
        "id-booleano",
        "id-fracao",
        "id-gigante",
        "id-nulo",
        "login-vazio",
        "login-longo",
        "login-nul",
        "login-espaco",
        "login-numero",
    ],
)
def test_github_usuario_malicioso_e_recusado(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidProviderIdentityError):
        github_identity(github_body(**changes))


@pytest.mark.parametrize(
    "body",
    [b"<html>erro</html>", b"[]", b"", b'{"login": "octocat"}'],
    ids=["html", "lista", "vazio", "sem-id"],
)
def test_github_corpo_que_nao_e_usuario_e_recusado(body: bytes) -> None:
    with pytest.raises(InvalidProviderIdentityError):
        github_identity(body)


def test_github_corpo_acima_do_teto_e_recusado_sem_ler_o_json() -> None:
    padding = "x" * MAX_GITHUB_USER_BYTES
    body = github_body(bio=padding)

    with pytest.raises(InvalidProviderIdentityError, match="grande"):
        github_identity(body)
