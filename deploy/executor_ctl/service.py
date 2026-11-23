"""Operações do executor-ctl: quatro sobre o container e a leitura da imagem (3.6, "Sandbox")."""

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from . import profile, workarea
from .config import Config
from .docker_cli import (
    ContainerNameConflictError,
    ContainerRunningError,
    ContainerStatus,
    DockerCli,
)
from .images import require_allowed
from .witness import PassiveObserver

# Teto da carência de stop (3.6, "Parada": SIGTERM, até 10 s, depois SIGKILL).
MAX_STOP_GRACE_S = 10
# Quanto start e remove esperam pelo lock da área antes de responder `busy`, e quanto o stop
# espera por ele antes de desistir de remover um container `created` (sem erro: o worker repete).
# Quem o segura normalmente sai em 1 a 3 s: o start faz inspect, `run --pull=never` (imagem já no
# daemon) e inspect; o remove faz inspect, rm e o rmtree da área, que só passa disso com saídas
# grandes; o stop, sob o lock, faz inspect e rm. Esperar além de 5 s seria fazer fila atrás de um
# Docker travado ou de um rmtree anormal.
AREA_LOCK_WAIT_S = 5.0

_log = logging.getLogger(__name__)


class AreaBusyError(Exception):
    """Outra operação segura a área além do prazo; nada foi feito, repetir depois é seguro."""


class ForeignContainerError(Exception):
    """O container com o nome da tentativa não é dela: outra label, nenhuma, ou outra imagem.

    O ctl não o adota, não o para nem o remove; o operador o inspeciona e remove à mão.
    """


