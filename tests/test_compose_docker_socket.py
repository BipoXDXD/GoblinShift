from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = sorted([*ROOT.glob("compose*.yaml"), ROOT / "deploy" / "edge" / "compose.yaml"])


def test_ha_arquivos_compose_para_verificar() -> None:
    assert len(COMPOSE_FILES) >= 4


@pytest.mark.parametrize(
    "compose_file", COMPOSE_FILES, ids=lambda path: str(path.relative_to(ROOT))
)
def test_nenhum_compose_monta_o_socket_do_docker(compose_file: Path) -> None:
    # Quem controla o daemon do Docker tem root no host (ADR 004); só o executor-ctl o acessa.
    assert "docker.sock" not in compose_file.read_text()
