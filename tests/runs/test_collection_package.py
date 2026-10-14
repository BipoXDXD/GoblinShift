"""Pacote comum em out/ (ADR 019): `manifest.json` e `<recipe_id>.zip`, dado do sandbox.

Os dois são publicados para o dono baixar, então a coleta os confere contra o que ela mesma leu de
out/: o manifesto lista exatamente as saídas citadas, com nome, tamanho e SHA-256; o ZIP tem
exatamente as saídas, o manifesto e o relatório, nessa ordem, cada entrada um arquivo regular com
o conteúdo do arquivo de out/ e a compressão do empacotador. Qualquer desvio é executor fora do
contrato (`CollectionError`); nada é descomprimido além do tamanho esperado.
"""

import os
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

from goblinshift.runs.lifecycle import ErrorCode
from goblinshift.runs.worker.collection import (
    DEFAULT_LIMITS,
    Collected,
    CollectionError,
    OutputLimitError,
    OutputLimits,
    collect_outputs,
)
from tests.runs.collection_support import (
    PACKAGE,
    RECIPE,
    entry,
    manifest_for,
    ok_item,
    package_entries,
    rewrite_package,
    write_manifest,
    write_out,
    write_package,
)

FILES = {"a.csv": b"nome\r\nAna\r\n", "images/b.png": b"\x89PNG imagem"}
NAMES = list(FILES)
# Cabeçalho local do ZIP: flags em +6, tamanho descomprimido em +22; central: +8 e +24.
_LOCAL_FLAGS, _LOCAL_SIZE = 6, 22
_CENTRAL_FLAGS, _CENTRAL_SIZE = 8, 24
_ENCRYPTED = 0x1


def valid_out(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, *NAMES)], FILES)
    write_package(out)
    return out


def collect(out: Path, limits: OutputLimits = DEFAULT_LIMITS) -> Collected:
    return collect_outputs(out, recipe_id=RECIPE, input_count=1, limits=limits)


def rebuild_with_manifest(out: Path, manifest: object) -> None:
    """Troca o manifesto e remonta o ZIP com ele: só o manifesto fica fora do contrato."""
    write_manifest(out, manifest)
    rewrite_package(out, package_entries(out))


# --- Pacote válido -------------------------------------------------------------------------------


def test_pacote_coerente_e_coletado_a_parte_do_conteudo(tmp_path: Path) -> None:
    out = valid_out(tmp_path)

    collected = collect(out)

    assert [f.name for f in collected.outputs] == NAMES
    assert collected.manifest_file is not None
    assert collected.manifest_file.name == "manifest.json"
    assert collected.package_file is not None
    assert collected.package_file.name == PACKAGE
    assert collected.package_file.path == out / PACKAGE
    assert collected.package_file.size == (out / PACKAGE).stat().st_size


def test_saidas_coletadas_trazem_o_sha256_lido_de_out(tmp_path: Path) -> None:
    out = valid_out(tmp_path)

    collected = collect(out)

    assert collected.outputs[0].sha256 == bytes.fromhex(manifest_for(out)["outputs"][0]["sha256"])


def test_executor_anterior_a_fatia_5_nao_deixa_pacote(tmp_path: Path) -> None:
    # Runs aceitas antes do deploy continuam na imagem fixada nelas, que não empacota.
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})

    collected = collect(out)

    assert collected.manifest_file is None
    assert collected.package_file is None


@pytest.mark.parametrize("only", ["manifest.json", PACKAGE])
def test_manifesto_sem_pacote_ou_pacote_sem_manifesto_e_recusado(tmp_path: Path, only: str) -> None:
    out = valid_out(tmp_path)
    (out / ({"manifest.json", PACKAGE} - {only}).pop()).unlink()

    with pytest.raises(CollectionError, match="juntos"):
        collect(out)


def test_pacote_com_nome_de_outra_receita_e_arquivo_nao_citado(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    (out / "image-forge.zip").write_bytes(b"PK")

    with pytest.raises(CollectionError, match="citadas"):
        collect(out)


def test_manifesto_e_pacote_nao_contam_no_teto_de_arquivos_nem_de_bytes(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})
    write_package(out)

    collected = collect(out, OutputLimits(max_files=1, max_bytes=1))

    assert [f.name for f in collected.outputs] == ["a.csv"]


