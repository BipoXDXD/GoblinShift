"""Área de trabalho de uma execução do Detox: entradas em disco e o `out/` de verdade."""

import hashlib
from pathlib import Path

import pytest

from goblinshift.processing.downloads_detox import Params
from goblinshift.processing.downloads_detox.recipe import RECIPE_ID, DownloadsDetoxV1
from goblinshift.processing.output_dir import RESERVED_NAMES, FileSystemOutputDir
from goblinshift.processing.recipe import InputFile, Report

# Os nomes que o executor reserva na raiz para o Detox (etapa 0, 2.3).
DETOX_RESERVED_NAMES = (*RESERVED_NAMES, f"{RECIPE_ID}.zip")


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

    def add(self, original_name: str, data: bytes) -> InputFile:
        position = len(self.inputs)
        path = self.inputs_dir / f"{position:04}"
        path.write_bytes(data)
        entry = InputFile(
            position, path, original_name, len(data), hashlib.sha256(data).hexdigest()
        )
        self.inputs.append(entry)
        return entry

    def run(self, params: Params | None = None, recipe: DownloadsDetoxV1 | None = None) -> Report:
        out = FileSystemOutputDir(self.out_dir, reserved_names=DETOX_RESERVED_NAMES)
        return (recipe or DownloadsDetoxV1()).run(
            params or Params(), self.inputs, out, self.progress
        )

    def output_paths(self) -> list[str]:
        return sorted(
            path.relative_to(self.out_dir).as_posix()
            for path in self.out_dir.rglob("*")
            if path.is_file()
        )

    def read(self, relative: str) -> bytes:
        return (self.out_dir / relative).read_bytes()


@pytest.fixture
def work(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)
