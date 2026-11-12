#!/usr/bin/env python3
"""Reprova se algum arquivo dos diretórios (inclusive dentro de .zip) tiver uma chave de API.

Roda antes de todo upload de artefato do Playwright: o trace.zip guarda os cabeçalhos dos pedidos,
e o job do gVisor subiu a chave `gsk_...` da conta de teste num artefato de repositório público.

Uso: python3 scripts/verificar_artefatos_sem_chave.py DIRETÓRIO...

O log do CI é público, então a saída traz só o caminho e a contagem de ocorrências, nunca o trecho
que bateu. Diretório que não existe é ignorado (o upload também o ignora). Um .zip que não abre
reprova: o que não dá para conferir não sobe.
"""

import io
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO

# O formato de src/goblinshift/identity/keys.py: gsk_<public_id de 16 hex>_<secret de 43>.
KEY_PATTERN = re.compile(rb"gsk_[0-9a-f]{16}_[A-Za-z0-9_-]{43}")
# Maior chave possível: o que sobra entre dois blocos para a chave que cruza a emenda.
KEY_MAX_LENGTH = 64
CHUNK_SIZE = 1024 * 1024
# trace.zip dentro de zip é raro; o teto evita laço em zip bomba.
MAX_ZIP_DEPTH = 3
EXIT_FOUND = 1
EXIT_USAGE = 2


def count_in_stream(stream: IO[bytes]) -> int:
    """Conta as chaves no fluxo, inclusive as que cruzam a emenda entre dois blocos."""
    total = 0
    carry = b""
    while chunk := stream.read(CHUNK_SIZE):
        window = carry + chunk
        total += sum(1 for match in KEY_PATTERN.finditer(window) if match.end() > len(carry))
        carry = window[-(KEY_MAX_LENGTH - 1) :]
    return total


def findings_in_zip(label: str, source: Path | io.BytesIO, depth: int) -> Iterator[tuple[str, int]]:
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        yield f"{label}: zip ilegível, não dá para conferir", -1
        return
    with archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_label = f"{label}!{member.filename}"
            if member.filename.lower().endswith(".zip") and depth < MAX_ZIP_DEPTH:
                nested = io.BytesIO(archive.read(member))
                yield from findings_in_zip(member_label, nested, depth + 1)
                continue
            with archive.open(member) as stream:
                found = count_in_stream(stream)
            if found:
                yield member_label, found


def findings_in_file(path: Path) -> Iterator[tuple[str, int]]:
    if path.suffix.lower() == ".zip":
        yield from findings_in_zip(str(path), path, depth=1)
        return
    with path.open("rb") as stream:
        found = count_in_stream(stream)
    if found:
        yield str(path), found


def scan(directories: list[Path]) -> int:
    reproved = 0
    for directory in directories:
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            for label, found in findings_in_file(path):
                reproved += 1
                if found < 0:
                    print(label, file=sys.stderr)
                else:
                    print(f"{label}: {found} ocorrência(s) do formato da chave", file=sys.stderr)
    return reproved


def main(argv: list[str]) -> int:
    if not argv:
        print("uso: verificar_artefatos_sem_chave.py DIRETÓRIO...", file=sys.stderr)
        return EXIT_USAGE
    directories = [Path(arg) for arg in argv if Path(arg).is_dir()]
    if scan(directories):
        print("artefato reprovado: não envie estes arquivos", file=sys.stderr)
        return EXIT_FOUND
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
