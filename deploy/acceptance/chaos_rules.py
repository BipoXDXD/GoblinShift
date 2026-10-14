"""Regras puras do teste de caos (acceptance/chaos.py): fronteiras, tabelas e julgamentos.

Nada aqui fala com Docker, banco ou rede, para que os testes locais (tests/acceptance) cubram a
lógica que decide onde a falha cai e o que conta como recuperação correta.
"""

import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any

from executor_ctl.profile import ATTEMPT_LABEL

DB_USER = "goblinshift"
DB_NAME = "goblinshift"
_PUBLIC_ID = re.compile(r"^chave ([0-9a-f]{16}) válida até ", re.MULTILINE)

RUN_PARAMETERS: dict[str, Any] = {
    "input_delimiter": ";",
    "trim_columns": ["nome"],
    "case_rules": [{"column": "email", "mode": "lower"}],
    "deduplicate_by": ["id"],
}
# Tabelas de ~7,3 MiB e 19.001 registros: abaixo dos limites de 1.7 (8 MiB e 20.000 registros),
# grandes o bastante para as janelas das fronteiras durarem.
ROWS_PER_TABLE = 19_000
NOTE_CHARS = 360
RECIPE_ID = "csv-janitor"
REPORT_NAME = "report.json"
OUTPUT_ROLE = "output"
REPORT_ROLE = "report"
# Desde a fatia 5, toda execução publica também o manifesto e o pacote, cada um no seu papel
# (ADR 019); o nome do pacote é o id da receita.
MANIFEST_NAME = "manifest.json"
PACKAGE_NAME = f"{RECIPE_ID}.zip"
ROLE_BY_NAME = {REPORT_NAME: REPORT_ROLE, MANIFEST_NAME: "manifest", PACKAGE_NAME: "package"}
# 3.4: até duas tentativas físicas por execução.
MAX_PHYSICAL_ATTEMPTS = 2
TERMINAL_STATUSES = frozenset({"succeeded", "succeeded_with_warnings", "canceled", "failed"})
COLLECTING_PHASES = frozenset({"packaging", "finalizing"})
WORK_AREA_BASELINE = frozenset({"lost+found"})
STOP_GRACE_S = 10
SETTLE_MARGIN_S = 60
MIB = 1024**2


class ChaosError(Exception):
    """O teste não conseguiu observar ou conduzir o cenário (não é veredito sobre o sistema)."""


class Fault(StrEnum):
    KILL = "sigkill"
    PAUSE = "sigstop"

    @property
    def label(self) -> str:
        return "SIGKILL" if self is Fault.KILL else "SIGSTOP além do lease"


class Boundary(StrEnum):
    CLAIMED = "claim"
    EXECUTING = "executor"
    COLLECTING = "coleta"

    @property
    def label(self) -> str:
        labels = {
            Boundary.CLAIMED: "depois do claim",
            Boundary.EXECUTING: "executor rodando",
            Boundary.COLLECTING: "coleta e publicação",
        }
        return labels[self]


class Position(Enum):
    BEFORE = "antes"
    AT = "na fronteira"
    PAST = "depois"


SCENARIOS: tuple[tuple[Fault, Boundary], ...] = tuple(
    (fault, boundary) for fault in Fault for boundary in Boundary
)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    evidence: str


@dataclass(frozen=True)
class Observation:
    """A execução e a Attempt mais recente dela, como o banco as mostra num instante."""

    run_status: str
    phase: str | None
    attempt_id: uuid.UUID | None
    generation: int | None
    executor_started: bool
    attempt_ended: bool
    outputs: int

    def describe(self) -> str:
        if self.attempt_id is None:
            return f"{self.run_status}, sem tentativa"
        started = "registrado" if self.executor_started else "não registrado"
        return (
            f"{self.run_status}/{self.phase or '-'}, g{self.generation}, executor {started}, "
            f"{self.outputs} saídas publicadas"
        )


@dataclass(frozen=True)
class AttemptRow:
    attempt_id: uuid.UUID
    generation: int
    executor_started: bool
    ended: bool
    error_code: str


@dataclass(frozen=True)
class OutputRow:
    position: int
    role: str
    file_name: str
    generation: int


@dataclass(frozen=True)
class RunRow:
    status: str
    generation: int
    error_code: str


@dataclass(frozen=True)
class Table:
    name: str
    content: bytes = field(repr=False)
    expected: bytes = field(repr=False)


