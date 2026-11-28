"""Parâmetros do downloads-detox v2 como modelo Pydantic estrito (docs/GoblinShiftPlan.md, 1.4).

A v2 muda só a semântica: aceita também PDF, WebP e GIF, reconhecidos pela assinatura. Os
parâmetros, os validadores e os defaults são os da v1; o modelo herda o da v1 e troca só o
título, para o contrato distinguir as variantes. O schema e os defaults de cada versão ficam
congelados em `tests/catalog/congeladas/` (ADR 067).
"""

from pydantic import ConfigDict

from goblinshift.catalog.downloads_detox_v1 import DownloadsDetoxV1Parameters


class DownloadsDetoxV2Parameters(DownloadsDetoxV1Parameters):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, title="DownloadsDetoxV2Parameters"
    )
