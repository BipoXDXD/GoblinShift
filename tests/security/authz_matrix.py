"""Matriz de autorização: toda rota da API, com a política de acesso de cada uma (1.6 e 3.6).

Negar por padrão: toda rota exige credencial, salvo as marcadas `PUBLIC`, que formam a allowlist
explícita. `test_authz_matrix.py` enumera as rotas registradas no app e exige igualdade com as
entradas: rota nova sem entrada reprova a suíte, e entrada sem rota também.
"""

from dataclasses import dataclass
from enum import Enum, auto

from goblinshift.identity import Scope


class Policy(Enum):
    # Allowlist: responde sem credencial.
    PUBLIC = auto()
    # Sem credencial, mas a mutação exige o par CSRF (cookie e header) da própria origem.
    CSRF_ONLY = auto()
    # Sessão ou qualquer chave válida, sem escopo específico.
    ANY_CREDENTIAL = auto()
    # Sessão (que tem todos os escopos) ou chave com o escopo da regra.
    SCOPE = auto()
    # Só sessão web; chave → 403 FORBIDDEN.
    SESSION = auto()
    # Sessão com login no provedor há no máximo 10 min; antiga → 401 REAUTH_REQUIRED.
    RECENT_SESSION = auto()


@dataclass(frozen=True)
class Rule:
    policy: Policy
    scope: Scope | None = None
    # Parâmetro do caminho que identifica um recurso com dono: outra conta recebe 404.
    resource_param: str | None = None

    def __post_init__(self) -> None:
        if (self.policy is Policy.SCOPE) != (self.scope is not None):
            raise ValueError("escopo existe só, e sempre, na política SCOPE")


def _scope(scope: Scope, resource_param: str | None = None) -> Rule:
    return Rule(Policy.SCOPE, scope=scope, resource_param=resource_param)


API = "/api/v1"

MATRIX: dict[tuple[str, str], Rule] = {
    # Infraestrutura e documentação (Swagger UI servido da própria origem: test_api_docs.py).
    ("GET", "/livez"): Rule(Policy.PUBLIC),
    ("GET", "/readyz"): Rule(Policy.PUBLIC),
    ("GET", "/docs"): Rule(Policy.PUBLIC),
    ("GET", "/docs/assets/{name}"): Rule(Policy.PUBLIC),
    ("GET", "/openapi.json"): Rule(Policy.PUBLIC),
    # Login (Frente A): o estado do login protege o callback, não uma credencial.
    ("GET", "/auth/login/{provider}"): Rule(Policy.PUBLIC),
    ("GET", "/auth/callback/{provider}"): Rule(Policy.PUBLIC),
    # Sem sessão válida também responde 204, o estado final é o mesmo (Frente B), mas só com o
    # par CSRF: outro site não manda o navegador apagar os dados da origem.
    ("POST", "/auth/logout"): Rule(Policy.CSRF_ONLY),
    ("POST", "/auth/logout-all"): Rule(Policy.SESSION),
    # Conta.
    ("GET", f"{API}/me"): Rule(Policy.ANY_CREDENTIAL),
    ("DELETE", f"{API}/me"): Rule(Policy.RECENT_SESSION),
    # O aceite dos termos é um ato da pessoa no navegador (ADR 029).
    ("POST", f"{API}/me/terms:accept"): Rule(Policy.SESSION),
    # Chaves pela sessão.
    ("GET", f"{API}/api-keys"): Rule(Policy.SESSION),
    ("POST", f"{API}/api-keys"): Rule(Policy.RECENT_SESSION),
    ("DELETE", f"{API}/api-keys/{{key_id}}"): Rule(Policy.SESSION, resource_param="key_id"),
    # Catálogo de receitas.
    ("GET", f"{API}/catalog"): Rule(Policy.ANY_CREDENTIAL),
    ("GET", f"{API}/catalog/{{recipe_id}}/versions/{{version}}"): Rule(Policy.ANY_CREDENTIAL),
    # Arquivos.
    ("POST", f"{API}/uploads"): _scope(Scope.FILES_WRITE),
    ("PUT", f"{API}/uploads/{{upload_id}}/content"): _scope(Scope.FILES_WRITE, "upload_id"),
    ("GET", f"{API}/uploads/{{upload_id}}"): _scope(Scope.FILES_READ, "upload_id"),
    ("DELETE", f"{API}/uploads/{{upload_id}}"): _scope(Scope.FILES_WRITE, "upload_id"),
    ("GET", f"{API}/artifacts/{{artifact_id}}"): _scope(Scope.FILES_READ, "artifact_id"),
    ("GET", f"{API}/artifacts/{{artifact_id}}/download"): _scope(Scope.FILES_READ, "artifact_id"),
    # Presets.
    ("GET", f"{API}/presets"): _scope(Scope.PRESETS_READ),
    ("POST", f"{API}/presets"): _scope(Scope.PRESETS_WRITE),
    ("GET", f"{API}/presets/{{preset_id}}"): _scope(Scope.PRESETS_READ, "preset_id"),
    ("PUT", f"{API}/presets/{{preset_id}}"): _scope(Scope.PRESETS_WRITE, "preset_id"),
    ("DELETE", f"{API}/presets/{{preset_id}}"): _scope(Scope.PRESETS_WRITE, "preset_id"),
    # Também exige `presets:read` (tests/api/test_runs_from_preset.py).
    ("POST", f"{API}/presets/{{preset_id}}/runs"): _scope(Scope.RUNS_WRITE, "preset_id"),
    # Execuções.
    ("POST", f"{API}/runs"): _scope(Scope.RUNS_WRITE),
    ("GET", f"{API}/runs"): _scope(Scope.RUNS_READ),
    ("GET", f"{API}/runs/{{run_id}}"): _scope(Scope.RUNS_READ, "run_id"),
    ("GET", f"{API}/runs/{{run_id}}/results"): _scope(Scope.RUNS_READ, "run_id"),
    ("POST", f"{API}/runs/{{run_id}}/cancel"): _scope(Scope.RUNS_WRITE, "run_id"),
}


def entries(*policies: Policy) -> list[tuple[str, str, Rule]]:
    """Entradas das políticas pedidas (todas, sem argumento), em ordem estável."""
    return [
        (method, path, rule)
        for (method, path), rule in sorted(MATRIX.items())
        if not policies or rule.policy in policies
    ]
