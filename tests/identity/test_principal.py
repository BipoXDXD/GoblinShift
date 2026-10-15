"""Principal: quem fez a requisição, por sessão web ou por chave (3.6; fatia 6)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from goblinshift.identity import Principal, Scope

CONTA = UUID("00000000-0000-7000-8000-0000000000cc")
SESSAO = UUID("00000000-0000-7000-8000-0000000000dd")
LOGIN = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_principal_de_chave_nao_tem_sessao() -> None:
    principal = Principal(owner_id=CONTA, scopes=frozenset({Scope.RUNS_READ}), via="api_key")

    assert principal.session_id is None
    assert principal.authenticated_at is None


def test_principal_de_sessao_tem_todos_os_escopos_e_o_login() -> None:
    principal = Principal(
        owner_id=CONTA,
        scopes=frozenset(Scope),
        via="session",
        session_id=SESSAO,
        authenticated_at=LOGIN,
    )

    assert principal.via == "session"
    assert principal.session_id == SESSAO
    assert principal.authenticated_at == LOGIN


@pytest.mark.parametrize(
    ("session_id", "authenticated_at"),
    [(None, LOGIN), (SESSAO, None), (None, None)],
    ids=["sem-id", "sem-login", "sem-nada"],
)
def test_principal_de_sessao_incompleto_e_recusado(
    session_id: UUID | None, authenticated_at: datetime | None
) -> None:
    with pytest.raises(ValueError, match="sessão"):
        Principal(
            owner_id=CONTA,
            scopes=frozenset(Scope),
            via="session",
            session_id=session_id,
            authenticated_at=authenticated_at,
        )


@pytest.mark.parametrize(
    ("session_id", "authenticated_at"),
    [(SESSAO, None), (None, LOGIN)],
    ids=["com-id", "com-login"],
)
def test_principal_de_chave_com_dados_de_sessao_e_recusado(
    session_id: UUID | None, authenticated_at: datetime | None
) -> None:
    with pytest.raises(ValueError, match="chave"):
        Principal(
            owner_id=CONTA,
            scopes=frozenset(Scope),
            via="api_key",
            session_id=session_id,
            authenticated_at=authenticated_at,
        )


def test_login_sem_fuso_e_recusado() -> None:
    with pytest.raises(ValueError, match="fuso"):
        Principal(
            owner_id=CONTA,
            scopes=frozenset(Scope),
            via="session",
            session_id=SESSAO,
            authenticated_at=datetime(2026, 10, 14, 12, 0),
        )
