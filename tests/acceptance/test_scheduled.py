"""Teste de aceitação agendado (F8-9): unit, timer, instalador e o script que pinga o healthchecks.

O script só roda na VPS, como root. Aqui `docker`, `python3` e `curl` são falsos que registram,
numa única lista, cada chamada na ordem em que aconteceu; o `curl` registra a URL que leu do
stdin, que é por onde ela passa (deploy/backup/lib.sh). A trava da publicação é um arquivo do
tmp_path e o `flock` é o de verdade.
"""

import configparser
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.host_python

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_DIR = ROOT / "deploy" / "acceptance"
SCRIPT = ACCEPTANCE_DIR / "scheduled.sh"
INSTALLER = ACCEPTANCE_DIR / "install.sh"
SERVICE = "goblinshift-acceptance.service"
TIMER = "goblinshift-acceptance.timer"
BACKUP_TIMER = ROOT / "deploy" / "backup" / "goblinshift-pg-backup.timer"
EXECUTOR_CTL_INSTALLER = ROOT / "deploy" / "executor-ctl" / "install.sh"
# Só o root grava aqui: o checkout e /srv/goblinshift/secrets pertencem ao goblinshift.
INSTALLED_SCRIPT = Path("/usr/local/lib/goblinshift/acceptance/scheduled.sh")
CONFIG_FILE = "/etc/goblinshift/acceptance.env"
CHECK_URL = "https://hc-ping.com/0b1c2d3e-CANARY-aceitacao"
WORKER = "w0rk3r"
SCRIPT_TIMEOUT_S = 30
SIGTERM_EXIT = 128 + signal.SIGTERM
# EX_TEMPFAIL: a trava da publicação estava ocupada ou ausente e nada foi tocado.
LOCK_BUSY_EXIT = 75
# Folga do TimeoutStartSec sobre o pior caso calculado, para o pull da imagem das sondas.
PULL_ALLOWANCE_S = 15 * 60
# Folga da tolerância do check semanal sobre sorteio + prazo do serviço.
GRACE_MARGIN_MIN = 30
# /proc/uptime: abaixo de 30 min o script espera o worker religar; acima, segue como sempre.
JUST_BOOTED_S = 60
LONG_AFTER_BOOT_S = 99999
# Prazo de quem espera um estado que já deveria ter chegado (não é margem para a corrida).
WAIT_DEADLINE_S = 10
# Campo `MAJ:MIN:INODE` de uma linha de /proc/locks (maior e menor em hexadecimal).
PROC_LOCKS_FILE = re.compile(r"^[0-9a-f]+:[0-9a-f]+:(\d+)$")

