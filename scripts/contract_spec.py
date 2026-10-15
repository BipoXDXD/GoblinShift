"""Deriva do contrato OpenAPI a spec que o Schemathesis consegue gerar (scripts/contract-test.sh).

O corpo de `POST /runs` é publicado como `allOf` (campos comuns) + `oneOf` (variantes) com
`unevaluatedProperties: false` (ver `goblinshift.catalog.versions`), e o gerador do Schemathesis
não suporta `unevaluatedProperties`. Este script reescreve cada corpo nesse formato como um
`oneOf` de objetos fechados: cada variante recebe as propriedades comuns e
`additionalProperties: false`. Os dois aceitam exatamente os mesmos documentos.

Qualquer outro uso de `unevaluatedProperties` é recusado com erro, em vez de reescrito por
aproximação: o teste de contrato não pode exercitar em silêncio um corpo diferente do publicado.

Uso: `uv run python -m scripts.contract_spec <spec.json> <derivada.json>`.
"""

import copy
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
_KEYWORD = "unevaluatedProperties"


class UnsupportedShapeError(ValueError):
    """`unevaluatedProperties` num formato que a derivação não sabe reescrever sem perda."""


def derive(spec: dict[str, Any]) -> dict[str, Any]:
    """Cópia de `spec` com cada `allOf` fechado por `unevaluatedProperties` achatado."""
    derived: dict[str, Any] = _rewrite(copy.deepcopy(spec))
    return derived


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("uso: python -m scripts.contract_spec <spec.json> <derivada.json>", file=sys.stderr)
        return EXIT_USAGE
    source, target = (Path(arg) for arg in args)
    spec = json.loads(source.read_text(encoding="utf-8"))
    target.write_text(json.dumps(derive(spec), ensure_ascii=False), encoding="utf-8")
    return EXIT_OK


def _rewrite(node: Any) -> Any:
    if isinstance(node, list):
        return [_rewrite(item) for item in node]
    if not isinstance(node, dict):
        return node
    if _KEYWORD in node:
        return _flatten(node)
    return {key: _rewrite(value) for key, value in node.items()}


def _flatten(schema: dict[str, Any]) -> dict[str, Any]:
    if set(schema) != {"allOf", _KEYWORD} or schema[_KEYWORD] is not False:
        raise UnsupportedShapeError(f"esperado só allOf + {_KEYWORD}: false, veio {sorted(schema)}")
    parts = schema["allOf"]
    if len(parts) != 2 or set(parts[1]) != {"oneOf"}:
        raise UnsupportedShapeError("esperado allOf com [campos comuns, {oneOf: variantes}]")
    common, variants = parts[0], parts[1]["oneOf"]
    if set(common) != {"type", "properties"} or common["type"] != "object":
        raise UnsupportedShapeError(f"campos comuns fora do formato: {sorted(common)}")
    return {"oneOf": [_close(variant, common["properties"]) for variant in variants]}


def _close(variant: dict[str, Any], common_properties: dict[str, Any]) -> dict[str, Any]:
    properties = variant.get("properties", {})
    repeated = set(common_properties) & set(properties)
    if repeated:
        raise UnsupportedShapeError(f"campo comum repetido na variante: {sorted(repeated)}")
    return {
        **variant,
        "properties": {**common_properties, **properties},
        "additionalProperties": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
