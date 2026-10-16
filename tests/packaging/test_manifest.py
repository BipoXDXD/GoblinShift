"""Esquema do manifesto (schema_version 1), congelado na etapa 0 da fatia 5 (3.6)."""

import json
from pathlib import Path
from typing import Any

import pytest

from goblinshift.processing.packaging import (
    DEFAULT_LIMITS,
    MANIFEST_SCHEMA_VERSION,
    FileDigest,
    PackageLimits,
    PackageSpec,
    encode_manifest,
)
from goblinshift.processing.recipe import (
    InputFile,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    Report,
    ReportWarning,
)

IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "e" * 64
WORK = Path("/work/inputs")
INPUTS = (
    InputFile(0, WORK / "0000.bin", "Foto.png", 1234, "a" * 64),
    InputFile(1, WORK / "0001.bin", "foto.PNG", 1234, "a" * 64),
    InputFile(2, WORK / "0002.bin", "x.html", 50, "b" * 64),
)
SPEC = PackageSpec(
    recipe_id="downloads-detox",
    recipe_version=1,
    executor_image=IMAGE,
    parameters={"name_prefix": "file", "deduplicate": True, "group_by": "kind"},
    inputs=INPUTS,
)
REPORT = Report(
    items=(
        ItemOk(
            0,
            ("images/Foto.png",),
            warnings=(ReportWarning("CSV_FORMULA_CELLS", "aviso do item"),),
            counts={"n": 1},
            details={"kind": "image"},
        ),
        ItemDuplicate(1, 0),
        ItemRejected(2, "UNSUPPORTED_FORMAT", "HTML não é aceito"),
    ),
    warnings=(ReportWarning("CSV_FORMULA_HEADER", "aviso global"),),
    details={"note": "ZIP opaco"},
)
DIGESTS = {"images/Foto.png": FileDigest(size=1234, sha256="c" * 64)}


def decoded(
    report: Report = REPORT, spec: PackageSpec = SPEC, digests: dict[str, FileDigest] = DIGESTS
) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(encode_manifest(report, spec, digests))
    return document


def test_manifesto_completo_tem_exatamente_o_esquema_1() -> None:
    assert decoded() == {
        "schema_version": 1,
        "recipe": {"id": "downloads-detox", "version": 1, "executor_image": IMAGE},
        "parameters": {"deduplicate": True, "group_by": "kind", "name_prefix": "file"},
        "inputs": [
            {
                "position": 0,
                "original_name": "Foto.png",
                "size": 1234,
                "sha256": "a" * 64,
                "status": "ok",
                "outputs": ["images/Foto.png"],
                "warnings": [{"code": "CSV_FORMULA_CELLS", "detail": "aviso do item"}],
            },
            {
                "position": 1,
                "original_name": "foto.PNG",
                "size": 1234,
                "sha256": "a" * 64,
                "status": "duplicate",
                "duplicate_of": 0,
            },
            {
                "position": 2,
                "original_name": "x.html",
                "size": 50,
                "sha256": "b" * 64,
                "status": "rejected",
                "code": "UNSUPPORTED_FORMAT",
                "reason": "HTML não é aceito",
            },
        ],
        "outputs": [
            {"path": "images/Foto.png", "size": 1234, "sha256": "c" * 64, "source_position": 0}
        ],
        "warnings": [{"code": "CSV_FORMULA_HEADER", "detail": "aviso global"}],
    }
    assert MANIFEST_SCHEMA_VERSION == 1


def test_details_e_counts_ficam_fora_do_manifesto() -> None:
    raw = encode_manifest(REPORT, SPEC, DIGESTS).decode()

    assert "details" not in raw
    assert "counts" not in raw
    assert "ZIP opaco" not in raw


def test_sem_caminho_real_identificador_de_execucao_nem_horario() -> None:
    raw = encode_manifest(REPORT, SPEC, DIGESTS).decode()

    for forbidden in ("/work", "0000.bin", "run_id", "upload_id", "created_at", "tmp"):
        assert forbidden not in raw


def test_imagem_desconhecida_na_janela_de_deploy_vira_null() -> None:
    spec = PackageSpec("csv-janitor", 1, None, {}, INPUTS[:1])
    report = Report(items=(ItemOk(0, ("a.csv",)),))

    document = decoded(report, spec, {"a.csv": FileDigest(1, "d" * 64)})

    assert document["recipe"] == {"id": "csv-janitor", "version": 1, "executor_image": None}