class ExecutorService:
    def __init__(
        self,
        config: Config,
        docker: DockerCli,
        executor_gid: int,
        *,
        observer: PassiveObserver | None = None,
        area_lock_wait_s: float = AREA_LOCK_WAIT_S,
        resolve_device: Callable[[int], str] | None = None,
    ) -> None:
        self._config = config
        self._docker = docker
        self._executor_gid = executor_gid
        self._observer = observer
        # A área inteira é a cota de uma tentativa: o "está limpa?" do start e o apagar do
        # remove não podem se intercalar. status não mexe na área e fica de fora; o stop só o
        # toma para remover um container `created`, que pode ser o que um start em curso criou.
        self._area_lock = threading.Lock()
        self._area_lock_wait_s = area_lock_wait_s
        # O dispositivo do teto de I/O (D13) sai a cada start, sob o lock: uma remontagem da área
        # pode trocar o loopN. Os testes injetam o resolvedor; o padrão segue o link do udev.
        self._resolve_device = resolve_device

    def start(self, attempt_id: uuid.UUID, image: str) -> ContainerStatus:
        """Cria e inicia o container; se ele já existe, devolve o estado dele sem criar outro."""
        policy = require_allowed(self._config.images_file, image)
        if self._observer is not None:
            self._observer.observe(attempt_id, image, self._config, policy)
        name = profile.container_name(attempt_id)
        with self._holding_area():
            existing = self._owned(attempt_id, self._docker.inspect(name), image=image)
            if existing.state != "absent":
                return existing
            # Antes de preparar a área: um dispositivo sem nó recusa o start sem deixar nada nela.
            throttle = profile.disk_throttle(
                self._config.work_area, self._config.io_limits, resolve_device=self._resolve_device
            )
            prepared = workarea.prepare_for_start(
                self._config.work_area, attempt_id, self._executor_gid
            )
            arguments = profile.run_arguments(
                attempt_id,
                image,
                work_dir=prepared.path,
                runtime=self._config.runtime,
                executor_gid=self._executor_gid,
                throttle=throttle,
            )
            try:
                self._docker.run(arguments)
            except ContainerNameConflictError:
                # Outro start da mesma tentativa venceu a corrida, ou um alheio tomou o nome: o
                # inspect abaixo só adota o container se ele for desta tentativa.
                _log.info("nome do container já em uso", extra={"attempt_id": str(attempt_id)})
        return self._owned(attempt_id, self._docker.inspect(name), image=image)

    def image_present(self, image: str) -> bool:
        """Se o daemon tem a imagem; só das que a política autoriza.

        A allowlist vem antes do Docker: o host é compartilhado, e sem ela um cliente do socket
        descobriria quais imagens de outros projetos estão no daemon.
        """
        require_allowed(self._config.images_file, image)
        return self._docker.image_present(image)

    def status(self, attempt_id: uuid.UUID) -> ContainerStatus:
        return self._owned(attempt_id, self._docker.inspect(profile.container_name(attempt_id)))

    def stop(self, attempt_id: uuid.UUID, grace_s: int) -> ContainerStatus:
        """Para o container; o que nunca iniciou (`created`) é removido e volta `absent`.

        Depois do inspect, tudo vai pelo `Id` lido: se o nome passar a outro container no meio,
        o stop e o rm não o alcançam.
        """
        if not 0 <= grace_s <= MAX_STOP_GRACE_S:
            raise ValueError(f"carência fora de 0..{MAX_STOP_GRACE_S} s")
        identity = self._owned(
            attempt_id, self._docker.inspect(profile.container_name(attempt_id))
        ).identity
        if identity is None:
            # `_owned` só deixa passar identidade nula no `absent`: nada a parar.
            return ContainerStatus.absent()
        self._docker.stop(identity.container_id, grace_s)
        stopped = self._docker.inspect(identity.container_id)
        if stopped.state != "created":
            return stopped
        return self._remove_never_started(attempt_id, identity.container_id, stopped)

    def _remove_never_started(
        self, attempt_id: uuid.UUID, container_id: str, seen: ContainerStatus
    ) -> ContainerStatus:
        """Remove o container que o runtime não iniciou, se nenhum start está em curso.

        `docker stop` não muda um `created`, e ele seguiria contando como existente: o worker o
        trata como rodando, o remove e a varredura nunca o apagam, e a área fica suja. Mas um
        start em curso também passa por `created` entre criar e iniciar; por isso o rm só sai
        com o lock da área e depois de reler o estado. Lock ocupado devolve o que foi lido, sem
        erro: o worker repete o stop.
        """
        try:
            with self._holding_area():
                current = self._docker.inspect(container_id)
                if current.state != "created":
                    return current
                self._docker.remove(container_id)
        except AreaBusyError:
            return seen
        _log.info("container que não iniciou removido", extra={"attempt_id": str(attempt_id)})
        return ContainerStatus.absent()

    def remove(self, attempt_id: uuid.UUID) -> None:
        """Remove o container parado e apaga `<area>/<id>`; repetir é sucesso."""
        name = profile.container_name(attempt_id)
        with self._holding_area():
            current = self._owned(attempt_id, self._docker.inspect(name))
            # Sem a consulta, o `docker rm` também recusaria; ela só evita depender do texto
            # do erro no caso comum. A corrida entre as duas chamadas o próprio rm cobre.
            if current.state == "running":
                raise ContainerRunningError("o container ainda roda; pare-o antes")
            # Pelo `Id` lido, como no stop; ausente, pelo nome, e repetir é sucesso.
            target = name if current.identity is None else current.identity.container_id
            self._docker.remove(target)
            workarea.remove_attempt_dir(self._config.work_area, attempt_id)

    def _owned(
        self, attempt_id: uuid.UUID, status: ContainerStatus, *, image: str | None = None
    ) -> ContainerStatus:
        """Devolve `status` se o container é desta tentativa; senão, ForeignContainerError.

        O host é compartilhado: um container de outro projeto, ou de um engano de operador, com
        o nome da tentativa não pode ser adotado, parado nem removido. Fail-closed: sem a label
        conta como alheio. Com `image` (o start), a imagem também precisa ser a pedida.
        """
        identity = status.identity
        if status.state == "absent" or (
            identity is not None
            and identity.attempt_label == str(attempt_id)
            and (image is None or identity.image == image)
        ):
            return status
        name = profile.container_name(attempt_id)
        _log.warning(
            "container %s não é desta tentativa; recusado",
            name,
            extra={"attempt_id": str(attempt_id)},
        )
        raise ForeignContainerError(
            f"{name} existe, mas não é desta tentativa; inspecione e remova à mão"
        )

    @contextmanager
    def _holding_area(self) -> Iterator[None]:
        if not self._area_lock.acquire(timeout=self._area_lock_wait_s):
            raise AreaBusyError(
                f"a área segue ocupada por outra operação após {self._area_lock_wait_s:g} s"
            )
        try:
            yield
        finally:
            self._area_lock.release()
