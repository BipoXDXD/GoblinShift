"""Leitura estrita de /etc/goblinshift/executor-ctl.json."""

import json
from pathlib import Path
from typing import Any

import pytest

from executor_ctl.config import Config, ConfigError, load_config

pytestmark = pytest.mark.usefixtures("synthetic_policy_metadata")

VALID: dict[str, Any] = {
    "socket": "/run/goblinshift/executor-ctl.sock",
    "group": "gs-exec",
    "work_area": "/srv/goblinshift/exec-work",
    "runtime": "runsc",
    "images_file": "/var/lib/coolify-release/goblinshift/executor-images.json",
}


def write_config(tmp_path: Path, content: object) -> Path:
    path = tmp_path.resolve() / "executor-ctl.json"
    path.write_text(json.dumps(content))
    return path


def test_configuracao_valida_vira_config_com_montagem_exigida(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID))

    assert config == Config(
        socket=Path("/run/goblinshift/executor-ctl.sock"),
        group="gs-exec",
        work_area=Path("/srv/goblinshift/exec-work"),
        runtime="runsc",
        images_file=Path("/var/lib/coolify-release/goblinshift/executor-images.json"),
        require_mount=True,
    )


def test_require_mount_pode_ser_desligado_para_testes(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, {**VALID, "require_mount": False}))

    assert config.require_mount is False


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"runtime": []}, "runtime com tipo inválido"),
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


@pytest.mark.parametrize(
    "path",
    [
        "/srv/goblinshift/executor/images.json",
        "/var/lib/coolify-release/goblinshift/other.json",
        "/var/lib/coolify-release/goblinshift/./executor-images.json",
    ],
)
def test_politica_fora_do_destino_administrativo_e_recusada(tmp_path: Path, path: str) -> None:
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, {**VALID, "images_file": path}))


def test_config_com_chave_duplicada_e_recusada(tmp_path: Path) -> None:
    path = write_config(tmp_path, VALID)
    path.write_text(json.dumps(VALID)[:-1] + ', "runtime": "runc"}')
    with pytest.raises(ConfigError):
        load_config(path)


def test_json_config_excessivamente_aninhado_e_recusado(tmp_path: Path) -> None:
    path = tmp_path.resolve() / "executor-ctl.json"
    path.write_text("[" * 2000 + "]" * 2000)
    with pytest.raises(ConfigError):
        load_config(path)


def test_decoder_recursion_failure_recusa_a_configuracao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, VALID)

    def depth_limit(*args: object, **kwargs: object) -> object:
        raise RecursionError("Python 3.12 JSON decoder depth limit")

    monkeypatch.setattr(json, "loads", depth_limit)
    with pytest.raises(ConfigError):
        load_config(path)


def test_config_por_symlink_e_recusada(tmp_path: Path) -> None:
    real = write_config(tmp_path, VALID)
    link = tmp_path.resolve() / "linked.json"
    link.symlink_to(real)
    with pytest.raises(ConfigError):
        load_config(link)
