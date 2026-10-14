"""JSON estrito para documentos que vêm do sandbox (`report.json`, `manifest.json`).

Chave repetida e `NaN`/`Infinity` são recusados: o `json` do Python aceita os dois em silêncio, e
um documento ambíguo não pode significar uma coisa para o worker e outra para quem o baixa.
"""

import json


def parse_strict_json(data: bytes) -> object:
    """Documento JSON; `ValueError` se não for JSON, se repetir chave ou trouxer `NaN`."""
    return json.loads(data, object_pairs_hook=_reject_duplicates, parse_constant=_reject)


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("chaves repetidas")
    return result


def _reject(name: str) -> object:
    raise ValueError(f"{name} não é um valor JSON aceito")
