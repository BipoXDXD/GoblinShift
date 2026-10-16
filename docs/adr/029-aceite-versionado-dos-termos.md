# ADR 029. Aceite versionado dos termos na conta, com bloqueio na autenticação

- **Estado:** aceito na fatia 8 (2026-10-14), tarefa F8-4 (A5) do plano da fatia 8. A versão vigente continua marcada "rascunho" até a revisão jurídica (F8-5)
- **Plano:** [1.8](../GoblinShiftPlan.md#s1-8), [3.3](../GoblinShiftPlan.md#s3-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 023](023-cadastro-fechado-ate-a-abertura.md), [ADR 026](026-auditoria-sobrevive-a-exclusao.md), [ADR 028](028-limite-de-requisicoes-no-postgresql.md)

## Contexto

1.8 pede termos de uso e aviso de privacidade "publicados e aceitos no primeiro login, com versão e data registradas". A migração 0006 já criou `app_user.terms_version` e `terms_accepted_at`, com um `CHECK` que exige as duas juntas, mas nada as gravava nem as conferia. Quando o texto muda, toda conta precisa aceitar a versão nova antes de continuar usando o serviço.

Há três perguntas: onde guardar o aceite, onde bloquear quem não aceitou e quem pode aceitar.

Onde guardar:

| Alternativa | Prós | Contras |
|---|---|---|
| **Colunas da 0006 na conta + evento `TERMS_ACCEPTED` na auditoria** | Já existem; um `UPDATE` condicional resolve a idempotência e a concorrência; a purga da conta leva o dado junto | A conta guarda só a última versão; o histórico de aceites anteriores fica na auditoria, que guarda 90 dias e não registra a versão |
| Tabela `terms_acceptance (user_id, version, accepted_at)` | Histórico completo de todas as versões aceitas | Duplica as colunas da 0006 (ou exige um *contract* para removê-las); a pergunta "precisa aceitar?" vira consulta com ordenação; o histórico só importa se a revisão jurídica pedir |

Onde bloquear:

| Alternativa | Prós | Contras |
|---|---|---|
| **Dentro de `authenticate`, com uma lista de rotas isentas** | Mesmo ponto do limite de requisições (ADR 028): rota nova já nasce bloqueada; nega por padrão | Uma leitura da conta por requisição autenticada |
| Dependência própria em cada rota | Explícita na assinatura | Rota nova sem a dependência escapa do bloqueio em silêncio |
| Só no callback do login, redirecionando à tela de aceite | Sem custo nas demais rotas | Não cobre chaves de API, sessões abertas antes da versão nova nem clientes que chamam a API direto |

## Decisão

- **Guardar na conta**: `accept_terms` faz `UPDATE app_user SET terms_version = :v, terms_accepted_at = :agora WHERE id = :conta AND terms_version IS DISTINCT FROM :v`. Se nenhuma linha muda, a versão já estava aceita e a data do primeiro aceite é devolvida. De dois aceites simultâneos, um grava e o outro encontra a versão aceita depois do commit do primeiro. Só o aceite novo vira evento `TERMS_ACCEPTED` na auditoria (migração 0008, que só amplia o `CHECK` do tipo de evento).
- **Versão vigente é constante do servidor**: `CURRENT_TERMS_VERSION` em `identity/terms.py` (`"v3-rascunho"` desde 2026-10-15; antes `"v2-rascunho"` e `"v1-rascunho"`), posta em `app.state.terms_version` na subida. Trocar o texto em `docs/legal` sobe essa constante no mesmo PR; `tests/test_legal_texts.py` confere que o cabeçalho e o histórico de versões dos dois textos declaram a mesma versão.
- **Bloqueio em `authenticate`**, depois do limite de requisições: conta do login sem o aceite da versão vigente recebe `403 TERMS_NOT_ACCEPTED` (problem+json, código novo do catálogo) em toda rota autenticada, por sessão ou por chave. Ficam isentas só as rotas que leem a própria conta, aceitam os termos ou reduzem o acesso: `GET /me`, `POST /me/terms:accept`, `DELETE /me`, `POST /auth/logout-all`, `GET /api-keys` e `DELETE /api-keys/{key_id}`. O `POST /auth/logout` não autentica e não passa pelo bloqueio. Recusar os termos nunca impede sair, revogar chaves nem pedir a exclusão da conta.
- **`POST /api/v1/me/terms:accept` só pela sessão web, com CSRF**: o aceite é um ato da pessoa no navegador, não de um script com chave (chave → 403 `FORBIDDEN`). O corpo estrito traz só `version`, a versão que a pessoa leu. Se não for a vigente, a resposta é 409 `STATE_CONFLICT` e nada é gravado. O valor gravado é o do servidor e a conta é sempre a da credencial: campo extra (`user_id`, `owner_id`, `accepted_at`) dá 422 e nada muda. Repetir responde 200 com a mesma data.
- **`GET /me` ganha `terms`**: `current_version`, `accepted_version`, `accepted_at` e `acceptance_required`. A SPA decide por esse campo se mostra a tela de aceite. O callback do login não muda.
- **Contas do operador não aceitam termos**: as criadas pelo `gs-admin` (emissor `urn:goblinshift:admin`, usadas pela aceitação e pelo teste de carga) não passam pelo bloqueio, porque o operador é quem publica os termos. A regra mora em `Account.needs_terms_acceptance`.

## Consequências

- Cada requisição autenticada fora das isentas faz mais uma leitura da conta pela chave primária. O teste de carga (F8-2) mede o custo junto com o do limite de requisições.
- Subir `CURRENT_TERMS_VERSION` bloqueia na hora todas as contas do login, inclusive as chaves de API usadas por scripts, até a pessoa aceitar na interface. É o efeito pedido por 1.8, e o `detail` do 403 diz qual versão aceitar e onde.
- O histórico de versões anteriores aceitas não fica guardado além dos 90 dias da auditoria, e a auditoria não registra qual versão foi aceita. Se a revisão jurídica pedir prova de aceites antigos, a tabela de histórico entra como *expand*, alimentada pelo mesmo `accept_terms`.
- Durante o deploy da 0008, a API anterior não bloqueia nem audita aceites. O `CHECK` ampliado só aceita um valor a mais (*expand*). O downgrade apaga os eventos `TERMS_ACCEPTED`, que o `CHECK` antigo recusaria; a versão aceita continua na conta.
- As páginas `/termos` e `/privacidade` e a tela de aceite ficam para a interface (`fatia-7-telas`), sobre o `GET /me` e o `POST /me/terms:accept`.

## Como verificar

- `tests/identity/test_terms.py`: o aceite grava versão e data; repetir mantém a data do primeiro; uma versão nova exige novo aceite; o aceite de uma conta não muda a outra; a conta do operador não passa pelo aceite; dois aceites simultâneos, com threads e barreira, gravam um só aceite.
- `tests/api/test_terms.py`: a conta sem aceite recebe `403 TERMS_NOT_ACCEPTED`, pela sessão e pela chave, sem dado da conta no corpo; toda rota autenticada da matriz de autorização fora da lista de isentas recusa; `GET /me` mostra a versão a aceitar; o aceite libera as rotas e é auditado uma vez; repetir é idempotente; versão nova bloqueia de novo e exige novo aceite; versão diferente da vigente dá 409; chave, corpo inválido, campo extra com o id de outra conta e CSRF forjado são recusados sem gravar nada; o aceite de uma conta não libera a outra.
- `tests/test_migration_0008.py`: a 0008 aceita o evento e o downgrade o apaga e restaura o `CHECK` antigo.
- `tests/security/authz_matrix.py`: `POST /me/terms:accept` é rota de sessão (chave → 403).
