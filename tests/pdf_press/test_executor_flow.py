"""PDF Press pelo executor inteiro: job.json, context.json, out/, pacote e failure.json (3.6)."""

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from goblinshift.catalog import CATALOG
from goblinshift.processing.executor.execution import ExitCode, main
from tests.pdf_press import samples

UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "e" * 64


def make_work_dir(
    root: Path,
    batch: list[tuple[str, bytes]],
    parameters: dict[str, Any],
    upload_ids: list[str] | None = None,
) -> Path:
    work = root / "work"
    inputs = work / "inputs"
    inputs.mkdir(parents=True)
    entries = []
    for position, (original_name, data) in enumerate(batch):
        name = f"{position:04}.bin"
        (inputs / name).write_bytes(data)
        entries.append(
            {
                "position": position,
                "path": name,
                "original_name": original_name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    job = {
        "recipe_id": "pdf-press",
        "recipe_version": 1,
        "parameters": CATALOG.resolve("pdf-press", 1, parameters),
        "inputs": entries,
    }
    (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
    context: dict[str, object] = {"executor_image": IMAGE}
    if upload_ids is not None:
        context["input_upload_ids"] = upload_ids
    (work / "context.json").write_text(json.dumps(context), encoding="utf-8")
    return work


def test_select_pelo_executor_publica_pdf_mapa_manifesto_e_zip(tmp_path: Path) -> None:
    batch = [("a.pdf", samples.document("a", pages=2)), ("b.pdf", samples.document("b"))]
    parameters = {
        "operation": "select",
        "select": [
            {"upload_id": UPLOAD_B, "page_number": 1},
            {"upload_id": UPLOAD_A, "page_number": 2},
        ],
    }
    work = make_work_dir(tmp_path, batch, parameters, [UPLOAD_A, UPLOAD_B])

    assert main([str(work)]) == ExitCode.SUCCEEDED

    out = work / "out"
    assert samples.markers((out / "selection.pdf").read_bytes()) == ["b:1", "a:2"]
    with zipfile.ZipFile(out / "pdf-press.zip") as archive:
        assert archive.namelist() == [
            "page-map.json",
            "selection.pdf",
            "manifest.json",
            "report.json",
        ]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [(o["path"], o["source_position"]) for o in manifest["outputs"]] == [
        ("page-map.json", 0),
        ("selection.pdf", 1),
    ]
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["summary"] == {
        "inputs": 2,
        "ok": 2,
        "rejected": 0,
        "duplicates": 0,
        "outputs": 2,
    }


def test_pdf_malicioso_falha_deterministicamente_com_o_codigo(tmp_path: Path) -> None:
    batch = [("a.pdf", samples.document("a", mutate=samples.javascript_name_tree))]
    work = make_work_dir(tmp_path, batch, {})

    assert main([str(work)]) == ExitCode.RECIPE_FAILURE

    failure = json.loads((work / "failure.json").read_text(encoding="utf-8"))
    assert failure["code"] == "PDF_UNSUPPORTED_FEATURE"
    assert not (work / "out" / "report.json").exists()


def test_select_sem_os_uploads_no_contexto_e_infraestrutura(tmp_path: Path) -> None:
    batch = [("a.pdf", samples.document("a"))]
    parameters = {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": 1}]}
    work = make_work_dir(tmp_path, batch, parameters, upload_ids=None)

    assert main([str(work)]) == ExitCode.INFRASTRUCTURE
    assert not (work / "failure.json").exists()


def test_uploads_do_contexto_em_numero_errado_e_infraestrutura(tmp_path: Path) -> None:
    batch = [("a.pdf", samples.document("a"))]
    work = make_work_dir(tmp_path, batch, {}, [UPLOAD_A, UPLOAD_B])

    assert main([str(work)]) == ExitCode.INFRASTRUCTURE
