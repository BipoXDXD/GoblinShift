"""Docker de teste com a identidade do container escolhida pelo teste: label e imagem."""

from executor_ctl.docker_cli import ContainerIdentity, ContainerStatus, DockerCli, State

# `Id` do container de mentira: 64 hex, como o do Docker, e diferente de qualquer nome.
CONTAINER_ID = "0123456789abcdef" * 4


class LabeledDocker(DockerCli):
    """Docker sem processo cujo container tem a label e a imagem que o teste pede.

    Grava cada chamada com o alvo ("stop <alvo>", "rm <alvo>"): o alvo mostra se o serviço agiu
    pelo nome da tentativa ou pelo `Id` que inspecionou.
    """

    def __init__(self, state: State, label: str | None, image: str) -> None:
        super().__init__(binary="docker-que-nao-roda")
        self.state: State = state
        self.identity = ContainerIdentity(
            container_id=CONTAINER_ID, attempt_label=label, image=image
        )
        self.calls: list[str] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append(f"inspect {name}")
        if self.state == "absent":
            return ContainerStatus.absent()
        exit_code = 0 if self.state == "exited" else None
        return ContainerStatus(self.state, exit_code, False, None, None, self.identity)

    def run(self, arguments: list[str]) -> None:
        self.calls.append("run")
        self.state = "running"

    def stop(self, name: str, grace_s: int) -> None:
        self.calls.append(f"stop {name}")
        if self.state == "running":
            self.state = "exited"

    def remove(self, name: str) -> None:
        self.calls.append(f"rm {name}")
        self.state = "absent"