def test_manifesto_no_teto_passa_e_acima_e_falha_deterministica(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    size = (out / "manifest.json").stat().st_size

    collect(out, OutputLimits(manifest_max_bytes=size))
    with pytest.raises(OutputLimitError) as caught:
        collect(out, OutputLimits(manifest_max_bytes=size - 1))

    assert caught.value.code is ErrorCode.REPORT_LIMIT_EXCEEDED


def test_pacote_no_teto_passa_e_acima_e_falha_deterministica(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    size = (out / PACKAGE).stat().st_size

    collect(out, OutputLimits(package_max_bytes=size))
    with pytest.raises(OutputLimitError) as caught:
        collect(out, OutputLimits(package_max_bytes=size - 1))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_tetos_padrao_do_pacote_sao_os_de_1_7() -> None:
    limits = OutputLimits()

    assert (limits.manifest_max_bytes, limits.package_max_bytes) == (8 * 1024**2, 512 * 1024**2)


@pytest.mark.parametrize("name", ["manifest.json", PACKAGE])
def test_manifesto_ou_pacote_que_e_symlink_e_recusado(tmp_path: Path, name: str) -> None:
    out = valid_out(tmp_path)
    (tmp_path / "segredo").write_bytes(b"x")
    (out / name).unlink()
    (out / name).symlink_to(tmp_path / "segredo")

    with pytest.raises(CollectionError, match="regular"):
        collect(out)


def test_manifest_json_dentro_de_um_grupo_e_conteudo_comum(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "text/manifest.json")], {"text/manifest.json": b"{}"})

    collected = collect(out)

    assert [f.name for f in collected.outputs] == ["text/manifest.json"]
    assert collected.manifest_file is None


# --- Manifesto: lista exatamente as saídas coletadas ---------------------------------------------


def _manifest_with(out: Path, change: str) -> object:
    manifest = manifest_for(out)
    outputs: list[Any] = manifest["outputs"]
    match change:
        case "sha256":
            outputs[0]["sha256"] = "0" * 64
        case "sha256-maiusculo":
            outputs[0]["sha256"] = outputs[0]["sha256"].upper()
        case "tamanho":
            outputs[0]["size"] += 1
        case "tamanho-texto":
            outputs[0]["size"] = str(outputs[0]["size"])
        case "caminho":
            outputs[0]["path"] = "../a.csv"
        case "saida-a-menos":
            outputs.pop()
        case "saida-a-mais":
            outputs.append(dict(outputs[0]))
        case "ordem":
            outputs.reverse()
        case "schema":
            manifest["schema_version"] = 2
        case "outputs-nao-lista":
            manifest["outputs"] = {}
        case "saida-nao-objeto":
            outputs[0] = "a.csv"
        case "documento-nao-objeto":
            return [manifest]
    return manifest


@pytest.mark.parametrize(
    "change",
    [
        "sha256",
        "sha256-maiusculo",
        "tamanho",
        "tamanho-texto",
        "caminho",
        "saida-a-menos",
        "saida-a-mais",
        "ordem",
        "schema",
        "outputs-nao-lista",
        "saida-nao-objeto",
        "documento-nao-objeto",
    ],
)
def test_manifesto_que_nao_lista_exatamente_as_saidas_e_recusado(
    tmp_path: Path, change: str
) -> None:
    out = valid_out(tmp_path)
    rebuild_with_manifest(out, _manifest_with(out, change))

    with pytest.raises(CollectionError, match="manifesto"):
        collect(out)


@pytest.mark.parametrize(
    "raw",
    [b"nao e json", b'{"schema_version": 1, "schema_version": 1}', b'{"a": NaN}'],
)
def test_manifesto_que_nao_e_json_estrito_e_recusado(tmp_path: Path, raw: bytes) -> None:
    out = valid_out(tmp_path)
    (out / "manifest.json").write_bytes(raw)
    rewrite_package(out, package_entries(out))

    with pytest.raises(CollectionError, match="manifesto"):
        collect(out)


# --- ZIP: exatamente as saídas, o manifesto e o relatório, como out/ os tem ---------------------


