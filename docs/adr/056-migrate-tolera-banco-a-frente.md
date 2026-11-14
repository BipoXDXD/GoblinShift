# ADR 056. O `migrate` da imagem anterior tolera o banco à frente; produção só sobe para o head

- **Estado:** aceito (coordenação, 14/11, por delegação do dono)
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8) (implantação, expand/contract)
- **Relacionado:** issue #442, `migrations/env.py`, `tests/test_migrate_tolerante.py`, `tests/test_migrations_contract.py`, `deploy/coolify/release/ISOLATION.md`

## Contexto

O serviço `migrate` roda `alembic upgrade head` a cada deploy, e `api` e `worker` esperam que ele termine com sucesso. Num rollback do app, a imagem anterior encontra o banco numa revisão que ela não conhece, e o Alembic falha com "Can't locate revision". O rollback trava no `migrate`, embora a política expand/contract garanta que o código anterior tolera o schema mais novo: é por isso que `schema_is_ready` já aceita revisão desconhecida no health check.

Duas outras lacunas: nada impedia um `alembic downgrade` com o ambiente de produção, que apaga colunas sem passar por backup, e nada conferia que uma migration nova respeitasse o expand/contract.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Status quo** | Nada a fazer | Rollback do app trava no `migrate`; downgrade em produção possível; contract sem conferência |
| **B. Wrapper no comando do serviço** (script antes do `alembic`) | Fica fora do Alembic | Muda o `command` do Compose e do JSON do Coolify de produção, que são do dono |
| **C. Tolerância e recusa no `migrations/env.py`, teste estático para o contract** (escolhida) | O comando do serviço não muda; vale para o CLI e para os testes; a decisão fica junto da transação | Usa `get_revision_argument()` do Alembic, API pública, mas acopla a regra ao `env.py` |

## Decisão

Opção C.

- **Banco à frente.** Com o destino igual ao head da imagem, o `env.py` lê as revisões do banco antes de migrar. Uma revisão que a imagem não conhece gera um WARN no stderr, com a revisão do banco e o head da imagem, e o comando sai com 0 sem migrar. Qualquer outro erro do Alembic, inclusive banco inacessível, continua falhando.
- **Produção.** Com `GS_ENVIRONMENT=production`, o único destino aceito é o head da imagem. `downgrade`, `base`, revisões relativas e `stamp` em outra revisão são recusados antes de conectar. Descer o schema em produção é restauração de backup (ADR 022).
- **Transação única.** Todas as revisões pendentes rodam numa transação só, com `statement_timeout` desligado nela: uma falha desfaz o lote inteiro, e o banco fica na revisão em que estava. A leitura das revisões fecha a própria transação implícita antes, para que o `begin_transaction` do Alembic não entre nela.
- **Contract.** `tests/test_migrations_contract.py` varre o `upgrade()` de cada migration e recusa `drop_column`, `drop_table`, `rename_table`, `alter_column(new_column_name=...)` e `op.execute` com DROP/RENAME de coluna ou tabela sem `# contract: <motivo>` na linha ou na linha acima. O `downgrade()` fica de fora.

## Consequências

- Um rollback do app para uma imagem que já tem esta mudança passa pelo `migrate`. Um rollback para uma imagem anterior a ela continua travando no `migrate` e exige restaurar o banco (ver `ISOLATION.md`).
- O WARN é o único sinal de que o schema está à frente da imagem: quem acompanha o rollback deve conferi-lo no log do `migrate`.
- O teste de contract é estático: SQL montado em variável ou f-string escapa dele. A revisão do PR continua valendo.

## Compliance

- `tests/test_migrate_tolerante.py`, contra PostgreSQL real e pelo CLI: banco à frente → saída 0, WARN com as duas revisões e versão inalterada; banco atrás → chega ao head; banco inexistente → saída diferente de 0; `downgrade -1` e `downgrade base` em produção → recusados com o banco intacto; fora de produção, `downgrade` continua permitido.
- `tests/test_migrations_contract.py`: as migrations existentes passam, e cada operação destrutiva sem a marca é recusada.
