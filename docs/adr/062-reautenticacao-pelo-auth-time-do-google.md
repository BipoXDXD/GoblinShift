# ADR 062. Recência da sessão pelo `auth_time` do Google

- **Estado:** aceito (2026-11-22, decisões D1 a D5 do dono no plano `docs/superpowers/plans/2026-11-22-login-e-borda.md`)
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6) (sessão e autenticação recente)
- **Relacionado:** [ADR 008](008-google-github-sem-vinculo-de-contas.md), [ADR 025](025-login-authlib-e-sessao-no-postgres.md), issue #366 (item "Reautenticação silenciosa")

## Contexto

Emitir chave de API (`POST /api-keys`) e excluir a conta (`DELETE /me`) exigem login no provedor há no máximo 10 min (`RECENT_AUTH_WINDOW`, `require_recent_session`). Até aqui, a sessão gravava como `authenticated_at` a hora do callback. Com o navegador já logado no Google (ou no GitHub), "Entre de novo" volta sem pedir senha, e a sessão nova conta como recente: quem pega um navegador destravado exclui a conta ou emite uma chave de longa duração.

A issue pedia `prompt=login` e `max_age`, conferidos pelo `auth_time`. A documentação do Google (consultada em 2026-11-22) diz outra coisa:

- `prompt` aceita só `none`, `consent` e `select_account`, e `max_age` não é parâmetro documentado (<https://developers.google.com/identity/openid-connect/openid-connect>);
- o "security bundle" diz "Google does not support Google Account reauth requests". O `auth_time` só vem no ID token com três condições: o pedido traz `claims={"id_token":{"auth_time":{"essential":true}}}`, o dono liga "Session age claims" em Google Auth Platform → Settings → Advanced Settings, e o app está publicado e verificado (<https://developers.google.com/identity/siwg/security-bundle>).

O GitHub não tem nem `prompt=login` nem `auth_time`.

## Alternativas

| Decisão | Alternativa | Escolha |
|---|---|---|
| D1. O que fazer, já que o Google não força senha | (A) recência pelo `auth_time`; (B) só aceitar o risco; (C) mandar `prompt=login` fora da documentação | **(A)** |
| D2. Reautenticação com `auth_time` velho | (A) recusar com aviso próprio (`reauth_stale`), mantendo a sessão atual; (B) abrir sessão nova não recente | **(A)** |
| D3. Login no Google sem `auth_time` | (A) falha aberta com alerta: vale a hora do callback; (B) falha fechada: sessão não recente | **(A)** |
| D4. Janela do `auth_time` | (A) a mesma `RECENT_AUTH_WINDOW` de 10 min, contada do `auth_time`; (B) constante nova | **(A)** |
| D5. Recusar o login comum com `auth_time` antigo | (A) não recusar; (B) recusar acima de X h | **(A)** |

## Decisão

- Todo pedido de autorização ao Google, com ou sem `reauth=1`, leva `claims={"id_token":{"auth_time":{"essential":true}}}` (`GOOGLE_AUTH_TIME_CLAIMS`). `REAUTH_PROMPT = "select_account"` continua, e nenhum pedido manda `prompt=login` nem `max_age`.
- `google_identity` lê o `auth_time` num modelo estrito (inteiro positivo; string, booleano e fração são recusados como identidade inválida, com o erro genérico de login) e o devolve em `ExternalIdentity.authenticated_at`, em UTC.
- O callback grava `web_session.authenticated_at = min(auth_time, agora)` (`_session_authenticated_at`). Um `auth_time` no futuro, de relógio adiantado, vira `agora`. A regra de `require_recent_session` (`agora - authenticated_at > 10 min` recusa) não muda: uma sessão com `auth_time` de 4 min atrás é recente por mais 6 min.
- Sem `auth_time`, vale a hora do callback, e a API registra `login google sem auth_time` com `alert=google_auth_time_missing`, que dispara o aviso `gs-causa-google-sem-auth-time`.
- Uma reautenticação (`reauth=1`) da mesma conta com `auth_time` fora da janela é recusada: a sessão atual continua, nenhuma sessão nasce, a auditoria grava `REAUTH_REQUIRED` e o navegador vai para `/?login_error=reauth_stale`, onde a SPA explica como sair do Google.
- O login comum aceita `auth_time` de qualquer idade (D5).
- **Teto absoluto.** `start_session` passa a contar o teto de 8 h da abertura da sessão (`now`), não de `authenticated_at`, e recusa só `authenticated_at` no futuro. Antes, um Google logado há mais de 8 h nem abriria sessão. Como todo chamador passava `authenticated_at = now`, nenhuma sessão existente muda e não há migração; a CHECK `absolute_expires_at > authenticated_at` continua valendo.
- **GitHub: risco aceito.** Sem `prompt=login` nem `auth_time`, o login pelo GitHub continua contando como recente. Quem pega um navegador destravado e logado no GitHub pode excluir a conta ou emitir chave nos 10 min seguintes ao login.

## Consequências

- Um login silencioso no Google deixa de abrir `DELETE /me` e a emissão de chave. A pessoa que precisa confirmar sai da conta Google no navegador e entra de novo.
- A proteção depende de um ajuste no console do Google e do app verificado. Sem ele, o comportamento é o anterior, e o alerta mostra que o risco continua.
- `LoginRefused.REAUTH_STALE` e o valor `reauth_stale` de `login_error` são novos; o `REAUTH_REQUIRED` com o `request_id` de um callback quer dizer reautenticação sem senha, não abuso (`docs/runbooks/abuso.md`).

### Passo manual do dono (M1)

Antes do deploy deste ADR: ligar "Session age claims" em Google Auth Platform → Settings → Advanced Settings e confirmar que o app está publicado e verificado. Depois do deploy, um login no Google não pode disparar `gs-causa-google-sem-auth-time`.

## Como verificar

- `tests/api/auth/test_auth_login.py::test_pedido_ao_google_pede_o_auth_time`
- `tests/api/auth/test_auth_callback_google.py::test_sessao_do_google_conta_recencia_pelo_auth_time`, `::test_sem_auth_time_vale_agora_e_alerta`, `::test_auth_time_no_futuro_vira_agora`, `::test_auth_time_com_tipo_errado_e_recusado`
- `tests/identity/test_sessions.py::test_login_antigo_no_provedor_abre_sessao_com_teto_contado_de_agora`
