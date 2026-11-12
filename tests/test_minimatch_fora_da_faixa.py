"""Nenhuma cópia do `minimatch` do front fica nas faixas vulneráveis (ADR 052).

Dois avisos de segurança cobrem o `minimatch`, e as faixas se sobrepõem em 11.0.0:

- GHSA-g3hv-75wg-m249: negação de serviço em `firstPhasePreProcess()`, em `>= 7.0.0, < 11.0.1`
  (sem correção na linha 10).
- GHSA-cf2h-pxjq-68xw: regressão de `mergeRows` na 11.0.0, em `>= 11.0.0, <= 11.0.6`.

O `overrides` do `web/package.json` empurra as cópias dos pais (`eslint`, `@eslint/config-array`,
`@typescript-eslint/typescript-estree`, `type-coverage-core`) para `^11.0.7`. Este teste lê o
`web/package-lock.json` e reprova qualquer cópia que volte a cair numa das duas faixas, por
exemplo se o override sair, se o piso baixar ou se um pai novo trouxer a 10.x.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_LOCK = ROOT / "web" / "package-lock.json"

# Faixas vulneráveis como intervalos semiabertos [início, fim).
VULNERABLE_RANGES = {
    "GHSA-g3hv-75wg-m249": ((7, 0, 0), (11, 0, 1)),
    "GHSA-cf2h-pxjq-68xw": ((11, 0, 0), (11, 0, 7)),
}


def _as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("-")[0].split(".")[:3])


def _locked_minimatch() -> dict[str, str]:
    packages = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))["packages"]
    return {
        path: str(entry["version"])
        for path, entry in packages.items()
        if path == "node_modules/minimatch" or path.endswith("/node_modules/minimatch")
    }


def _in_range(version: str, advisory: str) -> bool:
    start, end = VULNERABLE_RANGES[advisory]
    return start <= _as_tuple(version) < end


def test_leitor_do_lock_acha_copias_do_minimatch() -> None:
    # Sem isto, uma mudança no formato do lock deixaria os testes abaixo passarem em silêncio.
    assert _locked_minimatch()


def test_nenhuma_copia_na_faixa_do_ghsa_g3hv_75wg_m249() -> None:
    in_range = {
        path: version
        for path, version in _locked_minimatch().items()
        if _in_range(version, "GHSA-g3hv-75wg-m249")
    }

    assert in_range == {}


def test_nenhuma_copia_na_faixa_do_ghsa_cf2h_pxjq_68xw() -> None:
    in_range = {
        path: version
        for path, version in _locked_minimatch().items()
        if _in_range(version, "GHSA-cf2h-pxjq-68xw")
    }

    assert in_range == {}
