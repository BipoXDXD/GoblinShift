"""Propriedades do pacote para saídas geradas (3.9): determinismo, fidelidade, ordem e nomes."""

import hashlib
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.packaging import MANIFEST_NAME, REPORT_NAME, PackageSpec, package
from goblinshift.processing.recipe import InputFile, ItemOk, OutputGroup, OutputRole, Report

# Letras e dígitos de qualquer escrita, mais a pontuação comum em nomes de arquivo. Fica de fora o
# que o OutputDir recusa (separadores, controles) e o que o filesystem do Mac reescreveria
# (marcas combinantes, que o APFS compara normalizadas).
NAME_CHARS = st.characters(categories=("Lu", "Ll", "Lo", "Nd"), include_characters=" -_.()")
NAMES = st.text(NAME_CHARS, min_size=1, max_size=30).filter(lambda n: n not in (".", ".."))
GROUPS = st.one_of(st.none(), st.sampled_from(OutputGroup))
OUTPUT = st.tuples(GROUPS, NAMES, st.binary(max_size=2048))
ITEMS = st.lists(st.lists(OUTPUT, max_size=4), min_size=1, max_size=5)
PROPERTY_SETTINGS = settings(max_examples=40, deadline=timedelta(seconds=5))

type Outputs = list[list[tuple[OutputGroup | None, str, bytes]]]


@contextmanager
def packaged(per_item: Outputs) -> Iterator[tuple[Path, dict[str, bytes], list[str]]]:
    """Empacota as saídas num diretório novo; devolve o ZIP, os bytes de cada saída e a ordem."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        out.mkdir()
        directory = FileSystemOutputDir(out)
        items, contents, order = [], {}, []
        for position, outputs in enumerate(per_item):
            names = []
            for group, name, data in outputs:
                created = directory.create(name, OutputRole.CONTENT, position, group)
                with created.stream as stream:
                    stream.write(data)
                names.append(created.name)
                contents[created.name] = data
                order.append(created.name)
            items.append(ItemOk(position, tuple(names)))
        (out / REPORT_NAME).write_bytes(b"{}\n")
        inputs = tuple(
            InputFile(i, Path(f"/work/inputs/{i:04}.bin"), f"{i}.bin", 1, "0" * 64)
            for i in range(len(per_item))
        )
        spec = PackageSpec("downloads-detox", 1, None, {}, inputs)
        package(Report(items=tuple(items)), out, spec)
        yield out / spec.package_name, contents, order


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@PROPERTY_SETTINGS
@given(ITEMS)
def test_empacotar_duas_vezes_da_o_mesmo_sha256(per_item: Outputs) -> None:
    with packaged(per_item) as (first, _, _), packaged(per_item) as (second, _, _):
        assert sha256_of(first) == sha256_of(second)


@PROPERTY_SETTINGS
@given(ITEMS)
def test_ler_o_zip_devolve_exatamente_os_bytes_de_cada_saida(per_item: Outputs) -> None:
    with packaged(per_item) as (path, contents, _), zipfile.ZipFile(path) as archive:
        assert {name: archive.read(name) for name in contents} == contents
        assert archive.testzip() is None


@PROPERTY_SETTINGS
@given(ITEMS)
def test_ordem_e_a_do_relatorio_seguida_de_manifesto_e_relatorio(per_item: Outputs) -> None:
    with packaged(per_item) as (path, _, order), zipfile.ZipFile(path) as archive:
        assert archive.namelist() == [*order, MANIFEST_NAME, REPORT_NAME]


@PROPERTY_SETTINGS
@given(ITEMS)
def test_nenhum_nome_repete_por_casefold(per_item: Outputs) -> None:
    with packaged(per_item) as (path, _, _), zipfile.ZipFile(path) as archive:
        folded = [name.casefold() for name in archive.namelist()]
        assert len(set(folded)) == len(folded)