def _entries_with(out: Path, change: str) -> list[tuple[zipfile.ZipInfo, bytes]]:
    entries: list[tuple[zipfile.ZipInfo, bytes]] = list(package_entries(out))
    first = FILES["a.csv"]
    match change:
        case "entrada-extra":
            entries.append(entry("extra.txt", b"x"))
        case "entrada-a-menos":
            entries.pop(0)
        case "entrada-repetida":
            entries.insert(1, entry("a.csv", first))
        case "ordem":
            entries[0], entries[1] = entries[1], entries[0]
        case "caminho-com-ponto-ponto":
            entries[0] = entry("../a.csv", first)
        case "caminho-absoluto":
            entries[0] = entry("/a.csv", first)
        case "barra-invertida":
            entries[1] = entry("images\\b.png", FILES["images/b.png"])
        case "diretorio":
            entries.insert(0, entry("images/", b"", mode=0o40755))
        case "symlink":
            entries[0] = entry("a.csv", first, mode=0o120777)
        case "executavel-com-setuid":
            entries[0] = entry("a.csv", first, mode=0o104755)
        case "sem-modo-unix":
            entries[0] = entry("a.csv", first, mode=0)
        case "conteudo-trocado":
            entries[0] = entry("a.csv", first.upper())
        case "tamanho-diferente":
            entries[0] = entry("a.csv", first + b"x")
        case "compressao-trocada":
            entries[0] = entry("a.csv", first, method=zipfile.ZIP_STORED)
        case "png-comprimido":
            entries[1] = entry("images/b.png", FILES["images/b.png"], method=zipfile.ZIP_DEFLATED)
        case "bomba-declarada":
            entries[0] = entry("a.csv", b"\0" * (64 * 1024 * 1024))
    return entries


@pytest.mark.parametrize(
    "change",
    [
        "entrada-extra",
        "entrada-a-menos",
        "entrada-repetida",
        "ordem",
        "caminho-com-ponto-ponto",
        "caminho-absoluto",
        "barra-invertida",
        "diretorio",
        "symlink",
        "executavel-com-setuid",
        "sem-modo-unix",
        "conteudo-trocado",
        "tamanho-diferente",
        "compressao-trocada",
        "png-comprimido",
        "bomba-declarada",
    ],
)
def test_zip_fora_do_contrato_e_recusado(tmp_path: Path, change: str) -> None:
    out = valid_out(tmp_path)
    rewrite_package(out, _entries_with(out, change))

    with pytest.raises(CollectionError, match="pacote"):
        collect(out)


def test_zip_corrompido_e_recusado(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    (out / PACKAGE).unlink()
    (out / PACKAGE).write_bytes(b"PK\x03\x04 lixo")

    with pytest.raises(CollectionError, match="pacote"):
        collect(out)


def test_bomba_que_mente_o_tamanho_no_cabecalho_nao_e_descomprimida(tmp_path: Path) -> None:
    # 64 MiB de zeros comprimidos, com o cabeçalho dizendo que a entrada tem o tamanho de a.csv.
    out = valid_out(tmp_path)
    entries = _entries_with(out, "bomba-declarada")
    rewrite_package(out, entries)
    _patch_first_entry(out / PACKAGE, size=len(FILES["a.csv"]))

    with pytest.raises(CollectionError, match="pacote"):
        collect(out)


def test_entrada_cifrada_e_recusada(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    _patch_first_entry(out / PACKAGE, flags=_ENCRYPTED)

    with pytest.raises(CollectionError, match="pacote"):
        collect(out)


def test_zip_lido_sem_seguir_symlink_do_proprio_pacote(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    outside = tmp_path / "fora.zip"
    os.replace(out / PACKAGE, outside)
    (out / PACKAGE).symlink_to(outside)

    with pytest.raises(CollectionError, match="regular"):
        collect(out)


def _patch_first_entry(path: Path, *, size: int | None = None, flags: int | None = None) -> None:
    """Reescreve campos da primeira entrada nos dois cabeçalhos (local e diretório central)."""
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    for start, flags_at, size_at in (
        (0, _LOCAL_FLAGS, _LOCAL_SIZE),
        (central, _CENTRAL_FLAGS, _CENTRAL_SIZE),
    ):
        if size is not None:
            struct.pack_into("<I", data, start + size_at, size)
        if flags is not None:
            (current,) = struct.unpack_from("<H", data, start + flags_at)
            struct.pack_into("<H", data, start + flags_at, current | flags)
    path.write_bytes(bytes(data))


def test_conferencia_do_zip_chama_o_heartbeat_enquanto_le(tmp_path: Path) -> None:
    out = valid_out(tmp_path)
    beats: list[None] = []

    collect_outputs(out, recipe_id=RECIPE, input_count=1, heartbeat=lambda: beats.append(None))

    assert len(beats) >= len(FILES) + 2
