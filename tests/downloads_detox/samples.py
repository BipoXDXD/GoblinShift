"""Construtores de bytes para as fixtures do Downloads Detox, só com a biblioteca padrão.

Nada é versionado em binário (fatia 5, 5.4): cada amostra é montada aqui, com conteúdo
determinístico. `variant` muda os bytes sem mudar o tipo, para os testes de duplicata.
"""

import io
import struct
import zipfile
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
ZIP_LOCAL_HEADER = b"PK\x03\x04"
ZIP_END_OF_CENTRAL_DIRECTORY = b"PK\x05\x06"
JAVA_CLASS_MAGIC = b"\xca\xfe\xba\xbe"
PDF_SIGNATURE = b"%PDF-"
GIF87A = b"GIF87a"
GIF89A = b"GIF89a"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Diretório central vazio: assinatura do fim (4 bytes) e 18 bytes de campos zerados.
EMPTY_ZIP = ZIP_END_OF_CENTRAL_DIRECTORY + bytes(18)
PE_EXECUTABLE = b"MZ\x90\x00" + bytes(60)
ELF_EXECUTABLE = b"\x7fELF\x02\x01\x01" + bytes(9)
MACHO_32 = b"\xfe\xed\xfa\xce" + bytes(24)
MACHO_64 = b"\xfe\xed\xfa\xcf" + bytes(28)
MACHO_32_REVERSED = b"\xce\xfa\xed\xfe" + bytes(24)
MACHO_64_REVERSED = b"\xcf\xfa\xed\xfe" + bytes(28)
MACHO_FAT = JAVA_CLASS_MAGIC + bytes(4)
SHELL_SCRIPT = b"#!/bin/sh\necho oi\n"


def jpeg(variant: int = 0) -> bytes:
    """Cabeçalho JFIF mínimo, um comentário com `variant` e o fim da imagem."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    comment = f"amostra {variant}".encode()
    com = b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment
    return JPEG_SIGNATURE[:2] + app0 + com + b"\xff\xd9"


def png(variant: int = 0) -> bytes:
    """PNG válido de 1 por 1 pixel em tons de cinza; `variant` é o valor do pixel."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    pixels = zlib.compress(bytes([0, variant % 256]))
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", pixels) + _chunk(b"IEND", b"")


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data)
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def zip_archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(zipfile.ZipInfo(name, _ZIP_EPOCH), data)
    return buffer.getvalue()


def jar() -> bytes:
    """ZIP que é um JAR executável: manifesto com `Main-Class` e uma classe Java."""
    manifest = b"Manifest-Version: 1.0\r\nMain-Class: Ferramenta\r\n\r\n"
    java_class = JAVA_CLASS_MAGIC + b"\x00\x00\x00\x41" + bytes(16)
    return zip_archive({"META-INF/MANIFEST.MF": manifest, "Ferramenta.class": java_class})


def docx() -> bytes:
    """ZIP com a estrutura mínima de um documento do Word."""
    return zip_archive(
        {
            "[Content_Types].xml": b'<?xml version="1.0"?><Types/>',
            "word/document.xml": b'<?xml version="1.0"?><w:document/>',
        }
    )


def text(content: str = "linha\n") -> bytes:
    return content.encode()


def pdf(variant: int = 0) -> bytes:
    """Começo e fim de um PDF; o Detox só lê a assinatura, então o corpo não precisa ser válido."""
    return (
        PDF_SIGNATURE + b"1.7\n%\xe2\xe3\xcf\xd3\n" + f"% amostra {variant}\n".encode() + b"%%EOF\n"
    )


def webp(variant: int = 0) -> bytes:
    """Contêiner RIFF com a forma WEBP e um bloco VP8L curto."""
    body = b"WEBP" + b"VP8L" + struct.pack("<I", 5) + bytes([0x2F, 0, 0, 0, variant % 256]) + b"\0"
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif(signature: bytes = GIF89A, variant: int = 0) -> bytes:
    """GIF de 1 por 1 pixel; `signature` escolhe entre GIF87a e GIF89a."""
    screen = struct.pack("<HHBBB", 1, 1, 0x80, 0, 0) + bytes([0, 0, 0, variant % 256, 0, 0])
    image = b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00"
    return signature + screen + image + b"\x3b"