@dataclass(frozen=True)
class Timing:
    """Prazos do ambiente, lidos dos containers; os testes locais passam valores fixos."""

    lease_s: float
    maintenance_interval_s: float
    idle_in_transaction_s: float
    max_retry_jitter_s: float = 30
    attempt_timeout_s: float = 600

    @property
    def reconcile_timeout_s(self) -> float:
        """Até a manutenção encerrar a tentativa de um worker morto ou congelado.

        O lease vence; uma transação deixada aberta pelo worker congelado segura a Run até o
        `idle_in_transaction_session_timeout`; a manutenção pode precisar de dois ciclos (o
        primeiro pode passar antes do vencimento) e do `stop` do executor com a carência.
        """
        return (
            self.lease_s
            + self.idle_in_transaction_s
            + 2 * self.maintenance_interval_s
            + STOP_GRACE_S
            + SETTLE_MARGIN_S
        )

    @property
    def run_timeout_s(self) -> float:
        """Até o estado terminal: reconciliação, jitter da retentativa e uma tentativa inteira."""
        return self.reconcile_timeout_s + self.max_retry_jitter_s + self.attempt_timeout_s

    @property
    def cleanup_timeout_s(self) -> float:
        """Até a manutenção descartar candidatos e áreas que ficaram para trás."""
        return 2 * self.maintenance_interval_s + STOP_GRACE_S + SETTLE_MARGIN_S


def parse_key_public_id(stderr: str) -> str:
    """Identificador público da chave, que o `gs-admin issue-key` escreve no stderr."""
    match = _PUBLIC_ID.search(stderr)
    if match is None:
        raise ChaosError("o gs-admin não informou o identificador da chave emitida")
    return match.group(1)


class MarkedOutput:
    """Junta a saída do psql em linhas e entrega as de uma consulta quando o marcador chega."""

    def __init__(self, marker: str) -> None:
        self._marker = marker
        self._buffer = b""
        self._lines: list[str] = []

    @property
    def pending(self) -> str:
        return "\n".join([*self._lines, self._buffer.decode(errors="replace")])

    def feed(self, chunk: bytes) -> list[str] | None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            line = raw.decode()
            if line == self._marker:
                lines, self._lines = self._lines, []
                return lines
            self._lines.append(line)
        return None


def psql_command(container: str) -> list[str]:
    # Pelo socket local do container (confiança local da imagem oficial): nenhuma senha passa.
    return [
        "docker",
        "exec",
        "--interactive",
        container,
        "psql",
        "-X",
        "-q",
        "-A",
        "-t",
        "-F",
        "|",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
    ]


def gs_admin_command(container: str, *args: str) -> list[str]:
    return ["docker", "exec", container, "gs-admin", *args]


def inject_command(fault: Fault, worker: str) -> list[str]:
    if fault is Fault.KILL:
        return ["docker", "kill", "--signal=KILL", worker]
    return ["docker", "pause", worker]


def recover_command(fault: Fault, worker: str) -> list[str]:
    if fault is Fault.KILL:
        return ["docker", "start", worker]
    return ["docker", "unpause", worker]


def parse_observation(line: str) -> Observation:
    fields = line.split("|")
    if len(fields) != 7 or not fields[0]:
        raise ChaosError(f"observação malformada: {line!r}")
    status, phase, attempt_id, generation, started, ended, outputs = fields
    try:
        return Observation(
            run_status=status,
            phase=phase or None,
            attempt_id=uuid.UUID(attempt_id) if attempt_id else None,
            generation=int(generation) if generation else None,
            executor_started=started == "t",
            attempt_ended=ended == "t",
            outputs=int(outputs),
        )
    except ValueError as error:
        raise ChaosError(f"observação malformada: {line!r}") from error


def locate(boundary: Boundary, obs: Observation) -> Position:
    """Onde a execução está em relação à fronteira; `PAST` quando ela já passou sem ser atingida."""
    if obs.run_status in TERMINAL_STATUSES or obs.outputs > 0:
        return Position.PAST
    alive = obs.attempt_id is not None and not obs.attempt_ended
    if not alive:
        return Position.BEFORE
    match boundary:
        case Boundary.CLAIMED:
            return Position.PAST if obs.executor_started else Position.AT
        case Boundary.EXECUTING:
            if obs.phase in COLLECTING_PHASES:
                return Position.PAST
            at = obs.executor_started and obs.phase == "processing"
            return Position.AT if at else Position.BEFORE
        case Boundary.COLLECTING:
            return Position.AT if obs.phase in COLLECTING_PHASES else Position.BEFORE


def fault_landed(boundary: Boundary, hit: Observation, after: Observation) -> bool:
    """A falha pegou a tentativa ainda na fronteira (nada avançou entre a leitura e o sinal)."""
    return after.attempt_id == hit.attempt_id and locate(boundary, after) is Position.AT


def build_table(index: int, rows: int = ROWS_PER_TABLE, note_chars: int = NOTE_CHARS) -> Table:
    """Tabela com espaços, caixa alta e uma duplicata, e a saída limpa escrita sem a receita.

    O esperado sai da mesma fórmula que gera a entrada, não do CSV Janitor: o teste local
    (tests/acceptance) confere que a receita real produz exatamente isso.
    """
    note = "x" * note_chars
    source = ["id;nome;email;notas\r\n"]
    expected = ["id,nome,email,notas\r\n"]
    for number in range(rows):
        record_id = f"{index}-{number:06d}"
        source.append(f"{record_id}; Nome {number} ;USER{number}@X.COM;{note}\r\n")
        expected.append(f"{record_id},Nome {number},user{number}@x.com,{note}\r\n")
    # Duplicata do primeiro registro: a deduplicação por id a remove.
    source.append(f"{index}-{0:06d};Nome 0;user0@x.com;{note}\r\n")
    return Table(
        name=f"caos-{index}.csv",
        content="".join(source).encode(),
        expected="".join(expected).encode(),
    )


