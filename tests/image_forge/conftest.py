"""Área de trabalho dos testes da receita: entradas em disco e `out/` real."""

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from goblinshift.processing.image_forge import Params
from goblinshift.processing.image_forge.recipe import ImageForgeV1
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import InputFile, ItemOk, Report


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

    def add(self, original_name: str, data: bytes) -> None:
        position = len(self.inputs)
        path = self.inputs_dir / f"{position:04}"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.inputs.append(InputFile(position, path, original_name, len(data), digest))

    def run(self, params: Params | None = None, recipe: ImageForgeV1 | None = None) -> Report:
        return (recipe or ImageForgeV1()).run(
            params or Params(), self.inputs, FileSystemOutputDir(self.out_dir), self.progress
        )

    def output(self, name: str) -> bytes:
        return (self.out_dir / name).read_bytes()

    def open_output(self, name: str) -> Image.Image:
        image = Image.open(self.out_dir / name)
        image.load()
        return image

    def files(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.out_dir))
            for path in self.out_dir.rglob("*")
            if path.is_file()
        )


@pytest.fixture
def work(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


def only_ok(report: Report) -> ItemOk:
    (item,) = report.items
    assert isinstance(item, ItemOk), item
    return item
