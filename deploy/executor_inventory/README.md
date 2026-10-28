# Inventário finito para o operador

`reader.collect(provider, authority)` é uma interface interna nova, sem CLI ou endpoint.
Recebe um callback externo já autorizado, que empresta uma conexão PostgreSQL DB-API com
uso exclusivo, autocommit habilitado e nenhuma transação aberta. Não abre conexão,
procura credenciais/DSN em ENV/arquivos, altera autenticação ou fecha a conexão emprestada.
O contexto do provedor é dono da duração/fechamento e deve impor seus prazos de transporte.
O cursor precisa expor a origem das colunas do resultado recebido do servidor via
`pgresult.ftable`, extensão libpq disponível no Psycopg3 já usado pela aplicação.
Ausência de metadata de origem bloqueia a coleta; não pode ser substituída por OIDs
declarados ou pelo resultado de outra consulta. Não é API DB-API genérica para qualquer
driver: essa capacidade adicional faz parte explícita da interface interna.

Não há provedor operacional nem autorização de banco Goblin nesta entrega. O operador 69
fornecerá a capacidade real e os vínculos do ambiente numa passagem própria. Uma instância
temporária de testes não identifica produção. A chamada programática, depois da revisão
do operador, tem apenas esta forma:

```python
report = collect(operator_supplied_borrowed_connection, reviewed_authority)
```

## Entradas obrigatórias

`InventoryAuthority` exige os documentos integrais e hashes SHA-256 da autorização e do
corte, identidade esperada privada de banco/role, schema explícito, OIDs e allowlist exata
das relações/colunas da fonte, commit de release, identidade SHA-256 do consumer e
referência completa imutável do executor. Nenhum campo possui default operacional.
O schema é identificador ASCII simples; nomes privados são UTF-8 válidos de até 63 bytes.

A allowlist é fixa: `run(id,executor_digest,status,phase)` e
`attempt(run_id,ended_at,executor_started_at)`. Não aceita coluna extra, view no lugar
da tabela, OID diferente, schema descoberto por search_path ou autorização vazia/truncada.
As identidades de banco/role são comparadas por parâmetros em projeções booleanas;
nenhum nome de banco/usuário/DSN ou documento de autorização sai no relatório/erro.

O checksum confirma integridade dos bytes fornecidos. **Não autentica o autor nem prova
que o documento autoriza esses bindings.** O provedor/operador deve conferir autenticidade,
escopo, corte e vinculação dos campos antes de emprestar a conexão. O relatório rotula os
bindings como fornecidos pelo operador; não os apresenta como código carregado atestado.

## Recorte e encerramento

O adaptador recusa a conexão antes de qualquer SQL se houver transação herdada ou modo
incompatível. Sua única transação é `REPEATABLE READ READ ONLY`, com search_path restrito
a pg_catalog, `row_security=off`, limites locais de statement/lock/idle de 30/2/35 segundos.
Quando RLS filtraria o resultado, a leitura falha; filtro invisível não vira lista vazia.
Retém `ACCESS SHARE` nas duas relações antes de conferir OIDs, impedindo rename/troca
de tabela e TRUNCATE durante o recorte, com o mesmo lock_timeout finito. Esses são locks
de leitura, sem lock novo de aceitação. Somente depois de comparar identidades/modo/OIDs
a consulta lê as tabelas qualificadas.
Conserva o mesmo snapshot/conexão para todas as contagens e agrupamentos.

Uma troca de schema pode ocorrer apesar do lock da tabela e não pode ser validada pelo
catálogo antigo do snapshot. Por isso, a mesma consulta materializa as fontes uma vez e
devolve duas projeções sempre NULL provenientes dessas mesmas CTEs. Os OIDs de origem
dessas colunas no RowDescription do servidor são comparados aos OIDs esperados antes de
aceitar qualquer inventário. Isso vincula as relações realmente consultadas inclusive
quando vazias; não retorna IDs de Run/Attempt. O comportamento é apoiado pela
[documentação libpq/PQftable](https://www.postgresql.org/docs/18/libpq-exec.html#LIBPQ-PQFTABLE)
e foi exercitado em PostgreSQL18.6 com troca concorrente de schema/tabelas e TRUNCATE.

Inclui todas as Runs retidas, todos os estados/fases, queued sem Attempt e todas as
Attempts vinculadas por run_id, inclusive encerradas, sem início físico ou lease vencido.
Não inventa digest em Attempt. Ref completa distinta permanece distinta mesmo quando
o hash da imagem coincide. A consulta conta referências/estados inválidos e falhas de
junção sem publicar o texto inválido. Divergência, órfã, múltiplas Attempts não encerradas
ou mais de 4096 grupos referência/estado bloqueiam a exportação integral; nunca há página
parcial promovida a ALL. A saída total é limitada a 8 MiB. Erros têm códigos fixos sem
mensagem de driver, SQL privado ou exceção do provedor.

O resultado só é entregue após rollback bem-sucedido da própria transação. Erro/timeout
também faz rollback; transação herdada nunca recebe rollback. Falha no rollback impede
resultado e cabe ao dono da conexão tratar seu recurso. Não há retry, escrita de dados,
manutenção, FOR UPDATE, SKIP LOCKED ou lock novo de aceitação. Os prazos/tetos são limites
do adaptador; custo no host real ainda depende da revisão do operador.
Um provedor que suprime exceções não transforma coleta interrompida em sucesso. Mesmo
se o provedor lançar InventoryError com texto próprio, apenas códigos fixos permitidos
podem sair dessa fronteira.

## Significado da resposta

- `snapshot_valid` e `retained_database_all_complete`: verdadeiro apenas para o recorte
  de tabelas/identidades conferidas, reconciliado e encerrado sem erro.
- `all_complete`, `origin_refs_verified`, `active_reader_verified` e `seed_ready`:
  **sempre falsos nesta API**. Não existe função para gravar `{"allowed": [...]}`.
- `operator_supplied_bindings`: hashes dos recibos e vínculos explícitos do operador.
- `executed_sql_source_sha256`: hash dos mesmos bytes SQL que a chamada consumiu, sem
  alegar hash de código Python carregado ou autenticidade de sua origem.

O banco retido não cobre registros removidos pela retenção, referências de releases e
retornos sem Run, aprovação de origem por referência, compatibilidade de retorno ou
leitura da política pelo daemon real. Snapshot MVCC também não bloqueia novas aceitações.
ALL aplicável à migração requer a união administrativa completa e corte/frescura até a
transição sob coordenação do 69. Ausência de qualquer prova mantém seed bloqueado;
não copiar a política legada sob pai UID1001 nem inferir origem de GET/start/HTTP200.

## Verificação e fronteiras

Os novos testes usam PostgreSQL real para semântica SQL, estados, reconciliação,
concorrência, RLS, identidade, timeout e rollback. Fixtures são somente dados sintéticos.
O PostgreSQL local temporário não usa Docker, não escuta TCP e é removido ao encerrar.
No CI, os mesmos testes usam a fixture PostgreSQL já existente do projeto.

A observação opcional do leitor em `executor_ctl` é evidência de uma decisão sobre bytes
e descritores específicos. Ainda exige sink administrativo/identidade da instância
reais fornecidos e validados pelo 69. Não promove estes flags a verdadeiros, não inicia
container extra e não substitui inventário/origem/rollback. Nenhuma instalação, coleta
de produção ou publicação é autorizada pela existência desta interface.