FAKE_DOCKER = """#!/bin/sh
# O marcador do primeiro `start` lento nasce antes do registro: o teste sinaliza assim que vê a
# chamada no registro, e o `start` repetido pelo trap precisa achar o marcador para não dormir
# de novo.
slow_start=
if [ "$1" = start ] && [ -n "$FAKE_START_SLEEP_S" ] && [ ! -e "$FAKE_CALLS.start" ]; then
  : >"$FAKE_CALLS.start"
  slow_start=1
fi
echo "docker $*" >>"$FAKE_CALLS"
case "$1" in
  ps)
    # O worker só aparece a partir da consulta FAKE_WORKER_FROM_PS (o religamento da máquina).
    n=$(($(cat "$FAKE_CALLS.ps" 2>/dev/null || echo 0) + 1))
    echo "$n" >"$FAKE_CALLS.ps"
    if [ -n "$FAKE_WORKER" ] && [ "$n" -ge "${FAKE_WORKER_FROM_PS:-1}" ]; then
      echo "$FAKE_WORKER"
    fi
    exit "${FAKE_PS_RC:-0}"
    ;;
  stop)
    # Com FAKE_STOP_FIFO, o primeiro `stop` só sai quando o teste escrever no FIFO; o repetido
    # pelo trap não espera, porque ninguém escreve de novo.
    if [ -n "$FAKE_STOP_FIFO" ] && [ ! -e "$FAKE_CALLS.stop" ]; then
      : >"$FAKE_CALLS.stop"
      read -r _ <"$FAKE_STOP_FIFO"
    fi
    exit "${FAKE_STOP_RC:-0}"
    ;;
  start)
    if [ -n "$slow_start" ]; then
      sleep "$FAKE_START_SLEEP_S"
    fi
    exit "${FAKE_START_RC:-0}"
    ;;
  inspect) echo "${FAKE_RUNNING-true}"; exit "${FAKE_INSPECT_RC:-0}" ;;
esac
"""
# O daemon: o primeiro `stop` começa uma parada que sobrevive à morte do cliente, e `start` durante
# ela não faz nada e sai com 0 (NotModified). Só o `stop` seguinte espera a parada terminar.
FAKE_DAEMON_DOCKER = """#!/bin/sh
case "$1" in
  stop)
    if [ ! -e "$FAKE_DAEMON/stopping" ]; then
      : >"$FAKE_DAEMON/stopping"
      echo "docker stop $2 $3" >>"$FAKE_CALLS"
      sleep 30
    fi
    echo "docker stop $2 $3" >>"$FAKE_CALLS"
    rm -f "$FAKE_DAEMON/running"
    ;;
  start)
    echo "docker start $2" >>"$FAKE_CALLS"
    [ -e "$FAKE_DAEMON/stopping" ] && [ -e "$FAKE_DAEMON/running" ] && exit 0
    : >"$FAKE_DAEMON/running"
    rm -f "$FAKE_DAEMON/stopping"
    ;;
  inspect)
    echo "docker inspect" >>"$FAKE_CALLS"
    [ -e "$FAKE_DAEMON/running" ] && echo true || echo false
    ;;
  ps) echo "$FAKE_WORKER" ;;
esac
"""
FAKE_PYTHON = """#!/bin/sh
echo "python3 $* (em $(basename "$PWD"))" >>"$FAKE_CALLS"
sleep "${FAKE_ACCEPTANCE_SLEEP_S:-0}"
exit "${FAKE_ACCEPTANCE_RC:-0}"
"""
# O python3 que confere, durante a aceitação, se a trava da publicação continua com o script.
FAKE_PYTHON_PROBING_LOCK = """#!/bin/sh
if flock -n "$ACCEPTANCE_RELEASE_LOCK" true; then trava=livre; else trava=ocupada; fi
echo "python3 trava $trava" >>"$FAKE_CALLS"
"""
FAKE_CURL = """#!/bin/sh
echo "curl-argv $*" >>"$FAKE_ARGV"
echo "curl $(sed -n 's/^url = "\\(.*\\)"$/\\1/p')" >>"$FAKE_CALLS"
"""


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(ACCEPTANCE_DIR / name)
    return parser


@pytest.fixture
def fakes(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("python3", FAKE_PYTHON), ("curl", FAKE_CURL)):
        command = bin_dir / name
        command.write_text(body)
        command.chmod(0o755)
    (tmp_path / "release.lock").touch()
    (tmp_path / "uptime").write_text(f"{LONG_AFTER_BOOT_S}.42 1.00\n")
    return tmp_path


def script_env(fakes: Path, url: str | None, worker: str) -> dict[str, str]:
    env = {
        "PATH": f"{fakes / 'bin'}:{os.environ['PATH']}",
        "FAKE_CALLS": str(fakes / "calls"),
        "FAKE_ARGV": str(fakes / "argv"),
        "FAKE_WORKER": worker,
        "ACCEPTANCE_RELEASE_LOCK": str(fakes / "release.lock"),
        "ACCEPTANCE_PROC_UPTIME": str(fakes / "uptime"),
    }
    if url is not None:
        env["ACCEPTANCE_HEALTHCHECK_URL"] = url
    return env


