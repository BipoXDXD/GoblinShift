# ADR 055. Papéis do banco com menor privilégio: dono só no `migrate`, runtime só com DML

- **Estado:** proposto (issue #366; depende de segredo, senha e ordem de deploy, que são do dono)
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6) (arquivos e isolamento) e retenção do `audit_event` em [1.7](../GoblinShiftPlan.md#s1-7)
- **Relacionado:** issue #366, `deploy/db-roles.sql`, `tests/test_db_roles.py`

## Contexto

Hoje `migrate`, `api`, `worker` e `maintenance` conectam como o mesmo papel, `goblinshift`, criado pela imagem oficial do PostgreSQL com `POSTGRES_USER`. Esse papel é SUPERUSER e dono do esquema. Uma injeção de SQL ou um processo comprometido na API ganharia tudo: DDL, `COPY ... PROGRAM` (execução de comando no container do banco), leitura de qualquer tabela e reescrita do `audit_event`, que é justamente o registro que deveria sobreviver a um incidente.

A aplicação, em runtime, só faz DML: `SELECT`, `INSERT`, `UPDATE` e `DELETE` nas próprias tabelas, mais advisory locks e `LISTEN/NOTIFY`, que não pedem privilégio. DDL só acontece no `migrate`.

O `audit_event` tem um detalhe: a manutenção apaga os eventos depois da retenção de 90 dias ([1.7](../GoblinShiftPlan.md#s1-7); `identity_purge.delete_old_audit_events`), escolhendo o lote com `SELECT ... FOR UPDATE SKIP LOCKED`. O PostgreSQL só aceita `FOR UPDATE` de quem tem UPDATE em pelo menos uma coluna da tabela.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Papel único** (status quo) | Nada a fazer | SUPERUSER exposto à API; `COPY ... PROGRAM` vira execução de comando; o `audit_event` pode ser reescrito ou apagado por quem invadir a API |
| **B. Um papel de runtime para os três serviços, com DELETE no `audit_event`** | Um segredo novo só | A API e o worker também apagariam a trilha de auditoria; a issue pede o contrário |
| **C. Grupo `gs_runtime` sem atributo especial; login `gs_app` (api, worker) sem UPDATE/DELETE no `audit_event`; login `gs_maintenance` com DELETE e UPDATE só na coluna `id`** (escolhida) | Nenhum serviço de runtime tem DDL nem atributo especial; a API e o worker só inserem e leem auditoria; só a manutenção apaga, e ninguém altera o conteúdo de um evento; o código não muda | Dois segredos novos; o script precisa rodar de novo se uma migration recriar o `audit_event` |
| **D. Função `SECURITY DEFINER` de expurgo, com piso de 90 dias, e nenhum DELETE para o runtime** | Nem a manutenção apaga um evento recente | Exige migration e mudança de código; a função vira código privilegiado a revisar. Fica como evolução se a C se mostrar insuficiente |
| **E. Dono do esquema também sem SUPERUSER** (`REASSIGN OWNED` para um papel dono comum) | Tira o SUPERUSER até do `migrate` | Mexe na posse de todos os objetos de uma vez e no papel que a imagem cria; risco maior num único passo. Fica para uma segunda fase |

## Decisão

Opção C, em duas fases. Esta decisão cobre a primeira.

- **Dono.** `goblinshift` continua dono do esquema e só o `migrate` conecta com ele. A posse e o SUPERUSER dele saem numa fase futura (alternativa E), com ADR próprio.
- **Runtime.** `deploy/db-roles.sql` cria `gs_runtime` (NOLOGIN) e os logins `gs_app` e `gs_maintenance`, que herdam dele. Os três ficam com NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION e NOBYPASSRLS. Recebem CONNECT e TEMPORARY no banco, USAGE no esquema `public`, DML nas tabelas e USAGE/SELECT nas sequências. Nada de CREATE no esquema, de TRUNCATE nem de posse de objeto.
- **Versão do esquema.** `alembic_version` é só leitura para o runtime.
- **Auditoria.** `gs_runtime` lê e insere no `audit_event`, sem UPDATE, DELETE ou TRUNCATE. `gs_maintenance` recebe DELETE e `UPDATE (id)`: o `id` é `GENERATED ALWAYS AS IDENTITY`, e o PostgreSQL recusa atribuir outro valor a ele, então o único efeito do privilégio é permitir o `FOR UPDATE` do lote da retenção. Nenhuma coluna com conteúdo do evento é alterável.
- **Migrations futuras.** `ALTER DEFAULT PRIVILEGES` do dono faz tabelas e sequências novas já nascerem com DML para `gs_runtime`. Uma migration que recriar o `audit_event` ou o `alembic_version` volta a dar DML neles: o script precisa rodar de novo depois dela, e a migration deve dizer isso.
- **Execução.** O script é idempotente, roda numa transação e só como o dono do esquema (recusa outro papel). Não tem senha: o dono define com `\password`, que manda só o hash. **Nada no repositório o executa** (workflow, Compose, JSON do Coolify, entrypoint, script de deploy ou migration), e `tests/test_db_roles.py` falha se algum passar a chamá-lo.
- **Troca no deploy.** Num PR seguinte, depois que o dono criar os segredos e rodar o script: `api` e `worker` com `GS_DB_USER=gs_app` e o segredo `db_password_app`, `maintenance` com `GS_DB_USER=gs_maintenance` e `db_password_maintenance`, e o `migrate` como está. Os passos manuais estão na issue #366.

## Consequências

- Uma injeção de SQL na API deixa de dar DDL, `COPY ... PROGRAM`, criação de papel ou reescrita da auditoria. Continua dando DML nas tabelas da aplicação, que é o que a API precisa.
- `tests/test_db_roles.py` aplica o script duas vezes num PostgreSQL real e confirma o que a aplicação faz com os papéis novos: um ciclo completo de Run (conta, upload, fila, claim, executor e conclusão) como `gs_app`, e os ciclos de manutenção e de contas como `gs_maintenance`, incluindo apagar um evento vencido. Também confirma as recusas: UPDATE, DELETE e TRUNCATE no `audit_event` pelo `gs_app`, UPDATE de conteúdo pela manutenção, escrita no `alembic_version` e DDL por qualquer papel de runtime.
- Dois segredos novos para guardar e girar.
- Reverter é só voltar o `GS_DB_USER` e a senha para os de antes. Os papéis podem ficar no banco sem efeito, ou sair com `DROP OWNED BY` e `DROP ROLE`.

## Compliance

- `tests/test_db_roles.py`: nenhum arquivo de automação cita o script; o script não traz senha e é uma transação só; atributos, matriz de privilégios por tabela, privilégio por coluna no `audit_event`, fluxo real da aplicação e recusas, contra PostgreSQL real.
- Quando o PR da troca entrar, o teste de destinos do Compose deve exigir `GS_DB_USER` de runtime nos três serviços e o papel dono só no `migrate`.
