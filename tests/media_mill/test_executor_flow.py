"""Media Mill pelo executor real, num subprocesso, como o worker o chama (3.6; ADR 036).

Prova o que a receita sozinha não prova: o rascunho em `work/tmp`, o grupo `audio/` no pacote e o
FLAC guardado sem nova compressão no ZIP.
"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from tests.media_mill.conftest import ClipLibrary

SUBPROCESS_TIMEOUT_SECONDS = 120


def media_work_dir(root: Path, videos: list[tuple[str, bytes]], parameters: dict[str, Any]) -> Path:
    work = root / "work"
    inputs = work / "inputs"
    inputs.mkdir(parents=True)
    entries = []
    for position, (original_name, data) in enumerate(videos):
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
        "recipe_id": "media-mill",
        "recipe_version": 1,
        "parameters": parameters,
        "inputs": entries,
    }
    (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
    return work


def run_executor(work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [sys.executable, "-m", "goblinshift.processing.executor", str(work)],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


def test_executor_publica_audio_miniaturas_relatorio_manifesto_e_zip(
    tmp_path: Path, clips: ClipLibrary
) -> None:
    parameters = {
        "audio_format": "flac",
        "audio_stream_index": None,
        "extract_audio": True,
        "thumbnail_count": 3,
    }
    work = media_work_dir(tmp_path, [("férias.mp4", clips.bytes("mp4"))], parameters)

    completed = run_executor(work)

    assert completed.returncode == 0, completed.stderr
    out = work / "out"
    report = json.loads((out / "report.json").read_bytes())
    [item] = report["items"]
    assert item["status"] == "ok"
    assert item["outputs"] == [
        "audio/férias.flac",
        "thumbnails/férias-10pct.jpg",
        "thumbnails/férias-50pct.jpg",
        "thumbnails/férias-90pct.jpg",
    ]
    assert item["details"]["audio_stream_index"] == 0
    with zipfile.ZipFile(out / "media-mill.zip") as package:
        methods = {info.filename: info.compress_type for info in package.infolist()}
    assert methods["audio/férias.flac"] == zipfile.ZIP_STORED
    assert methods["thumbnails/férias-10pct.jpg"] == zipfile.ZIP_STORED
    assert methods["report.json"] == zipfile.ZIP_DEFLATED
    assert list((work / "tmp").iterdir()) == []


def test_executor_registra_rejeicao_do_video_hostil(tmp_path: Path) -> None:
    playlist = b"#EXTM3U\n#EXTINF:1,\n/etc/passwd\n"
    work = media_work_dir(tmp_path, [("x.mp4", playlist)], {})

    completed = run_executor(work)

    assert completed.returncode == 0, completed.stderr
    report = json.loads((work / "out" / "report.json").read_bytes())
    assert [(i["status"], i["code"]) for i in report["items"]] == [
        ("rejected", "UNSUPPORTED_FORMAT")
    ]