def scheduled(
    fakes: Path,
    *,
    url: str | None = CHECK_URL,
    worker: str = WORKER,
    running: str = "true",
    extra_env: dict[str, str] | None = None,
    **rc: int,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    env = {
        **script_env(fakes, url, worker),
        **(extra_env or {}),
        "FAKE_RUNNING": running,
        **{f"FAKE_{name.upper()}_RC": str(code) for name, code in rc.items()},
    }
    result = subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT)],  # noqa: S607 - o bash do PATH, como na unit
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
    )
    calls = fakes / "calls"
    return result, calls.read_text().splitlines() if calls.exists() else []


def interrupted(
    fakes: Path,
    *,
    reached: str,
    group: bool,
    after_signal: Callable[[], None] | None = None,
    **env: str,
) -> tuple[int, list[str]]:
    """Roda o script e manda SIGTERM assim que a chamada `reached` aparecer no registro.

    O systemd sinaliza o cgroup inteiro (`group`); o sinal só no bash cobre o instante entre dois
    comandos. O novo grupo de processos deixa o sinal longe do pytest. `after_signal` roda depois
    do sinal e antes de esperar o script, para soltar um fake que segura o comando em curso.
    """
    worker = env.pop("FAKE_WORKER", WORKER)
    with (fakes / "stderr").open("w") as stderr:
        process = subprocess.Popen(  # noqa: S603 - script do repositório
            ["bash", str(SCRIPT)],  # noqa: S607 - o bash do PATH, como na unit
            env={**script_env(fakes, CHECK_URL, worker), **env},
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            start_new_session=True,
        )
    calls = fakes / "calls"
    deadline = time.monotonic() + SCRIPT_TIMEOUT_S
    while not (calls.exists() and reached in calls.read_text()):
        assert time.monotonic() < deadline, f"o script não chegou a {reached}"
        time.sleep(0.01)
    if group:
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.send_signal(signal.SIGTERM)
    if after_signal is not None:
        after_signal()
    code = process.wait(timeout=SCRIPT_TIMEOUT_S)
    return code, calls.read_text().splitlines()


def worker_state() -> str:
    return f"docker inspect --type=container --format={{{{.State.Running}}}} {WORKER}"


def worker_lookup() -> str:
    return (
        "docker ps --quiet --filter=label=com.docker.compose.project=goblinshift"
        " --filter=label=com.docker.compose.service=worker"
    )


# --- Script -----------------------------------------------------------------------------------


def test_sucesso_pinga_start_e_sucesso_com_o_worker_parado_durante_o_teste(fakes: Path) -> None:
    result, calls = scheduled(fakes)

    assert result.returncode == 0, result.stderr
    assert calls == [
        f"curl {CHECK_URL}/start",
        worker_lookup(),
        f"docker stop --time=30 {WORKER}",
        "python3 -E -s -B -m acceptance.run (em deploy)",
        f"docker start {WORKER}",
        worker_state(),
        f"curl {CHECK_URL}",
    ]


def test_aceitacao_reprovada_pinga_fail_religa_o_worker_e_sai_com_o_codigo_dela(
    fakes: Path,
) -> None:
    result, calls = scheduled(fakes, acceptance=1)

    assert result.returncode == 1
    assert calls[-3:] == [f"docker start {WORKER}", worker_state(), f"curl {CHECK_URL}/fail"]


def test_worker_que_nao_rodava_fica_como_estava(fakes: Path) -> None:
    result, calls = scheduled(fakes, worker="")

    assert result.returncode == 0, result.stderr
    assert calls == [
        f"curl {CHECK_URL}/start",
        worker_lookup(),
        "python3 -E -s -B -m acceptance.run (em deploy)",
        f"curl {CHECK_URL}",
    ]


def test_worker_que_nao_parou_impede_a_aceitacao(fakes: Path) -> None:
    # As sondas enchem a área de trabalho: com uma tentativa real ao lado, as duas falhariam.
    result, calls = scheduled(fakes, stop=1)

    assert result.returncode != 0
    assert not any(call.startswith("python3") for call in calls)
    assert calls[-3:] == [f"docker start {WORKER}", worker_state(), f"curl {CHECK_URL}/fail"]


