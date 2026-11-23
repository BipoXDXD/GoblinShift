"""Operações do executor-ctl: quatro sobre o container e a leitura da imagem (3.6, "Sandbox")."""

import logging
import os
import threading
import uuid
from collections.abc import Iterator
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


class ExecutorService:
    def __init__(
        self,
        config: Config,
        docker: DockerCli,
        executor_gid: int,
        *,
        observer: PassiveObserver | None = None,
        area_lock_wait_s: float = AREA_LOCK_WAIT_S,
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
        self._throttle = _disk_throttle(config)

    def start(self, attempt_id: uuid.UUID, image: str) -> ContainerStatus:
        """Cria e inicia o container; se ele já existe, devolve o estado dele sem criar outro."""
        policy = require_allowed(self._config.images_file, image)
        if self._observer is not None:
            self._observer.observe(attempt_id, image, self._config, policy)
        name = profile.container_name(attempt_id)
        with self._holding_area():
            existing = self._docker.inspect(name)
            if existing.state != "absent":
                return existing
            prepared = workarea.prepare_for_start(
                self._config.work_area, attempt_id, self._executor_gid
            )
            arguments = profile.run_arguments(
                attempt_id,
                image,
                work_dir=prepared.path,
                runtime=self._config.runtime,
                executor_gid=self._executor_gid,
                throttle=self._throttle,
            )
            try:
                self._docker.run(arguments)
            except ContainerNameConflictError:
                # Outro start da mesma tentativa venceu a corrida: adota o container dele.
                _log.info("container já existia; adotado", extra={"attempt_id": str(attempt_id)})
        return self._docker.inspect(name)

    def image_present(self, image: str) -> bool:
        """Se o daemon tem a imagem; só das que a política autoriza.

        A allowlist vem antes do Docker: o host é compartilhado, e sem ela um cliente do socket
        descobriria quais imagens de outros projetos estão no daemon.
        """
        require_allowed(self._config.images_file, image)
        return self._docker.image_present(image)

    def status(self, attempt_id: uuid.UUID) -> ContainerStatus:
        return self._docker.inspect(profile.container_name(attempt_id))

    def stop(self, attempt_id: uuid.UUID, grace_s: int) -> ContainerStatus:
        """Para o container; o que nunca iniciou (`created`) é removido e volta `absent`."""
        if not 0 <= grace_s <= MAX_STOP_GRACE_S:
            raise ValueError(f"carência fora de 0..{MAX_STOP_GRACE_S} s")
        name = profile.container_name(attempt_id)
        self._docker.stop(name, grace_s)
        stopped = self._docker.inspect(name)
        if stopped.state != "created":
            return stopped
        return self._remove_never_started(attempt_id, name, stopped)

    def _remove_never_started(
        self, attempt_id: uuid.UUID, name: str, seen: ContainerStatus
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
                current = self._docker.inspect(name)
                if current.state != "created":
                    return current
                self._docker.remove(name)
        except AreaBusyError:
            return seen
        _log.info("container que não iniciou removido", extra={"attempt_id": str(attempt_id)})
        return ContainerStatus.absent()

    def remove(self, attempt_id: uuid.UUID) -> None:
        """Remove o container parado e apaga `<area>/<id>`; repetir é sucesso."""
        name = profile.container_name(attempt_id)
        with self._holding_area():
            # Sem a consulta, o `docker rm` também recusaria; ela só evita depender do texto
            # do erro no caso comum. A corrida entre as duas chamadas o próprio rm cobre.
            if self._docker.inspect(name).state == "running":
                raise ContainerRunningError("o container ainda roda; pare-o antes")
            self._docker.remove(name)
            workarea.remove_attempt_dir(self._config.work_area, attempt_id)

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


def _disk_throttle(config: Config) -> profile.DiskThrottle | None:
    """O teto vai no dispositivo do filesystem da área (o loop device do exec-work).

    Resolvido uma vez, na subida: a área é um filesystem montado antes do serviço e não troca
    de dispositivo com ele no ar.
    """
    if config.io_limits is None:
        return None
    device = profile.block_device(os.stat(config.work_area).st_dev)
    return profile.DiskThrottle(device=device, limits=config.io_limits)
