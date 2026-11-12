# ADR 054. Entrada adulterada no storage é falha terminal, com código próprio e alerta

- **Estado:** aceito (coordenação, 12/11, por delegação do dono)
- **Plano:** [3.4](../GoblinShiftPlan.md#s3-4) (trabalho durável), [3.5](../GoblinShiftPlan.md#s3-5) (estados e recuperação, com a linha nova da tabela de transições), [3.6](../GoblinShiftPlan.md#s3-6) (arquivos e isolamento) e [3.7](../GoblinShiftPlan.md#s3-7) (sinais e alertas); catálogo de códigos em [1.6](../GoblinShiftPlan.md#s1-6)
- **Relacionado:** item 9 do backlog, PR #343 (testes que fixam o comportamento anterior), [ADR 047](047-pausa-de-claims-apos-falha-de-banco.md), modelo de ameaças ([T do storage](../seguranca/modelo-de-ameacas.md))

## Contexto

`FileStorage.open` abre com `O_NOFOLLOW`. Um symlink no lugar do objeto levanta `ELOOP`, que vira `ObjectTamperedError`: a chave existe, mas não é um arquivo que o storage poderia ter criado. É evidência de adulteração, não de perda.

Na preparação da Run, `_copy_input` tratava esse erro como qualquer `StorageError` e o convertia em `PreparationError`. A tentativa terminava como falha transitória (`EXECUTOR_UNAVAILABLE`), a Run ia para `running/retry_wait` e cada claim seguinte repetia a mesma falha, até o quinto claim virar `INFRA_POISON`. O resultado: um sinal de segurança aparecia como "falha repetida de infraestrutura", sem alerta, depois de gastar claims que não resolviam nada. Repetir não desfaz um symlink.

O worker não tem o que decidir diferente na segunda vez: a entrada continua adulterada até alguém agir. Entrada ausente (`ObjectMissingError`, que a manutenção também marca) e `OSError` genérica (E/S, permissão) são outra coisa: podem passar sozinhas ou pedir ação do host, e continuam transitórias.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Manter o retry** (comportamento anterior) | Nenhuma mudança | Gasta até 4 claims por Run; termina em `INFRA_POISON`, que esconde a causa; nenhum alerta; a pessoa usuária vê "falhas repetidas de infraestrutura" por algo que é de segurança |
| **B. Terminal, sem alerta** | Para o desperdício; código próprio diz a causa na API | A adulteração só aparece a quem procurar o código no banco; sem sinal, ninguém age |
| **C. Terminal, com código próprio e `alert=`** (escolhida) | Para o desperdício; o código aparece na API; o evento `alert=input_tampered` chega ao span da tentativa e alimenta um alerta; o log não leva caminho do disco | Um código novo no catálogo; o alerta precisa ser aplicado na instância pelo dono |

## Decisão

Opção C.

- `ObjectTamperedError` na cópia da entrada levanta `InputTamperedError` (tipo próprio, só com a posição da entrada), e não `PreparationError`. A decisão não depende do texto da mensagem do storage.
- `Coordinator._drive` conclui a tentativa com `DeterministicFailure(INPUT_TAMPERED)`: a Run termina `failed` com `error_code = INPUT_TAMPERED` na primeira tentativa, a Attempt guarda o mesmo código, os usos reservados são liberados e a área de trabalho é removida como em qualquer desfecho. O executor nunca inicia, então nenhuma tentativa física é gasta.
- O log `entrada adulterada no storage` leva `run_id`, `attempt_id`, `generation`, `attempt.number`, `position` e `alert = input_tampered`. Não leva o caminho no disco nem a mensagem do storage. O evento é distinto de `executor_start_failed`, para não contar no alerta `gs-causa-executor-nao-iniciou`.
- `INPUT_TAMPERED` entra em `WORKER_DETECTED_CODES` e em `TERMINAL_FAILURE_CODES` (o que `DeterministicFailure` aceita), mas **não** em `DETERMINISTIC_CODES`: o `failure.json` é escrito pelo executor, que roda a receita no sandbox, e só o worker, que lê o storage, pode constatar a adulteração.
- A API devolve o código em `error.code` com a mensagem "Um arquivo de entrada não passou na verificação de integridade." (sem citar storage). `error.code` já é `string` no contrato, então o OpenAPI e os tipos do front não mudam (`oasdiff breaking` e `changelog`: "No changes detected").

Por quê: o custo de errar para o lado terminal é uma Run que a pessoa reenvia; o de errar para o lado do retry é esconder uma adulteração e gastar claims. A mudança é reversível (basta voltar a levantar `PreparationError`).

## Consequências

- A Run com entrada adulterada termina em segundos, não depois de quatro claims e das esperas de retentativa.
- `INPUT_TAMPERED` fica **fora do SLO de execução**: nem evento bom nem ruim, nem no numerador nem no denominador. A consulta (`deploy/openobserve/sql/execucao.eventos.sql`) exclui o código. O SLO mede a confiabilidade da plataforma, e a adulteração é entrada maliciosa, como um 4xx. Contada como ruim, quem planta o symlink gastaria o error budget de propósito; contada como boa, maquiaria o SLO. Antes desta mudança a mesma Run terminava em `INFRA_POISON` e contava como ruim: a contabilidade muda de propósito. O sinal de segurança é o alerta abaixo, à parte. Teste: `tests/openobserve/test_sql.py::test_run_com_entrada_adulterada_fica_fora_do_sli_de_execucao`.
- O alerta `gs-causa-entrada-adulterada` (severidade alta, destino `gs-urgente`, um evento no período basta) lê `alert.input_tampered` no span `attempt` (`AlertEventCounter`). Ele existe no catálogo do repositório (`deploy/openobserve/catalog.py` e `sql/causa-entrada-adulterada.sql`), e `tests/openobserve/test_catalog.py` exige um alerta para todo evento `alert=` do log. **Pendente com o dono:** aplicar o catálogo na instância (`openobserve provision`) é passo do dono; este ADR e o PR não o executam, e até lá o alerta não existe fora do repositório.
- A varredura de objetos ausentes da manutenção (`files/missing.py`) usa `lstat`, que não segue o link: antes, um symlink ou diretório no lugar do objeto contava como presente, e a adulteração só aparecia quando alguém abria o arquivo. `FileStorage.stat` passa a levantar `ObjectTamperedError` para o que não é arquivo comum, e a varredura relata o lote com o mesmo evento `alert=input_tampered` (log `objetos adulterados no storage`, chaves lógicas, sem caminho do disco nem alvo do link), contado também em `objects_missing_tampered` no ciclo. O objeto não é marcado `missing` (como a API, que responde 410 sem marcar) e nada no disco é removido. Como o alerta lê o contador no span `main`, o `gs-causa-entrada-adulterada` o vê sem evento novo.
- Adulteração por divergência de hash ou tamanho (`PreparationError("... diverge do hash ou tamanho ...")`) continua transitória. Ela também é evidência de adulteração, mas a causa pode ser uma gravação interrompida; tratá-la como terminal é uma segunda decisão, fora do escopo.
- Os testes do PR #343 que fixam o comportamento anterior (reagenda, log sem alerta) passam a descrever o contrário e precisam ser trocados por estes.

## Compliance

- `tests/openobserve/test_catalog.py` e `tests/openobserve/test_sql.py`: o alerta existe no catálogo, lê o próprio evento e dispara só com `alert.input_tampered > 0` em span `main`; a Run adulterada não muda o numerador nem o denominador do SLI de execução.
- `tests/runs/test_attempt_span.py::test_entrada_adulterada_conta_no_span_sem_virar_start_recusado`: o span `attempt` leva `alert.input_tampered` e não `alert.executor_start_failed`.
- `tests/runs/test_input_tampered.py`, com Postgres real: falha terminal na primeira tentativa sem `retry_wait`; nenhuma tentativa física nem segundo claim; código `INPUT_TAMPERED` na Run e na Attempt; log com `alert=input_tampered`, sem caminho do disco e sem o log `área de trabalho falhou`; área de trabalho limpa; entrada ausente e `OSError` genérica continuam reagendadas e sem alerta.
- `tests/runs/test_lifecycle.py::test_entrada_adulterada_termina_failed_sem_gastar_tentativa_fisica`: a máquina de estados aceita o código e não gasta tentativa.
- `tests/runs/test_failure_code.py::test_executor_nao_declara_entrada_adulterada`: o executor não consegue declarar o código.
- `tests/api/test_runs.py::test_execucao_com_entrada_adulterada_traz_o_codigo_e_texto_sem_detalhe_do_storage`: o código e a mensagem aparecem na API.
- `tests/security/test_threat_model.py`: o modelo de ameaças cita a mitigação e os testes acima.
