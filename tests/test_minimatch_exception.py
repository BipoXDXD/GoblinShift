"""Exceção do `minimatch` vulnerável no front (ADR 046).

O GHSA-g3hv-75wg-m249 atinge o `minimatch` de 7.0.0 até antes de 11.0.1. O ADR 046 aceita as
cópias abaixo porque só servem às ferramentas de desenvolvimento e nenhuma liga o
`optimizationLevel >= 2` de que o defeito depende. A exceção vale só para estes caminhos e esta
versão: qualquer mudança no `web/package-lock.json` que os toque (um pai que aceite `^11`, uma
cópia nova na faixa, uma cópia que vá para produção) reprova aqui e obriga a revisar o ADR.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_LOCK = ROOT / "web" / "package-lock.json"

VULNERABLE_FROM = (7, 0, 0)
FIRST_PATCHED = (11, 0, 1)
ACCEPTED_PATHS = {
    "node_modules/@eslint/config-array/node_modules/minimatch": "10.2.6",
    "node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch": "10.2.6",
    "node_modules/eslint/node_modules/minimatch": "10.2.6",
    "node_modules/type-coverage-core/node_modules/minimatch": "10.2.6",
}


def _as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("-")[0].split(".")[:3])


def _locked_minimatch() -> dict[str, dict[str, object]]:
    packages = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))["packages"]
    return {
        path: entry
        for path, entry in packages.items()
        if path == "node_modules/minimatch" or path.endswith("/node_modules/minimatch")
    }


def _vulnerable(entries: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        path: entry
        for path, entry in entries.items()
        if VULNERABLE_FROM <= _as_tuple(str(entry["version"])) < FIRST_PATCHED
    }


def test_copias_vulneraveis_sao_exatamente_as_aceitas_no_adr_046() -> None:
    vulnerable = _vulnerable(_locked_minimatch())

    assert {path: entry["version"] for path, entry in vulnerable.items()} == ACCEPTED_PATHS


def test_copias_vulneraveis_sao_so_de_desenvolvimento() -> None:
    vulnerable = _vulnerable(_locked_minimatch())

    assert vulnerable
    assert all(entry.get("dev") is True for entry in vulnerable.values())
