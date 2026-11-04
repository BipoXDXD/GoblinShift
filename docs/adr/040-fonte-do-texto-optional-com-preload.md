# ADR 040. Fonte do texto com `font-display: optional` e preload

- **Estado:** proposto (2026-11-04), pendente de revisão do usuário no PR
- **Plano:** P9 (CLS abaixo de 0,05, `layout-shift.spec.ts`), D5 (fontes auto-hospedadas, `fonts.css`)
- **Relacionado:** [ADR 031](031-aviso-global-no-bundle.md)

## Contexto

As três famílias (Bricolage no título, Atkinson Hyperlegible Next no texto, Atkinson Mono no código) usavam `font-display: swap`, e só a do título tinha preload. A do texto só era pedida quando o React pintava a primeira tela.

No CI, a `layout-shift.spec.ts` falhou duas vezes, só no celular e só com o aviso global, sempre com CLS 0,13401507080078123. O trace do run 41781291110 mostra a primeira pintura no fallback do Linux (DejaVu Sans, bem mais largo que a Atkinson): o aviso quebrava numa linha a mais (175 px em vez de 151 px) e, quando a Atkinson chegava, o `main` subia 24 px. O mesmo valor se reproduz num contêiner do Playwright com DejaVu, segurando a fonte até depois da primeira pintura. No macOS o fallback tem métricas próximas e a corrida não aparece.

## Decisão

- A Atkinson Next (latin e latin-ext) passa a `font-display: optional`: se não chegar dentro do período de bloqueio (até 100 ms), a página inteira fica no fallback e nunca reflowa na troca. A fonte continua no cache para a próxima carga.
- O arquivo latin da Atkinson Next ganha preload, como o da Bricolage, para chegar antes da primeira pintura no caso comum. `check-budget` exige os dois preloads.
- Título e código seguem com `swap`: a Bricolage já tem preload e a Mono aparece em pouco texto.

## Alternativas descartadas

| Alternativa                               | Motivo                                                                                                                      |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Só o preload, mantendo `swap`             | Reduz a chance, mas segue sendo corrida: com a rede ou a CPU lentas a fonte ainda chega depois da pintura e o conteúdo pula |
| Fallback com `size-adjust` e `*-override` | Um ajuste vale para uma fonte local só (Arial, DejaVu e SF têm métricas diferentes) e não garante a mesma quebra de linha   |
| Reservar a altura do aviso                | Resolve só o aviso; qualquer bloco de texto acima do conteúdo teria o mesmo problema                                        |

## Consequências

- Numa primeira visita com rede lenta, a sessão inteira pode ficar na fonte do sistema (o SPA não recarrega a página). Da segunda carga em diante, a fonte vem do cache.
- O preload compete com o JS inicial por cerca de 33 KB, já contados no teto de fontes latin.

## Como verificar

- `web/e2e/specs/layout-shift.spec.ts`, "fonte do texto chegando depois da primeira pintura": segura a fonte até depois da pintura e do período de bloqueio e responde com a Atkinson Mono, de métricas diferentes de qualquer fallback. Com `swap`, o CLS passa de 0,05.
- `npm run e2e:sabotage -- "fonte do texto"` troca `optional` por `swap` no CSS construído e confirma que a spec cai.
- `web/scripts/check-budget.test.ts` exige os dois preloads.
