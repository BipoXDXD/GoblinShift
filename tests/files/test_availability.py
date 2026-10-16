"""Disponibilidade derivada (1.7): a ordem entre exclusão, expiração e objeto ausente."""

from datetime import UTC, datetime, timedelta

import pytest

from goblinshift.files.states import Availability

EXPIRES = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("deleted_at", "expired", "object_missing", "expected"),
    [
        (None, False, False, Availability.AVAILABLE),
        (None, False, True, Availability.MISSING),
        (None, True, True, Availability.EXPIRED),
        (EXPIRES, False, True, Availability.EXPIRED),
        (EXPIRES - timedelta(seconds=1), False, True, Availability.DELETED),
        (EXPIRES - timedelta(seconds=1), True, False, Availability.DELETED),
    ],
    ids=[
        "disponivel",
        "objeto-ausente",
        "vencido-vence-o-ausente",
        "expirado-pela-retencao-vence-o-ausente",
        "excluido-pelo-dono-vence-o-ausente",
        "excluido-pelo-dono-vence-o-vencido",
    ],
)
def test_disponibilidade_segue_a_causa_mais_explicavel(
    deleted_at: datetime | None, expired: bool, object_missing: bool, expected: Availability
) -> None:
    availability = Availability.of(
        deleted_at=deleted_at, expires_at=EXPIRES, expired=expired, object_missing=object_missing
    )

    assert availability is expected
