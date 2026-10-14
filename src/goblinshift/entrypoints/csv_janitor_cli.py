"""CLI do CSV Janitor: limpa tabelas locais com a mesma função que o executor vai chamar.

Uso: gs-csv-janitor --params params.json --output-dir saida/ a.csv b.csv

Para cada tabela limpa, grava `<nome>.csv` em `--output-dir`, sem sobrescrever nada; o relatório
JSON vai para a saída padrão, na ordem das entradas. Sai com 0 se todas foram limpas, 1 se alguma
foi rejeitada e 2 se a configuração ou os caminhos forem inválidos.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from goblinshift.processing.csv_janitor import (
    InvalidParametersError,
    Limits,
    Params,
    TableCleaned,
    TableRejected,
    clean_table,
    params_from_mapping,
)

EXIT_ALL_CLEANED = 0
EXIT_SOME_REJECTED = 1
EXIT_USAGE = 2


class UsageError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        params = _load_params(args.params)
        outputs = _output_paths(args.tables, args.output_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        results = [
            _clean_file(table, output, params)
            for table, output in zip(args.tables, outputs, strict=True)
        ]
    except UsageError as error:
        print(f"gs-csv-janitor: {error}", file=sys.stderr)
        return EXIT_USAGE
    json.dump({"tables": results}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    rejected = any(result["outcome"] == "rejected" for result in results)
    return EXIT_SOME_REJECTED if rejected else EXIT_ALL_CLEANED


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gs-csv-janitor", description=__doc__.splitlines()[0])
    parser.add_argument("--params", type=Path, required=True, help="configuração em JSON (1.3)")
    parser.add_argument("--output-dir", type=Path, required=True, help="destino das tabelas limpas")
    parser.add_argument("tables", type=Path, nargs="+", help="tabelas CSV de entrada")
    return parser


def _load_params(path: Path) -> Params:
    try:
        raw = json.loads(
            path.read_bytes(),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (OSError, ValueError) as error:
        raise UsageError(f"configuração ilegível em {path}: {error}") from None
    if not isinstance(raw, dict):
        raise UsageError("a configuração deve ser um objeto JSON")
    try:
        return params_from_mapping(raw)
    except InvalidParametersError as error:
        raise UsageError(f"configuração inválida: {error}") from None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"chaves repetidas: {repeated}")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} não é um valor JSON aceito")


def _output_paths(tables: Sequence[Path], output_dir: Path) -> list[Path]:
    names = [table.with_suffix(".csv").name for table in tables]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise UsageError(f"entradas gerariam saídas com o mesmo nome: {repeated}")
    return [output_dir / name for name in names]


def _clean_file(table: Path, output: Path, params: Params) -> dict[str, object]:
    limit = Limits().table_bytes
    try:
        with table.open("rb") as source:
            # Um byte além do limite basta para a biblioteca rejeitar a tabela pelo tamanho.
            data = source.read(limit + 1)
    except OSError as error:
        raise UsageError(f"não foi possível ler {table}: {error}") from None
    match clean_table(data, params):
        case TableCleaned() as cleaned:
            _write_new(output, cleaned.content)
            return {
                "input": str(table),
                "outcome": "cleaned",
                "output": str(output),
                "counts": asdict(cleaned.counts),
                "formula_cells": dict(cleaned.formula_cells),
                "warnings": [asdict(warning) for warning in cleaned.warnings],
            }
        case TableRejected() as rejected:
            return {
                "input": str(table),
                "outcome": "rejected",
                "code": rejected.code,
                "reason": rejected.reason,
            }


def _write_new(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as target:
            target.write(content)
    except OSError as error:
        raise UsageError(f"não foi possível criar {path}: {error}") from None


if __name__ == "__main__":
    raise SystemExit(main())
