"""Fábrica exclusiva do fuzzer local; não é copiada para a imagem de produção.

O limite normal (10 criações/minuto) esgotaria na fase de cobertura e esconderia as validações
das fases seguintes atrás de 429. Aumenta só o orçamento por minuto neste processo de teste;
o contador PostgreSQL continua ativo. Os limites reais são exercitados por test_rate_limit.py.
Não muda autenticação, CSRF, cotas de capacidade, persistência nem a spec exportada.
"""

from fastapi import FastAPI

from goblinshift.entrypoints.api.app import create_app as application
from goblinshift.identity.rate_limit import REQUESTS_PER_MINUTE
from goblinshift.platform.settings import Settings

FUZZ_REQUEST_BUDGET = 1_000_000


def create_app() -> FastAPI:
    settings = Settings()
    if (
        settings.environment != "test"
        or settings.db_host not in {"localhost", "127.0.0.1"}
        or settings.db_password_file is not None
        or settings.runner != "local"
    ):
        raise RuntimeError(
            "contract_server exige ambiente test, runner local e banco local sem arquivo"
        )
    for request_class in REQUESTS_PER_MINUTE:
        REQUESTS_PER_MINUTE[request_class] = FUZZ_REQUEST_BUDGET
    return application(settings)
