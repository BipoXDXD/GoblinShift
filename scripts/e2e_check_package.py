"""Confere o ZIP baixado nos scripts de ponta a ponta contra o expected.json do gerador.

Uso: python3 -I scripts/e2e_check_package.py <pacote.zip> <expected.json> <dir das fixtures>

Roda no Python do host, só com a biblioteca padrão (sem Pillow, sem o ambiente do projeto) e com
`-I`, que isola de site-packages e do diretório atual. Confere:
- a lista e a ordem exatas das entradas do ZIP;
- o manifesto: receita, parâmetros enviados, cada entrada (nome, tamanho, SHA-256 do arquivo
  local, status, saídas, código de rejeição, `duplicate_of`) e cada saída (caminho, tamanho e
  SHA-256 dos bytes dentro do ZIP);
- os extras do cenário: dimensões de JPEG lidas do SOF, ausência de EXIF, saída com os mesmos
  bytes de uma entrada, taxa, canais e bits do áudio (STREAMINFO do FLAC) e
  texto que não pode aparecer em nenhuma entrada do ZIP (o canário dos metadados de um vídeo).
Sai com 0 e uma linha "ok" se tudo confere; senão, 1 com a lista de divergências no stderr.
"""

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
REPORT_NAME = "report.json"
_SOF_MARKERS = (0xC0, 0xC1, 0xC2)
_EXIF_HEADER = b"Exif\x00\x00"
_FLAC_MAGIC = b"fLaC"
_FLAC_STREAMINFO = 0
# STREAMINFO começa depois do "fLaC" e do cabeçalho do bloco (4 + 4 bytes); taxa, canais e bits
# ficam nos 8 bytes a partir do 11º byte do bloco (RFC 9639, 8.2).
_FLAC_FIELDS = slice(18, 26)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    package, expected_path, fixtures = Path(argv[0]), Path(argv[1]), Path(argv[2])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(package) as archive:
        problems = check(archive, expected, fixtures)
    if problems:
        print(f"{package.name}: {len(problems)} divergência(s)", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"ok: {package.name} confere com {expected_path.name}")
    return 0


def check(archive: zipfile.ZipFile, expected: dict[str, Any], fixtures: Path) -> list[str]:
    names = archive.namelist()
    if names != expected["zip_entries"]:
        return [f"entradas do ZIP {names}; esperadas {expected['zip_entries']}"]
    if archive.testzip() is not None:
        return ["ZIP com CRC inválido"]
    manifest = json.loads(archive.read(MANIFEST_NAME))
    json.loads(archive.read(REPORT_NAME))
    return (
        _check_recipe(manifest, expected)
        + _check_inputs(manifest, expected, fixtures)
        + _check_outputs(archive, manifest, expected)
        + _check_jpeg_sizes(archive, expected.get("jpeg_sizes", {}))
        + _check_without_exif(archive, expected.get("jpeg_without_exif", []))
        + _check_same_bytes(archive, expected.get("same_bytes_as_input", {}), expected, fixtures)
        + _check_audio(archive, expected.get("audio", {}))
        + _check_absent_text(archive, expected.get("absent_text", []))
    )


