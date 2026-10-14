"""deploy/backup/dump_check.py: escolha do dump e conferência da retenção (ADR 9xx).

A chave da VPS escreve no bucket, então o nome do dump não é confiável: o instante que vale é o
`LastModified` que o S3 grava. Um nome com data futura é sinal de dump forjado e reprova. A
retenção é a do Object Lock em modo compliance, lida nos headers do HEAD.
"""

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "backup" / "dump_check.py"
PREFIX = "pg/"
SUFFIX = "0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


def key(instant: datetime, suffix: str = SUFFIX) -> str:
    return f"{PREFIX}goblinshift-{instant:%Y%m%dT%H%M%SZ}-{suffix}.dump.age"


def listing(*objects: tuple[str, datetime], truncated: bool = False) -> str:
    contents = "".join(
        f"<Contents><Key>{name}</Key>"
        f"<LastModified>{modified:%Y-%m-%dT%H:%M:%S.000Z}</LastModified>"
        "<Size>10</Size></Contents>"
        for name, modified in objects
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>gs-backups</Name><Prefix>{PREFIX}</Prefix>"
        f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>{contents}"
        "</ListBucketResult>"
    )


def run(*args: str, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args, "--now", NOW.isoformat()],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def latest(body: str, max_age_hours: int = 26) -> subprocess.CompletedProcess[str]:
    return run("latest", "--prefix", PREFIX, "--max-age-hours", str(max_age_hours), stdin=body)


# Escolha do dump mais recente


def test_escolhe_pelo_last_modified_e_devolve_chave_e_idade() -> None:
    old = NOW - timedelta(hours=30)
    new = NOW - timedelta(hours=5, minutes=59)
    body = listing((key(old), old), (key(new), new + timedelta(minutes=20)))

    result = latest(body)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [key(new), "5"]


def test_last_modified_vence_a_ordem_dos_nomes() -> None:
    """Nome com instante antigo mas enviado agora: o S3 diz quando ele chegou."""
    renamed = NOW - timedelta(days=3)
    real = NOW - timedelta(hours=2)
    body = listing((key(renamed, "f" * 32), NOW - timedelta(hours=1)), (key(real), real))

    result = latest(body)

    assert result.stdout.splitlines()[0] == key(renamed, "f" * 32)


@pytest.mark.parametrize("ahead", [timedelta(hours=1, seconds=1), timedelta(days=3650)])
def test_nome_com_data_futura_reprova(ahead: timedelta) -> None:
    good = NOW - timedelta(hours=2)
    body = listing((key(good), good), (key(NOW + ahead), NOW - timedelta(hours=3)))

    result = latest(body)

    assert result.returncode != 0
    assert "futur" in result.stderr


def test_relogio_adiantado_dentro_da_folga_de_uma_hora_passa() -> None:
    ahead = NOW + timedelta(minutes=59)
    body = listing((key(ahead), ahead))

    result = latest(body)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [key(ahead), "0"]


def test_last_modified_no_futuro_alem_da_folga_reprova() -> None:
    body = listing((key(NOW), NOW + timedelta(hours=2)))

    result = latest(body)

    assert result.returncode != 0
    assert "futur" in result.stderr


def test_nome_posterior_ao_envio_reprova() -> None:
    """O nome é gerado antes do dump; um instante depois do envio não veio do pg-backup.sh."""
    sent = NOW - timedelta(hours=5)
    body = listing((key(sent + timedelta(hours=1, seconds=1)), sent))

    result = latest(body)

    assert result.returncode != 0
    assert "envio" in result.stderr


@pytest.mark.parametrize(("hours", "passes"), [(25, True), (26, False)])
def test_idade_maxima_pelo_last_modified(hours: int, passes: bool) -> None:
    sent = NOW - timedelta(hours=hours)
    body = listing((key(sent - timedelta(minutes=10)), sent))

    result = latest(body, max_age_hours=26)

    assert (result.returncode == 0) is passes, result.stderr
    if not passes:
        assert "horas" in result.stderr


@pytest.mark.parametrize(
    "name",
    [
        f"{PREFIX}goblinshift-20261014T110000Z.dump.age",
        f"{PREFIX}goblinshift-20261014T110000Z-{SUFFIX}.dump.age.tmp",
        f"outro/goblinshift-20261014T110000Z-{SUFFIX}.dump.age",
        f"{PREFIX}goblinshift-20261314T110000Z-{SUFFIX}.dump.age",
    ],
)
def test_chave_fora_do_formato_e_ignorada(name: str) -> None:
    body = listing((name, NOW - timedelta(hours=1)))

    result = latest(body)

    assert result.returncode != 0
    assert "nenhum dump" in result.stderr


def test_listagem_truncada_reprova() -> None:
    sent = NOW - timedelta(hours=1)

    result = latest(listing((key(sent), sent), truncated=True))

    assert result.returncode != 0
    assert "truncada" in result.stderr


def test_listagem_que_nao_e_xml_reprova() -> None:
    result = latest("<html>erro do proxy")

    assert result.returncode != 0
    assert "listagem" in result.stderr


# Retenção do Object Lock


def head(mode: str | None = "COMPLIANCE", retention: timedelta | None = timedelta(days=30)) -> str:
    modified = NOW - timedelta(hours=3)
    lines = ["HTTP/1.1 200 OK", f"Last-Modified: {format_datetime(modified, usegmt=True)}"]
    if mode is not None:
        lines.append(f"x-amz-object-lock-mode: {mode}")
    if retention is not None:
        until = modified + retention
        lines.append(f"X-Amz-Object-Lock-Retain-Until-Date: {until:%Y-%m-%dT%H:%M:%SZ}")
    return "\r\n".join(lines) + "\r\n\r\n"


def retention(headers: str) -> subprocess.CompletedProcess[str]:
    return run("retention", "--min-days", "30", stdin=headers)


def test_compliance_por_30_dias_passa() -> None:
    result = retention(head())

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("mode", "kept"),
    [
        (None, None),
        ("GOVERNANCE", timedelta(days=30)),
        ("COMPLIANCE", timedelta(days=29)),
        ("COMPLIANCE", None),
    ],
)
def test_sem_compliance_por_30_dias_reprova(mode: str | None, kept: timedelta | None) -> None:
    result = retention(head(mode, kept))

    assert result.returncode != 0
    assert "retenção" in result.stderr


def test_listagem_com_dtd_reprova_sem_expandir_entidades() -> None:
    body = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><r>&a;</r>'

    result = latest(body)

    assert result.returncode != 0
    assert "DTD" in result.stderr
