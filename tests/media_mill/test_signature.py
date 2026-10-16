"""Assinatura do contêiner pelos primeiros bytes, antes de qualquer chamada ao FFmpeg (2.3).

A assinatura escolhe o demuxer forçado (`-f mov` ou `-f matroska`); o que não tiver assinatura da
allowlist, inclusive texto disfarçado de `.mp4` (playlist HLS, lista do `concat`), nunca chega ao
FFmpeg.
"""

import pytest

from goblinshift.processing.media_mill.rejection import MediaRejected, RejectionCode
from goblinshift.processing.media_mill.signature import Container, detect_container

EBML_MAGIC = b"\x1a\x45\xdf\xa3"


def iso_box(kind: bytes) -> bytes:
    return b"\x00\x00\x00\x20" + kind + b"isom\x00\x00\x02\x00isomiso2avc1mp41"


def ebml_header(doc_type: bytes) -> bytes:
    return EBML_MAGIC + b"\xa3\x42\x86\x81\x01\x42\x82" + bytes([0x80 | len(doc_type)]) + doc_type


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(iso_box(b"ftyp"), id="mp4-ftyp"),
        pytest.param(iso_box(b"moov"), id="quicktime-moov"),
        pytest.param(iso_box(b"wide"), id="quicktime-wide"),
        pytest.param(iso_box(b"mdat"), id="quicktime-mdat"),
        pytest.param(iso_box(b"free"), id="quicktime-free"),
        pytest.param(iso_box(b"skip"), id="quicktime-skip"),
    ],
)
def test_caixa_iso_no_inicio_e_mov(header: bytes) -> None:
    assert detect_container(header) is Container.MOV


def test_ebml_com_doctype_webm_e_webm() -> None:
    assert detect_container(ebml_header(b"webm")) is Container.WEBM


def test_demuxer_forcado_por_contêiner() -> None:
    assert (Container.MOV.demuxer, Container.WEBM.demuxer) == ("mov", "matroska")


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:1,\n/etc/passwd\n", id="hls"),
        pytest.param(b"ffconcat version 1.0\nfile '/etc/passwd'\n", id="ffconcat"),
        pytest.param(b"file '/etc/passwd'\n", id="concat-sem-cabecalho"),
        pytest.param(ebml_header(b"matroska"), id="mkv-nao-webm"),
        pytest.param(EBML_MAGIC + b"\x00" * 60, id="ebml-sem-doctype"),
        pytest.param(b"\x00\x00\x00\x20" + b"abcd" + b"\x00" * 24, id="caixa-desconhecida"),
        pytest.param(b"\xff\xd8\xff\xe0" + b"\x00" * 28, id="jpeg"),
        pytest.param(b"RIFF\x00\x00\x00\x00AVI LIST", id="avi"),
        pytest.param(b"ftyp", id="curto-demais"),
        pytest.param(b"", id="vazio"),
    ],
)
def test_sem_assinatura_da_allowlist_e_recusado(header: bytes) -> None:
    with pytest.raises(MediaRejected) as rejected:
        detect_container(header)

    assert rejected.value.code is RejectionCode.UNSUPPORTED_FORMAT
