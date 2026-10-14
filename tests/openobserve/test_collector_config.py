"""O Collector de produção enxerga os filesystems do alerta de disco, e só eles, só leitura."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WATCHED = {"/srv/goblinshift/pg", "/srv/goblinshift/storage", "/srv/goblinshift/telemetry"}


def _yaml(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def _production_binds() -> list[dict[str, Any]]:
    collector = _yaml(ROOT / "compose.prod.yaml")["services"]["otel-collector"]
    return [v for v in collector["volumes"] if isinstance(v, dict) and v["type"] == "bind"]


def _scraped_mount_points() -> set[str]:
    config = _yaml(ROOT / "docker" / "otel-collector" / "config.yaml")
    filesystem = config["receivers"]["hostmetrics"]["scrapers"]["filesystem"]
    assert filesystem["include_mount_points"]["match_type"] == "strict"
    return set(filesystem["include_mount_points"]["mount_points"])


def test_producao_monta_pg_storage_e_telemetria_no_collector() -> None:
    assert {bind["source"] for bind in _production_binds()} == WATCHED


def test_montagens_do_collector_sao_so_leitura_e_nao_criam_diretorio() -> None:
    for bind in _production_binds():
        assert bind["read_only"] is True, bind["source"]
        # Sem o filesystem montado, o container não sobe, em vez de medir um diretório vazio
        # na raiz e nunca alertar.
        assert bind["bind"] == {"create_host_path": False}, bind["source"]


def test_collector_le_exatamente_os_pontos_montados_em_producao() -> None:
    assert {bind["target"] for bind in _production_binds()} == _scraped_mount_points()


def test_area_do_executor_fica_fora_do_alerta_de_disco() -> None:
    # A área do executor enche até ENOSPC por desenho (teste de aceitação, 3.8).
    assert not any("exec-work" in point for point in _scraped_mount_points())


def test_metricas_de_disco_seguem_para_o_openobserve() -> None:
    pipelines = _yaml(ROOT / "docker" / "otel-collector" / "config.yaml")["service"]["pipelines"]

    assert pipelines["metrics"]["receivers"] == ["hostmetrics"]
    assert pipelines["metrics"]["exporters"] == ["otlphttp/openobserve"]
