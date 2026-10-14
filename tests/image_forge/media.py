"""Construtores de imagens de teste, todos devolvendo `bytes` montados no próprio código.

Usam o Pillow para o que ele sabe gravar e `zlib`/`struct` para o que ele não grava (PNG de 16
bits colorido, cabeçalho gigante, bloco de texto bomba) ou para editar bytes (SOF do JPEG). Nada
é baixado nem versionado em binário; os scripts de ponta a ponta reaproveitam este módulo.
"""

import io
import struct
import zlib
from collections.abc import Iterable, Sequence

from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXIF_ORIENTATION = 0x0112
EXIF_IFD = 0x8769
EXIF_COLOR_SPACE = 0xA001
GPS_IFD = 0x8825
GPS_LATITUDE_REF = 1


def solid(mode: str, size: tuple[int, int], color: object) -> Image.Image:
    return Image.new(mode, size, color)  # type: ignore[arg-type]


def encoded(image: Image.Image, image_format: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, image_format, **options)
    return buffer.getvalue()


def jpeg(image: Image.Image, **options: object) -> bytes:
    return encoded(image, "JPEG", quality=95, **options)


def png(image: Image.Image, **options: object) -> bytes:
    return encoded(image, "PNG", **options)


def exif(
    orientation: int | None = None,
    color_space: int | None = None,
    with_gps: bool = False,
) -> bytes:
    data = Image.Exif()
    if orientation is not None:
        data[EXIF_ORIENTATION] = orientation
    if color_space is not None:
        data.get_ifd(EXIF_IFD)[EXIF_COLOR_SPACE] = color_space
    if with_gps:
        data.get_ifd(GPS_IFD)[GPS_LATITUDE_REF] = "S"
    return data.tobytes()


def mpo(frames: Sequence[Image.Image]) -> bytes:
    """Foto de celular com vários quadros: o primeiro é o principal."""
    return encoded(frames[0], "MPO", save_all=True, append_images=list(frames[1:]))


def apng(frames: Sequence[Image.Image]) -> bytes:
    return encoded(frames[0], "PNG", save_all=True, append_images=list(frames[1:]))


def chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data)
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def ihdr(width: int, height: int, bit_depth: int = 8, color_type: int = 2) -> bytes:
    return chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0))


def raw_png(header: bytes, chunks: Iterable[bytes] = ()) -> bytes:
    return PNG_SIGNATURE + header + b"".join(chunks) + chunk(b"IEND", b"")


SAMPLES_PER_PIXEL = {0: 1, 2: 3, 4: 2, 6: 4}  # cinza, RGB, cinza com alfa, RGBA


def png_16_bit(width: int, height: int, color_type: int) -> bytes:
    """PNG válido de 16 bits por amostra; o Pillow lê os coloridos, mas não os grava."""
    row = b"\0" + b"\x12\x34" * SAMPLES_PER_PIXEL[color_type] * width  # filtro 0
    idat = chunk(b"IDAT", zlib.compress(row * height))
    return raw_png(ihdr(width, height, 16, color_type), [idat])


def png_header_only(width: int, height: int, bit_depth: int = 8, color_type: int = 2) -> bytes:
    """Só o cabeçalho válido; o IDAT é lixo. Decodificar daria erro de formato, não de tamanho."""
    return raw_png(ihdr(width, height, bit_depth, color_type), [chunk(b"IDAT", b"lixo")])


def png_bomb(width: int, height: int) -> bytes:
    """Cabeçalho gigante com IDAT de zeros comprimidos (só o começo: nunca é decodificado)."""
    zeros = zlib.compress(b"\0" * (1024 * 1024), 9)
    return raw_png(ihdr(width, height), [chunk(b"IDAT", zeros)])


def with_text_bomb(png_bytes: bytes, decompressed_bytes: int) -> bytes:
    """Insere um zTXt que descomprime em `decompressed_bytes` zeros logo depois do IHDR."""
    payload = b"Comment\0\0" + zlib.compress(b"\0" * decompressed_bytes, 9)
    return insert_after_ihdr(png_bytes, chunk(b"zTXt", payload))


def insert_after_ihdr(png_bytes: bytes, extra: bytes) -> bytes:
    end_of_ihdr = len(PNG_SIGNATURE) + 8 + 13 + 4
    return png_bytes[:end_of_ihdr] + extra + png_bytes[end_of_ihdr:]


def png_chunk_types(png_bytes: bytes) -> list[bytes]:
    types = []
    offset = len(PNG_SIGNATURE)
    while offset < len(png_bytes):
        (length,) = struct.unpack_from(">I", png_bytes, offset)
        types.append(png_bytes[offset + 4 : offset + 8])
        offset += 12 + length
    return types


def jpeg_markers(jpeg_bytes: bytes) -> list[int]:
    """Marcadores até o início da imagem comprimida (SOS), na ordem."""
    markers = []
    offset = 2  # depois do SOI
    while offset < len(jpeg_bytes):
        marker = jpeg_bytes[offset + 1]
        markers.append(marker)
        if marker == 0xDA:  # SOS: daqui em diante é dado comprimido
            break
        (length,) = struct.unpack_from(">H", jpeg_bytes, offset + 2)
        offset += 2 + length
    return markers


def with_sof_size(jpeg_bytes: bytes, width: int, height: int) -> bytes:
    """Reescreve as dimensões do SOF0/SOF2 sem tocar no resto (JPEG "bomba")."""
    data = bytearray(jpeg_bytes)
    for marker in (b"\xff\xc0", b"\xff\xc2"):
        index = data.find(marker)
        if index != -1:
            struct.pack_into(">HH", data, index + 5, height, width)
            return bytes(data)
    raise ValueError("JPEG sem SOF0 nem SOF2")
