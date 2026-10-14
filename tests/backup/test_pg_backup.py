"""Backup externo do PostgreSQL e restauração testada (docs/GoblinShiftPlan.md, 3.8).

Os scripts reais rodam contra um PostgreSQL e um S3 locais: o dump sai cifrado com `age` por
uma chave que só escreve, a restauração baixa com uma chave que só lê, decifra, restaura num
banco vazio e confere o resultado; o healthchecks.io é um servidor HTTP falso.
"""

import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.backup.conftest import (
    BACKUP_SCRIPT,
    BUCKET,
    READER,
    RESTORE_SCRIPT,
    S3,
    S3_REGION,
    UNLOCKED_BUCKET,
    WRITER,
    AgeKeys,
    Credentials,
    Database,
    FakeHealthchecks,
    SourceDatabase,
)

pytestmark = [pytest.mark.integration, pytest.mark.backup]

AGE_HEADER = b"age-encryption.org/v1"
# Instante UTC do início do dump, que ordena os nomes, e 128 bits aleatórios.
DUMP_KEY = re.compile(r"goblinshift-\d{8}T\d{6}Z-(?P<suffix>[0-9a-f]{32})\.dump\.age")


@pytest.fixture
def prefix() -> str:
    """Prefixo próprio por teste: o bucket é compartilhado pela sessão."""
    return f"pg-{uuid.uuid4().hex[:8]}/"


def backup_env(
    s3: S3,
    source: SourceDatabase,
    keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    credentials: Credentials = WRITER,
) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "BACKUP_PG_CONTAINER": source.database.container_id,
        "BACKUP_PG_USER": source.database.user,
        "BACKUP_PG_DB": source.database.name,
        "BACKUP_AGE_RECIPIENTS_FILE": str(keys.recipients),
        "BACKUP_S3_ENDPOINT": s3.endpoint,
        "BACKUP_S3_REGION": S3_REGION,
        "BACKUP_S3_BUCKET": BUCKET,
        "BACKUP_S3_PREFIX": prefix,
        "BACKUP_S3_KEY_ID": credentials.key_id,
        "BACKUP_S3_SECRET": credentials.secret,
        "BACKUP_HEALTHCHECK_URL": healthchecks.url,
    }


def restore_env(
    s3: S3,
    target: Database,
    keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "BACKUP_S3_ENDPOINT": s3.endpoint,
        "BACKUP_S3_REGION": S3_REGION,
        "BACKUP_S3_BUCKET": BUCKET,
        "BACKUP_S3_PREFIX": prefix,
        "BACKUP_S3_KEY_ID": READER.key_id,
        "BACKUP_S3_SECRET": READER.secret,
        "BACKUP_AGE_IDENTITY_FILE": str(keys.identity),
        "RESTORE_PG_CONTAINER": target.container_id,
        "RESTORE_PG_USER": target.user,
        "RESTORE_PG_DB": target.name,
        "RESTORE_KNOWN_REVISIONS": known_revisions,
        "RESTORE_MAX_AGE_HOURS": "26",
        "RESTORE_HEALTHCHECK_URL": healthchecks.url,
    }


