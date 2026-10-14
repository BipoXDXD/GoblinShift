"""Teste de caos do protocolo de execução (docs/GoblinShiftPlan.md, 3.4, 3.5, 3.7 e 3.9, fatia 4).

Roda na própria VPS, como root, com a pilha de produção de pé e a fila vazia:

    cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.chaos

Opções: `--base-url` (padrão http://localhost), `--user <id>` para reaproveitar uma conta de
teste em vez de criar outra, e `--scenario <falha>/<fronteira>` (repetível) para rodar só parte.

Para cada fronteira do protocolo, cada falha do worker, e depois a recuperação:

Fronteiras, lidas do banco numa sessão psql aberta durante o teste (uma consulta a cada 20 ms; um
`docker exec` por consulta custaria mais que a janela da coleta):
- `claim`: Attempt viva sem `executor_started_at` (claim feito, executor ainda não registrado:
  cópia das entradas ou `start` em curso no executor-ctl);
- `executor`: `executor_started_at` preenchido e fase `processing` (o container `gs-exec-*` roda
  a receita; o estado dele entra na evidência);
- `coleta`: fase `packaging` ou `finalizing` sem `run_output` (coleta, candidatos e publicação).
Para alargar as janelas, cada execução leva 5 tabelas de ~7,3 MiB e 19.000 registros (o limite
de 1.7 é 8 MiB e 20.000): a cópia das entradas, a receita e a gravação dos candidatos levam
segundos ou centenas de milissegundos, e não microssegundos. A coleta é a janela mais estreita;
se a falha não cair nela (a publicação já aconteceu, ou a tentativa mudou de fase antes do sinal),
o cenário é refeito com outra execução, até 3 vezes, e a linha da fronteira diz em qual vez caiu.

Falhas, no container do worker:
- SIGKILL: `docker kill --signal=KILL`. Depois o MESMO container volta com `docker start`, sem
  depender do `restart: unless-stopped` (que o Docker pode ou não aplicar a um `kill`);
- SIGSTOP além do lease: `docker pause`, que congela todos os processos do container pelo freezer
  do cgroup. Para o processo é o mesmo que SIGSTOP (para de rodar sem ser avisado e retoma do
  ponto exato), com duas vantagens: não depende de qual PID é o worker dentro do container, e o
  `docker unpause` é o SIGCONT do grupo inteiro. A pausa dura até a manutenção encerrar a
  tentativa congelada (o lease venceu e o executor foi confirmado parado), o que prova que ela
  passou do lease; só então o worker é descongelado, e o que ele tentar escrever com a geração
  vencida tem de afetar zero linhas.

Por que `docker` e não `docker compose`: o compose.prod.yaml interpola a versão implantada
(`GS_EXECUTOR_IMAGE`, `GS_IMAGE_TAG`), que só o deploy/compose.sh carrega; o Compose sem ela recusa
o arquivo ou, pior, recria o worker com outra imagem, e o teste não precisa do Compose para isso.
Os containers são achados pelos rótulos do projeto Compose, como no acceptance/run.py, e a chave
de API sai do `gs-admin` no container da API.

Afirma, para cada cenário (uma linha por critério): a fronteira atingida; a recuperação do worker
(de volta depois do SIGKILL; tentativa congelada encerrada pela manutenção durante a pausa); o
estado terminal `succeeded`; as tentativas (a atingida encerrada numa geração anterior à final, no
máximo 2 físicas, a final sem erro); a publicação única (5 saídas e o relatório, todos da geração
final); os candidatos da geração vencida descartados; nenhum container com o rótulo
`goblinshift.attempt` e a área de trabalho só com `lost+found`; e o download igual, byte a byte, à
saída escrita à mão para cada tabela. As regras puras (fronteiras, tabelas, julgamentos) ficam em
acceptance/chaos_rules.py, coberta pelos testes locais de tests/acceptance.

Tempo: cada cenário espera o lease vencer (até 60 s depois da última renovação), um ciclo da
manutenção (30 s), o jitter da retentativa (5 a 30 s) e uma tentativa completa; na VPS, cerca de
2 a 3 minutos por cenário, ou 15 a 20 minutos para os 6. Sai com 0 só se todos os critérios
passarem, 1 se algum falhar e 2 se as pré-condições não valem (fila com trabalho, container de
executor ou área de trabalho sujos). A chave emitida fica só na memória e é revogada no fim; a
conta de teste permanece (o gs-admin não remove contas), com as saídas expirando em 48 h.

Usa só a biblioteca padrão e roda no Python do host (3.12 no Ubuntu 24.04).
"""

