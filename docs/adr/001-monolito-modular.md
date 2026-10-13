# ADR 001. Monólito modular, com processos `api`, `worker` e `maintenance`

- **Estado:** aceito
- **Plano:** [3.1](../GoblinShiftPlan.md#s3-1), [3.2](../GoblinShiftPlan.md#s3-2), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [B13](../GoblinShiftReferencias.md#l-4) (cap. 1 e 10), [B14](../GoblinShiftReferencias.md#l-4) (cap. 6 e 11)

## Contexto

O GoblinShift é um projeto de uma pessoa, com um banco PostgreSQL e um servidor, uma VPS KVM 2 compartilhada com outro projeto. O serviço recebe arquivos de contas externas e os processa em receitas, então a separação que importa não é entre equipes ou escalas de implantação, e sim entre quem pode fazer o quê: quem acessa o banco e o storage, quem acessa o Docker e quem executa conteúdo de terceiros.

## Decisão

Um único código-base e um único banco, organizados em módulos (`processing`, `runs`, `files`, `catalog`, `presets`, `identity`, `platform` e os entrypoints) com grafo de dependências acíclico, executados como processos distintos: `api`, `worker`, `maintenance` (e uma `cli` de administração), mais o executor descartável. O `executor-ctl` é um serviço do host. A divisão em processos serve para separar privilégios: só o `executor-ctl` fala com o Docker, o worker não, e o executor não tem rede nem credenciais. Sem broker de mensagens, Redis, Kafka, Kubernetes, mesh ou event sourcing.

As regras de dependência são fitness functions verificadas no CI com import-linter: contrato `layers` (entrypoints → `runs` → `presets` → `catalog`, `files` e `identity` → `processing` e `platform`), contrato `forbidden` (`processing` não importa `sqlalchemy`, `fastapi`, `starlette`, `authlib`, `httpx` nem outro módulo do projeto) e contrato `independence` (`catalog`, `files` e `identity` não importam uns aos outros).

## Alternativas descartadas

| Alternativa   | Motivo                                                                                           |
|---------------|--------------------------------------------------------------------------------------------------|
| Microserviços | Um desenvolvedor, um banco, um servidor; a separação que importa é de processos e de privilégios |

## Consequências

- As fronteiras entre módulos não são de rede: quem as sustenta são os contratos do import-linter, que precisam continuar rodando no CI.
- Os subdomínios recebem estilos diferentes: o core (`processing`, `runs`) tem modelo de domínio e testes mais rigorosos, o supporting usa transaction scripts com a Session direto e o generic (`identity`) é comprado pronto.
- Os módulos recebem `owner_id` do entrypoint, já autenticado, em vez de dependerem de `identity`; `files` não conhece execuções, e `runs` registra e libera o uso dos objetos com `hold` e `release`.
- Há uma exceção pragmática, registrada no ADR 006: a publicação grava a Run, os `RunOutput` e os `Artifact` na mesma transação curta.
- Durante um deploy, processos de versões diferentes convivem por alguns instantes, então toda mudança de schema é feita em duas etapas (*expand/contract*, [3.8](../GoblinShiftPlan.md#s3-8)).

## Como verificar

- `uv run lint-imports` passa no CI (passo "Contratos de arquitetura (import-linter)" de `.github/workflows/ci.yml`). Em `pyproject.toml`, `[tool.importlinter]` já tem os contratos `layers` e `forbidden`, mas o `forbidden` ainda não lista `authlib`, que o plano exige; o contrato `independence` entre `catalog`, `files` e `identity` ainda não consta. Os dois precisam estar no `pyproject.toml` o mais tardar na fatia 6, quando `identity` e o Authlib entram.
- Um teste confirma que a imagem do executor não consegue importar `sqlalchemy` (passa a existir com a imagem do executor, fatia 4).
- O Compose sobe `worker` a partir da fatia 3 e `maintenance` a partir da fatia 4 ([3.9](../GoblinShiftPlan.md#s3-9)), como serviços separados de `api`; nenhum arquivo Compose monta `/var/run/docker.sock`.
