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


def _github_body_of_size(size: int) -> bytes:
    padding = "x" * (size - len(github_body(bio="")))
    return github_body(bio=padding)


def test_github_corpo_com_exatamente_o_teto_e_aceito() -> None:
    body = _github_body_of_size(65_536)

    assert len(body) == 65_536
    assert github_identity(body).subject == "583231"


def test_github_corpo_um_byte_acima_do_teto_e_recusado() -> None:
    body = _github_body_of_size(65_537)

    assert len(body) == 65_537
    with pytest.raises(InvalidProviderIdentityError, match="grande"):
        github_identity(body)


@pytest.mark.parametrize(
    ("user_id", "subject"),
    [
        pytest.param(1, "1", id="menor-id"),
        pytest.param(9223372036854775807, "9223372036854775807", id="maior-bigint"),
    ],
)
def test_github_id_nas_fronteiras_do_bigint_positivo_e_aceito(user_id: int, subject: str) -> None:
    assert github_identity(github_body(id=user_id)).subject == subject


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("1" * 255, id="255-caracteres"),
        pytest.param("!", id="primeiro-ascii-visivel"),
        pytest.param("~", id="ultimo-ascii-visivel"),
    ],
)
def test_google_sub_na_fronteira_do_oidc_e_aceito(subject: str) -> None:
    assert google_identity({"iss": GOOGLE_ISSUER, "sub": subject}).subject == subject


def test_google_sub_com_del_e_recusado() -> None:
    with pytest.raises(InvalidProviderIdentityError):
        google_identity({"iss": GOOGLE_ISSUER, "sub": "a\x7fb"})


# O caso "sub-espaco" acima também tem `ç` e seria recusado mesmo que o espaço passasse; aqui, e
# nos casos seguintes, só o caractere em teste difere de um `sub` válido.
@pytest.mark.parametrize(
    "subject",
    [
        pytest.param(" 1092837465", id="inicio"),
        pytest.param("1092 837465", id="meio"),
        pytest.param("1092837465 ", id="fim"),
        pytest.param(" ", id="um-espaco"),
        pytest.param("   ", id="so-espacos"),
        pytest.param(" " * 255, id="so-espacos-no-teto"),
        pytest.param(" " + "1" * 254, id="inicio-dentro-do-teto"),
        pytest.param("1" * 254 + " ", id="fim-dentro-do-teto"),
    ],
)
def test_google_sub_com_espaco_e_recusado_em_vez_de_normalizado(subject: str) -> None:
    with pytest.raises(InvalidProviderIdentityError):
        google_identity({"iss": GOOGLE_ISSUER, "sub": subject})


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("\x001092837465", id="nul-inicio"),
        pytest.param("1092837465\x00", id="nul-fim"),
        pytest.param("1092837465\x01", id="soh-fim"),
        pytest.param("1092\x1f837465", id="us-vizinho-do-espaco"),
        pytest.param("\t1092837465", id="tab-inicio"),
        pytest.param("1092837465\t", id="tab-fim"),
        pytest.param("\n1092837465", id="lf-inicio"),
        pytest.param("1092\n837465", id="lf-meio"),
        pytest.param("1092837465\n", id="lf-fim"),
        pytest.param("1092837465\r", id="cr-fim"),
        pytest.param("1092837465\r\n", id="crlf-fim"),
        pytest.param("\x7f1092837465", id="del-inicio"),
        pytest.param("1092837465\x7f", id="del-fim"),
    ],
)
def test_google_sub_com_caractere_de_controle_e_recusado(subject: str) -> None:
    with pytest.raises(InvalidProviderIdentityError):
        google_identity({"iss": GOOGLE_ISSUER, "sub": subject})


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("1092837465\x80", id="primeiro-acima-do-ascii"),
        pytest.param("1092837465\x85", id="nel"),
        pytest.param("1092837465\xa0", id="nbsp"),
        pytest.param("1092837465\u2003", id="em-space"),
        pytest.param("1092837465\u3000", id="espaco-ideografico"),
        pytest.param("1092\u200b837465", id="largura-zero"),
        pytest.param("\uff11092837465", id="digito-largo"),
        pytest.param("1" * 254 + "é", id="nao-ascii-dentro-do-teto"),
        pytest.param("\ud800", id="surrogate-isolado"),
    ],
)
def test_google_sub_fora_do_ascii_e_recusado(subject: str) -> None:
    with pytest.raises(InvalidProviderIdentityError):
        google_identity({"iss": GOOGLE_ISSUER, "sub": subject})


def test_google_sub_de_um_caractere_e_aceito() -> None:
    assert google_identity({"iss": GOOGLE_ISSUER, "sub": "1"}).subject == "1"


def test_google_sub_preserva_a_caixa_sem_normalizar() -> None:
    upper = google_identity({"iss": GOOGLE_ISSUER, "sub": "AbC-123"})
    lower = google_identity({"iss": GOOGLE_ISSUER, "sub": "abc-123"})

    assert upper.subject == "AbC-123"
    assert lower.subject == "abc-123"
