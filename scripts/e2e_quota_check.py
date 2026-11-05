"""Decide, a partir do `GET /me`, se a conta de teste comporta mais uma rodada da jornada.

Uso: api GET /me | python3 -I scripts/e2e_quota_check.py

A rodada de produção (scripts/e2e-pdf-press.sh e e2e-media-mill.sh) cria quatro execuções, e a
cota é de 30 a cada 24 h corridas (1.7): num dia de oito releases, ela acabaria no meio. Sem folga,
a rodada é pulada em vez de falhar, porque cota gasta pelas rodadas anteriores não é defeito do
serviço. Quem chama não pinga nada nesse caso, e o healthchecks.io avisa se isso se prolongar.

Chave sem escopo ou conta com termos pendentes são erro: nenhuma rodada futura passaria.

Roda no Python do host, só com a biblioteca padrão e com `-I`. Imprime `fits=true` ou `fits=false`
no stdout, no formato do $GITHUB_OUTPUT. Sai com 1 e nada no stdout se a conta não serve.
"""

import json
import sys
from typing import Any

REQUIRED_SCOPES = frozenset({"files:read", "files:write", "runs:read", "runs:write"})
JOURNEY_RUNS = 4
# Teto folgado para entradas e saídas das quatro execuções: as entradas somam cerca de 1,6 MiB.
JOURNEY_BYTES = 8 * 1024 * 1024


def shortfall(me: dict[str, Any]) -> str | None:
    """Por que a rodada não cabe agora, ou None se cabe."""
    limits, usage = me["limits"], me["usage"]
    if usage["active_run_id"] is not None:
        return f"execução ativa na conta ({usage['active_run_id']})"
    if usage["runs_last_24h"] + JOURNEY_RUNS > limits["runs_per_24h"]:
        return f"{usage['runs_last_24h']} de {limits['runs_per_24h']} execuções em 24 h"
    if usage["input_bytes_last_24h"] + JOURNEY_BYTES > limits["input_bytes_per_24h"]:
        return f"{usage['input_bytes_last_24h']} bytes de entrada em 24 h"
    if usage["storage_reserved_bytes"] + JOURNEY_BYTES > limits["storage_bytes"]:
        return f"{usage['storage_reserved_bytes']} bytes de armazenamento reservados"
    return None


def account_problem(me: dict[str, Any]) -> str | None:
    """O que impede qualquer rodada nesta conta, ou None."""
    missing = sorted(REQUIRED_SCOPES - set(me["scopes"]))
    if missing:
        return f"a chave não tem os escopos {missing}"
    if me["terms"]["acceptance_required"]:
        return "a conta de teste precisa aceitar os termos vigentes"
    return None


def main() -> int:
    me = json.load(sys.stdin)
    problem = account_problem(me)
    if problem is not None:
        print(f"::error title=Conta de teste::{problem}", file=sys.stderr)
        return 1
    reason = shortfall(me)
    if reason is not None:
        print(f"::notice title=Rodada pulada::{reason}", file=sys.stderr)
    print(f"fits={'true' if reason is None else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
