# ADR 021. Digest da imagem do executor no manifesto, por um `context.json` opcional

- **Estado:** aceito na etapa 0 da fatia 5 (2026-10-14) e implementado na fatia 5, pendente de revisão do usuário no PR da fatia; o arquivo passa a obrigatório num deploy posterior (contract)
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md), [ADR 018](018-pillow-na-imagem-do-executor.md)

## Contexto

O manifesto lista a versão e o digest da imagem que processou a Run (3.6). O executor não sabe em que imagem roda, e o `job.json` não traz o digest. O leitor do `job.json` exige chaves exatas: um executor antigo recusaria uma chave nova. Como a imagem é fixada na Run na aceitação, Runs aceitas antes do deploy continuam rodando na imagem antiga depois dele.

## Decisão

- **O worker grava `context.json` ao lado do `job.json`** com `{"executor_image": "<repo>@sha256:<hex>"}`, a imagem fixada na Run (`local-dev` fora de produção).
- **O executor lê o arquivo se ele existir** (`read_context`). Ausente, `executor_image` é `None`, e o manifesto registra `null`: é a janela de deploy, com um worker anterior à fatia 5. Presente, é lido como o job (JSON estrito, sem chave repetida nem `NaN`, sem seguir symlink, teto de 64 KiB), mas ignora chaves desconhecidas (tolerant reader), para um worker mais novo poder acrescentar campos.
- Num deploy posterior, com todas as Runs antigas terminadas, o arquivo passa a obrigatório (expand/contract).

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Chave nova obrigatória no `job.json` | O executor antigo recusa chaves desconhecidas: Runs aceitas antes do deploy (imagem antiga fixada) falhariam por infraestrutura |
| Digest só nos metadados externos | Contraria 3.6 ("o manifesto lista … versão/digest") |

## Consequências

- Compatível nas duas direções durante o deploy: o executor antigo ignora o arquivo e o worker antigo não o escreve.
- Um manifesto com `executor_image: null` é possível numa janela curta de rollout.
- O `executor-ctl` não precisa mudar: o arquivo fica em `<id>`, que o executor já lê.

## Como verificar

- `tests/processing/test_context_file.py`: arquivo ausente, `null`, `local-dev`, chave desconhecida ignorada, JSON fora do contrato, teto e symlink.
- `tests/runs/test_worker_flow.py`: a preparação grava o contexto com a imagem fixada na Run.
- `tests/packaging/test_manifest.py`: `executor_image` `None` vira `null` no manifesto.
