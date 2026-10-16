"""Executor de uma tentativa: `python -m goblinshift.processing.executor <work_dir>`.

É o processo que, a partir da fatia 4, roda dentro do container gVisor (docs/GoblinShiftPlan.md,
3.6); na fatia 3 o `Runner` local o chama em subprocesso. Não usa rede nem banco: tudo o que
entra e sai passa pelo diretório de trabalho.

Diretório de trabalho
---------------------
O worker prepara, o executor só lê:

- `job.json`: objeto JSON estrito (campos desconhecidos, chaves repetidas e `NaN` são recusados)::

    {
      "recipe_id": "csv-janitor",
      "recipe_version": 1,
      "parameters": {...},            # já resolvidos pelo catálogo (defaults aplicados)
      "inputs": [
        {"position": 0,               # 0, 1, 2… na ordem da Run
         "path": "0000.bin",          # um componente, relativo a work_dir/inputs/
         "original_name": "a.csv",    # dado, nunca caminho
         "size": 123,                 # bytes
         "sha256": "<64 hex minúsculos>"}
      ]
    }

- `context.json` (opcional, fatia 5, ADR 021): `{"executor_image": "<repo>@sha256:<hex>",
  "input_upload_ids": ["<uuid>", …]}`, a imagem fixada na Run, só para o manifesto, e o upload de
  cada entrada na ordem das posições, para o `select` do PDF Press (ADR 035; ausente num worker
  anterior). Ausente é a janela de deploy (`null` no manifesto); chaves desconhecidas são
  ignoradas, ao contrário do `job.json`.
- `inputs/`: as entradas, conferidas por tamanho e SHA-256 antes de qualquer processamento.

O executor escreve, sempre com criação exclusiva:

- `out/`: criado pelo executor (ou vazio); recebe as saídas da receita, `report.json` e, por
  último, o pacote comum (fatia 5, ADR 019): `manifest.json` e `<recipe_id>.zip`, gravados por
  `goblinshift.processing.packaging`. Manifesto acima de 8 MiB sai com `REPORT_LIMIT_EXCEEDED`;
  ZIP acima de 512 MiB, com `OUTPUT_LIMIT_EXCEEDED` (código 3, como a falha da receita).
- `progress.jsonl`: uma linha `{"position": n}` por entrada avaliada (aceita ou rejeitada), na
  ordem de conclusão; o worker conta as linhas para `progress.processed`.
- `failure.json`: só na falha determinística, `{"code": "<código de 1.6>", "detail": "…"}`.

`out/report.json` (`schema_version` 1; mudanças só aditivas)::

    {
      "schema_version": 1,
      "recipe_id": "csv-janitor",
      "recipe_version": 1,
      "summary": {"inputs": 3, "ok": 1, "rejected": 1, "duplicates": 1, "outputs": 1},
      "items": [                      # um por entrada, na ordem das entradas
        {"position": 0, "original_name": "a.csv", "status": "ok",
         "outputs": ["a.csv"],        # caminhos relativos a out/ ("images/foto.jpg")
         "counts": {...}, "details": {...},
         "warnings": [{"code": "…", "detail": "…"}]},
        {"position": 1, "original_name": "b.csv", "status": "rejected",
         "code": "CSV_STRUCTURE_INVALID", "reason": "…"},
        {"position": 2, "original_name": "c.csv", "status": "duplicate",
         "duplicate_of": 0}           # Detox: mesmo SHA-256 e tamanho da posição 0
      ],
      "warnings": [],                 # avisos da execução inteira
      "details": {}                   # fatos da execução que não são aviso
    }

`duplicates`, `details` e o item `duplicate` entraram na fatia 5 sem mudar a `schema_version`.

Relatório sem nenhum item `ok` sai com código 0: o worker decide `failed` com `NO_VALID_OUTPUTS`
na publicação (3.5), e o relatório guarda o motivo de cada rejeição.

Códigos de saída do processo (`ExitCode`)
-----------------------------------------
- 0, `SUCCEEDED`: `report.json` escrito, inclusive com rejeições.
- 3, `RECIPE_FAILURE`: falha determinística; o código está em `failure.json`. Não repetir.
- 4, `INFRASTRUCTURE`: job fora do contrato (inclusive mais entradas que o `max_inputs` da
  versão), receita desconhecida, `out/` ocupado ou qualquer exceção inesperada (detalhe só no
  stderr).
- 5, `INTEGRITY`: entrada ausente, que não é arquivo regular, ou com tamanho ou SHA-256
  diferente do job.

Qualquer outro código (o 1 de uma exceção que escapou, morte por sinal) também é infraestrutura.

O pacote não reexporta `main`: o worker importa `report_file` daqui, e `execution` traz o registro
com todas as receitas (e o PIL do Image Forge), que o worker não precisa carregar. O ponto de
entrada é `__main__.py`, que importa `execution` diretamente.
"""
