"""As quatro operações do executor-ctl sobre o container de uma tentativa (3.6, "Sandbox")."""

import logging
import threading
import uuid

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

_log = logging.getLogger(__name__)


class ExecutorService:
    def __init__(
        self,
        config: Config,
        docker: DockerCli,
        executor_gid: int,
        *,
        observer: PassiveObserver | None = None,
    ) -> None:
        self._config = config
        self._docker = docker
        self._executor_gid = executor_gid
        self._observer = observer
        # A área inteira é a cota de uma tentativa: o "está limpa?" do start e o apagar do
        # remove não podem se intercalar. stop e status não mexem na área e ficam de fora.
        self._area_lock = threading.Lock()

    def start(self, attempt_id: uuid.UUID, image: str) -> ContainerStatus:
        """Cria e inicia o container; se ele já existe, devolve o estado dele sem criar outro."""
        policy = require_allowed(self._config.images_file, image)
        if self._observer is not None:
            self._observer.observe(attempt_id, image, self._config, policy)
        name = profile.container_name(attempt_id)
        with self._area_lock:
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
            )
            try:
                self._docker.run(arguments)
            except ContainerNameConflictError:
                # Outro start da mesma tentativa venceu a corrida: adota o container dele.
                _log.info("container já existia; adotado", extra={"attempt_id": str(attempt_id)})
        return self._docker.inspect(name)

    def status(self, attempt_id: uuid.UUID) -> ContainerStatus:
        return self._docker.inspect(profile.container_name(attempt_id))

    def stop(self, attempt_id: uuid.UUID, grace_s: int) -> ContainerStatus:
        if not 0 <= grace_s <= MAX_STOP_GRACE_S:
            raise ValueError(f"carência fora de 0..{MAX_STOP_GRACE_S} s")
        name = profile.container_name(attempt_id)
        self._docker.stop(name, grace_s)
        return self._docker.inspect(name)

    def remove(self, attempt_id: uuid.UUID) -> None:
        """Remove o container parado e apaga `<area>/<id>`; repetir é sucesso."""
        name = profile.container_name(attempt_id)
        with self._area_lock:
            # Sem a consulta, o `docker rm` também recusaria; ela só evita depender do texto
            # do erro no caso comum. A corrida entre as duas chamadas o próprio rm cobre.
            if self._docker.inspect(name).state == "running":
                raise ContainerRunningError("o container ainda roda; pare-o antes")
            self._docker.remove(name)
            workarea.remove_attempt_dir(self._config.work_area, attempt_id)
