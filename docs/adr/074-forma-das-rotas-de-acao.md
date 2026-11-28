# ADR 074. Uma forma para as rotas de ação

- **Estado:** aceito (2026-11-28, decisão D4 (a) do dono no plano `docs/superpowers/plans/2026-11-21-contrato-e-front.md`, Tarefa 3)
- **Plano:** [1.6](../GoblinShiftPlan.md#s1-6) (contrato da API)
- **Relacionado:** [ADR 029](029-aceite-versionado-dos-termos.md) (aceite dos termos), [ADR 061](061-contrato-v1-so-aditivo-no-ci.md) (contrato só aditivo), issue #442 (item 65b)

## Contexto

A `/api/v1` tinha duas rotas `POST` de ação, cada uma numa forma: `POST /runs/{run_id}/cancel`, com barra, e `POST /me/terms:accept`, com dois-pontos (estilo AIP-136 do Google). Duas formas obrigam quem escreve cliente a adivinhar a próxima, e o `:` em caminho exige escape em alguns roteadores e geradores.

O `cancel` é usado por clientes com chave de API. O aceite dos termos só pela sessão web (ADR 029), ou seja, só pela SPA.

## Alternativas

| Decisão | Alternativa | Escolha |
|---|---|---|
| D4. Forma única das rotas de ação | (a) barra: `POST <recurso>/<verbo>`; `:accept` vira alias `deprecated`; (b) dois-pontos: `cancel` ganha alias `:cancel`; (c) deixar como está | **(a)** |

A forma com barra fica porque é a da rota que clientes externos já usam: mudar o aceite só afeta a SPA, que muda junto no mesmo PR.

## Decisão

- Ação sobre um recurso é `POST <recurso>/<verbo>`, com o verbo em inglês minúsculo como último segmento (`/runs/{run_id}/cancel`, `/me/terms/accept`).
- Nenhuma rota que não seja `deprecated` tem `:` no caminho. `tests/test_openapi_export.py` (`test_rotas_nao_deprecated_nao_usam_dois_pontos`) trava a regra na spec.
- `POST /api/v1/me/terms/accept` (`operationId: acceptCurrentTerms`) é a rota do aceite. `POST /api/v1/me/terms:accept` continua, com o mesmo `operationId` de antes (`acceptTerms`), a mesma função, o mesmo corpo e as mesmas respostas, agora `deprecated: true`. As duas estão na isenção do bloqueio por termos (`_TERMS_EXEMPT_ROUTES`) e na matriz de autorização como rotas só de sessão.
- O alias fica até a `/api/v2`, sem `Sunset`.

## Consequências

- O `oasdiff breaking` (ADR 061) não acusa nada: rota nova e rota marcada `deprecated` são aditivas. Na v1.37.0 fixada, `endpoint-deprecated` sai como INFO, e `api-deprecated-sunset-missing` não dispara sem prazo de depreciação configurado, então nenhuma entrada em `docs/api/quebras-aceitas.txt` é necessária (o ADR 061 previa uma).
- A SPA (`web/src/account/api.ts`) e a API falsa do e2e passam ao caminho com barra.
- Rota de ação nova segue a forma com barra; o teste da spec recusa outra.
