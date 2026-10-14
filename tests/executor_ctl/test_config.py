"""Leitura estrita de /etc/goblinshift/executor-ctl.json."""

import json
from pathlib import Path
from typing import Any

import pytest

from executor_ctl.config import Config, ConfigError, load_config

VALID: dict[str, Any] = {
    "socket": "/run/goblinshift/executor-ctl.sock",
    "group": "gs-exec",
    "work_area": "/srv/goblinshift/exec-work",
    "runtime": "runsc",
    "images_file": "/srv/goblinshift/executor/images.json",
}


def write_config(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "executor-ctl.json"
    path.write_text(json.dumps(content))
    return path


def test_configuracao_valida_vira_config_com_montagem_exigida(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID))

    assert config == Config(
        socket=Path("/run/goblinshift/executor-ctl.sock"),
        group="gs-exec",
        work_area=Path("/srv/goblinshift/exec-work"),
        runtime="runsc",
        images_file=Path("/srv/goblinshift/executor/images.json"),
        require_mount=True,
    )


def test_require_mount_pode_ser_desligado_para_testes(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, {**VALID, "require_mount": False}))

    assert config.require_mount is False


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"runtime": "kata"}, "runtime fora da allowlist"),
        ({"runtime": "runsc --privileged"}, "runtime com opção embutida"),
        ({"work_area": "relativo/exec-work"}, "caminho relativo"),
        ({"work_area": "/srv/goblinshift/../etc"}, "caminho com .."),
        ({"work_area": "/srv/a,dst=/etc"}, "vírgula quebraria o --mount"),
        ({"socket": 7}, "tipo errado"),
        ({"group": "gs exec"}, "grupo com espaço"),
        ({"require_mount": "sim"}, "booleano como texto"),
        ({"extra": 1}, "chave desconhecida"),
    ],
)
def test_configuracao_invalida_e_recusada(
    tmp_path: Path, change: dict[str, object], reason: str
) -> None:
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, {**VALID, **change}))


def test_chave_obrigatoria_ausente_e_recusada(tmp_path: Path) -> None:
    content = {key: value for key, value in VALID.items() if key != "images_file"}

    with pytest.raises(ConfigError, match="images_file"):
        load_config(write_config(tmp_path, content))


def test_json_que_nao_e_objeto_e_recusado(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, [VALID]))


def test_arquivo_ausente_e_recusado(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nao-existe.json")
