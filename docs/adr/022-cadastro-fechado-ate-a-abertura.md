# ADR 022. Cadastro fechado até a abertura pública

- **Estado:** aceito na Fase 0 da fatia 6 (2026-10-14), decisão D-2 do plano da fatia; revisto na fatia 8, com a abertura
- **Plano:** [1.8](../GoblinShiftPlan.md#s1-8), [3.6](../GoblinShiftPlan.md#s3-6), [3.9](../GoblinShiftPlan.md#s3-9)
- **Relacionado:** [ADR 008](008-google-github-sem-vinculo-de-contas.md), [ADR 012](012-servico-aberto-com-cotas.md)

## Contexto

O ADR 012 abre o serviço a qualquer conta Google ou GitHub, mas a abertura pública só acontece na fatia 8, com termos revisados, limites por classe de rota e teste de carga. A fatia 6 já traz o login real; sem um freio, qualquer pessoa com conta Google ou GitHub entraria antes da hora.

## Decisão

- `GS_SIGNUP_MODE=closed` é o padrão até a fatia 8. Uma identidade `(issuer, subject)` desconhecida cria a conta com `status = 'blocked'` e o login é recusado com o mesmo erro genérico de qualquer outra recusa (sem enumeração).
- O administrador libera a conta com `gs-admin unblock --user <id>`; `gs-admin list-users --pending` mostra as contas bloqueadas que nunca tiveram sessão.
- `GS_SIGNUP_MODE=open` faz a identidade nova nascer `active`. Abrir na fatia 8 é trocar a variável.
- No Google, o app fica em "Em teste" com usuários de teste, o que fecha o login também do lado do provedor; no GitHub não há equivalente, e o modo fechado basta.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Allowlist de `(issuer, subject)` na configuração | Exige saber o `sub` antes do primeiro login e um deploy por pessoa |
| Cadastro aberto já na fatia 6 | Contraria 3.9: o serviço só abre na fatia 8 |
| Estado novo `pending` em `app_user` | Coluna e CHECK a mais para um estado que só existe até a fatia 8 |

## Consequências

- Até a fatia 8, conta pendente e conta bloqueada por abuso ficam indistinguíveis no banco; o critério "nunca teve sessão" as separa no `list-users`.
- O teste de login do ADR 012 ("identidade desconhecida cria um usuário") vale com `signup_mode=open`; com o padrão, a conta nasce bloqueada.

## Como verificar

- `tests/test_settings_secrets.py`: o padrão é fechado e só `closed` e `open` são aceitos.
- Frente A da fatia 6: `resolve_login` com `signup_open=False` devolve `LoginRefused.PENDING_APPROVAL` para identidade nova, e o callback responde o mesmo redirect genérico.
- Frente C da fatia 6: `gs-admin unblock` libera a conta e o login seguinte completa.
