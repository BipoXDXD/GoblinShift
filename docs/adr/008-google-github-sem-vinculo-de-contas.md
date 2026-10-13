# ADR 008. Google e GitHub, sem vínculo automático de contas

- **Estado:** aceito
- **Plano:** [1.8](../GoblinShiftPlan.md#s1-8), [2.1](../GoblinShiftPlan.md#s2-1), [3.3](../GoblinShiftPlan.md#s3-3), [3.6](../GoblinShiftPlan.md#s3-6), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D15](../GoblinShiftReferencias.md#d-4)

## Contexto

O serviço é aberto a qualquer conta Google ou GitHub, sem convite ([1.8](../GoblinShiftPlan.md#s1-8)). É preciso identificar o usuário de forma estável, pedir o mínimo aos provedores e não guardar senhas.

## Decisão

Dois provedores via Authlib, ambos com Authorization Code, `state`, PKCE S256 e redirect exato por provedor. O Google usa OpenID Connect (escopo `openid` e `nonce`). O GitHub usa OAuth 2.0, que não é OIDC, sem escopos: o access token serve para uma única chamada a `GET https://api.github.com/user` e é descartado. A identidade é `(issuer, subject)`: `https://accounts.google.com` + `sub` no Google, `https://github.com` + o `id` numérico no GitHub, nunca o `login`, que pode mudar. Não há vínculo automático de contas por e-mail: a mesma pessoa entrando pelos dois provedores tem duas contas. O serviço não gerencia senhas.

## Alternativas descartadas

| Alternativa         | Motivo                                                                                             |
|---------------------|----------------------------------------------------------------------------------------------------|
| Vincular por e-mail | E-mail não prova identidade entre provedores                                                       |
| Senhas próprias     | Não gerenciar senhas: login externo e sessão própria revogável ([2.1](../GoblinShiftPlan.md#s2-1)) |

## Consequências

- Quem entra por Google e por GitHub tem duas contas separadas, cada uma com seus arquivos, histórico e cotas.
- Só se guarda `issuer`, `subject` e um nome de exibição que venha sem escopo extra (o `login` do GitHub). E-mail não é coletado.
- Conta `blocked` não completa o login, e o bloqueio vale para a mesma identidade.
- Nenhum dos provedores deixa o cliente exigir a senha de novo; a autenticação recente prova um login recente no provedor, não a digitação da senha ([3.6](../GoblinShiftPlan.md#s3-6)).
- A autorização dos recursos continua no GoblinShift; o provedor de login não recebe acesso a arquivos do serviço.

## Como verificar

- A tabela `User` tem `UNIQUE (issuer, subject)` e nenhuma coluna de e-mail (vale desde a migração que a cria, o mais tardar na fatia 6).
- Na fatia 6, os testes de login com provedores simulados confirmam: identidade pelo `id` numérico do GitHub e não pelo `login`; dois logins (Google e GitHub) com o mesmo e-mail criam dois usuários; conta `blocked` não completa o login.
- Na fatia 6, os pedidos de autorização enviados aos provedores pedem só o escopo `openid` ao Google e nenhum escopo ao GitHub, e o access token do GitHub não é gravado no banco.
