# ADR 028. Limite de requisições por conta em janela fixa no PostgreSQL

- **Estado:** aceito na fatia 8 (2026-10-14), decisão D-9 do plano da fatia 6 (tarefa F8-1)
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.7](../GoblinShiftPlan.md#s3-7)
- **Relacionado:** [ADR 003](003-sem-redis.md), [ADR 012](012-servico-aberto-com-cotas.md), [ADR 024](024-livro-razao-de-capacidade.md)

## Contexto

1.7 limita as requisições **por conta e por classe de rota**: leituras e polling a 120 por minuto, comandos a 60 e criação de execução a 10. O envio e o download de conteúdo ficam de fora, porque valem as transferências simultâneas. As rotas `/auth/*` têm o limite por IP no Nginx. A fatia 6 só classificou as rotas (`rate_limit.class` no evento amplo) e deixou o mecanismo para a fatia 8 (ADR 024). O ADR 003 exclui o Redis.

O limite precisa valer entre réplicas da API, contar a conta e não o IP, e nunca deixar passar mais do que o limite quando várias requisições chegam juntas.

Alternativas consideradas:

| Alternativa | Prós | Contras |
|---|---|---|
| **Janela fixa no PostgreSQL, um `INSERT … ON CONFLICT DO UPDATE … WHERE` por requisição** | Atômico pela trava da linha, vale entre réplicas, sem serviço novo, testável com o banco real | Uma escrita por requisição limitada; rajada de até 2× na virada do minuto |
| Contador em memória do processo | Sem escrita no banco | Cada réplica tem o seu, o limite multiplica pelo número de réplicas e zera no restart |
| `limit_req` do Nginx | Já existe para `/auth/*` | Não conhece a conta, só o IP: trocar de IP escapa, e o NAT pune contas inocentes |
| Janela deslizante (log de requisições ou duas janelas ponderadas) | Sem a rajada da virada | Uma linha por requisição ou aritmética de aproximação, para um ganho que esta escala não pede |
| Redis | Contadores com TTL nativo | Excluído pelo ADR 003: mais um armazenamento numa VPS só |

## Decisão

- **Tabela `request_window`** (migração 0007), com a chave primária `(owner_id, request_class, window_start)` e a coluna `request_count`. Há um `CHECK` com as três classes de `RequestClass` e outro com `request_count > 0`. A FK para `app_user` usa `ON DELETE CASCADE`: a linha é dado efêmero, a purga da conta a leva junto, sem etapa própria.
- **Janela fixa de um minuto em UTC**, a partir do relógio da aplicação (`current_time`, injetável nos testes).
- **Contagem atômica**: `INSERT … VALUES (…, 1) ON CONFLICT (pk) DO UPDATE SET request_count = request_count + 1 WHERE request_count < :limite RETURNING request_count`. Sem linha devolvida, a requisição é recusada. A disputa pela mesma janela, inclusive pela primeira inserção, é serializada pelo próprio PostgreSQL, sem "ler e depois gravar" no código.
- **Transação própria e curta**, logo depois da autenticação: a requisição conta mesmo que falhe depois, inclusive com 4xx.
- **Ponto único na borda**: a contagem fica dentro de `authenticate`, a dependência pela qual passa toda rota autenticada. Assim nenhuma rota nova escapa do limite por esquecimento. A classe vem de `rate_limit_class(método, template)`, a mesma função da telemetria. O template completo da rota vem de `fastapi.routing.iter_route_contexts`, porque o `scope["route"]` traz só o trecho do roteador mais interno. `TRANSFER` não conta.
- **Resposta**: `429 RATE_LIMITED` em problem+json, com `Retry-After` igual ao tempo até o fim da janela, em segundos inteiros arredondados para cima, nunca 0. O evento amplo ganha `rate_limit.limited` (verdadeiro ou falso) em toda requisição autenticada que passou pelo limite.
- **Requisição sem credencial não conta**: ela não tem conta, termina no 401 e não grava nada.
- **Limpeza**: o ciclo de contas da manutenção apaga em lotes (`FOR UPDATE SKIP LOCKED`) as janelas anteriores à janela anterior. A anterior fica, porque uma réplica com o relógio um pouco atrasado ainda conta nela. O evento do ciclo registra `request_windows_removed` e o tamanho que sobrou, `request_windows`.
- **Sem índice em `window_start`**: a tabela guarda só as janelas dos últimos minutos de cada conta ativa, e a limpeza lê poucas linhas. Um índice entra só se a consulta medida pedir (regra SQL 9).

## Consequências

- Cada requisição autenticada fora das transferências custa mais uma escrita curta no banco. O teste de carga da fatia 8 (F8-2) mede esse custo contra os SLOs.
- Na virada do minuto, uma conta pode fazer até o dobro do limite em poucos segundos. O limite existe para proteger a VPS de uma conta só, não para medir vazão exata.
- Relógios de réplicas diferentes podem discordar em alguns milissegundos. Na fronteira, uma requisição pode cair na janela vizinha, e isso não muda o teto de cada janela.
- Durante o deploy da 0007, a API anterior não conta requisições. A tabela nova é ignorada por ela (*expand*).
- Requisições sem credencial ficam sem limite por conta. O 401 sai antes de qualquer trabalho caro, e o abuso por IP fica com o Nginx.

## Como verificar

- `tests/identity/test_rate_limit.py`: a requisição de número `limite` passa e a seguinte é recusada, nas três classes; o `retry_after` é o tempo exato até o fim da janela; a janela é o minuto em UTC; contas e classes são independentes; requisições simultâneas, com threads e barreira, não passam do limite, nem na fronteira nem na janela nova; a limpeza respeita a janela anterior e o lote; o banco recusa classe desconhecida e contagem não positiva.
- `tests/api/test_rate_limit.py`: a 121ª leitura, o 61º comando e a 11ª criação de execução recebem `429 RATE_LIMITED` com `Retry-After`; a conta continua limitada vinda de outro IP; outra conta no mesmo IP não é afetada; o `Retry-After` segue o relógio injetado e a conta volta a ser atendida depois dele; o envio de conteúdo e a requisição sem credencial não contam.
- `tests/api/test_wide_event.py`: `rate_limit.limited` é falso na requisição atendida, verdadeiro com `error.code = RATE_LIMITED` na recusada, e ausente sem credencial.
- `tests/runs/test_maintenance_identity.py`: o ciclo de contas remove as janelas velhas e registra o tamanho; a purga da conta não deixa janela para trás.
- `tests/test_schema_constraints.py`: a migração 0007 bate com o modelo e é reversível.

## Atualização de 2026-11-27 (#442)

A mutação recusada pelo CSRF passou a contar no limite (item 46b): a contagem veio para antes do CSRF. `authenticate` identifica a credencial sem conferir o CSRF e sem gravar o uso da sessão, conta a requisição na classe da rota, confere o CSRF na mutação pela sessão e só então registra o uso da sessão (`record_session_use`). Antes, o CSRF vinha primeiro e a recusa não contava: uma página de outro site, ou um subdomínio irmão (`Sec-Fetch-Site: same-site`), podia mandar o navegador repetir mutações sem limite, e cada recusa estendia a inatividade da sessão.

- A mutação com CSRF inválido conta como `COMMAND` (ou `RUN_CREATE`). Depois do limite, a resposta é `429 RATE_LIMITED`, não mais `403 CSRF_FAILED`.
- Um site hostil consegue gastar o limite de comandos da conta durante um minuto. O custo é o mesmo de quem tem a credencial, e o limite existe para proteger a VPS. A recusa não estende a sessão e não chega aos serviços.
- Assimetria no evento amplo: o `429` que vem pela sessão é atribuído à conta (`record_principal`), porque o balde é dela; o `403 CSRF_FAILED` não, porque o pedido forjado só leva o cookie da vítima e não prova que veio dela. A recusa do CSRF fica na auditoria.
- Risco aceito: um site irmão same-site consegue esgotar o balde `COMMAND` da vítima com POSTs forjados, já que a recusa do CSRF conta no limite. A vítima fica sem mutações até o fim da janela de um minuto. O risco é baixo enquanto não houver subdomínios de terceiros sob o mesmo site registrável; se passar a haver, revisar esta decisão.
- Verificação: `tests/api/test_csrf.py::test_mutacao_com_csrf_invalido_conta_no_limite_de_comandos` (o 61º POST sem CSRF válido recebe 429) e `::test_post_same_site_recusado_nao_estende_a_sessao`.

## Adendo (issue #442, item 49a): janela por canal

O balde passou a ser por conta, **canal** e classe de rota. O canal é o `Principal.via`: `session` (a sessão web) ou `api_key` (todas as chaves da conta juntas). Antes, uma chave com `files:write` esgotava `COMMAND` com `DELETE /uploads/{id}` e `READ` com `GET /catalog`, e o titular recebia 429 pela sessão em `GET /api-keys`, `DELETE /api-keys/{id}` e `POST /auth/logout-all`: só o `gs-admin` conseguia revogar a chave. Decisão D1 = (C) do plano `sessao-auth-chaves`, aceita pelo dono.

- **Tabela nova `channel_request_window`** (migração 0031), com a chave primária `(owner_id, channel, request_class, window_start)` e um `CHECK` com os dois canais. Não foi `channel` na `request_window` com a PK recriada: a réplica anterior segue servindo depois do `migrate`, e o `INSERT … ON CONFLICT (owner_id, request_class, window_start)` dela feriria o `NOT NULL` e perderia o índice único, um 500 em toda requisição autenticada durante o deploy. Com duas tabelas, cada versão grava na sua; a contagem em curso se perde na troca, o que a janela de um minuto torna irrelevante.
- **Contract adiado**: a `request_window` da 0007 fica, sem escrita do código novo. A limpeza da manutenção esvazia as duas, com um lote só para as duas juntas, e `request_windows` soma as duas. O `DROP TABLE request_window` vem num PR seguinte, depois do deploy.
- **Teto**: o teto da conta no máximo dobra (um balde por canal), qualquer que seja o número de chaves ou sessões. As 5 chaves continuam dividindo um balde, e as sessões da conta também. O limite por credencial (opção A) multiplicaria o teto pelo número de sessões, que não tem limite.
- **Downgrade**: apaga a tabela nova; com janelas gravadas, só roda com `GS_ALLOW_LOSSY_DOWNGRADE=1`.
- Verificação: `tests/api/test_rate_limit.py::test_chave_que_esgota_a_conta_nao_tranca_a_sessao`, `::test_chave_esgotada_nao_bloqueia_a_sessao_da_mesma_conta`, `::test_sessao_esgotada_nao_bloqueia_a_chave`, `::test_sessao_continua_limitada_no_proprio_canal`, `::test_cinco_chaves_dividem_o_mesmo_balde`; `tests/identity/test_rate_limit.py::test_janelas_de_canais_diferentes_sao_independentes` e `::test_limpeza_esvazia_tambem_a_tabela_sem_canal_da_0007`; `tests/test_migration_0031.py::test_insert_da_replica_anterior_continua_valendo_na_tabela_antiga` e `::test_upgrade_downgrade_upgrade_com_a_guarda_das_janelas_por_canal`.
