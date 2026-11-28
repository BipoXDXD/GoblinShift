"""Recompressão Flate sem perdas, com tetos de bytes decodificados (ADR 070)."""

import zlib

import pikepdf
from pikepdf import Array, Dictionary, Name

from goblinshift.processing.pdf_press.compression import FlateRecompressor, RecompressionStats

KIB = 1024
MIB = 1024 * KIB
GENEROUS = 64 * MIB
REPETITIVE = b"GoblinShift recomprime sem perdas. " * 6000  # ~200 KiB de texto repetitivo


def stream(
    pdf: pikepdf.Pdf,
    raw: bytes,
    filters: Name | Array | None = None,
    parms: Dictionary | None = None,
) -> pikepdf.Object:
    """Stream (sempre indireto no qpdf) com `raw` já codificado por `filters`."""
    created = pdf.make_stream(b"")
    created.write(raw, filter=filters, decode_parms=parms)
    pdf.Root[f"/S{len(pdf.Root.keys())}"] = created
    return created


def test_flate_ruim_e_recomprimido_menor_e_com_o_mesmo_conteudo() -> None:
    pdf = pikepdf.new()
    raw = zlib.compress(REPETITIVE, 1)
    target = stream(pdf, raw, Name.FlateDecode)

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert stats == RecompressionStats(recompressed=1, kept=0)
    assert len(target.read_raw_bytes()) < len(raw)
    assert target.read_bytes() == REPETITIVE
    assert target.Filter == Name.FlateDecode


def test_filtro_em_lista_de_um_flate_tambem_e_recomprimido() -> None:
    pdf = pikepdf.new()
    target = stream(pdf, zlib.compress(REPETITIVE, 0), Array([Name.FlateDecode]))

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert stats.recompressed == 1
    assert target.read_bytes() == REPETITIVE


def test_stream_acima_do_teto_fica_como_esta() -> None:
    pdf = pikepdf.new()
    raw = zlib.compress(bytes(2 * MIB), 1)
    target = stream(pdf, raw, Name.FlateDecode)

    stats = FlateRecompressor(
        max_stream_decoded_bytes=MIB, max_run_decoded_bytes=GENEROUS
    ).recompress(pdf)

    assert target.read_raw_bytes() == raw
    assert stats == RecompressionStats(recompressed=0, kept=1)


def test_orcamento_da_execucao_esgotado_deixa_os_seguintes_sem_decodificar() -> None:
    pdf = pikepdf.new()
    raws = [zlib.compress(bytes([n]) * 600 * KIB, 0) for n in range(3)]
    targets = [stream(pdf, raw, Name.FlateDecode) for raw in raws]

    stats = FlateRecompressor(GENEROUS, max_run_decoded_bytes=MIB).recompress(pdf)

    assert stats == RecompressionStats(recompressed=1, kept=2)
    assert sum(t.read_raw_bytes() == raw for t, raw in zip(targets, raws, strict=True)) == 2


def test_orcamento_vale_para_a_execucao_inteira() -> None:
    # Uma instância por execução: o segundo documento herda o que sobrou do primeiro.
    recompressor = FlateRecompressor(GENEROUS, max_run_decoded_bytes=MIB)
    first, second = pikepdf.new(), pikepdf.new()
    stream(first, zlib.compress(bytes(600 * KIB), 0), Name.FlateDecode)
    raw = zlib.compress(bytes(600 * KIB), 0)
    stream(second, raw, Name.FlateDecode)

    assert recompressor.recompress(first) == RecompressionStats(recompressed=1, kept=0)
    assert recompressor.recompress(second) == RecompressionStats(recompressed=0, kept=1)


def test_filtro_encadeado_ou_lzw_nao_e_decodificado() -> None:
    pdf = pikepdf.new()
    chained_raw = zlib.compress(REPETITIVE, 1).hex().encode() + b">"
    chained = stream(pdf, chained_raw, Array([Name.ASCIIHexDecode, Name.FlateDecode]))
    lzw_raw = b"\x80\x0b\x60\x50\x22\x0c\x0c\x85\x01"  # LZW de "-----A---B"
    lzw = stream(pdf, lzw_raw, Name.LZWDecode)

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert chained.read_raw_bytes() == chained_raw
    assert lzw.read_raw_bytes() == lzw_raw
    assert stats == RecompressionStats(recompressed=0, kept=2)


def test_stream_sem_filtro_nao_entra_na_conta() -> None:
    pdf = pikepdf.new()
    stream(pdf, REPETITIVE)

    assert FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf) == RecompressionStats(0, 0)


def test_flate_corrompido_fica_como_esta() -> None:
    pdf = pikepdf.new()
    raw = b"isto nao e zlib" * 10
    target = stream(pdf, raw, Name.FlateDecode)

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert target.read_raw_bytes() == raw
    assert stats == RecompressionStats(recompressed=0, kept=1)


def test_flate_truncado_fica_como_esta() -> None:
    pdf = pikepdf.new()
    raw = zlib.compress(REPETITIVE, 0)[:-100]
    target = stream(pdf, raw, Name.FlateDecode)

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert target.read_raw_bytes() == raw
    assert stats.kept == 1


def test_flate_que_nao_fica_menor_fica_como_esta() -> None:
    pdf = pikepdf.new()
    raw = zlib.compress(REPETITIVE, 9)
    target = stream(pdf, raw, Name.FlateDecode)

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert target.read_raw_bytes() == raw
    assert stats == RecompressionStats(recompressed=0, kept=1)


def test_preditor_png_e_preservado() -> None:
    pdf = pikepdf.new()
    # Linhas de 4 bytes com o tipo de filtro PNG 0 (None) na frente: o preditor 12 é o "Up".
    rows = b"".join(b"\x00" + bytes([n % 256]) * 4 for n in range(20_000))
    parms = Dictionary(Predictor=12, Columns=4)
    target = stream(pdf, zlib.compress(rows, 0), Name.FlateDecode, parms)
    decoded = target.read_bytes()

    stats = FlateRecompressor(GENEROUS, GENEROUS).recompress(pdf)

    assert stats.recompressed == 1
    assert target.DecodeParms.Predictor == 12
    assert target.DecodeParms.Columns == 4
    assert target.read_bytes() == decoded