def _check_recipe(manifest: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    problems = []
    recipe = manifest.get("recipe", {})
    if (recipe.get("id"), recipe.get("version")) != (
        expected["recipe_id"],
        expected["recipe_version"],
    ):
        problems.append(f"receita no manifesto: {recipe}")
    # O manifesto traz os parâmetros resolvidos (com defaults); os enviados têm de estar lá.
    for key, value in expected["parameters"].items():
        if manifest.get("parameters", {}).get(key) != value:
            problems.append(f"parâmetro {key}: {manifest.get('parameters', {}).get(key)!r}")
    return problems


def _check_inputs(manifest: dict[str, Any], expected: dict[str, Any], fixtures: Path) -> list[str]:
    actual = manifest.get("inputs", [])
    if len(actual) != len(expected["inputs"]):
        return [f"manifesto com {len(actual)} entradas; esperadas {len(expected['inputs'])}"]
    problems = []
    for entry, found in zip(expected["inputs"], actual, strict=True):
        wanted = entry["manifest"]
        local = _sha256((fixtures / entry["path"]).read_bytes())
        if found.get("sha256") != local:
            problems.append(f"entrada {wanted['position']}: SHA-256 diferente do arquivo enviado")
        for key, value in wanted.items():
            if found.get(key) != value:
                problems.append(
                    f"entrada {wanted['position']} ({wanted['original_name']}): "
                    f"{key} = {found.get(key)!r}; esperado {value!r}"
                )
    return problems


def _check_outputs(
    archive: zipfile.ZipFile, manifest: dict[str, Any], expected: dict[str, Any]
) -> list[str]:
    content = [n for n in expected["zip_entries"] if n not in (MANIFEST_NAME, REPORT_NAME)]
    outputs = manifest.get("outputs", [])
    if [o.get("path") for o in outputs] != content:
        return [f"saídas do manifesto {[o.get('path') for o in outputs]}; esperadas {content}"]
    problems = []
    for output in outputs:
        data = archive.read(output["path"])
        if (output.get("size"), output.get("sha256")) != (len(data), _sha256(data)):
            problems.append(f"saída {output['path']}: tamanho ou SHA-256 diferente do ZIP")
    return problems


def _check_jpeg_sizes(archive: zipfile.ZipFile, sizes: dict[str, Any]) -> list[str]:
    problems = []
    for path, (width, height) in sizes.items():
        found = _jpeg_size(archive.read(path))
        if found != (width, height):
            problems.append(f"{path}: {found}; esperado {(width, height)}")
    return problems


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Largura e altura do primeiro SOF, percorrendo os segmentos a partir do SOI."""
    offset = 2
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        (length,) = struct.unpack_from(">H", data, offset + 2)
        if marker in _SOF_MARKERS:
            height, width = struct.unpack_from(">HH", data, offset + 5)
            return width, height
        offset += 2 + length
    return None


def _check_without_exif(archive: zipfile.ZipFile, paths: list[str]) -> list[str]:
    return [f"{path}: ainda tem EXIF" for path in paths if _EXIF_HEADER in archive.read(path)]


def _check_same_bytes(
    archive: zipfile.ZipFile, pairs: dict[str, Any], expected: dict[str, Any], fixtures: Path
) -> list[str]:
    problems = []
    for path, position in pairs.items():
        original = (fixtures / expected["inputs"][position]["path"]).read_bytes()
        if archive.read(path) != original:
            problems.append(f"{path}: bytes diferentes da entrada {position}")
    return problems


def _check_audio(archive: zipfile.ZipFile, specs: dict[str, Any]) -> list[str]:
    problems = []
    for path, wanted in specs.items():
        found = _audio_format(archive.read(path))
        if found != wanted:
            problems.append(f"{path}: {found}; esperado {wanted}")
    return problems


def _audio_format(data: bytes) -> dict[str, int] | None:
    """Taxa, canais e bits por amostra do STREAMINFO de um FLAC; None se não for FLAC."""
    if not data.startswith(_FLAC_MAGIC) or data[4] & 0x7F != _FLAC_STREAMINFO:
        return None
    fields = int.from_bytes(data[_FLAC_FIELDS], "big")
    return {
        "sample_rate": fields >> 44,
        "channels": ((fields >> 41) & 0x7) + 1,
        "bits_per_sample": ((fields >> 36) & 0x1F) + 1,
    }


def _check_absent_text(archive: zipfile.ZipFile, texts: list[str]) -> list[str]:
    return [
        f"{name}: contém {text!r}"
        for text in texts
        for name in archive.namelist()
        if text.encode() in archive.read(name)
    ]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