def test_worker_que_nao_volta_reprova_mesmo_com_a_aceitacao_aprovada(fakes: Path) -> None:
    result, calls = scheduled(fakes, start=1)

    assert result.returncode != 0
    assert calls[-1] == f"curl {CHECK_URL}/fail"
    assert f"docker start {WORKER}" in result.stderr


def test_sigterm_na_aceitacao_religa_o_worker_e_pinga_fail(fakes: Path) -> None:
    code, calls = interrupted(fakes, reached="python3", group=True, FAKE_ACCEPTANCE_SLEEP_S="30")

    assert code == SIGTERM_EXIT
    assert calls[-4:] == [
        f"docker stop --time=30 {WORKER}",
        f"docker start {WORKER}",
        worker_state(),
        f"curl {CHECK_URL}/fail",
    ]


def test_sigterm_entre_comandos_nao_comeca_a_aceitacao(fakes: Path, tmp_path: Path) -> None:
    # O SIGTERM chega ao bash com o `stop` preso no FIFO; o bash só roda o trap quando o `stop`
    # sai, e o FIFO só é escrito depois do sinal. A ordem não depende de tempo.
    fifo = tmp_path / "stop.fifo"
    os.mkfifo(fifo)
    code, calls = interrupted(
        fakes,
        reached="docker stop",
        group=False,
        FAKE_STOP_FIFO=str(fifo),
        after_signal=lambda: fifo.write_text("\n"),
    )

    assert code == SIGTERM_EXIT
    assert not [call for call in calls if call.startswith("python3")]
    assert calls[-2:] == [worker_state(), f"curl {CHECK_URL}/fail"]


def test_sigterm_no_cliente_do_stop_espera_a_parada_antes_de_religar(fakes: Path) -> None:
    daemon = fakes / "daemon"
    daemon.mkdir()
    (daemon / "running").touch()
    docker = fakes / "bin" / "docker"
    docker.write_text(FAKE_DAEMON_DOCKER)

    code, calls = interrupted(fakes, reached="docker stop", group=True, FAKE_DAEMON=str(daemon))

    assert code == SIGTERM_EXIT
    assert (daemon / "running").exists()
    assert calls[-5:] == [
        f"docker stop --time=30 {WORKER}",
        f"docker stop --time=30 {WORKER}",
        f"docker start {WORKER}",
        "docker inspect",
        f"curl {CHECK_URL}/fail",
    ]


def test_sigterm_no_cliente_do_start_repete_o_religamento(fakes: Path) -> None:
    code, calls = interrupted(fakes, reached="docker start", group=True, FAKE_START_SLEEP_S="30")

    assert code == SIGTERM_EXIT
    assert calls[-5:] == [
        f"docker start {WORKER}",
        f"docker stop --time=30 {WORKER}",
        f"docker start {WORKER}",
        worker_state(),
        f"curl {CHECK_URL}/fail",
    ]


def test_start_lento_fica_marcado_antes_de_a_chamada_aparecer(fakes: Path) -> None:
    # O teste do SIGTERM sinaliza assim que vê a chamada no registro: se o marcador do start lento
    # viesse depois, o start repetido pelo trap dormiria os 30 s inteiros. Com o registro numa FIFO
    # sem leitor, o fake trava ao abri-lo, e só a ordem decide se o marcador já existe nesse ponto.
    calls = fakes / "calls"
    os.mkfifo(calls)
    process = subprocess.Popen(  # noqa: S603 - fake do próprio teste
        [str(fakes / "bin" / "docker"), "start", WORKER],
        env={**script_env(fakes, CHECK_URL, WORKER), "FAKE_START_SLEEP_S": "0"},
    )
    try:
        deadline = time.monotonic() + 5
        while not (fakes / "calls.start").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (fakes / "calls.start").exists()
    finally:
        with calls.open() as reader:
            reader.read()
        process.wait(timeout=SCRIPT_TIMEOUT_S)


@pytest.mark.parametrize("state", ["false", ""])
def test_worker_que_nao_ficou_rodando_reprova_mesmo_com_o_start_aceito(
    fakes: Path, state: str
) -> None:
    result, calls = scheduled(fakes, running=state)

    assert result.returncode == 1
    assert "o worker não voltou" in result.stderr
    assert calls[-2:] == [worker_state(), f"curl {CHECK_URL}/fail"]


def test_estado_do_worker_ilegivel_reprova(fakes: Path) -> None:
    result, calls = scheduled(fakes, inspect=1)

    assert result.returncode == 1
    assert calls[-1] == f"curl {CHECK_URL}/fail"


def test_servico_tem_prazo_de_parada_acima_da_limpeza() -> None:
    # A parada da manutenção (60 s) e a limpeza passam perto dos 90 s padrão do systemd.
    assert unit(SERVICE)["Service"]["TimeoutStopSec"] == "3min"


def test_lista_de_workers_ilegivel_reprova_sem_rodar_a_aceitacao(fakes: Path) -> None:
    result, calls = scheduled(fakes, worker="", ps=1)

    assert result.returncode == 1
    assert calls == [f"curl {CHECK_URL}/start", worker_lookup(), f"curl {CHECK_URL}/fail"]


@contextmanager
def release_lock_held(path: Path) -> Iterator[None]:
    """Segura a trava como o receptor da publicação: outro processo, `flock` exclusivo.

    Um processo só (o `sleep` herda o descritor), para que matá-lo solte a trava e feche o pipe.
    """
    holder = subprocess.Popen(  # noqa: S603 - script fixo
        [  # noqa: S607 - o bash e o flock do PATH, como na unit
            "bash",
            "-c",
            'exec 9>>"$1" && flock 9 && echo segurando && exec sleep 60',
            "_",
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "segurando"
        yield
    finally:
        holder.kill()
        holder.communicate(timeout=SCRIPT_TIMEOUT_S)


def test_trava_ocupada_nao_para_o_worker(fakes: Path) -> None:
    # Uma publicação em curso recriaria worker e manutenção no meio das sondas: o script espera
    # até o prazo e desiste sem tocar em nada.
    with release_lock_held(fakes / "release.lock"):
        result, calls = scheduled(fakes, extra_env={"ACCEPTANCE_LOCK_WAIT_S": "0.5"})

    assert result.returncode == LOCK_BUSY_EXIT
    assert "publicação" in result.stderr
    assert calls == [f"curl {CHECK_URL}/fail"]


def test_trava_ausente_nao_para_o_worker(fakes: Path) -> None:
    (fakes / "release.lock").unlink()

    result, calls = scheduled(fakes)

    assert result.returncode == LOCK_BUSY_EXIT
    assert calls == [f"curl {CHECK_URL}/fail"]


def flock_waiter_on(path: Path) -> bool:
    """Há alguém bloqueado à espera de um `flock` em `path` (linha `->` de /proc/locks)?"""
    inode = path.stat().st_ino
    for line in Path("/proc/locks").read_text().splitlines():
        fields = line.split()
        if "->" not in fields:
            continue
        for field in fields:
            match = PROC_LOCKS_FILE.match(field)
            if match and int(match.group(1)) == inode:
                return True
    return False


def wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_DEADLINE_S
    while not condition():
        assert time.monotonic() < deadline, f"{what} não aconteceu"
        time.sleep(0.01)


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="/proc/locks só existe no Linux")
def test_trava_liberada_dentro_do_prazo_segue_com_o_teste(fakes: Path) -> None:
    # A trava só é solta depois que o kernel mostra o script bloqueado nela (/proc/locks): o
    # `flock -w` já está esperando, sem depender de sleep nem de um registrador antes do `exec`.
    lock = fakes / "release.lock"
    calls_file = fakes / "calls"
    with release_lock_held(lock):
        process = subprocess.Popen(  # noqa: S603 - script do repositório
            ["bash", str(SCRIPT)],  # noqa: S607 - o bash do PATH, como na unit
            env={**script_env(fakes, CHECK_URL, WORKER), "ACCEPTANCE_LOCK_WAIT_S": "20"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        wait_until(lambda: flock_waiter_on(lock), "o script esperar a trava")
        assert not calls_file.exists()
    _, stderr = process.communicate(timeout=SCRIPT_TIMEOUT_S)
    calls = calls_file.read_text().splitlines()

    assert process.returncode == 0, stderr
    assert calls[0] == f"curl {CHECK_URL}/start"
    assert "python3 -E -s -B -m acceptance.run (em deploy)" in calls


def test_trava_livre_e_segurada_durante_o_teste(fakes: Path) -> None:
    (fakes / "bin" / "python3").write_text(FAKE_PYTHON_PROBING_LOCK)

    result, calls = scheduled(fakes)

    assert result.returncode == 0, result.stderr
    assert "python3 trava ocupada" in calls


def booted(fakes: Path, seconds: int) -> None:
    (fakes / "uptime").write_text(f"{seconds}.17 3.00\n")


def test_logo_depois_do_boot_espera_o_worker_aparecer(fakes: Path) -> None:
    # Persistent=true roda o teste perdido logo depois do boot: o worker pode ainda não ter subido.
    booted(fakes, JUST_BOOTED_S)

    result, calls = scheduled(
        fakes,
        extra_env={"FAKE_WORKER_FROM_PS": "2", "ACCEPTANCE_WORKER_POLL_S": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert calls.count(worker_lookup()) >= 2
    stop = calls.index(f"docker stop --time=30 {WORKER}")
    assert stop < calls.index("python3 -E -s -B -m acceptance.run (em deploy)")


@pytest.mark.parametrize(
    "uptime", [f"{LONG_AFTER_BOOT_S}.17 3.00\n", None], ids=["longe", "ilegivel"]
)
def test_longe_do_boot_ou_uptime_ilegivel_nao_espera(fakes: Path, uptime: str | None) -> None:
    if uptime is None:
        (fakes / "uptime").unlink()
    else:
        (fakes / "uptime").write_text(uptime)

    result, calls = scheduled(
        fakes,
        extra_env={"FAKE_WORKER_FROM_PS": "2", "ACCEPTANCE_WORKER_POLL_S": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert calls.count(worker_lookup()) == 1
    assert f"docker stop --time=30 {WORKER}" not in calls


def test_worker_que_nao_aparece_no_prazo_segue_sem_parar(fakes: Path) -> None:
    booted(fakes, JUST_BOOTED_S)

    result, calls = scheduled(
        fakes,
        worker="",
        extra_env={"ACCEPTANCE_WORKER_WAIT_S": "1", "ACCEPTANCE_WORKER_POLL_S": "0.2"},
    )

    assert result.returncode == 0, result.stderr
    assert calls.count(worker_lookup()) >= 2
    assert not [call for call in calls if call.startswith("docker stop")]
    assert "python3 -E -s -B -m acceptance.run (em deploy)" in calls
    assert "worker" in result.stderr


@pytest.mark.parametrize("group", [True, False], ids=["cgroup", "so-o-bash"])
def test_sigterm_na_espera_depois_do_boot_sai_sem_tocar_no_worker(fakes: Path, group: bool) -> None:
    booted(fakes, JUST_BOOTED_S)

    code, calls = interrupted(
        fakes,
        reached=worker_lookup(),
        group=group,
        FAKE_WORKER_FROM_PS="1000",
        ACCEPTANCE_WORKER_POLL_S="0.2",
    )

    assert code == SIGTERM_EXIT
    assert not [c for c in calls if c.startswith(("docker stop", "docker start", "python3"))]
    assert calls[-1] == f"curl {CHECK_URL}/fail"
    assert "não apareceu" not in (fakes / "stderr").read_text()


def test_sem_a_url_nao_roda_nada(fakes: Path) -> None:
    result, calls = scheduled(fakes, url=None)

    assert result.returncode == 2
    assert "ACCEPTANCE_HEALTHCHECK_URL" in result.stderr
    assert calls == []


def test_url_do_check_nunca_vai_para_o_argv(fakes: Path) -> None:
    scheduled(fakes, acceptance=1)

    assert "CANARY" not in (fakes / "argv").read_text()


@pytest.mark.parametrize("script", [SCRIPT, INSTALLER], ids=["scheduled", "install"])
def test_scripts_passam_no_shellcheck(script: Path) -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck ausente")
    result = subprocess.run(  # noqa: S603 - binário localizado pelo which
        [shellcheck, "--external-sources", str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
        cwd=script.parent,
    )
    assert result.returncode == 0, result.stdout


# --- Units e instalador -----------------------------------------------------------------------


def test_servico_roda_como_root_so_codigo_e_configuracao_que_so_o_root_grava() -> None:
    service = unit(SERVICE)["Service"]
    installer = INSTALLER.read_text()

    assert "User" not in service, "o teste de aceitação exige root (deploy/acceptance/run.py)"
    assert service["Type"] == "oneshot"
    assert Path(service["ExecStart"]) == INSTALLED_SCRIPT
    assert f"PREFIX={INSTALLED_SCRIPT.parent.parent}" in installer
    assert service["EnvironmentFile"] == CONFIG_FILE
    assert f"CONFIG={CONFIG_FILE}" in installer
    assert "/srv/goblinshift/app" not in (ACCEPTANCE_DIR / SERVICE).read_text()
    assert "hc-ping" not in (ACCEPTANCE_DIR / SERVICE).read_text()


def test_servico_root_so_grava_na_area_de_trabalho() -> None:
    service = unit(SERVICE)["Service"]
    work_area = re.search(r"^WORK_AREA=(\S+)$", EXECUTOR_CTL_INSTALLER.read_text(), re.MULTILINE)
    assert work_area is not None

    assert service["ProtectSystem"] == "strict"
    assert service["ReadWritePaths"] == work_area[1]
    assert service["ProtectHome"] == "yes"
    assert service["PrivateTmp"] == "yes"
    assert service["NoNewPrivileges"] == "yes"


def test_servico_root_so_tem_as_capabilities_das_sondas() -> None:
    # mkdir + chown/chmod 2770 de <área>/<id> para o UID do worker e leitura da área de todos os
    # donos (run.py); o Docker é o daemon que faz o resto, pelo socket.
    service = unit(SERVICE)["Service"]

    assert set(service["CapabilityBoundingSet"].split()) == {
        "CAP_CHOWN",
        "CAP_FOWNER",
        "CAP_FSETID",
        "CAP_DAC_OVERRIDE",
        "CAP_DAC_READ_SEARCH",
    }
    for directive in (
        "PrivateDevices",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "LockPersonality",
    ):
        assert service[directive] == "yes", directive


def worst_case_run_s() -> int:
    """Pior caso de uma rodada, das constantes do script e do run.py, fora o pull da imagem.

    Espera pela trava, espera pelo worker depois do boot, parada do worker e da manutenção e o
    prazo de cada sonda.
    """
    script = SCRIPT.read_text()
    run_py = (ACCEPTANCE_DIR / "run.py").read_text()

    def default(name: str) -> int:
        match = re.search(rf'^{name}="\$\{{\w+:-(\d+)\}}"$', script, re.M)
        assert match is not None, name
        return int(match[1])

    worker_stop = re.search(r"^WORKER_STOP_S=(\d+)$", script, re.M)
    maintenance_stop = re.search(r"^MAINTENANCE_STOP_S = (\d+)$", run_py, re.M)
    probes = [int(s) for s in re.findall(r'run_probe\(host, "\w+", timeout_s=(\d+)\)', run_py)]
    assert worker_stop is not None
    assert maintenance_stop is not None
    assert len(probes) == 6, probes
    stops = int(worker_stop[1]) + int(maintenance_stop[1])
    return default("LOCK_WAIT_S") + default("WORKER_WAIT_S") + stops + sum(probes)


def test_servico_tem_prazo_acima_do_pior_caso_com_folga_para_o_pull() -> None:
    timeout = _minutes_of(unit(SERVICE)["Service"]["TimeoutStartSec"]) * 60

    assert timeout >= worst_case_run_s() + PULL_ALLOWANCE_S


def test_timer_e_semanal_com_jitter_e_recupera_execucao_perdida() -> None:
    # Uma vez por semana, no domingo de madrugada: o worker fica parado de 10 a 30 min (ADR 017).
    timer = unit(TIMER)["Timer"]

    assert timer["OnCalendar"] == "Sun *-*-* 07:30:00 UTC"
    assert timer["RandomizedDelaySec"] == "30min"
    assert timer["Persistent"] == "true"


def test_timer_nao_cruza_a_janela_do_backup() -> None:
    backup = unit_at(BACKUP_TIMER)["Timer"]
    acceptance = unit(TIMER)["Timer"]

    backup_window_end = _minutes(backup["OnCalendar"]) + _minutes_of(backup["RandomizedDelaySec"])

    assert _minutes(acceptance["OnCalendar"]) >= backup_window_end + 60


def test_instalador_exige_o_que_o_script_exige() -> None:
    script = SCRIPT.read_text()
    match = re.search(r"^require_env (.+)$", script, re.MULTILINE)
    assert match is not None
    installer = INSTALLER.read_text()
    required = re.search(r"^REQUIRED=\(([^)]*)\)", installer, re.MULTILINE)
    assert required is not None

    assert set(required.group(1).split()) == set(match.group(1).split())


def test_runbook_tem_o_check_semanal_com_periodo_de_7_dias() -> None:
    runbook = (ROOT / "deploy" / "openobserve" / "README.md").read_text()

    assert re.search(
        r"^\| `aceitação semanal` \| healthchecks\.io \| 7 dias / \d+ h \|", runbook, re.M
    )
    assert "aceitação diária" not in runbook
    assert "aceitação diária" not in (ROOT / "README.md").read_text()


def test_tolerancia_do_check_semanal_cobre_o_sorteio_e_o_prazo_do_servico() -> None:
    # O sucesso desta semana pode chegar até sorteio + prazo do serviço depois do da semana
    # passada (que pode ter saído logo no começo da janela).
    match = re.search(
        r"^\| `aceitação semanal` \| healthchecks\.io \| 7 dias / (\d+) h \|",
        (ROOT / "deploy" / "openobserve" / "README.md").read_text(),
        re.M,
    )
    assert match is not None
    timer = unit(TIMER)["Timer"]
    service = unit(SERVICE)["Service"]
    latest = _minutes_of(timer["RandomizedDelaySec"]) + _minutes_of(service["TimeoutStartSec"])

    assert int(match[1]) * 60 >= latest + GRACE_MARGIN_MIN
    assert f"7 dias, tolerância de {match[1]} h" in (ROOT / "README.md").read_text()


def unit_at(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(path)
    return parser


def _minutes(on_calendar: str) -> int:
    """Minuto do dia de um `OnCalendar` em UTC (`[Dia ]*-*-* HH:MM:SS UTC`)."""
    match = re.fullmatch(r"(?:[A-Z][a-z]{2} )?\*-\*-\* (\d\d):(\d\d):00 UTC", on_calendar)
    assert match is not None, on_calendar
    return int(match[1]) * 60 + int(match[2])


def _minutes_of(span: str) -> int:
    match = re.fullmatch(r"(\d+)min", span)
    assert match is not None, span
    return int(match[1])