import argparse
import json
import os
import select
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acceptance.chaos_rules import (
    OUTPUT_ROLE,
    RECIPE_ID,
    RUN_PARAMETERS,
    SCENARIOS,
    TERMINAL_STATUSES,
    Boundary,
    ChaosError,
    Fault,
    MarkedOutput,
    Observation,
    Position,
    RunRow,
    Table,
    Timing,
    Verdict,
    build_table,
    expected_publication,
    fault_landed,
    gs_admin_command,
    inject_command,
    judge_attempts,
    judge_cleanup,
    judge_downloads,
    judge_outcome,
    judge_publication,
    locate,
    parse_attempt,
    parse_key_public_id,
    parse_observation,
    parse_output,
    parse_run,
    psql_command,
    recover_command,
)
from executor_ctl.config import DEFAULT_CONFIG_PATH, load_config
from executor_ctl.profile import ATTEMPT_LABEL, container_name

COMPOSE_PROJECT = "goblinshift"
DEFAULT_BASE_URL = "http://localhost"
API_PREFIX = "/api/v1"
KEY_SCOPES = "files:read,files:write,runs:read,runs:write"
# A menor validade que o gs-admin aceita; a chave é revogada no fim de qualquer forma.
KEY_DAYS = 1
RECIPE_VERSION = 1
TABLES_PER_RUN = 5

# Uma consulta a cada 20 ms acha a coleta, que dura centenas de milissegundos.
BOUNDARY_POLL_S = 0.02
# Da aceitação até a fronteira: fila vazia, claim imediato e uma tentativa de minutos no máximo.
BOUNDARY_WAIT_S = 300
MAX_TRIES = 3
STATE_POLL_S = 1.0
WORKER_START_TIMEOUT_S = 60
HTTP_TIMEOUT_S = 60
PSQL_TIMEOUT_S = 30
PSQL_MARKER = "__goblinshift_chaos_end__"
READ_CHUNK_BYTES = 65536
# Falhas de condução de um cenário: ele é reprovado e o teste segue para o próximo.
SCENARIO_ERRORS = (ChaosError, subprocess.CalledProcessError)

# Configuração efetiva lida dentro do container (os defaults vivem na aplicação, não aqui).
SETTINGS_SNIPPET = (
    "import json\n"
    "from goblinshift.platform.settings import Settings\n"
    "from goblinshift.runs.lifecycle import MAX_RETRY_JITTER\n"
    "s = Settings()\n"
    "print(json.dumps({'lease_s': s.worker_lease_s,"
    " 'maintenance_interval_s': s.maintenance_interval_s,"
    " 'idle_in_transaction_s': s.db_idle_in_transaction_timeout_ms / 1000,"
    " 'max_retry_jitter_s': MAX_RETRY_JITTER.total_seconds(),"
    " 'attempt_timeout_s': s.attempt_timeout_s}))\n"
)

# Consultas fixas; o único dado variável é um UUID já validado, passado como variável do psql.
OBSERVE_SQL = """
SELECT r.status, coalesce(r.phase, ''), coalesce(a.id::text, ''),
       coalesce(a.generation::text, ''),
       CASE WHEN a.id IS NULL THEN '' WHEN a.executor_started_at IS NULL THEN 'f' ELSE 't' END,
       CASE WHEN a.id IS NULL THEN '' WHEN a.ended_at IS NULL THEN 'f' ELSE 't' END,
       (SELECT count(*) FROM run_output o WHERE o.run_id = r.id)
FROM run r
LEFT JOIN LATERAL (
    SELECT * FROM attempt WHERE attempt.run_id = r.id ORDER BY generation DESC LIMIT 1
) a ON true
WHERE r.id = :'run_id';"""
RUN_SQL = "SELECT status, generation, coalesce(error_code, '') FROM run WHERE id = :'run_id';"
ATTEMPT_ENDED_SQL = "SELECT ended_at IS NOT NULL FROM attempt WHERE id = :'attempt_id';"
ATTEMPTS_SQL = """
SELECT id, generation, executor_started_at IS NOT NULL, ended_at IS NOT NULL,
       coalesce(error_code, '')
FROM attempt WHERE run_id = :'run_id' ORDER BY generation;"""
OUTPUTS_SQL = """
SELECT o.position, a.role, a.file_name, s.generation
FROM run_output o
JOIN artifact a ON a.id = o.artifact_id
JOIN stored_object s ON s.id = a.object_id
WHERE o.run_id = :'run_id' ORDER BY o.position;"""
# Candidatos ainda vivos de gerações que não publicaram: o descarte da manutenção os zera.
STALE_CANDIDATES_SQL = """
SELECT count(*)
FROM stored_object s
JOIN attempt a ON a.id = s.attempt_id
JOIN run r ON r.id = a.run_id
WHERE r.id = :'run_id' AND s.generation <> r.generation
  AND s.state IN ('pending', 'committed');"""
