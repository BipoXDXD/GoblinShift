"""A CI só cancela o run em andamento quando ele é de pull request.

No push da `main`, dois merges seguidos não podem interromper o primeiro run no meio da
publicação das imagens e da tag (`image`, `web-image`, `executor-image`, `tag-main`).
"""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _ci() -> dict[Any, Any]:
    loaded = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_ci_so_cancela_run_em_andamento_de_pull_request() -> None:
    assert _ci()["concurrency"] == {
        "group": "ci-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }
