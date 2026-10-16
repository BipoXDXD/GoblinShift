"""Área de trabalho dos testes da receita: vídeos sintéticos em disco e `out/` real."""

import hashlib
from pathlib import Path

import pytest

from goblinshift.processing.media_mill import Params
from goblinshift.processing.media_mill.recipe import MediaMillV1
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import InputFile, Report
from tests.media_mill.media import CLIPS, generate


class RecordedProgress:
    def __init__(self) -> None:
        self.done: list[int] = []

    def item_done(self, position: int) -> None:
        self.done.append(position)


class ClipLibrary:
    """Gera cada vídeo sintético uma vez por sessão, sob demanda."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, name: str) -> Path:
        target = self._root / f"{name}.bin"
        if not target.exists():
            generate(CLIPS[name], self._root / f"{name}{_suffix(name)}").rename(target)
        return target

    def bytes(self, name: str) -> bytes:
        return self.path(name).read_bytes()


def _suffix(name: str) -> str:
    if name.startswith("webm"):
        return ".webm"
    return ".mov" if name.startswith("mov") else ".mp4"


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
        path = self.inputs_dir / f"{position:04}.bin"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.inputs.append(InputFile(position, path, original_name, len(data), digest))

    def run(self, params: Params | None = None, recipe: MediaMillV1 | None = None) -> Report:
        return (recipe or MediaMillV1()).run(
            params or Params(), self.inputs, FileSystemOutputDir(self.out_dir), self.progress
        )

    def path(self, name: str) -> Path:
        return self.out_dir / name

    def files(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.out_dir))
            for path in self.out_dir.rglob("*")
            if path.is_file()
        )


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> ClipLibrary:
    return ClipLibrary(tmp_path_factory.mktemp("media-mill-clips"))


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)