def test_saidas_seguem_a_ordem_dos_itens_e_depois_a_de_outputs() -> None:
    inputs = INPUTS[:2]
    spec = PackageSpec("image-forge", 1, IMAGE, {}, inputs)
    report = Report(
        items=(
            ItemOk(0, ("images/z.jpg", "thumbnails/z.jpg")),
            ItemOk(1, ("images/a.jpg", "thumbnails/a.jpg")),
        )
    )
    digests = {
        name: FileDigest(1, "f" * 64)
        for name in ("images/a.jpg", "images/z.jpg", "thumbnails/a.jpg", "thumbnails/z.jpg")
    }

    document = decoded(report, spec, digests)

    assert [(o["path"], o["source_position"]) for o in document["outputs"]] == [
        ("images/z.jpg", 0),
        ("thumbnails/z.jpg", 0),
        ("images/a.jpg", 1),
        ("thumbnails/a.jpg", 1),
    ]


def test_parametros_saem_com_chaves_ordenadas_em_todos_os_niveis() -> None:
    spec = PackageSpec(
        "csv-janitor", 1, IMAGE, {"z": [{"b": 1, "a": 2}], "a": {"y": 1, "x": 2}}, INPUTS[:1]
    )
    report = Report(items=(ItemRejected(0, "UNSUPPORTED_FORMAT", "r"),))

    raw = encode_manifest(report, spec, {}).decode()

    assert raw.index('"a": {') < raw.index('"z": [')
    assert raw.index('"x": 2') < raw.index('"y": 1')
    assert raw.index('"a": 2') < raw.index('"b": 1')


def test_mesma_entrada_da_os_mesmos_bytes_mesmo_com_parametros_em_outra_ordem() -> None:
    reordered = PackageSpec(
        SPEC.recipe_id,
        SPEC.recipe_version,
        SPEC.executor_image,
        dict(reversed(list(SPEC.parameters.items()))),
        SPEC.inputs,
    )

    assert encode_manifest(REPORT, SPEC, DIGESTS) == encode_manifest(REPORT, reordered, DIGESTS)


def test_nome_nao_ascii_sai_em_utf8_sem_escape() -> None:
    inputs = (InputFile(0, WORK / "0000.bin", "Relatório.csv", 1, "a" * 64),)
    spec = PackageSpec("csv-janitor", 1, IMAGE, {}, inputs)
    report = Report(items=(ItemOk(0, ("Relatório.csv",)),))

    raw = encode_manifest(report, spec, {"Relatório.csv": FileDigest(1, "d" * 64)})

    assert "Relatório.csv".encode() in raw
    assert raw.endswith(b"\n")


def test_saida_sem_digest_e_erro_de_programacao() -> None:
    with pytest.raises(ValueError, match=r"images/Foto\.png"):
        encode_manifest(REPORT, SPEC, {})


def test_digest_de_arquivo_que_nao_e_saida_e_erro_de_programacao() -> None:
    digests = {**DIGESTS, "solto.txt": FileDigest(1, "0" * 64)}

    with pytest.raises(ValueError, match=r"solto\.txt"):
        encode_manifest(REPORT, SPEC, digests)


def test_relatorio_que_nao_casa_com_as_entradas_e_erro_de_programacao() -> None:
    report = Report(items=(ItemRejected(0, "UNSUPPORTED_FORMAT", "r"),))

    with pytest.raises(ValueError, match="posições"):
        encode_manifest(report, SPEC, {})


def test_tetos_padrao_sao_os_de_1_7() -> None:
    assert (
        PackageLimits(manifest_max_bytes=8 * 1024 * 1024, package_max_bytes=512 * 1024 * 1024)
        == DEFAULT_LIMITS
    )


@pytest.mark.parametrize(
    ("recipe_id", "expected"),
    [
        ("downloads-detox", "downloads-detox.zip"),
        ("image-forge", "image-forge.zip"),
        ("csv-janitor", "csv-janitor.zip"),
    ],
)
def test_nome_do_pacote_e_o_id_da_receita(recipe_id: str, expected: str) -> None:
    assert PackageSpec(recipe_id, 1, None, {}, ()).package_name == expected
