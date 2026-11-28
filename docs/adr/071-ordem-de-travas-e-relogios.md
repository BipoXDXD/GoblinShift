# ADR 071. Ordem de travas e relógios num lugar só

- **Estado:** aceito (registra decisão já em vigor; issue #442, achado 14 da auditoria de design)
- **Plano:** [3.4](../GoblinShiftPlan.md#s3-4) (concorrência, ordem de locks)
- **Relacionado:** issue #442, ADR 006, ADR 024, `src/goblinshift/platform/db.py`

## Contexto

A ordem total de travas está no plano (3.4), mas 13 comentários em 12 arquivos a repetiam em pedaços, e seis deles não apontavam a fonte. As duas políticas de relógio do projeto (o relógio do banco em `runs` e `files`; o `datetime.now(UTC)` injetável em `identity`, `AccountJobs` e nos comandos do `gs-admin` que gravam um instante) não estavam escritas em lugar nenhum. Os relógios do banco moravam em `runs/store.py`, um módulo de domínio, com nomes (`db_now`, `db_clock`) que não diziam o que mediam.

## Decisão

Nada muda no comportamento; este ADR dá um endereço ao que já vale.

- **Ordem total de travas:** Run → Attempt → Upload/Artifact → StoredObject → reserva → pool da conta → pool global, com IDs em ordem dentro de cada grupo. O pool global é a linha mais disputada e vem por último, para ficar preso o menor tempo possível. A tabela por fluxo de 3.4 é a fonte de cada caminho (aceitação, claim, publicação, cancelamento, exclusão de upload, manutenção, `DELETE /me`). Entre as linhas dessa ordem, cada caminho trava um trecho dela, na sequência dela.
- **Travas fora da ordem total, sempre antes dela:** algumas travas não são linhas da cadeia acima e vêm antes de qualquer uma delas no mesmo caminho.
  - **Aceitação** (`runs/acceptance.py`): chave de idempotência (`pg_try_advisory_xact_lock`, sem espera) → preset de origem (`presets.hold_for_run`, `FOR KEY SHARE`), quando há → fila global (`pg_advisory_xact_lock` de chave constante) → nova Run → Uploads (`FOR SHARE`) e usos dos objetos → reserva nos pools (conta e depois global).
  - **Lote de upload** (`files/upload_requests.py`, `files/uploads.py`): chave do lote (`pg_try_advisory_xact_lock`, sem espera) → advisory dos uploads vivos da conta (`pg_advisory_xact_lock`) → pools do `capacity`.
  - **Conta** (`app_user`): a emissão de chave por sessão e a revogação de credenciais (`logout-all`, bloqueio) travam a linha da conta antes de sessões e chaves; o pedido de exclusão (`DELETE /me`, `gs-admin request-deletion`) faz o `UPDATE` da conta e revoga as credenciais na mesma transação, e o cancelamento da Run ativa vem depois do commit, em transações próprias; a purga trava a conta (`FOR UPDATE`) antes de apagar chaves, sessões, capacidade e a conta. A conta nunca é travada depois de uma linha da ordem total.
  - As travas sem espera (`pg_try_advisory_xact_lock`) recusam na hora em vez de esperar, por isso não criam ciclo de deadlock.
- **Relógio de prazo:** `transaction_now` (o `now()` da transação), usado por `runs` e `files` em toda decisão de prazo: é o mesmo instante em todas as leituras da transação. `db_wall_clock` (`clock_timestamp()`) é lido depois de uma trava, quando a espera por ela conta, como na renovação do lease: o `now()` da transação ficaria parado no começo dela.
- **Relógio injetável:** `datetime.now(UTC)` da aplicação em `identity`, `AccountJobs` e `gs-admin`, sempre recebido por parâmetro (`now`/`clock`) para que o teste fixe o instante. Exceção no `gs-admin`: o comando `usage` lê `transaction_now`, porque conta o consumo pela mesma janela que a cota diária de `runs` usa.

## Consequências

- Uma mudança na ordem de travas muda este ADR e o plano juntos; os comentários só citam o trecho do seu caminho e apontam para cá.
- Quem precisa da hora do banco importa de `platform/db.py`, e não de um módulo de domínio.
- Não há teste que confira a ordem de travas; fica como achado para a issue guarda-chuva.

## Compliance

- Os comentários que citam ordem de travas apontam "(ADR 071)": `git grep -n -i "ordem d[aeo]s\? \(locks\|travas\)" -- src | grep -v "ADR 071"` sai vazio.
- Os relógios do banco só vêm de `platform/db.py` (`transaction_now`, `db_wall_clock`); `select(func.now())` e `select(func.clock_timestamp())` avulsos em outros módulos são revistos na revisão de código.
