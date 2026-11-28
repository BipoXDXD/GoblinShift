"""Os tetos da caixa de entrada do compartilhamento (web/src/share/inbox.ts, ADR 064) são os do
servidor (files/limits.py). O service worker não tem o catálogo, então repete os valores; este
teste impede que os dois lados se afastem.

O teto por arquivo (50 MiB) depende do #567: até ele entrar, `MAX_FILE_BYTES` é 32 MiB e
`test_teto_por_arquivo_igual_ao_do_servidor` falha de propósito (o #577 entra depois do #567).
"""

import re
from pathlib import Path

import pytest

from goblinshift.files import limits

INBOX = Path(__file__).resolve().parents[1] / "web" / "src" / "share" / "inbox.ts"
MIB = 1024 * 1024


def _ts_constants() -> dict[str, int]:
    source = INBOX.read_text(encoding="utf-8")
    found: dict[str, int] = {}
    for name, number, unit in re.findall(
        r"^const (SHARE_MAX_\w+) = ([\d_]+)( \* MIB)?;$", source, flags=re.MULTILINE
    ):
        found[name] = int(number.replace("_", "")) * (MIB if unit else 1)
    return found


def _ts_video_extensions() -> tuple[str, ...]:
    source = INBOX.read_text(encoding="utf-8")
    match = re.search(r"^const VIDEO_NAME = /\\\.\(\?:([a-z0-9|]+)\)\$/i;$", source, re.MULTILINE)
    assert match is not None, "VIDEO_NAME não encontrado em inbox.ts"
    return tuple(f".{extension}" for extension in match.group(1).split("|"))


@pytest.mark.parametrize(
    ("ts_name", "server_value"),
    [
        ("SHARE_MAX_FILES", limits.MAX_BATCH_FILES),
        ("SHARE_MAX_VIDEO_BYTES", limits.MAX_VIDEO_FILE_BYTES),
        ("SHARE_MAX_TOTAL_BYTES", limits.MAX_BATCH_BYTES),
    ],
)
def test_teto_do_lote_e_do_video_igual_ao_do_servidor(ts_name: str, server_value: int) -> None:
    assert _ts_constants()[ts_name] == server_value


def test_teto_por_arquivo_igual_ao_do_servidor() -> None:
    assert _ts_constants()["SHARE_MAX_FILE_BYTES"] == limits.MAX_FILE_BYTES


def test_extensoes_de_video_iguais_as_do_servidor() -> None:
    assert sorted(_ts_video_extensions()) == sorted(limits.VIDEO_EXTENSIONS)
