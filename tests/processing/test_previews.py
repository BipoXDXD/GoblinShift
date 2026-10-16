"""Previews: escritos depois do conteúdo, só enquanto cabem nos tetos de 1.7 (3.6)."""

from pathlib import Path

from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.previews import PendingPreview, write_previews
from goblinshift.processing.recipe import OutputRole, Preview


def pending(position: int, of: str, size: int = 3) -> PendingPreview:
    return PendingPreview(
        position=position, of=of, logical_name=f"p{position}.jpg", content=b"x" * size
    )


def test_grava_cada_preview_no_grupo_previews(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    written = write_previews(out, [pending(0, "images/a.jpg"), pending(1, "images/b.jpg")])

    assert written == {
        0: (Preview("previews/p0.jpg", "images/a.jpg"),),
        1: (Preview("previews/p1.jpg", "images/b.jpg"),),
    }
    assert (tmp_path / "previews" / "p0.jpg").read_bytes() == b"xxx"
    assert out.created == (
        ("previews/p0.jpg", OutputRole.PREVIEW),
        ("previews/p1.jpg", OutputRole.PREVIEW),
    )


def test_para_de_gravar_quando_acaba_o_espaco_sem_falhar(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path, max_files=2, max_bytes=100)

    written = write_previews(out, [pending(0, "a.csv"), pending(1, "b.csv"), pending(2, "c.csv")])

    assert list(written) == [0, 1]
    assert not (tmp_path / "previews" / "p2.jpg").exists()


def test_preview_maior_que_os_bytes_restantes_e_pulado(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path, max_bytes=10)

    written = write_previews(out, [pending(0, "a.csv", size=11), pending(1, "b.csv", size=10)])

    assert list(written) == [1]


def test_sem_previews_nao_cria_o_grupo(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    assert write_previews(out, []) == {}
    assert not (tmp_path / "previews").exists()
