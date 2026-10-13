# ADR 012. Serviço aberto a qualquer conta Google ou GitHub, com cotas

- **Estado:** aceito
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [1.8](../GoblinShiftPlan.md#s1-8), [3.9](../GoblinShiftPlan.md#s3-9), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** nenhuma; o plano não cita fonte para esta decisão

## Contexto

O GoblinShift é um projeto de portfólio com código público e, ao mesmo tempo, um serviço aberto. Ser aberto torna abuso, custo fixo de hospedagem e privacidade requisitos de produto. A hospedagem tem custo fixo e capacidade finita, então nenhuma conta pode monopolizar o serviço.

## Decisão

Qualquer conta Google (OpenID Connect) ou GitHub (OAuth 2.0) cria um usuário no primeiro login, sem convite, e usa o serviço dentro de cotas. O abuso é contido por limites e não por controle de entrada:

- Uma execução não terminal por conta, cotas diárias (30 execuções e 2 GiB de entrada processada a cada 24 horas), limites por classe de rota e fila global limitada ([1.7](../GoblinShiftPlan.md#s1-7)).
- Recusar cedo: fila cheia ou cota esgotada respondem na aceitação, nunca depois de horas de espera.
- Consumo por conta visível em `GET /me`, com alerta ao administrador quando uma conta ou o ambiente se aproximam dos tetos.
- Conteúdo privado, que expira em 48 horas, sem links públicos de compartilhamento.
- Bloqueio de conta por CLI do administrador, e termos de uso com canal de contato para denúncias.

## Alternativas descartadas

| Alternativa          | Motivo                                                                      |
|----------------------|-----------------------------------------------------------------------------|
| Allowlist ou convite | Decisão de produto: portfólio e serviço aberto, contido por cotas e limites |

## Consequências

- O serviço fica exposto a abuso e custo, e depende de cotas e limites funcionando de fato; o teste de carga da fatia 8 confirma os limites por classe de rota.
- Termos de uso e aviso de privacidade (LGPD) são publicados e aceitos no primeiro login, e os textos passam por revisão jurídica antes da abertura pública.
- A mesma pessoa entrando por Google e por GitHub tem duas contas; não há vínculo automático por e-mail (ADR 008).
- Enquanto a fatia 8 não chega, o serviço não abre ao público: o acesso é por chave de API emitida pelo administrador ([3.9](../GoblinShiftPlan.md#s3-9)).

## Como verificar

- O código de login não tem allowlist nem fluxo de convite: a partir da fatia 6, o teste de login com provedor simulado cria um usuário novo para uma identidade desconhecida; na fatia 8, isso vale em produção, para qualquer conta Google ou GitHub.
- A partir da fatia 6, os testes de segurança e a matriz de autorização (toda rota × dono, outra conta e escopo ausente) passam; o estado da conta (`active` ou `blocked`), as cotas diárias e `DELETE /me` funcionam; e o bloqueio por CLI revoga sessões e chaves, cancela a execução não terminal e impede novos logins da mesma identidade.
- Na fatia 8, o teste de carga confirma os limites por classe de rota, e o roteiro de resposta a abuso foi executado uma vez de ponta a ponta.
- Antes da abertura pública, termos e aviso de privacidade têm revisão jurídica.
