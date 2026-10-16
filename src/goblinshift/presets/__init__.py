"""Presets privados com controle de revisão (supporting).

Ver docs/GoblinShiftPlan.md, seções 1.6, 1.7 e 3.3.
"""

from goblinshift.presets.drafts import (
    MAX_NAME_CHARS,
    InvalidPresetError,
    PresetDraft,
    parse_draft,
)
from goblinshift.presets.store import (
    MAX_PRESETS_PER_ACCOUNT,
    Preset,
    PresetLimitReachedError,
    PresetNameTakenError,
    PresetNotFoundError,
    StaleRevisionError,
    create_preset,
    delete_preset,
    get_preset,
    hold_for_run,
    list_presets,
    replace_preset,
)

__all__ = [
    "MAX_NAME_CHARS",
    "MAX_PRESETS_PER_ACCOUNT",
    "InvalidPresetError",
    "Preset",
    "PresetDraft",
    "PresetLimitReachedError",
    "PresetNameTakenError",
    "PresetNotFoundError",
    "StaleRevisionError",
    "create_preset",
    "delete_preset",
    "get_preset",
    "hold_for_run",
    "list_presets",
    "parse_draft",
    "replace_preset",
]
