"""`package`: manifesto e ZIP determinístico gravados em out/ (3.2, 3.6, 3.9)."""

import hashlib
import json
import os
import time
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.packaging import (
    MANIFEST_NAME,
    REPORT_NAME,
    PackageLimits,
    PackageSpec,
    package,
)
from goblinshift.processing.recipe import (
    FailureCode,
    InputFile,
    ItemOk,
    ItemRejected,
    OutputGroup,
    OutputRole,
    RecipeFailure,
    Report,
)

REPORT_BYTES = b'{"relatorio": "qualquer"}\n'
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
REGULAR_FILE_0644 = 0o100644 << 16
UNIX = 3

type Output = tuple[OutputGroup | None, str, bytes]


def inputs_for(count: int) -> tuple[InputFile, ...]:
    return tuple(
        InputFile(i, Path(f"/work/inputs/{i:04}.bin"), f"e{i}.bin", 10 + i, f"{i:x}" * 64)
        for i in range(count)
    )


def spec_for(count: int, recipe_id: str = "image-forge") -> PackageSpec:
    return PackageSpec(recipe_id, 1, None, {"quality": 85}, inputs_for(count))


def prepare_out(out: Path, per_item: Sequence[Sequence[Output]]) -> Report:
    """Cria as saídas pelo `OutputDir` real (um item por entrada) e o report.json."""
    out.mkdir()
    directory = FileSystemOutputDir(out)
    items = []
    for position, outputs in enumerate(per_item):
        names = []
        for group, name, data in outputs:
            created = directory.create(name, OutputRole.CONTENT, position, group)
            with created.stream as stream:
                stream.write(data)
            names.append(created.name)
        items.append(ItemOk(position, tuple(names)))
    (out / REPORT_NAME).write_bytes(REPORT_BYTES)
    return Report(items=tuple(items))


TWO_ITEMS: list[list[Output]] = [
    [(OutputGroup.IMAGES, "z.jpg", b"\xff\xd8jpeg z"), (OutputGroup.THUMBNAILS, "z.jpg", b"tz")],
    [(OutputGroup.TABLES, "a.csv", b"nome\r\nAna\r\n" * 50)],
]