def parse_attempt(line: str) -> AttemptRow:
    attempt_id, generation, started, ended, error_code = line.split("|")
    return AttemptRow(
        attempt_id=uuid.UUID(attempt_id),
        generation=int(generation),
        executor_started=started == "t",
        ended=ended == "t",
        error_code=error_code,
    )


def parse_output(line: str) -> OutputRow:
    position, role, file_name, generation = line.split("|")
    return OutputRow(int(position), role, file_name, int(generation))


def parse_run(line: str) -> RunRow:
    status, generation, error_code = line.split("|")
    return RunRow(status, int(generation), error_code)


def judge_outcome(row: RunRow) -> Verdict:
    passed = row.status == "succeeded"
    detail = f" ({row.error_code})" if row.error_code else ""
    return Verdict(passed, f"{row.status}{detail} na geração {row.generation}")


def judge_attempts(
    rows: Sequence[AttemptRow], *, faulted: uuid.UUID, final_generation: int
) -> Verdict:
    problems = []
    hit = next((row for row in rows if row.attempt_id == faulted), None)
    if hit is None:
        problems.append("tentativa atingida não registrada")
    else:
        if hit.generation >= final_generation:
            problems.append("a tentativa atingida é a da geração final")
        if not hit.ended:
            problems.append("a tentativa atingida segue viva")
    if any(not row.ended for row in rows):
        problems.append("há tentativa viva")
    finals = [row for row in rows if row.generation == final_generation]
    if len(finals) != 1 or finals[0].error_code:
        problems.append("a geração final não terminou limpa")
    physical = sum(row.executor_started for row in rows)
    if physical > MAX_PHYSICAL_ATTEMPTS:
        problems.append(f"{physical} tentativas físicas")
    listed = ", ".join(_describe_attempt(row, faulted) for row in rows)
    return Verdict(not problems, "; ".join([*problems, listed]))


def _describe_attempt(row: AttemptRow, faulted: uuid.UUID) -> str:
    marks = "".join(
        [
            " atingida" if row.attempt_id == faulted else "",
            " física" if row.executor_started else "",
            f" {row.error_code}" if row.error_code else "",
            "" if row.ended else " viva",
        ]
    )
    return f"g{row.generation}{marks}"


def expected_publication(output_names: set[str]) -> set[str]:
    """As saídas da receita mais o relatório, o manifesto e o pacote."""
    return output_names | set(ROLE_BY_NAME)


def judge_publication(
    rows: Sequence[OutputRow], *, final_generation: int, expected: set[str]
) -> Verdict:
    problems = []
    names = Counter(row.file_name for row in rows)
    if names != Counter(expected):
        problems.append(f"saídas {sorted(names.elements())}, esperadas {sorted(expected)}")
    stale = sorted({row.generation for row in rows if row.generation != final_generation})
    if stale:
        problems.append(f"publicação de geração vencida {stale}")
    wrong_role = [
        row.file_name for row in rows if row.role != ROLE_BY_NAME.get(row.file_name, OUTPUT_ROLE)
    ]
    if wrong_role:
        problems.append(f"papel errado em {wrong_role}")
    evidence = f"{len(rows)} run_output, todos da geração {final_generation}"
    return Verdict(not problems, "; ".join(problems) or evidence)


def judge_downloads(downloaded: dict[str, bytes], expected: dict[str, bytes]) -> Verdict:
    missing = sorted(expected.keys() - downloaded.keys())
    extra = sorted(downloaded.keys() - expected.keys())
    different = sorted(
        name for name in expected.keys() & downloaded.keys() if downloaded[name] != expected[name]
    )
    problems = [
        f"{label}: {names}"
        for label, names in (("faltando", missing), ("a mais", extra), ("diferentes", different))
        if names
    ]
    total_mib = sum(len(data) for data in downloaded.values()) / MIB
    evidence = f"{len(downloaded)} saídas idênticas byte a byte ({total_mib:.1f} MiB)"
    return Verdict(not problems, "; ".join(problems) or evidence)


def judge_cleanup(*, containers: Sequence[str], work_entries: Sequence[str]) -> Verdict:
    leftovers = sorted(set(work_entries) - WORK_AREA_BASELINE)
    passed = not containers and not leftovers
    return Verdict(
        passed,
        f"containers com {ATTEMPT_LABEL}: {list(containers) or 'nenhum'}; "
        f"área de trabalho: {sorted(work_entries)}",
    )