ACTIVE_RUNS_SQL = "SELECT count(*) FROM run WHERE status IN ('queued', 'running', 'stopping');"


@dataclass(frozen=True)
class Outcome:
    criterion: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class Credentials:
    user_id: uuid.UUID
    public_id: str
    # Fora do repr: nenhum print, log ou traceback mostra o segredo.
    token: str = field(repr=False)


@dataclass(frozen=True)
class Containers:
    api: str
    postgres: str
    worker: str
    maintenance: str


@dataclass(frozen=True)
class Injection:
    trial: int
    hit: Observation
    after: Observation
    landed: bool
    executor_state: str
    recovery: Verdict


# Banco ---------------------------------------------------------------------------------------


class Psql:
    """Uma sessão psql aberta durante o teste, no container do PostgreSQL."""

    def __init__(self, container: str) -> None:
        self._process = subprocess.Popen(
            psql_command(container),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        self._output = MarkedOutput(PSQL_MARKER)

    def query(self, sql: str, **variables: uuid.UUID) -> list[str]:
        """Linhas da consulta; as variáveis são UUIDs, citados pelo próprio psql (`:'nome'`)."""
        assert self._process.stdin is not None  # noqa: S101 - Popen com PIPE sempre o cria
        script = "".join(f"\\set {name} {value}\n" for name, value in variables.items())
        script += f"{sql}\n\\echo {PSQL_MARKER}\n"
        self._process.stdin.write(script.encode())
        self._process.stdin.flush()
        deadline = time.monotonic() + PSQL_TIMEOUT_S
        while True:
            lines = self._output.feed(self._read(deadline))
            if lines is not None:
                return lines

    def scalar(self, sql: str, **variables: uuid.UUID) -> str:
        lines = self.query(sql, **variables)
        if len(lines) != 1:
            raise ChaosError(f"esperava uma linha, veio {lines!r}")
        return lines[0]

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=PSQL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def _read(self, deadline: float) -> bytes:
        assert self._process.stdout is not None  # noqa: S101 - Popen com PIPE sempre o cria
        descriptor = self._process.stdout.fileno()
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([descriptor], [], [], max(remaining, 0))
        if not ready:
            raise ChaosError(f"o psql não respondeu em {PSQL_TIMEOUT_S} s")
        chunk = os.read(descriptor, READ_CHUNK_BYTES)
        if not chunk:
            raise ChaosError(f"o psql terminou: {self._output.pending[-500:]!r}")
        return chunk


def observe(psql: Psql, run_id: uuid.UUID) -> Observation:
    return parse_observation(psql.scalar(OBSERVE_SQL, run_id=run_id))


def read_run(psql: Psql, run_id: uuid.UUID) -> RunRow:
    return parse_run(psql.scalar(RUN_SQL, run_id=run_id))


# API -----------------------------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, credentials: Credentials) -> None:
        self._base = base_url.rstrip("/") + API_PREFIX
        self._credentials = credentials

    def json(
        self,
        method: str,
        path: str,
        body: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        all_headers = {"Content-Type": "application/json", **(headers or {})}
        return json.loads(self._send(method, path, data, all_headers))

    def put_content(self, path: str, content: bytes) -> Any:
        headers = {"Content-Type": "application/octet-stream"}
        return json.loads(self._send("PUT", path, content, headers))

    def download(self, path: str) -> bytes:
        return self._send("GET", path, None, {})

    def _send(self, method: str, path: str, data: bytes | None, headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(  # noqa: S310 - URL montada aqui, só http(s) do argumento
            self._base + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self._credentials.token}", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:  # noqa: S310
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as error:
            detail = error.read()[:300].decode(errors="replace")
            raise ChaosError(f"{method} {path}: HTTP {error.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ChaosError(f"{method} {path}: {error}") from None


def submit_run(api: Api, tables: Sequence[Table]) -> uuid.UUID:
    """Uploads em lote, conteúdo de cada um e a execução com uma `Idempotency-Key` nova."""
    created = api.json(
        "POST", "/uploads", {"items": [{"name": t.name, "size": len(t.content)} for t in tables]}
    )
    upload_ids = []
    for item, table in zip(created["items"], tables, strict=True):
        received = api.put_content(f"/uploads/{item['id']}/content", table.content)
        if received.get("state") != "ready":
            raise ChaosError(f"upload de {table.name} terminou em {received.get('state')}")
        upload_ids.append(item["id"])
    accepted = api.json(
        "POST",
        "/runs",
        {
            "recipe_id": RECIPE_ID,
            "recipe_version": RECIPE_VERSION,
            "input_upload_ids": upload_ids,
            "parameters": RUN_PARAMETERS,
        },
        headers={"Idempotency-Key": f"caos-{uuid.uuid4().hex}"},
    )
    return uuid.UUID(accepted["id"])


def download_outputs(api: Api, run_id: uuid.UUID) -> dict[str, bytes]:
    results = api.json("GET", f"/runs/{run_id}/results")
    return {
        artifact["file_name"]: api.download(f"/artifacts/{artifact['artifact_id']}/download")
        for artifact in results["artifacts"]
        if artifact["role"] == OUTPUT_ROLE
    }


# Docker --------------------------------------------------------------------------------------


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def find_containers() -> Containers:
    return Containers(
        api=_service_container("api", running_only=True),
        postgres=_service_container("postgres", running_only=True),
        worker=_single_worker(),
        maintenance=_service_container("maintenance", running_only=True),
    )


def _service_ids(service: str, *extra: str) -> list[str]:
    return _docker(
        "ps",
        "--quiet",
        *extra,
        f"--filter=label=com.docker.compose.project={COMPOSE_PROJECT}",
        f"--filter=label=com.docker.compose.service={service}",
    ).split()


def _service_container(service: str, *, running_only: bool) -> str:
    # Durante o rollout a API tem dois containers por instantes; qualquer um serve ao gs-admin.
    ids = _service_ids(service) if running_only else _service_ids(service, "--all")
    if not ids:
        raise ChaosError(f"serviço {service} do {COMPOSE_PROJECT} não está rodando")
    return ids[0]


def _single_worker() -> str:
    ids = _service_ids("worker", "--all")
    if len(ids) != 1:
        raise ChaosError(f"esperava um container do worker, há {len(ids)}")
    return ids[0]


def read_timing(containers: Containers) -> Timing:
    worker = _settings_of(containers.worker)
    maintenance = _settings_of(containers.maintenance)
    return Timing(
        lease_s=worker["lease_s"],
        maintenance_interval_s=maintenance["maintenance_interval_s"],
        idle_in_transaction_s=worker["idle_in_transaction_s"],
        max_retry_jitter_s=worker["max_retry_jitter_s"],
        attempt_timeout_s=worker["attempt_timeout_s"],
    )


def _settings_of(container: str) -> dict[str, float]:
    settings: dict[str, float] = json.loads(
        _docker("exec", container, "python", "-c", SETTINGS_SNIPPET)
    )
    return settings


def worker_state(worker: str) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(
        _docker("inspect", "--type=container", "--format={{json .State}}", worker)
    )
    return state


def ensure_worker_running(worker: str) -> float:
    """Descongela ou reinicia o worker e espera ele rodar; devolve quanto demorou."""
    started = time.monotonic()
    while time.monotonic() - started < WORKER_START_TIMEOUT_S:
        state = worker_state(worker)
        if state["Paused"]:
            subprocess.run(recover_command(Fault.PAUSE, worker), check=False, capture_output=True)
        elif state["Running"] and not state["Restarting"]:
            return time.monotonic() - started
        elif not state["Restarting"]:
            # Se o Docker já estiver reiniciando pela política do Compose, o start só repete.
            subprocess.run(recover_command(Fault.KILL, worker), check=False, capture_output=True)
        time.sleep(STATE_POLL_S / 2)
    raise ChaosError(f"o worker não voltou a rodar em {WORKER_START_TIMEOUT_S} s")


def executor_state(attempt_id: uuid.UUID | None) -> str:
    if attempt_id is None:
        return "sem tentativa"
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--type=container",
            "--format={{.State.Status}}",
            container_name(attempt_id),
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "ausente"


def attempt_containers() -> list[str]:
    return _docker("ps", "--all", "--format={{.Names}}", f"--filter=label={ATTEMPT_LABEL}").split()


def work_entries(work_area: Path) -> list[str]:
    return sorted(entry.name for entry in work_area.iterdir())


# Conta de teste ------------------------------------------------------------------------------


def create_user(api_container: str) -> uuid.UUID:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    output = _docker(
        "exec", api_container, "gs-admin", "create-user", "--display-name", f"Caos {stamp}"
    )
    return uuid.UUID(output.strip())


def issue_key(api_container: str, user_id: uuid.UUID) -> Credentials:
    command = gs_admin_command(
        api_container,
        "issue-key",
        "--user",
        str(user_id),
        "--scopes",
        KEY_SCOPES,
        "--days",
        str(KEY_DAYS),
    )
    # Sem check=True: o CalledProcessError carregaria o stdout, que é o segredo.
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ChaosError(f"gs-admin issue-key saiu com {result.returncode}: {result.stderr}")
    token = result.stdout.strip()
    if not token.startswith("gsk_"):
        raise ChaosError("o gs-admin não devolveu uma chave no formato gsk_…")
    return Credentials(user_id, parse_key_public_id(result.stderr), token)


def revoke_key(api_container: str, credentials: Credentials) -> Outcome:
    result = subprocess.run(
        gs_admin_command(api_container, "revoke-key", "--key", credentials.public_id),
        capture_output=True,
        text=True,
    )
    return Outcome(
        "chave de teste revogada",
        result.returncode == 0,
        f"{credentials.public_id} da conta {credentials.user_id}: {result.stderr.strip()}",
    )


# Cenários ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    api: Api
    psql: Psql
    containers: Containers
    timing: Timing
    work_area: Path


def run_scenario(ctx: Context, fault: Fault, boundary: Boundary) -> list[Outcome]:
    prefix = f"{fault.label} / {boundary.label}"
    try:
        outcomes = _run_trials(ctx, fault, boundary)
    except SCENARIO_ERRORS as error:
        outcomes = [Outcome("cenário", False, _describe_error(error))]
    finally:
        ensure_worker_running(ctx.containers.worker)
    return [Outcome(f"{prefix}: {o.criterion}", o.passed, o.evidence) for o in outcomes]


def _describe_error(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"{error}: {str(error.stderr).strip()[-300:]}"
    return str(error)


def _run_trials(ctx: Context, fault: Fault, boundary: Boundary) -> list[Outcome]:
    for trial in range(1, MAX_TRIES + 1):
        tables = [build_table(index) for index in range(1, TABLES_PER_RUN + 1)]
        run_id = submit_run(ctx.api, tables)
        try:
            hit = _wait_for_boundary(ctx.psql, run_id, boundary)
            injection = None if hit is None else _inject(ctx, fault, boundary, hit, run_id, trial)
            final = _wait_terminal(ctx.psql, run_id, ctx.timing.run_timeout_s)
        except SCENARIO_ERRORS:
            _cancel(ctx, run_id)
            raise
        if final.status not in TERMINAL_STATUSES:
            _cancel(ctx, run_id)
        if injection is not None and (injection.landed or trial == MAX_TRIES):
            return _judge_scenario(ctx, run_id, final, tables, injection)
    return [
        Outcome("fronteira atingida", False, f"a execução passou da fronteira {MAX_TRIES} vezes")
    ]


def _wait_for_boundary(psql: Psql, run_id: uuid.UUID, boundary: Boundary) -> Observation | None:
    """A primeira observação na fronteira; `None` se a execução passou dela sem ser vista."""
    deadline = time.monotonic() + BOUNDARY_WAIT_S
    while time.monotonic() < deadline:
        obs = observe(psql, run_id)
        position = locate(boundary, obs)
        if position is Position.AT:
            return obs
        if position is Position.PAST:
            return None
        time.sleep(BOUNDARY_POLL_S)
    raise ChaosError(f"a execução {run_id} não chegou à fronteira em {BOUNDARY_WAIT_S} s")


def _inject(
    ctx: Context,
    fault: Fault,
    boundary: Boundary,
    hit: Observation,
    run_id: uuid.UUID,
    trial: int,
) -> Injection:
    worker = ctx.containers.worker
    subprocess.run(inject_command(fault, worker), check=True, capture_output=True)
    try:
        after = observe(ctx.psql, run_id)
        state = executor_state(hit.attempt_id)
        recovery = _restart_after_kill(worker) if fault is Fault.KILL else _hold_pause(ctx, hit)
    finally:
        ensure_worker_running(worker)
    return Injection(trial, hit, after, fault_landed(boundary, hit, after), state, recovery)


def _restart_after_kill(worker: str) -> Verdict:
    elapsed = ensure_worker_running(worker)
    return Verdict(True, f"o mesmo container rodando de novo em {elapsed:.1f} s")


def _hold_pause(ctx: Context, hit: Observation) -> Verdict:
    """Mantém o worker congelado até a manutenção encerrar a tentativa dele, e descongela."""
    assert hit.attempt_id is not None  # noqa: S101 - toda fronteira tem tentativa viva
    paused = time.monotonic()
    deadline = paused + ctx.timing.reconcile_timeout_s
    reconciled = False
    while not reconciled and time.monotonic() < deadline:
        reconciled = ctx.psql.scalar(ATTEMPT_ENDED_SQL, attempt_id=hit.attempt_id) == "t"
        if not reconciled:
            time.sleep(STATE_POLL_S)
    held_s = time.monotonic() - paused
    subprocess.run(
        recover_command(Fault.PAUSE, ctx.containers.worker), check=True, capture_output=True
    )
    passed = reconciled and held_s > ctx.timing.lease_s
    verdict = "encerrada pela manutenção" if reconciled else "ainda viva"
    return Verdict(
        passed,
        f"congelado {held_s:.0f} s (lease {ctx.timing.lease_s:.0f} s); tentativa {verdict} "
        "antes do unpause",
    )


def _wait_terminal(psql: Psql, run_id: uuid.UUID, timeout_s: float) -> RunRow:
    deadline = time.monotonic() + timeout_s
    row = read_run(psql, run_id)
    while row.status not in TERMINAL_STATUSES and time.monotonic() < deadline:
        time.sleep(STATE_POLL_S)
        row = read_run(psql, run_id)
    return row


def _cancel(ctx: Context, run_id: uuid.UUID) -> None:
    """Melhor esforço: a conta só admite uma execução ativa, e o próximo cenário precisa dela."""
    ensure_worker_running(ctx.containers.worker)
    try:
        ctx.api.json("POST", f"/runs/{run_id}/cancel")
    except ChaosError as error:
        print(f"aviso: cancelamento de {run_id} falhou: {error}", file=sys.stderr)
    _wait_terminal(ctx.psql, run_id, ctx.timing.run_timeout_s)


def _judge_scenario(
    ctx: Context, run_id: uuid.UUID, final: RunRow, tables: Sequence[Table], injection: Injection
) -> list[Outcome]:
    assert injection.hit.attempt_id is not None  # noqa: S101 - toda fronteira tem tentativa viva
    cleanup, stale = _wait_cleanup(ctx, run_id)
    attempts = [parse_attempt(line) for line in ctx.psql.query(ATTEMPTS_SQL, run_id=run_id)]
    outputs = [parse_output(line) for line in ctx.psql.query(OUTPUTS_SQL, run_id=run_id)]
    expected_names = expected_publication({table.name for table in tables})
    downloads = (
        judge_downloads(
            download_outputs(ctx.api, run_id), {table.name: table.expected for table in tables}
        )
        if final.status == "succeeded"
        else Verdict(False, f"sem download: a execução terminou em {final.status}")
    )
    verdicts = {
        "fronteira atingida": _boundary_verdict(injection),
        "recuperação do worker": injection.recovery,
        "estado terminal": judge_outcome(final),
        "tentativas e geração": judge_attempts(
            attempts, faulted=injection.hit.attempt_id, final_generation=final.generation
        ),
        "publicação única": judge_publication(
            outputs, final_generation=final.generation, expected=expected_names
        ),
        "candidatos da geração vencida descartados": stale,
        "sem container órfão e área limpa": cleanup,
        "download confere": downloads,
    }
    return [Outcome(name, v.passed, v.evidence) for name, v in verdicts.items()]


def _boundary_verdict(injection: Injection) -> Verdict:
    return Verdict(
        injection.landed,
        f"vez {injection.trial} de {MAX_TRIES}; no sinal: {injection.hit.describe()}; "
        f"logo depois: {injection.after.describe()}; executor {injection.executor_state}",
    )


def _wait_cleanup(ctx: Context, run_id: uuid.UUID) -> tuple[Verdict, Verdict]:
    """Espera a manutenção limpar containers, área e candidatos; devolve os dois julgamentos."""
    deadline = time.monotonic() + ctx.timing.cleanup_timeout_s
    while True:
        cleanup = judge_cleanup(
            containers=attempt_containers(), work_entries=work_entries(ctx.work_area)
        )
        stale_count = int(ctx.psql.scalar(STALE_CANDIDATES_SQL, run_id=run_id))
        if (cleanup.passed and stale_count == 0) or time.monotonic() >= deadline:
            break
        time.sleep(STATE_POLL_S)
    stale = Verdict(stale_count == 0, f"{stale_count} candidatos vivos de gerações vencidas")
    return cleanup, stale


# Execução ------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    if os.geteuid() != 0:
        print("rode com sudo", file=sys.stderr)
        return 2
    started = time.monotonic()
    try:
        containers = find_containers()
        timing = read_timing(containers)
    except SCENARIO_ERRORS as error:
        print(f"pré-condição: {_describe_error(error)}", file=sys.stderr)
        return 2
    work_area = load_config(DEFAULT_CONFIG_PATH).work_area
    print(
        f"lease {timing.lease_s:.0f} s, manutenção a cada {timing.maintenance_interval_s:.0f} s, "
        f"prazo de reconciliação {timing.reconcile_timeout_s:.0f} s\n"
    )
    with _session(containers.postgres) as psql:
        problems = _preconditions(psql, containers.worker, work_area)
        if problems:
            for problem in problems:
                print(f"pré-condição: {problem}", file=sys.stderr)
            return 2
        user_id = args.user or create_user(containers.api)
        credentials = issue_key(containers.api, user_id)
        ctx = Context(Api(args.base_url, credentials), psql, containers, timing, work_area)
        outcomes: list[Outcome] = []
        try:
            for fault, boundary in _selected(args.scenario):
                for outcome in run_scenario(ctx, fault, boundary):
                    _print(outcome)
                    outcomes.append(outcome)
        finally:
            ensure_worker_running(containers.worker)
            revoked = revoke_key(containers.api, credentials)
            _print(revoked)
            outcomes.append(revoked)
    minutes, seconds = divmod(round(time.monotonic() - started), 60)
    failed = sum(not outcome.passed for outcome in outcomes)
    passed = len(outcomes) - failed
    print(f"\n{passed} de {len(outcomes)} critérios passaram em {minutes} min {seconds} s")
    return 0 if failed == 0 else 1


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acceptance.chaos", description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="URL da API (sem /api/v1)")
    parser.add_argument(
        "--user", type=uuid.UUID, help="conta de teste existente (padrão: cria uma)"
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[f"{fault}/{boundary}" for fault, boundary in SCENARIOS],
        help="roda só este cenário (repetível; padrão: todos)",
    )
    return parser


def _selected(names: Sequence[str] | None) -> list[tuple[Fault, Boundary]]:
    if not names:
        return list(SCENARIOS)
    return [(f, b) for f, b in SCENARIOS if f"{f}/{b}" in names]


@contextmanager
def _session(postgres: str) -> Iterator[Psql]:
    psql = Psql(postgres)
    try:
        yield psql
    finally:
        psql.close()


def _preconditions(psql: Psql, worker: str, work_area: Path) -> list[str]:
    """O teste derruba o worker: com trabalho de outra pessoa na fila, ele o atrapalharia."""
    problems = []
    state = worker_state(worker)
    if not state["Running"] or state["Paused"]:
        problems.append(f"o worker não está rodando: {state['Status']}")
    active = int(psql.scalar(ACTIVE_RUNS_SQL))
    if active:
        problems.append(f"{active} execuções não terminais; rode com a fila vazia")
    leftovers = judge_cleanup(containers=attempt_containers(), work_entries=work_entries(work_area))
    if not leftovers.passed:
        problems.append(f"executor ou área suja antes do teste: {leftovers.evidence}")
    return problems


def _print(outcome: Outcome) -> None:
    verdict = "PASSOU" if outcome.passed else "FALHOU"
    print(f"{verdict}  {outcome.criterion}: {outcome.evidence}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
