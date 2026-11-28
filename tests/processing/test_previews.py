"""Previews: escritos depois do conteúdo, só enquanto cabem nos tetos de 1.7 (3.6)."""

from pathlib import Path

import pytest

from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.previews import (
    IMAGE_PREVIEW_MAX_BYTES,
    TABLE_PREVIEW_MAX_BYTES,
    PendingPreview,
    Processed,
    process_each,
    report_with_previews,
    write_previews,
)
from goblinshift.processing.recipe import (
    InputFile,
    ItemOk,
    ItemRejected,
    ItemRejectionCode,
    OutputRole,
    Preview,
)


def pending(position: int, of: str, size: int = 3, extension: str = ".jpg") -> PendingPreview:
    return PendingPreview(
        position=position, of=of, logical_name=f"p{position}{extension}", content=b"x" * size
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


def test_jpeg_acima_do_teto_da_coleta_e_pulado_sem_falhar(tmp_path: Path) -> None:
    # A coleta do worker recusaria a execução inteira por um preview grande demais (3.6).
    out = FileSystemOutputDir(tmp_path)

    written = write_previews(
        out,
        [
            pending(0, "a.jpg", size=IMAGE_PREVIEW_MAX_BYTES + 1),
            pending(1, "b.jpg", size=IMAGE_PREVIEW_MAX_BYTES),
        ],
    )

    assert list(written) == [1]
    assert not (tmp_path / "previews" / "p0.jpg").exists()


def test_amostra_acima_do_teto_da_coleta_e_pulada(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    written = write_previews(
        out,
        [
            pending(0, "a.csv", size=TABLE_PREVIEW_MAX_BYTES + 1, extension=".csv"),
            pending(1, "b.csv", size=TABLE_PREVIEW_MAX_BYTES, extension=".csv"),
        ],
    )

    assert list(written) == [1]


class RecordedProgress:
    def __init__(self) -> None:
        self.done: list[int] = []

    def item_done(self, position: int) -> None:
        self.done.append(position)


def input_files(tmp_path: Path, count: int) -> list[InputFile]:
    return [
        InputFile(position, tmp_path / f"{position:04}", f"e{position}", 0, "0" * 64)
        for position in range(count)
    ]


def rejected(position: int) -> ItemRejected:
    return ItemRejected(position, ItemRejectionCode.UNSUPPORTED_FORMAT, "formato")


def test_process_each_processa_na_ordem_e_avisa_o_progresso_de_cada_item(tmp_path: Path) -> None:
    progress = RecordedProgress()
    seen: list[tuple[int, list[int]]] = []

    def process_one(source: InputFile, pending: list[PendingPreview]) -> ItemRejected:
        # O progresso do item anterior já foi avisado quando o seguinte começa.
        seen.append((source.position, list(progress.done)))
        pending.append(PendingPreview(source.position, f"o{source.position}", "p.jpg", b"x"))
        return rejected(source.position)

    processed = process_each(input_files(tmp_path, 3), process_one, progress)

    assert [item.position for item in processed.items] == [0, 1, 2]
    assert progress.done == [0, 1, 2]
    assert seen == [(0, []), (1, [0]), (2, [0, 1])]
    assert [preview.position for preview in processed.pending] == [0, 1, 2]


def test_erro_num_item_sobe_sem_progresso_nem_preview(tmp_path: Path) -> None:
    progress = RecordedProgress()
    boom = RuntimeError("falha no segundo item")

    def process_one(source: InputFile, pending: list[PendingPreview]) -> ItemRejected:
        if source.position == 1:
            raise boom
        pending.append(PendingPreview(source.position, "o0", "p0.jpg", b"x"))
        return rejected(source.position)

    with pytest.raises(RuntimeError) as raised:
        process_each(input_files(tmp_path, 3), process_one, progress)

    assert raised.value is boom
    assert progress.done == [0]
    assert not (tmp_path / "out" / "previews").exists()


def test_report_with_previews_anexa_por_posicao_e_guarda_os_details(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)
    processed = Processed(
        items=[ItemOk(position=0, outputs=("x.csv",)), rejected(1)],
        pending=[PendingPreview(0, "x.csv", "x.csv", b"a,b\r\n")],
    )

    report = report_with_previews(processed, out, details={"k": 1})

    first = report.items[0]
    assert isinstance(first, ItemOk)
    assert first.previews == (Preview("previews/x.csv", "x.csv"),)
    assert report.items[1] == processed.items[1]
    assert report.details == {"k": 1}
    assert out.created == (("previews/x.csv", OutputRole.PREVIEW),)


def test_report_with_previews_sem_details_usa_o_default_do_relatorio(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)
    processed = Processed(items=[rejected(0)], pending=[])

    report = report_with_previews(processed, out)

    assert report.items == [rejected(0)]
    assert report.details == {}
    assert not (tmp_path / "previews").exists()
