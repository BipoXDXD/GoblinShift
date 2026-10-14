"""Perfis ICC v4 de matriz e curva montados à mão, para as fixtures de cor.

Calculados a partir das primárias publicadas (sRGB: IEC 61966-2-1; Display P3: SMPTE EG 432-1
com o branco D65 e a curva do sRGB, como a Apple define; Adobe RGB (1998): especificação da
Adobe), com adaptação de Bradford de D65 para o D50 da PCS (ICC.1:2022, anexo E). Este módulo é
escrito sem olhar `color.py`, que reconhece perfis por constantes: o teste não reaproveita a
lógica testada. Nada é baixado nem versionado em binário.
"""

import struct
from collections.abc import Sequence
from dataclasses import dataclass

type Vector = tuple[float, float, float]
type Matrix = tuple[Vector, Vector, Vector]

D65_XY = (0.3127, 0.3290)
D50_XYZ: Vector = (0.9642, 1.0, 0.8249)
BRADFORD: Matrix = (
    (0.8951, 0.2664, -0.1614),
    (-0.7502, 1.7135, 0.0367),
    (0.0389, -0.0685, 1.0296),
)
HEADER_BYTES = 128
TAG_ENTRY_BYTES = 12


@dataclass(frozen=True)
class Curve:
    """`parametricCurveType`: função 0 (só gama) ou 3 (curva do sRGB)."""

    function: int
    parameters: tuple[float, ...]


SRGB_CURVE = Curve(3, (2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045))
ADOBE_RGB_CURVE = Curve(0, (563 / 256,))


@dataclass(frozen=True)
class RgbSpace:
    primaries: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    curve: Curve


SRGB = RgbSpace(((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), SRGB_CURVE)
DISPLAY_P3 = RgbSpace(((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)), SRGB_CURVE)
ADOBE_RGB = RgbSpace(((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)), ADOBE_RGB_CURVE)


def rgb_profile(space: RgbSpace, description: str) -> bytes:
    """Perfil de monitor RGB v4.3 com `desc`, `cprt`, `wtpt`, `chad`, colorantes e curvas."""
    chad = _adaptation_to_d50(_xy_to_xyz(D65_XY))
    colorants = _matmul(chad, _rgb_to_xyz(space.primaries, D65_XY))
    curve = _para(space.curve)
    tags: list[tuple[bytes, bytes]] = [
        (b"desc", _mluc(description)),
        (b"cprt", _mluc("Sem direitos reservados: perfil sintético de teste")),
        (b"wtpt", _xyz_type(D50_XYZ)),
        (b"chad", _sf32(chad)),
        (b"rXYZ", _xyz_type(_column(colorants, 0))),
        (b"gXYZ", _xyz_type(_column(colorants, 1))),
        (b"bXYZ", _xyz_type(_column(colorants, 2))),
        (b"rTRC", curve),
        (b"gTRC", curve),
        (b"bTRC", curve),
    ]
    return _assemble(tags)


def _assemble(tags: Sequence[tuple[bytes, bytes]]) -> bytes:
    table_end = HEADER_BYTES + 4 + TAG_ENTRY_BYTES * len(tags)
    entries = bytearray(struct.pack(">I", len(tags)))
    data = bytearray()
    for signature, payload in tags:
        offset = table_end + len(data)
        entries += signature + struct.pack(">II", offset, len(payload))
        data += payload + b"\0" * (-len(payload) % 4)
    body = bytes(entries) + bytes(data)
    return _header(HEADER_BYTES + len(body)) + body


def _header(size: int) -> bytes:
    header = (
        struct.pack(">I", size)
        + b"\0\0\0\0"  # CMM preferido: nenhum
        + struct.pack(">I", 0x04300000)  # versão 4.3
        + b"mntr"
        + b"RGB "
        + b"XYZ "
        + struct.pack(">6H", 2026, 1, 1, 0, 0, 0)
        + b"acsp"
        + b"\0" * 4  # plataforma
        + b"\0" * 4  # flags
        + b"\0" * 8  # fabricante e modelo
        + b"\0" * 8  # atributos
        + struct.pack(">I", 1)  # intenção: colorimétrica relativa
        + _xyz_number(D50_XYZ)
        + b"\0" * 4  # criador
        + b"\0" * 16  # profile ID zerado: "não calculado", permitido pela especificação
    )
    return header + b"\0" * (HEADER_BYTES - len(header))


def _mluc(text: str) -> bytes:
    encoded = text.encode("utf-16-be")
    record_offset = 28
    return (
        b"mluc"
        + b"\0" * 4
        + struct.pack(">II", 1, 12)
        + b"enUS"
        + struct.pack(">II", len(encoded), record_offset)
        + encoded
    )


def _xyz_type(xyz: Vector) -> bytes:
    return b"XYZ " + b"\0" * 4 + _xyz_number(xyz)


def _sf32(matrix: Matrix) -> bytes:
    return b"sf32" + b"\0" * 4 + b"".join(_s15f16(value) for row in matrix for value in row)


def _para(curve: Curve) -> bytes:
    return (
        b"para"
        + b"\0" * 4
        + struct.pack(">HH", curve.function, 0)
        + b"".join(_s15f16(value) for value in curve.parameters)
    )


def _xyz_number(xyz: Vector) -> bytes:
    return b"".join(_s15f16(value) for value in xyz)


def _s15f16(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xy_to_xyz(xy: tuple[float, float]) -> Vector:
    x, y = xy
    return (x / y, 1.0, (1 - x - y) / y)


def _rgb_to_xyz(
    primaries: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    white: tuple[float, float],
) -> Matrix:
    columns = [_xy_to_xyz(primary) for primary in primaries]
    unscaled = _rows(columns)
    scale = _apply(_inverse(unscaled), _xy_to_xyz(white))
    return _rows([tuple(v * s for v in column) for column, s in zip(columns, scale, strict=True)])


def _adaptation_to_d50(source_white: Vector) -> Matrix:
    source = _apply(BRADFORD, source_white)
    target = _apply(BRADFORD, D50_XYZ)
    diagonal: Matrix = (
        (target[0] / source[0], 0.0, 0.0),
        (0.0, target[1] / source[1], 0.0),
        (0.0, 0.0, target[2] / source[2]),
    )
    return _matmul(_inverse(BRADFORD), _matmul(diagonal, BRADFORD))


def _rows(columns: Sequence[Sequence[float]]) -> Matrix:
    return (
        (columns[0][0], columns[1][0], columns[2][0]),
        (columns[0][1], columns[1][1], columns[2][1]),
        (columns[0][2], columns[1][2], columns[2][2]),
    )


def _column(matrix: Matrix, index: int) -> Vector:
    return (matrix[0][index], matrix[1][index], matrix[2][index])


def _apply(matrix: Matrix, vector: Vector) -> Vector:
    return (
        sum(matrix[0][k] * vector[k] for k in range(3)),
        sum(matrix[1][k] * vector[k] for k in range(3)),
        sum(matrix[2][k] * vector[k] for k in range(3)),
    )


def _matmul(left: Matrix, right: Matrix) -> Matrix:
    return _rows([_apply(left, _column(right, c)) for c in range(3)])


def _inverse(m: Matrix) -> Matrix:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return (
        ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det),
    )
