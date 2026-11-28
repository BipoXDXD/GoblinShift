# ADR 061. Contrato de `/api/v1` só aditivo, barrado no CI com `oasdiff breaking`

- **Estado:** Aceito (2026-11-21, por decisão do dono: D1 = a, D2 = a, D3 = b, plano `contrato-e-front`)
- **Plano:** [1.6](../GoblinShiftPlan.md#s1-6) ("Em `/api/v1` só entram mudanças aditivas")
- **Relacionado:** #442 (item 63)

## Contexto

O plano só admite mudança aditiva em `/api/v1`: mudança incompatível vai para `/api/v2`, e o
cliente tem de tolerar valor novo em enum de saída. Nada no CI conferia isso.
`tests/test_openapi_export.py` só garante que `web/openapi/openapi.json` bate com o código, e o
Schemathesis (`scripts/contract-test.sh`) valida a API contra a spec nova. Tirar um campo da
resposta ou tornar obrigatório um campo de entrada passava nos dois.

## Decisão

- **Ferramenta (D1 = a):** `oasdiff breaking`, da imagem `tufin/oasdiff` fixada por tag e digest
  em `scripts/openapi-breaking.sh` (v1.37.0, a última release estável com mais de duas semanas em
  2026-11-27). O script só olha os caminhos `^/api/v1/` e sai com o código do oasdiff.
- **Comparação:** o job `contract` faz checkout com `fetch-depth: 2` e compara a spec do commit
  com a de `HEAD^1`. No `pull_request`, `HEAD` é o merge sintético e `HEAD^1` é a ponta da `main`;
  no `push` da `main`, `HEAD^1` é a `main` anterior. O passo roda nos dois eventos. Como merge e
  deploy são imediatos, a `main` faz as vezes da spec de produção.
- **Nível que reprova:** `--fail-on ERR`. Com os níveis da v1.37.0 conferidos em `oasdiff checks
  changelog`, e dois ajustes no `--severity-levels` do script:
  - `response-property-enum-value-added` (valor novo em enum de saída) vem como ERR e passa para
    **WARN**, porque o plano manda o cliente tolerar esse valor. Fica no relatório e não reprova.
  - `response-optional-property-removed` (tirar da resposta um campo opcional) vem como INFO e
    passa para **ERR**. É remoção, não acréscimo.
  - `response-optional-property-added` (campo opcional novo na resposta) é INFO e só aparece com
    `oasdiff changelog`.
  - Reprovam, entre outras, `response-required-property-removed`,
    `new-required-request-property`, `request-property-became-required`,
    `request-property-type-changed`, `request-property-max-items-decreased` e
    `request-property-enum-value-removed`.
  - `api-deprecated-sunset-missing` é ERR: marcar uma rota como `deprecated` sem `x-sunset`
    reprova. Quem fizer isso (a Tarefa 3 do plano, com o alias de `/me/terms:accept`) registra a
    entrada nas quebras aceitas, com o ADR da mudança.
- **Isolamento:** a spec nova vem do PR. O contêiner roda com `--network none`, com
  `--allow-external-refs=false` e com os arquivos montados só para leitura.
- **Exceção (D2 = a):** mudança incompatível vai para `/api/v2`. Fora disso, a única saída é uma
  entrada em `docs/api/quebras-aceitas.txt` (o `--err-ignore` do oasdiff), logo abaixo de uma linha
  `# docs/adr/<arquivo>.md` com o ADR próprio que justifica a quebra.
  `tests/test_ci_contrato_compativel.py` confere que o ADR citado existe.
- **Spectral OWASP (D3 = b):** fica fora deste portão. O lint de segurança da spec é um item do
  guarda-chuva #442.

## Alternativas descartadas

- Script Python próprio com um subconjunto de regras: cada regra seria código nosso, e o que
  ficasse de fora passaria em silêncio.
- Snapshot de uma spec "v1 congelada" comparada por igualdade: toda mudança aditiva exigiria
  atualizar o snapshot, e a quebra se esconderia na mesma atualização.
- Label `contrato-incompativel` no PR: o CI teria de ouvir `labeled`, e a aprovação sumiria do
  histórico do git.

## Consequências

- O job `contract` depende do Docker Hub para baixar a imagem. Atualizar o oasdiff é trocar a tag
  e o digest no script, conferir de novo os níveis em `oasdiff checks changelog` e rodar os testes
  com o marker `oasdiff`.
- Entradas antigas em `quebras-aceitas.txt` deixam de casar depois do merge e são limpas à mão.
- A comparação é commit a commit: uma sequência de mudanças aditivas nunca soma uma quebra.
