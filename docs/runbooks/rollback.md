# Roteiro de rollback da aplicação

Como voltar a aplicação para a versão anterior quando uma publicação deu errado. Vale para o recurso da aplicação no Coolify (`api`, `worker`, `maintenance`, `migrate`, `nginx`); o rollback do receptor de releases é outro procedimento, descrito em [`deploy/coolify/release/ISOLATION.md`](../../deploy/coolify/release/ISOLATION.md). Base: [plano, 3.8](../GoblinShiftPlan.md#s3-8), [ADR 056](../adr/056-migrate-tolera-banco-a-frente.md) e [ADR 022](../adr/022-backup-externo.md).

## Rollback é um `git revert` na `main`

Não há botão de "voltar versão". O workflow [Coolify release](../../.github/workflows/coolify.yml) só publica o commit que é o HEAD atual da `main`: o `gate.py` (`current_main`, em [`deploy/coolify/release/gate.py`](../../deploy/coolify/release/gate.py)) confere o SHA pedido contra a `main` e recusa qualquer outro com "Superseded main commit; refusing stale deployment". Republicar um commit antigo, pelo `workflow_dispatch` ou à mão no Coolify, não é caminho.

O rollback é, então, um commit novo que desfaz o anterior:

1. Ache o merge que trouxe o defeito (`git log --first-parent main`).
2. Numa branch nova a partir da `main` atualizada, reverta o merge (`git revert -m 1 <sha-do-merge>`) e abra um PR próprio, dizendo no corpo o que está sendo revertido e por quê.
3. O PR passa pela CI e pela revisão como qualquer outro. Depois do merge, a CI da `main` roda e o `coolify.yml` publica o novo HEAD, que é o código anterior.

Enquanto o revert não chega à produção, a versão com defeito continua no ar. Se o defeito for de segurança ou estiver corrompendo dados, avalie parar o serviço afetado no Coolify durante a espera (o `worker`, por exemplo, para não aceitar Runs novas).

## O schema não volta junto

Reverter o código não reverte o banco. As migrations seguem expand/contract ([plano, 3.8](../GoblinShiftPlan.md#s3-8); `tests/test_migrations_contract.py`): uma revisão nova só acrescenta, e o código anterior continua funcionando com o schema mais novo.

- **Banco à frente da imagem.** O `migrate` da imagem anterior encontra o banco numa revisão que não conhece. Pelo ADR 056 ele avisa (WARN no stderr, com a revisão do banco e o head da imagem) e sai com 0 sem migrar, e `api` e `worker` sobem normalmente. Confira o aviso no log: `sudo docker logs --tail 100 goblinshift-migrate-1`.
- **Downgrade em produção é recusado.** Com `GS_ENVIRONMENT=production`, `migrations/env.py` (`_check_production_destination`) recusa todo `alembic downgrade` e qualquer destino diferente do head da imagem, com "em produção o migrate só sobe para o head; descer o schema é restaurar o backup". Não tente contornar.
- **Revisão de contract.** Se a revisão que entrou com o defeito não é expand (remove ou renomeia coluna ou tabela, marcada com `# contract: <motivo>`), o código anterior pode não funcionar com o schema novo. Nesse caso o rollback exige restaurar o banco a partir do backup para uma revisão compatível, com perda do que foi gravado depois do dump. O roteiro de restauração ainda não está escrito; até lá, siga o [ADR 022](../adr/022-backup-externo.md) e a seção "Backup do PostgreSQL (fora da VPS)" do [README](../../README.md#backup-do-postgresql-fora-da-vps), e depois da restauração limpe os arquivos sem linha com `gs-admin orphan-files --delete-orphans --yes` (README, "Arquivos órfãos no storage").

Antes de reverter, confira se o merge trouxe migration: `git show --stat <sha-do-merge> -- migrations/versions/`. Sem migration, o revert é só código.

## O que conferir depois

1. **Prontidão.** `curl -fsS https://goblinshift.dev/readyz` responde 200. O `/readyz` aceita revisão desconhecida do banco (banco à frente), então um 200 aqui não diz que o schema voltou: diz que a API está de pé e enxerga o banco.
2. **Imagem certa.** O receptor só registra sucesso depois de conferir saúde e imagem dos containers ([`deploy/coolify/release/receiver.py`](../../deploy/coolify/release/receiver.py)); confira no Coolify que a publicação do SHA do revert terminou com sucesso.
3. **Migrate.** O log do `goblinshift-migrate-1` mostra o WARN de banco à frente (esperado quando havia migration) ou nada a migrar; nenhum erro.
4. **Alertas.** No OpenObserve, os alertas `gs-causa-*` ([`deploy/openobserve/README.md`](../../deploy/openobserve/README.md)) voltam ao normal no ciclo seguinte (fila cheia, disco, execução presa, exclusões). Um alerta que continua disparando depois do rollback indica que a causa não estava no código revertido.
