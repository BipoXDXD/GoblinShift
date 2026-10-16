"""CLI do administrador contra o PostgreSQL real (docs/GoblinShiftPlan.md, fatia 3 em 3.9)."""

import hashlib
import hmac
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.admin_cli import EXIT_NOT_FOUND, EXIT_OK, EXIT_USAGE, main
from goblinshift.files.storage import FileStorage
from goblinshift.identity import (
    Scope,
    authenticate_api_key,
    block_account,
    request_account_deletion,
)
from goblinshift.identity.tables import api_key, app_user, audit_event
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import run as run_table
from tests.identity.support import new_user
from tests.runs.worker_support import queue_run

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def cli_environment(db_settings: Settings, engine: object) -> Iterator[None]:
    """A CLI lê as mesmas variáveis GS_* que a API; `engine` deixa o banco limpo."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GS_DB_HOST", db_settings.db_host)
        mp.setenv("GS_DB_PORT", str(db_settings.db_port))
        mp.setenv("GS_DB_USER", db_settings.db_user)
        mp.setenv("GS_DB_PASSWORD", db_settings.db_password.get_secret_value())
        mp.setenv("GS_DB_NAME", db_settings.db_name)
        yield


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    exit_code = main(list(args))
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def create_user(capsys: pytest.CaptureFixture[str]) -> str:
    exit_code, out, _ = run(capsys, "create-user", "--display-name", "Ana")
    assert exit_code == EXIT_OK
    return out.strip()


def test_create_user_imprime_so_o_id_da_conta(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, _ = run(capsys, "create-user", "--display-name", "Ana")

    assert exit_code == EXIT_OK
    UUID(out.strip())
    assert out.count("\n") == 1


def test_chave_emitida_pela_cli_autentica_ate_ser_revogada(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session]
) -> None:
    user_id = create_user(capsys)

    exit_code, out, err = run(
        capsys, "issue-key", "--user", user_id, "--scopes", "files:read,runs:write", "--days", "7"
    )
    token = out.strip()

    assert exit_code == EXIT_OK
    assert out.count("\n") == 1
    assert token.startswith("gsk_")
    # Os metadados vão para stderr, para que `> arquivo` capture só o segredo.
    public_id = token.split("_")[1]
    assert public_id in err
    assert token not in err
    with unit_of_work(factory) as session:
        principal = authenticate_api_key(session, token, datetime.now(UTC))
        expires_at = session.execute(select(api_key.c.expires_at)).scalar_one()
    assert principal is not None
    assert principal.owner_id == UUID(user_id)
    assert principal.scopes == {Scope.FILES_READ, Scope.RUNS_WRITE}
    assert timedelta(days=6) < expires_at - datetime.now(UTC) <= timedelta(days=7)

    exit_code, _, _ = run(capsys, "revoke-key", "--key", public_id)

    assert exit_code == EXIT_OK
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, token, datetime.now(UTC)) is None


def test_validade_padrao_e_de_30_dias(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session]
) -> None:
    user_id = create_user(capsys)

    exit_code, _, _ = run(capsys, "issue-key", "--user", user_id, "--scopes", "runs:read")

    assert exit_code == EXIT_OK
    with unit_of_work(factory) as session:
        row = session.execute(select(api_key.c.created_at, api_key.c.expires_at)).one()
    assert row.expires_at - row.created_at == timedelta(days=30)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(["--scopes", "files:read,admin"], id="escopo-desconhecido"),
        pytest.param(["--scopes", ""], id="sem-escopos"),
        pytest.param(["--scopes", "files:read", "--days", "91"], id="validade-longa"),
        pytest.param(["--scopes", "files:read", "--days", "0"], id="validade-zero"),
    ],
)
def test_emissao_com_parametro_invalido_sai_com_erro_de_uso(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], extra: list[str]
) -> None:
    user_id = create_user(capsys)

    exit_code, out, err = run(capsys, "issue-key", "--user", user_id, *extra)

    assert exit_code == EXIT_USAGE
    assert out == ""
    assert err.startswith("gs-admin: ")
    with unit_of_work(factory) as session:
        assert session.execute(select(api_key.c.id)).first() is None


def test_emissao_para_conta_inexistente_sai_como_nao_encontrada(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, out, _ = run(capsys, "issue-key", "--user", str(uuid7()), "--scopes", "files:read")

    assert exit_code == EXIT_NOT_FOUND
    assert out == ""


def test_revogar_chave_inexistente_sai_como_nao_encontrada(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, _, err = run(capsys, "revoke-key", "--key", "0" * 16)

    assert exit_code == EXIT_NOT_FOUND
    assert "0" * 16 in err


def test_nome_em_branco_sai_com_erro_de_uso(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, _ = run(capsys, "create-user", "--display-name", "   ")

    assert exit_code == EXIT_USAGE
    assert out == ""


def test_id_de_conta_que_nao_e_uuid_e_recusado_pelo_argparse(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["issue-key", "--user", "ana", "--scopes", "files:read"])

    assert raised.value.code == EXIT_USAGE


# --- Fatia 6: bloqueio, liberação e listagem ---------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FileStorage:
    """O bloqueio cancela a execução ativa, que pode apagar saídas descartadas do disco."""
    root = tmp_path / "storage"
    monkeypatch.setenv("GS_STORAGE_ROOT", str(root))
    return FileStorage(root)


def status_of(factory: sessionmaker[Session], user_id: str) -> str:
    with factory() as session:
        status: str = session.execute(
            select(app_user.c.status).where(app_user.c.id == UUID(user_id))
        ).scalar_one()
    return status


def audited(factory: sessionmaker[Session], user_id: str) -> list[str]:
    with factory() as session:
        return list(
            session.execute(
                select(audit_event.c.event_type)
                .where(audit_event.c.user_id == UUID(user_id))
                .order_by(audit_event.c.id)
            ).scalars()
        )


def test_block_bloqueia_revoga_chaves_e_cancela_a_execucao(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    user_id = create_user(capsys)
    _, token, _ = run(capsys, "issue-key", "--user", user_id, "--scopes", "files:read")
    run_id = queue_run(factory, storage, UUID(user_id))

    exit_code, out, err = run(capsys, "block", "--user", user_id)

    assert exit_code == EXIT_OK
    assert out == ""
    assert user_id in err
    assert status_of(factory, user_id) == "blocked"
    with unit_of_work(factory) as session:
        assert authenticate_api_key(session, token.strip(), datetime.now(UTC)) is None
        run_status = session.execute(select(run_table.c.status).where(run_table.c.id == run_id))
        assert run_status.scalar_one() == Status.CANCELED
    assert audited(factory, user_id) == ["ACCOUNT_BLOCKED"]


def test_block_repetido_completa_o_que_faltou(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    # A primeira chamada caiu entre as duas transações: conta bloqueada, execução na fila.
    user_id = create_user(capsys)
    run_id = queue_run(factory, storage, UUID(user_id))
    with unit_of_work(factory) as session:
        block_account(session, UUID(user_id), datetime.now(UTC))

    exit_code, _, _ = run(capsys, "block", "--user", user_id)

    assert exit_code == EXIT_OK
    with factory() as session:
        status = session.execute(
            select(run_table.c.status).where(run_table.c.id == run_id)
        ).scalar_one()
    assert status == Status.CANCELED


def test_block_de_conta_inexistente_sai_como_nao_encontrada(
    capsys: pytest.CaptureFixture[str], storage: FileStorage
) -> None:
    exit_code, out, _ = run(capsys, "block", "--user", str(uuid7()))

    assert exit_code == EXIT_NOT_FOUND
    assert out == ""


def test_unblock_libera_a_conta(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    user_id = create_user(capsys)
    run(capsys, "block", "--user", user_id)

    exit_code, _, err = run(capsys, "unblock", "--user", user_id)

    assert exit_code == EXIT_OK
    assert user_id in err
    assert status_of(factory, user_id) == "active"
    assert audited(factory, user_id) == ["ACCOUNT_BLOCKED", "ACCOUNT_UNBLOCKED"]


def test_unblock_de_conta_inexistente_sai_como_nao_encontrada(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, _, _ = run(capsys, "unblock", "--user", str(uuid7()))

    assert exit_code == EXIT_NOT_FOUND


def test_unblock_de_conta_em_exclusao_e_recusado_sem_auditoria(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    user_id = create_user(capsys)
    run(capsys, "block", "--user", user_id)
    with unit_of_work(factory) as session:
        request_account_deletion(session, UUID(user_id), datetime.now(UTC))

    exit_code, _, err = run(capsys, "unblock", "--user", user_id)

    assert exit_code == EXIT_NOT_FOUND
    assert "exclusão" in err
    assert status_of(factory, user_id) == "blocked"
    assert audited(factory, user_id) == ["ACCOUNT_BLOCKED"]


def test_list_users_imprime_id_emissor_estado_e_criacao_sem_sujeito(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session]
) -> None:
    user_id = create_user(capsys)
    with factory() as session:
        row = session.execute(select(app_user).where(app_user.c.id == UUID(user_id))).one()

    exit_code, out, _ = run(capsys, "list-users")

    assert exit_code == EXIT_OK
    [line] = out.splitlines()
    assert line.split("\t") == [user_id, row.issuer, "active", row.created_at.isoformat()]
    assert row.subject not in out


def test_list_users_pending_mostra_so_as_pendentes(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session]
) -> None:
    create_user(capsys)
    pending = str(new_user(factory, blocked=True))

    exit_code, out, _ = run(capsys, "list-users", "--pending")

    assert exit_code == EXIT_OK
    assert [line.split("\t")[0] for line in out.splitlines()] == [pending]


def test_list_users_sem_contas_nao_imprime_nada(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, _ = run(capsys, "list-users")

    assert exit_code == EXIT_OK
    assert out == ""


# --- Roteiro de abuso: do `user.hash` da telemetria à conta ------------------------------------

USER_HASH_SECRET = "segredo-de-teste-do-user-hash-com-32-chars"  # noqa: S105 (valor de teste)


def user_hash(user_id: str) -> str:
    """O `user.hash` dos eventos amplos (3.7): HMAC-SHA256 do UUID em bytes."""
    return hmac.new(USER_HASH_SECRET.encode(), UUID(user_id).bytes, hashlib.sha256).hexdigest()


@pytest.fixture
def user_hash_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_USER_HASH_SECRET", USER_HASH_SECRET)


@pytest.mark.usefixtures("user_hash_secret")
def test_find_user_traduz_o_user_hash_no_id_da_conta(capsys: pytest.CaptureFixture[str]) -> None:
    create_user(capsys)
    wanted = create_user(capsys)

    exit_code, out, _ = run(capsys, "find-user", "--user-hash", user_hash(wanted))

    assert exit_code == EXIT_OK
    assert out == f"{wanted}\n"


@pytest.mark.usefixtures("user_hash_secret")
def test_find_user_aceita_o_hash_em_maiusculas(capsys: pytest.CaptureFixture[str]) -> None:
    wanted = create_user(capsys)

    exit_code, out, _ = run(capsys, "find-user", "--user-hash", user_hash(wanted).upper())

    assert exit_code == EXIT_OK
    assert out == f"{wanted}\n"


@pytest.mark.usefixtures("user_hash_secret")
def test_find_user_de_hash_sem_conta_sai_como_nao_encontrada(
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_user(capsys)

    exit_code, out, err = run(capsys, "find-user", "--user-hash", "0" * 64)

    assert exit_code == EXIT_NOT_FOUND
    assert out == ""
    assert "nenhuma conta" in err


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param("", id="vazio"),
        pytest.param("a" * 63, id="63-caracteres"),
        pytest.param("a" * 65, id="65-caracteres"),
        pytest.param("g" * 64, id="nao-hexadecimal"),
    ],
)
def test_find_user_com_hash_malformado_sai_com_erro_de_uso(
    capsys: pytest.CaptureFixture[str], malformed: str
) -> None:
    exit_code, out, _ = run(capsys, "find-user", "--user-hash", malformed)

    assert exit_code == EXIT_USAGE
    assert out == ""


# --- `--user-hash -`: o hash pela entrada padrão, fora da linha de comando e do log do sudo ----


def run_with_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, stdin: str
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return run(capsys, "find-user", "--user-hash", "-")


@pytest.mark.usefixtures("user_hash_secret")
@pytest.mark.parametrize(
    "surround",
    [
        pytest.param("{}", id="sem-quebra-de-linha"),
        pytest.param("{}\n", id="quebra-de-linha"),
        pytest.param("  {}  \n", id="espacos"),
        pytest.param("\t{}\r\n", id="tab-e-crlf"),
    ],
)
def test_find_user_le_o_hash_da_entrada_padrao(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, surround: str
) -> None:
    wanted = create_user(capsys)

    exit_code, out, _ = run_with_stdin(capsys, monkeypatch, surround.format(user_hash(wanted)))

    assert exit_code == EXIT_OK
    assert out == f"{wanted}\n"


@pytest.mark.parametrize(
    "stdin",
    [
        pytest.param("", id="vazia"),
        pytest.param("\n", id="so-quebra-de-linha"),
        pytest.param("a" * 63 + "\n", id="63-caracteres"),
        pytest.param("a" * 32 + " " + "a" * 32, id="espaco-no-meio"),
        pytest.param("a" * 64 + "\n" + "b" * 64 + "\n", id="dois-hashes"),
        pytest.param("g" * 64, id="nao-hexadecimal"),
    ],
)
def test_find_user_com_entrada_padrao_vazia_ou_malformada_sai_com_erro_de_uso(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, stdin: str
) -> None:
    exit_code, out, err = run_with_stdin(capsys, monkeypatch, stdin)

    assert exit_code == EXIT_USAGE
    assert out == ""
    assert "64 caracteres hexadecimais" in err


# --- set-limits e usage (1.7 e 1.8) ----------------------------------------------------------


def usage_lines(capsys: pytest.CaptureFixture[str], user_id: str) -> dict[str, list[str]]:
    exit_code, out, _ = run(capsys, "usage", "--user", user_id)
    assert exit_code == EXIT_OK
    lines = [line.split("\t") for line in out.strip().splitlines()]
    return {fields[0]: fields[1:] for fields in lines}


def test_usage_de_conta_nova_mostra_consumo_zero_e_limites_padrao(
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_id = create_user(capsys)

    found = usage_lines(capsys, user_id)

    assert found == {
        "runs_last_24h": ["0"],
        "input_bytes_last_24h": ["0"],
        "executor_ms_last_24h": ["0"],
        "storage_reserved_bytes": ["0"],
        "runs_per_24h": ["30", "padrão"],
        "input_bytes_per_24h": [str(2 * 1024**3), "padrão"],
        "storage_bytes": [str(4 * 1024**3), "padrão"],
    }


def test_set_limits_sobrepoe_e_default_desfaz(capsys: pytest.CaptureFixture[str]) -> None:
    user_id = create_user(capsys)

    exit_code, out, err = run(
        capsys,
        "set-limits",
        "--user",
        user_id,
        "--runs-per-24h",
        "5",
        "--storage-bytes",
        "1048576",
    )
    changed = usage_lines(capsys, user_id)
    run(capsys, "set-limits", "--user", user_id, "--runs-per-24h", "default")
    restored = usage_lines(capsys, user_id)

    assert (exit_code, out) == (EXIT_OK, "")
    assert user_id in err
    assert changed["runs_per_24h"] == ["5", "conta"]
    assert changed["storage_bytes"] == ["1048576", "conta"]
    assert changed["input_bytes_per_24h"] == [str(2 * 1024**3), "padrão"]
    assert restored["runs_per_24h"] == ["30", "padrão"]
    assert restored["storage_bytes"] == ["1048576", "conta"]


@pytest.mark.parametrize(
    "args",
    [
        ("--runs-per-24h", "0"),
        ("--runs-per-24h", "-3"),
        ("--input-bytes-per-24h", "muito"),
        ("--storage-bytes", "0"),
    ],
)
def test_set_limits_recusa_valor_invalido_sem_mudar_nada(
    capsys: pytest.CaptureFixture[str], args: tuple[str, ...]
) -> None:
    user_id = create_user(capsys)
    before = usage_lines(capsys, user_id)

    with pytest.raises(SystemExit) as usage_error:
        main(["set-limits", "--user", user_id, *args])
    capsys.readouterr()

    assert usage_error.value.code == EXIT_USAGE
    assert usage_lines(capsys, user_id) == before


def test_set_limits_sem_nada_a_mudar_sai_com_2(capsys: pytest.CaptureFixture[str]) -> None:
    user_id = create_user(capsys)

    exit_code, _, err = run(capsys, "set-limits", "--user", user_id)

    assert exit_code == EXIT_USAGE
    assert "nada a mudar" in err


def test_set_limits_abaixo_do_reservado_sai_com_2(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], tmp_path: Path
) -> None:
    owner = new_user(factory)
    queue_run(factory, FileStorage(tmp_path), owner)  # entrada de alguns bytes reservada

    exit_code, _, err = run(capsys, "set-limits", "--user", str(owner), "--storage-bytes", "1")

    assert exit_code == EXIT_USAGE
    assert "reservou" in err


@pytest.mark.parametrize("command", ["usage", "set-limits"])
def test_conta_inexistente_sai_com_1(capsys: pytest.CaptureFixture[str], command: str) -> None:
    extra = ["--runs-per-24h", "5"] if command == "set-limits" else []

    exit_code, _, err = run(capsys, command, "--user", str(uuid7()), *extra)

    assert exit_code == EXIT_NOT_FOUND
    assert "não existe" in err
