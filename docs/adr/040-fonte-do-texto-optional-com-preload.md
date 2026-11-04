# ADR 040. Fonte do texto com `font-display: optional` e preload

- **Estado:** proposto (2026-11-04), pendente de revisão do usuário no PR
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (frontend). Os rótulos P9 (CLS abaixo de 0,05, `web/e2e/specs/layout-shift.spec.ts`) e D5 (fontes auto-hospedadas, `web/src/styles/fonts.css`) são os usados no código; o plano não os define por id.
- **Substitui:** o item "O `font-display: swap` continua" de [revisão de design, 4.4](../superpowers/specs/2026-10-15-revisao-design-livros-design.md), só para a fonte do texto.
- **Relacionado:** [ADR 031](031-aviso-global-no-bundle.md)

## Contexto

As três famílias (Bricolage no título, Atkinson Hyperlegible Next no texto, Atkinson Mono no código) usavam `font-display: swap`, e só a do título tinha preload. A do texto só era pedida quando o React pintava a primeira tela.

No CI, a `layout-shift.spec.ts` falhou duas vezes, só no celular e só com o aviso global, sempre com CLS 0,13401507080078123. O trace do run 41781291110 mostra a primeira pintura no fallback do Linux (DejaVu Sans, bem mais largo que a Atkinson): o aviso quebrava numa linha a mais (175 px em vez de 151 px) e, quando a Atkinson chegava, o `main` subia 24 px. O mesmo valor se reproduz num contêiner do Playwright com DejaVu, segurando a fonte até depois da primeira pintura. No macOS o fallback tem métricas próximas e a corrida não aparece.

A Atkinson Hyperlegible foi escolhida pela legibilidade. Qualquer opção que deixe a pessoa no fallback custa exatamente isso.

## Medições

Teste de `layout-shift.spec.ts`: a fonte é segurada até a tela estar pintada e mais 1 s, e responde com a Atkinson Mono (métricas bem diferentes de qualquer fallback). Build com o aviso, CLS somado até a página assentar, teto 0,05.

| Fonte segurada | Tela | Desktop | Celular |
|---|---|---|---|
| Texto, com `swap` (antes) | Bancada | 0,225 (macOS) | 0,068 (macOS) |
| Texto, com `optional` (depois) | Bancada | abaixo do teto | abaixo do teto |
| Título, com `swap` | Bancada | 0 (macOS e Linux) | 0 (macOS e Linux) |
| Título, com `swap` | Oficina | 0,0014 (macOS), 0,0015 (Linux) | 0,031 (macOS), 0,015 (Linux) |

Linux é o contêiner `mcr.microsoft.com/playwright:v1.64.1-noble` com `fonts-dejavu-core`, o fallback do runner do CI. O título não passa do teto porque os títulos ocupam poucas linhas e o bloco quase não muda de altura na troca. O caso mais apertado é o título de abertura da Oficina no celular, em várias linhas. A Mono (código) não foi medida: aparece em pouco texto e em blocos próprios.

## Decisão

- A Atkinson Next (latin e latin-ext) passa a `font-display: optional`: se não chegar no período de bloqueio, a página fica no fallback e nunca reflowa na troca. A fonte continua no cache para a próxima carga.
- O arquivo latin da Atkinson Next ganha preload, como o da Bricolage, para diminuir a chance de perder o período de bloqueio. `check-budget` exige os dois preloads, com `as="font"`, `type="font/woff2"` e `crossorigin`.
- Título e código seguem em `swap`. O preload sozinho não basta para o texto porque ele continua sendo corrida. O título fica em `swap` não pelo preload, mas pelo valor medido acima, e os testes do título na Oficina e na Bancada ficam como regressão.

## Alternativas

| Alternativa | A favor | Contra |
|---|---|---|
| **Só o preload, mantendo `swap`** | A Atkinson sempre aparece, mesmo tarde | Continua sendo corrida: com rede ou CPU lentas a fonte chega depois da pintura e o conteúdo pula (foi o que o CI pegou) |
| **`swap` + preload + `@font-face` de fallback com `size-adjust`, `ascent-override` e `descent-override` sobre `local()` por plataforma** (Arial/Helvetica, Roboto, Segoe UI, DejaVu) | A Atkinson sempre aparece e a troca mexe pouco | Um conjunto de valores por fonte local, a calibrar e manter; `system-ui` não aceita os overrides; métricas verticais iguais não garantem a mesma largura de linha, então a quebra ainda pode mudar; fonte local fora da lista cai no fallback sem ajuste |
| **`font-display: fallback`** | Período de troca curto (cerca de 3 s): a Atkinson ainda aparece se chegar logo | Dentro desse período a troca acontece e o conteúdo pula do mesmo jeito; só reduz a janela |
| **Preload por cabeçalho `Link` ou Early Hints (103) no Nginx** | O pedido da fonte sai antes do HTML ser lido, o que aumenta a chance de chegar a tempo | Não muda o `font-display`: com `swap` segue sendo corrida; o nome do arquivo tem hash do build, então a configuração do Nginx teria de ser gerada a cada deploy |
| **Reservar a altura do aviso** | Resolve o caso do CI sem mexer nas fontes | Só cobre o aviso; qualquer bloco de texto acima do conteúdo teria o mesmo problema |

## Consequências

- Custo de identidade e de acessibilidade: numa primeira visita com rede lenta, quem perder o período de bloqueio fica a sessão inteira do SPA na fonte do sistema, sem a Atkinson Hyperlegible escolhida pela legibilidade. Da segunda carga em diante a fonte vem do cache. Não medimos com que frequência isso acontece em produção.
- Não verificamos em documentação oficial do Chrome se, com `optional` e preload, a primeira pintura espera a fonte (fala-se em cerca de 100 ms). O ADR não depende disso.
- O preload compete com o JS inicial por cerca de 33 KB, já contados no teto de fontes latin.

## Como verificar

- `web/e2e/specs/layout-shift.spec.ts`: "fonte do texto chegando depois da primeira pintura" (Bancada) e "fonte do título chegando depois da primeira pintura" (Oficina e Bancada).
- `npm run e2e:sabotage -- "fonte do texto"` troca `optional` por `swap` no CSS do `dist-notice` e confirma que a spec cai no desktop e no celular.
- `web/scripts/check-budget.test.ts` exige os dois preloads com `as`, `type` e `crossorigin`.