def entries(path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(path) as archive:
        return archive.infolist()


def test_grava_manifesto_e_zip_na_raiz_de_out(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)

    packaged = package(report, out, spec_for(2))

    assert packaged.manifest_name == MANIFEST_NAME
    assert packaged.package_name == "image-forge.zip"
    assert packaged.entries == 5
    assert (out / MANIFEST_NAME).is_file()
    assert (out / "image-forge.zip").is_file()


def test_zip_traz_conteudo_na_ordem_do_relatorio_e_depois_manifesto_e_relatorio(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)

    package(report, out, spec_for(2))

    with zipfile.ZipFile(out / "image-forge.zip") as archive:
        assert archive.namelist() == [
            "images/z.jpg",
            "thumbnails/z.jpg",
            "tables/a.csv",
            MANIFEST_NAME,
            REPORT_NAME,
        ]
        assert archive.testzip() is None
        assert archive.read("images/z.jpg") == b"\xff\xd8jpeg z"
        assert archive.read("tables/a.csv") == b"nome\r\nAna\r\n" * 50
        assert archive.read(MANIFEST_NAME) == (out / MANIFEST_NAME).read_bytes()
        assert archive.read(REPORT_NAME) == REPORT_BYTES


def test_entradas_tem_data_fixa_permissao_0644_e_sistema_unix(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)

    package(report, out, spec_for(2))

    infos = entries(out / "image-forge.zip")
    assert {info.date_time for info in infos} == {FIXED_DATE}
    assert {info.external_attr for info in infos} == {REGULAR_FILE_0644}
    assert {info.create_system for info in infos} == {UNIX}
    assert not any(info.is_dir() for info in infos)


def test_compressao_de_cada_entrada_segue_a_extensao(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)

    package(report, out, spec_for(2))

    compression = {info.filename: info.compress_type for info in entries(out / "image-forge.zip")}
    assert compression == {
        "images/z.jpg": zipfile.ZIP_STORED,
        "thumbnails/z.jpg": zipfile.ZIP_STORED,
        "tables/a.csv": zipfile.ZIP_DEFLATED,
        MANIFEST_NAME: zipfile.ZIP_DEFLATED,
        REPORT_NAME: zipfile.ZIP_DEFLATED,
    }


def test_manifesto_traz_tamanho_e_sha256_reais_de_cada_saida(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)

    package(report, out, spec_for(2))

    manifest = json.loads((out / MANIFEST_NAME).read_bytes())
    data = b"nome\r\nAna\r\n" * 50
    assert manifest["outputs"][2] == {
        "path": "tables/a.csv",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_position": 1,
    }


def test_sem_saidas_de_conteudo_o_zip_tem_so_manifesto_e_relatorio(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / REPORT_NAME).write_bytes(REPORT_BYTES)
    report = Report(items=(ItemRejected(0, "CSV_STRUCTURE_INVALID", "aspas"),))

    packaged = package(report, out, spec_for(1, "csv-janitor"))

    assert packaged.entries == 2
    with zipfile.ZipFile(out / "csv-janitor.zip") as archive:
        assert archive.namelist() == [MANIFEST_NAME, REPORT_NAME]
        assert archive.testzip() is None


def test_nome_nao_ascii_vai_em_utf8_e_volta_igual(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, [[(OutputGroup.IMAGES, "Côte d Ivoire 日本.png", b"png")]])

    package(report, out, spec_for(1))

    [content, *_] = entries(out / "image-forge.zip")
    assert content.filename == "images/Côte d Ivoire 日本.png"


def test_manifesto_exatamente_no_teto_passa_e_um_byte_acima_falha(tmp_path: Path) -> None:
    first = tmp_path / "a"
    report = prepare_out(first, TWO_ITEMS)
    package(report, first, spec_for(2))
    size = (first / MANIFEST_NAME).stat().st_size

    on_point = tmp_path / "b"
    prepare_out(on_point, TWO_ITEMS)
    package(report, on_point, spec_for(2), PackageLimits(manifest_max_bytes=size))

    off_point = tmp_path / "c"
    prepare_out(off_point, TWO_ITEMS)
    with pytest.raises(RecipeFailure) as failure:
        package(report, off_point, spec_for(2), PackageLimits(manifest_max_bytes=size - 1))
    assert failure.value.code is FailureCode.REPORT_LIMIT_EXCEEDED
    assert not (off_point / MANIFEST_NAME).exists()
    assert not (off_point / "image-forge.zip").exists()


def test_zip_exatamente_no_teto_passa_e_um_byte_acima_falha_sem_deixar_arquivo(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a"
    report = prepare_out(first, TWO_ITEMS)
    package(report, first, spec_for(2))
    size = (first / "image-forge.zip").stat().st_size

    on_point = tmp_path / "b"
    prepare_out(on_point, TWO_ITEMS)
    package(report, on_point, spec_for(2), PackageLimits(package_max_bytes=size))
    assert (on_point / "image-forge.zip").stat().st_size == size

    off_point = tmp_path / "c"
    prepare_out(off_point, TWO_ITEMS)
    with pytest.raises(RecipeFailure) as failure:
        package(report, off_point, spec_for(2), PackageLimits(package_max_bytes=size - 1))
    assert failure.value.code is FailureCode.OUTPUT_LIMIT_EXCEEDED
    assert not (off_point / "image-forge.zip").exists()


def test_nunca_sobrescreve_um_pacote_ja_existente(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)
    (out / "image-forge.zip").write_bytes(b"de outra tentativa")

    with pytest.raises(FileExistsError):
        package(report, out, spec_for(2))
    assert (out / "image-forge.zip").read_bytes() == b"de outra tentativa"


def test_saida_citada_que_virou_symlink_nao_e_seguida(tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = prepare_out(out, TWO_ITEMS)
    secret = tmp_path / "segredo.txt"
    secret.write_bytes(b"fora de out/")
    (out / "tables" / "a.csv").unlink()
    (out / "tables" / "a.csv").symlink_to(secret)

    with pytest.raises(OSError):
        package(report, out, spec_for(2))
    assert not (out / "image-forge.zip").exists()


@pytest.fixture
def other_environment() -> Iterator[None]:
    """Umask e fuso diferentes do padrão durante o segundo empacotamento."""
    previous_umask = os.umask(0o077)
    previous_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"
    time.tzset()
    try:
        yield
    finally:
        os.umask(previous_umask)
        if previous_tz is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()


def test_mesmo_conteudo_da_os_mesmos_bytes_com_mtime_umask_e_fuso_diferentes(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    first = tmp_path / "a"
    report = prepare_out(first, TWO_ITEMS)
    package(report, first, spec_for(2))

    second = tmp_path / "b"
    request.getfixturevalue("other_environment")
    prepare_out(second, TWO_ITEMS)
    for path in second.rglob("*"):
        os.utime(path, (1_900_000_000, 1_900_000_000))
    package(report, second, spec_for(2))

    assert (first / "image-forge.zip").read_bytes() == (second / "image-forge.zip").read_bytes()
    assert (first / MANIFEST_NAME).read_bytes() == (second / MANIFEST_NAME).read_bytes()
