"""Token CSRF assinado e ligado à sessão, e Fetch Metadata (docs/GoblinShiftPlan.md, 3.6).

Unitário, sem banco. As chaves de exemplo têm baixa entropia de propósito, para o gitleaks não
as confundir com segredos reais.
"""

from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from goblinshift.identity import (
    cross_site_request,
    csrf_pair_matches,
    csrf_token_matches,
    issue_csrf_token,
)

KEY = b"k" * 32
OTHER_KEY = b"q" * 32
SESSION_ID = UUID("00000000-0000-0000-0000-000000000001")
# aleatório ‖ "." ‖ base64url(HMAC-SHA256(KEY, SESSION_ID.bytes ‖ aleatório)), calculado à parte.
KNOWN_TOKEN = "A" * 43 + ".Wow566Gd6QLBHO3RVWk479PZZANaXqN8jy-RN9zSyAo"
ORIGIN = "https://goblinshift.dev"
TOKEN_LENGTH = 87


def _matches(token: str | None, header: str | None = None, session_id: UUID = SESSION_ID) -> bool:
    return csrf_token_matches(KEY, session_id, token, token if header is None else header)


# --- Token ------------------------------------------------------------------------------------


def test_token_calculado_a_mao_confere() -> None:
    assert _matches(KNOWN_TOKEN)


def test_token_emitido_confere_com_a_propria_sessao() -> None:
    token = issue_csrf_token(KEY, SESSION_ID)

    assert _matches(token)


def test_token_emitido_e_ascii_de_tamanho_fixo() -> None:
    token = issue_csrf_token(KEY, SESSION_ID)

    assert len(token) == TOKEN_LENGTH
    assert token.isascii()


def test_dois_tokens_da_mesma_sessao_sao_diferentes() -> None:
    assert issue_csrf_token(KEY, SESSION_ID) != issue_csrf_token(KEY, SESSION_ID)


def test_token_assinado_com_outra_chave_nao_confere() -> None:
    token = issue_csrf_token(OTHER_KEY, SESSION_ID)

    assert not _matches(token)


@pytest.mark.parametrize(
    ("cookie", "header"),
    [
        pytest.param(None, KNOWN_TOKEN, id="sem-cookie"),
        pytest.param(KNOWN_TOKEN, None, id="sem-header"),
        pytest.param(None, None, id="sem-nenhum"),
        pytest.param("", "", id="vazios"),
        pytest.param(KNOWN_TOKEN, KNOWN_TOKEN[:-1] + "B", id="header-diferente-do-cookie"),
        pytest.param(KNOWN_TOKEN + "A", KNOWN_TOKEN + "A", id="um-caractere-a-mais"),
        pytest.param(KNOWN_TOKEN[:-1], KNOWN_TOKEN[:-1], id="um-caractere-a-menos"),
        pytest.param("A" * 43 + "." + "A" * 43, "A" * 43 + "." + "A" * 43, id="hmac-inventado"),
        pytest.param(KNOWN_TOKEN.replace(".", "_"), KNOWN_TOKEN.replace(".", "_"), id="sem-ponto"),
        pytest.param("é" * TOKEN_LENGTH, "é" * TOKEN_LENGTH, id="nao-ascii"),
        pytest.param(KNOWN_TOKEN + "\n", KNOWN_TOKEN + "\n", id="quebra-de-linha-no-fim"),
        pytest.param("\x00" * TOKEN_LENGTH, "\x00" * TOKEN_LENGTH, id="nul"),
    ],
)
def test_token_ausente_malformado_ou_divergente_nao_confere(
    cookie: str | None, header: str | None
) -> None:
    assert not csrf_token_matches(KEY, SESSION_ID, cookie, header)


@given(st.uuids())
def test_token_de_uma_sessao_nunca_vale_em_outra(other: UUID) -> None:
    token = issue_csrf_token(KEY, SESSION_ID)

    assert _matches(token, session_id=other) is (other == SESSION_ID)


@given(
    st.integers(min_value=0, max_value=TOKEN_LENGTH - 1),
    st.characters(codec="ascii", exclude_characters="."),
)
def test_qualquer_caractere_trocado_invalida(position: int, replacement: str) -> None:
    token = issue_csrf_token(KEY, SESSION_ID)
    tampered = token[:position] + replacement + token[position + 1 :]

    assert _matches(tampered) is (tampered == token)


@given(st.text(max_size=200), st.text(max_size=200))
def test_texto_qualquer_nunca_confere_nem_levanta(cookie: str, header: str) -> None:
    assert not csrf_token_matches(KEY, uuid4(), cookie, header)


# --- Fetch Metadata e Origin ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sec_fetch_site", "origin"),
    [
        pytest.param("same-origin", ORIGIN, id="same-origin-com-origin"),
        pytest.param("same-origin", None, id="same-origin-sem-origin"),
        pytest.param(None, ORIGIN, id="so-origin"),
        pytest.param(None, None, id="cliente-sem-os-dois-headers"),
    ],
)
def test_mesma_origem_nao_e_cross_site(sec_fetch_site: str | None, origin: str | None) -> None:
    assert not cross_site_request(sec_fetch_site, origin, ORIGIN)


@pytest.mark.parametrize(
    ("sec_fetch_site", "origin"),
    [
        pytest.param("cross-site", None, id="cross-site"),
        pytest.param("same-site", None, id="same-site"),
        pytest.param("none", None, id="none"),
        pytest.param("Same-Origin", None, id="caixa-diferente"),
        pytest.param("", None, id="vazio"),
        pytest.param("same-origin", "https://evil.example", id="origin-de-outra-origem"),
        pytest.param(None, "https://evil.example", id="so-origin-de-outra-origem"),
        pytest.param(None, "null", id="origin-null"),
        pytest.param(None, "http://goblinshift.dev", id="outro-esquema"),
        pytest.param(None, "https://goblinshift.dev:8443", id="outra-porta"),
        pytest.param(None, "https://app.goblinshift.dev", id="subdominio"),
        pytest.param(None, ORIGIN + "/", id="barra-no-fim"),
    ],
)
def test_outra_origem_e_cross_site(sec_fetch_site: str | None, origin: str | None) -> None:
    assert cross_site_request(sec_fetch_site, origin, ORIGIN)


# --- Par sem sessão (POST /auth/logout sem sessão válida) -------------------------------------


def test_par_igual_e_bem_formado_confere_sem_sessao() -> None:
    token = issue_csrf_token(KEY, SESSION_ID)

    assert csrf_pair_matches(token, token)


@pytest.mark.parametrize(
    ("cookie", "header"),
    [
        pytest.param(None, KNOWN_TOKEN, id="sem-cookie"),
        pytest.param(KNOWN_TOKEN, None, id="sem-header"),
        pytest.param(None, None, id="sem-os-dois"),
        pytest.param(KNOWN_TOKEN, "B" * 43 + KNOWN_TOKEN[43:], id="divergente"),
        pytest.param("x", "x", id="fora-do-formato"),
        pytest.param(KNOWN_TOKEN + "\n", KNOWN_TOKEN + "\n", id="quebra-de-linha"),
        pytest.param(KNOWN_TOKEN, KNOWN_TOKEN[:-1] + "é", id="header-nao-ascii"),
    ],
)
def test_par_ausente_divergente_ou_malformado_nao_confere(
    cookie: str | None, header: str | None
) -> None:
    assert not csrf_pair_matches(cookie, header)


@given(st.text(max_size=200), st.text(max_size=200))
def test_par_qualquer_nunca_levanta(cookie: str, header: str) -> None:
    csrf_pair_matches(cookie, header)