def run_script(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def dump_suffix(key: str, prefix: str) -> str:
    match = DUMP_KEY.fullmatch(key.removeprefix(prefix))
    assert match is not None, key
    return match["suffix"]


def back_up(
    s3: S3, source: SourceDatabase, keys: AgeKeys, healthchecks: FakeHealthchecks, prefix: str
) -> None:
    result = run_script(BACKUP_SCRIPT, backup_env(s3, source, keys, healthchecks, prefix))
    assert result.returncode == 0, result.stderr
    healthchecks.pings.clear()


# Backup


def test_backup_envia_o_dump_cifrado_com_a_chave_que_so_escreve(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
) -> None:
    result = run_script(BACKUP_SCRIPT, backup_env(s3, source_db, age_keys, healthchecks, prefix))

    assert result.returncode == 0, result.stderr
    keys = s3.keys(prefix)
    assert len(keys) == 1
    assert DUMP_KEY.fullmatch(keys[0].removeprefix(prefix)), keys[0]
    content = s3.get(keys[0])
    assert content.startswith(AGE_HEADER)
    assert source_db.canary.encode() not in content
    assert healthchecks.pings == ["/start", "/"]


def test_nome_do_dump_tem_sufixo_aleatorio_que_nao_vai_para_o_journal(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
) -> None:
    """Com o nome adivinhável, a chave que só escreve sobrescreveria o dump daquele instante."""
    env = backup_env(s3, source_db, age_keys, healthchecks, prefix)

    first = run_script(BACKUP_SCRIPT, env)
    second = run_script(BACKUP_SCRIPT, env)

    suffixes = {dump_suffix(key, prefix) for key in s3.keys(prefix)}
    assert len(suffixes) == 2
    for suffix in suffixes:
        assert suffix not in first.stdout + first.stderr + second.stdout + second.stderr


def test_backup_nao_imprime_o_segredo_do_s3(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
) -> None:
    env = backup_env(s3, source_db, age_keys, healthchecks, prefix)
    env["BACKUP_S3_SECRET"] = f"CANARY-{uuid.uuid4()}"

    result = run_script(BACKUP_SCRIPT, env)

    assert result.returncode != 0
    assert "CANARY-" not in result.stdout + result.stderr


def test_credencial_recusada_pinga_falha_e_nao_grava_nada(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
) -> None:
    env = backup_env(s3, source_db, age_keys, healthchecks, prefix)
    env["BACKUP_S3_SECRET"] = "segredo-errado-" + "x" * 24

    result = run_script(BACKUP_SCRIPT, env)

    assert result.returncode != 0
    assert s3.keys(prefix) == []
    assert healthchecks.pings == ["/start", "/fail"]


def test_dump_que_falha_nao_envia_nada(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
) -> None:
    env = backup_env(s3, source_db, age_keys, healthchecks, prefix)
    env["BACKUP_PG_DB"] = "banco_que_nao_existe"

    result = run_script(BACKUP_SCRIPT, env)

    assert result.returncode != 0
    assert s3.keys(prefix) == []
    assert healthchecks.pings == ["/start", "/fail"]


def test_sem_destinatario_age_falha_sem_enviar_texto_claro(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    tmp_path: Path,
) -> None:
    only_comments = tmp_path / "vazio.txt"
    only_comments.write_text("# coloque aqui a chave pública age\n\n")
    env = backup_env(s3, source_db, age_keys, healthchecks, prefix)
    env["BACKUP_AGE_RECIPIENTS_FILE"] = str(only_comments)

    result = run_script(BACKUP_SCRIPT, env)

    assert result.returncode != 0
    assert "destinatário" in result.stderr
    assert s3.keys(prefix) == []
    assert healthchecks.pings == ["/start", "/fail"]


@pytest.mark.parametrize(
    "missing",
    ["BACKUP_S3_BUCKET", "BACKUP_S3_SECRET", "BACKUP_HEALTHCHECK_URL", "BACKUP_PG_CONTAINER"],
)
def test_configuracao_incompleta_falha_antes_de_comecar(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    missing: str,
) -> None:
    env = backup_env(s3, source_db, age_keys, healthchecks, prefix)
    del env[missing]

    result = run_script(BACKUP_SCRIPT, env)

    assert result.returncode != 0
    assert missing in result.stderr
    assert s3.keys(prefix) == []


# Restauração


def test_restauracao_do_dump_mais_recente_passa_nas_verificacoes(
    s3: S3,
    source_db: SourceDatabase,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> None:
    back_up(s3, source_db, age_keys, healthchecks, prefix)
    (latest,) = s3.keys(prefix)
    # Um dump antigo de outro banco vem antes na ordem: a restauração precisa escolher o novo.
    old = latest.replace("goblinshift-2", "goblinshift-1")
    s3.put(old, Path(__file__))

    result = run_script(
        RESTORE_SCRIPT,
        restore_env(s3, restore_db, age_keys, healthchecks, prefix, known_revisions),
    )

    assert result.returncode == 0, result.stderr
    assert dump_suffix(latest, prefix) not in result.stdout + result.stderr
    assert restore_db.scalar("SELECT display_name FROM app_user") == source_db.canary
    assert restore_db.scalar("SELECT count(*) FROM run") == "1"
    assert healthchecks.pings == ["/start", "/"]


def test_restauracao_de_dump_sem_object_lock_falha(
    s3: S3,
    source_db: SourceDatabase,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> None:
    """Sem retenção em compliance, a chave que só escreve sobrescreveria o dump."""
    backup = backup_env(s3, source_db, age_keys, healthchecks, prefix)
    backup["BACKUP_S3_BUCKET"] = UNLOCKED_BUCKET
    assert run_script(BACKUP_SCRIPT, backup).returncode == 0
    healthchecks.pings.clear()
    env = restore_env(s3, restore_db, age_keys, healthchecks, prefix, known_revisions)
    env["BACKUP_S3_BUCKET"] = UNLOCKED_BUCKET

    result = run_script(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert "retenção" in result.stderr
    assert restore_db.scalar("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == "0"
    assert healthchecks.pings == ["/start", "/fail"]


def test_restauracao_com_dump_de_data_futura_falha(
    s3: S3,
    source_db: SourceDatabase,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> None:
    """Um nome no futuro venceria qualquer dump real pela ordem dos nomes, para sempre."""
    back_up(s3, source_db, age_keys, healthchecks, prefix)
    (latest,) = s3.keys(prefix)
    s3.put(latest.replace("goblinshift-2026", "goblinshift-2099"), Path(__file__))

    result = run_script(
        RESTORE_SCRIPT,
        restore_env(s3, restore_db, age_keys, healthchecks, prefix, known_revisions),
    )

    assert result.returncode != 0
    assert "futura" in result.stderr
    assert healthchecks.pings == ["/start", "/fail"]


def test_restauracao_sem_dump_falha(
    s3: S3,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> None:
    result = run_script(
        RESTORE_SCRIPT,
        restore_env(s3, restore_db, age_keys, healthchecks, prefix, known_revisions),
    )

    assert result.returncode != 0
    assert "nenhum dump" in result.stderr
    assert healthchecks.pings == ["/start", "/fail"]


def test_restauracao_de_dump_velho_demais_falha(
    s3: S3,
    source_db: SourceDatabase,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> None:
    back_up(s3, source_db, age_keys, healthchecks, prefix)
    env = restore_env(s3, restore_db, age_keys, healthchecks, prefix, known_revisions)
    env["RESTORE_MAX_AGE_HOURS"] = "0"

    result = run_script(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert "horas" in result.stderr
    assert healthchecks.pings == ["/start", "/fail"]


def test_restauracao_com_revisao_desconhecida_falha(
    s3: S3,
    source_db: SourceDatabase,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
) -> None:
    back_up(s3, source_db, age_keys, healthchecks, prefix)

    result = run_script(
        RESTORE_SCRIPT,
        restore_env(s3, restore_db, age_keys, healthchecks, prefix, "0001 0002"),
    )

    assert result.returncode != 0
    assert "alembic_version" in result.stderr
    assert healthchecks.pings == ["/start", "/fail"]


def test_restauracao_com_chave_age_errada_falha(
    s3: S3,
    source_db: SourceDatabase,
    restore_db: Database,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
    tmp_path: Path,
) -> None:
    back_up(s3, source_db, age_keys, healthchecks, prefix)
    other = tmp_path / "outra-identidade.txt"
    subprocess.run(["age-keygen", "-o", str(other)], check=True, capture_output=True)
    env = restore_env(s3, restore_db, age_keys, healthchecks, prefix, known_revisions)
    env["BACKUP_AGE_IDENTITY_FILE"] = str(other)

    result = run_script(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert restore_db.scalar("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == "0"
    assert healthchecks.pings == ["/start", "/fail"]


def test_restauracao_em_banco_que_ja_tem_tabelas_falha(
    s3: S3,
    source_db: SourceDatabase,
    age_keys: AgeKeys,
    healthchecks: FakeHealthchecks,
    prefix: str,
    known_revisions: str,
) -> None:
    """O destino tem de ser descartável: restaurar por cima de dados não prova nada."""
    back_up(s3, source_db, age_keys, healthchecks, prefix)

    result = run_script(
        RESTORE_SCRIPT,
        restore_env(s3, source_db.database, age_keys, healthchecks, prefix, known_revisions),
    )

    assert result.returncode != 0
    assert "vazio" in result.stderr
    assert healthchecks.pings == ["/start", "/fail"]
