"""Área de trabalho de uma execução do PDF Press: entradas em disco e o `out/` de verdade."""

import hashlib
import json
from pathlib import Path

import pytest

from goblinshift.processing.output_dir import RESERVED_NAMES, FileSystemOutputDir
from goblinshift.processing.pdf_press import RECIPE_ID, Params
from goblinshift.processing.pdf_press.recipe import PdfPressV1
from goblinshift.processing.recipe import InputFile, Report

PDF_PRESS_RESERVED_NAMES = (*RESERVED_NAMES, f"{RECIPE_ID}.zip")


class RecordedProgress:
    def __init__(self) -> None:
        self.done: list[int] = []

    def item_done(self, position: int) -> None:
        self.done.append(position)


class Workspace:
    def __init__(self, root: Path) -> None:
        self.inputs_dir = root / "inputs"
        self.inputs_dir.mkdir()
        self.out_dir = root / "out"
        self.out_dir.mkdir()
        self.inputs: list[InputFile] = []
        self.progress = RecordedProgress()

    def add(self, original_name: str, data: bytes, upload_id: str | None = None) -> InputFile:
        position = len(self.inputs)
        path = self.inputs_dir / f"{position:04}.bin"
        path.write_bytes(data)
        entry = InputFile(
            position,
            path,
            original_name,
            len(data),
            hashlib.sha256(data).hexdigest(),
            upload_id=upload_id,
        )
        self.inputs.append(entry)
        return entry

    def run(self, params: Params | None = None, recipe: PdfPressV1 | None = None) -> Report:
        out = FileSystemOutputDir(self.out_dir, reserved_names=PDF_PRESS_RESERVED_NAMES)
        return (recipe or PdfPressV1()).run(params or Params(), self.inputs, out, self.progress)

    def output_paths(self) -> list[str]:
        return sorted(
            path.relative_to(self.out_dir).as_posix()
            for path in self.out_dir.rglob("*")
            if path.is_file()
        )

    def read(self, relative: str) -> bytes:
        return (self.out_dir / relative).read_bytes()

    def page_map(self) -> object:
        return json.loads(self.read("page-map.json"))


@pytest.fixture
def work(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)
