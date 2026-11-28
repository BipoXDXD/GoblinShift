# ADR 071. Ordem de travas e relógios num lugar só

- **Estado:** aceito (registra decisão já em vigor; issue #442, achado 14 da auditoria de design)
- **Plano:** [3.4](../GoblinShiftPlan.md#s3-4) (concorrência, ordem de locks)
- **Relacionado:** ADR 006, ADR 024, `src/goblinshift/platform/db.py`, `docs/superpowers/plans/2026-11-22-auditoria-de-design.md`

## Contexto

A ordem total de travas está no plano (3.4), mas 13 comentários em 11 arquivos a repetiam em pedaços, e cinco deles não apontavam a fonte. As duas políticas de relógio do projeto (o relógio do banco em `runs` e `files`; o `datetime.now(UTC)` injetável em `identity`, `AccountJobs` e `gs-admin`) não estavam escritas em lugar nenhum. Os relógios do banco moravam em `runs/store.py`, um módulo de domínio, com nomes (`db_now`, `db_clock`) que não diziam o que mediam.

## Decisão

Nada muda no comportamento; este ADR dá um endereço ao que já vale.

- **Ordem total de travas:** Run → Attempt → Upload/Artifact → StoredObject → reserva → pool da conta → pool global, com IDs em ordem dentro de cada grupo. O pool global é a linha mais disputada e vem por último, para ficar preso o menor tempo possível. A tabela por fluxo de 3.4 é a fonte de cada caminho (aceitação, claim, publicação, cancelamento, exclusão de upload, manutenção, `DELETE /me`); cada caminho trava um prefixo ou um trecho dessa ordem, nunca fora dela.
- **Relógio de prazo:** `transaction_now` (o `now()` da transação), usado por `runs` e `files` em toda decisão de prazo: é o mesmo instante em todas as leituras da transação. `db_wall_clock` (`clock_timestamp()`) é lido depois de uma trava, quando a espera por ela conta, como na renovação do lease: o `now()` da transação ficaria parado no começo dela.
- **Relógio injetável:** `datetime.now(UTC)` da aplicação em `identity`, `AccountJobs` e `gs-admin`, sempre recebido por parâmetro (`now`/`clock`) para que o teste fixe o instante.

## Consequências

- Uma mudança na ordem de travas muda este ADR e o plano juntos; os comentários só citam o trecho do seu caminho e apontam para cá.
- Quem precisa da hora do banco importa de `platform/db.py`, e não de um módulo de domínio.
- Não há teste que confira a ordem de travas; fica como achado para a issue guarda-chuva.

## Compliance

- Os comentários que citam ordem de travas apontam "(ADR 071)": `git grep -n -i "ordem d[aeo]s\? \(locks\|travas\)" -- src | grep -v "ADR 071"` sai vazio.
- Os relógios do banco só vêm de `platform/db.py` (`transaction_now`, `db_wall_clock`); `select(func.now())` e `select(func.clock_timestamp())` avulsos em outros módulos são revistos na revisão de código.
