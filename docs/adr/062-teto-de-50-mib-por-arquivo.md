# ADR 062. Teto de 50 MiB por arquivo comum, mostrado como "50 MB"

- **Estado:** Aceito (2026-11-22, por decisão do dono: teto "50 MB" = `50 * MIB`; D1 = A do plano `paridade-plataforma-e-50mb`)
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7)
- **Relacionado:** [ADR 024](024-livro-razao-de-capacidade.md), [ADR 036](036-media-mill-ffmpeg-minimo-no-executor.md), #442

## Contexto

O teto por arquivo comum era 32 MiB (`files/limits.py` `MAX_FILE_BYTES` e
`catalog/versions.py` `COMMON_MAX_INPUT_BYTES`). Na revisão de paridade com a concorrência, o dono
decidiu em 22/11 subir para "50 MB". Falta fixar a unidade e conferir o que o valor novo toca.

## Decisão

- **Valor:** `MAX_FILE_BYTES = 50 * MIB` = 52.428.800 bytes. `COMMON_MAX_INPUT_BYTES` segue igual a
  ele (o teste do catálogo confere). Vídeo (256 MiB, ADR 036) não muda.
- **Unidades:** a interface mostra "50 MB". `formatBytes` mantém a base 1024 e passa a rotular KB,
  MB e GB (D1 = A), então nenhum arquivo que o sistema operacional mostre com "50 MB" ou menos é
  recusado: no Finder, que usa base 1000, 50 MB = 50.000.000 B, abaixo de 52.428.800 B. A API, o
  OpenAPI e os docs técnicos dizem "50 MiB".
- **Versões de receita:** o teto por arquivo é limite operacional, fora da versão
  ([3.2](../GoblinShiftPlan.md#s3-2)); as v1 publicam o valor novo sem virar v2.
- **Testes:** os literais dos testes são escritos por extenso (`50 * 1024 * 1024`), nunca pela
  constante. `test_arquivo_no_teto_passa_e_um_byte_acima_e_recusado` (API) prende a fronteira.

## Impacto conferido

| Área | Situação com 50 MiB |
|---|---|
| Lote (`MAX_BATCH_BYTES` = 512 MiB) | Fica. Cabem 10 arquivos cheios (500 MiB); o teto de 100 arquivos vale para os menores. |
| Cotas | Ficam: armazenamento 4 GiB por conta e 20 GiB global (`capacity/ledger.py`), entrada de 2 GiB por 24 h (`runs/quotas.py`). A intenção reserva `max(expected_size, 64 KiB)` (ADR 024), então um arquivo maior só reserva mais, dentro dos mesmos tetos. A saída por execução não depende da entrada. |
| Proxy | A location do envio já aceita 256m (`docker/nginx/templates/default.conf.template`, por causa do vídeo); o padrão de 64k de `/api/` não vale para ela. Os Caddy (`deploy/edge/`, `docker/gateway/`) não limitam o corpo. Nada muda. |
| Vídeo e Media Mill | Não mudam (256 MiB). Os tetos de lote do PDF Press (256 MiB) e do Detox (480 MiB) continuam mandando. |
| Executor | 2 GiB de memória (`deploy/executor_ctl/profile.py`). Image Forge limita por pixels (24 MP), o PDF Press abre o documento inteiro, o Detox copia em fluxo e o CSV corta em 8 MiB por tabela; nenhum lê o teto do envio. |
| SLO de execução (`runs/finished.py`, até 10 arquivos e 50 MiB) | Uma execução com um único arquivo no teto passa a entrar no SLO. Vigiar o p95 no OpenObserve depois do deploy. |

## Consequências

- **Vazão:** para caber nos 10 min de `TRANSFER_MAX_DURATION`, 50 MiB pedem média de pelo menos
  52.428.800 / 600 = 87.382 B/s (cerca de 85,3 KiB/s, 700 kbit/s). O piso atual de 32 KiB/s
  depois de 30 s (`TRANSFER_MIN_RATE_BYTES_PER_S`) deixa passar envios que morrem no prazo total;
  o termo proporcional ao `Content-Length` (D2 do mesmo plano) entra num PR próprio.
- **Assinatura de vídeo:** o limiar acima do qual o envio exige a assinatura de vídeo segue
  `MAX_FILE_BYTES` e passa a 50 MiB sozinho.
- Recusa de 1 byte acima do teto: a declaração (`POST /api/v1/uploads`) recebe 422 sem criar
  nada; um envio maior que o declarado recebe 413 e não deixa arquivo no storage.
