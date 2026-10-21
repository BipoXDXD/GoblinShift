# Revisão do design da SPA pelos livros de UI

- **Data:** 2026-10-15
- **Status:** aprovado em conversa, aguardando revisão desta spec.
- **Fontes:** notas em registros locais do responsável:
  - `refactoring-ui.md` (RUI);
  - `practical-ui.md` (PUI);
  - `designing-interfaces.md` (DI);
  - `css-in-depth.md` (CID).
- **Depende de:** PR #42, que coloca as goblins no site. Esta revisão começa depois do merge dele, porque mexe nas mesmas telas.

## Objetivo

Refinar o sistema visual que já existe em `web/src/styles/tokens.css`, sem redesenhar o site. Esta revisão resolve três grupos de pendências:

- os conflitos de papel de cor;
- as divergências entre RUI e PUI;
- os defeitos de CSS e de interação que a leitura apontou.

**Critério de sucesso:** cada decisão abaixo implementada e coberta por teste automático, com os testes de contraste, de classes e de acessibilidade (axe) verdes nos dois temas.

## Fora do escopo

Ficam como estão, por decisão anterior ou desta rodada:

- paleta musgo/latão/pedra/brasa em OKLCH (substituída depois pela Taverna escura, ver nota abaixo);
- cinzas quentes;
- raio único de 8 px;
- fontes Bricolage Grotesque e Atkinson Hyperlegible Next (variável, 200–800);
- pesos 400 e 600;
- escala de tipo;
- alvo de toque de 44 px;
- escala de espaço atual (4–128 px);
- "Carregar mais" por cursor no histórico.

> **Nota (2026-10-15, depois desta spec):** a paleta musgo/latão/pedra foi substituída pela "Taverna escura" ([ADR 032](../../adr/032-paleta-taverna-escura.md)), por decisão do usuário. Onde esta spec diz musgo, leia cobre (a cor do que se clica); latão vira âmbar (foco, aviso e seleção); pedra vira madeira (neutros). A pátina foi para o matiz 190° e a regra "papel de estado nunca na cor interativa" passa a recusar o cobre. O restante das decisões vale como está.

## Decisões

### 1. Cores: sucesso ganha pátina

- **Pátina:** rampa nova `--color-patina-50…950`, em OKLCH, com matiz perto de 175° (verde-azulado de cobre oxidado). O croma é ajustado para não competir com o musgo.
- **Papéis de sucesso:** `--success-bg`, `--success-fg` e o acento de sucesso, quando houver, passam a usar a pátina nos dois temas.
- **Progresso:** `--progress-fill` também usa a pátina (600 no claro, 400 no escuro), porque a barra mostra estado e não é clicável. O trilho continua em pedra, com 3:1 contra o preenchimento.
- **Musgo:** só para elemento interativo (botão primário, link, controle selecionado). Nenhum selo ou texto estático usa musgo. (PUI: cor primária reservada para o que é clicável.)
- **Latão:** continua como acento, foco e aviso. Ele aparece pouco como acento, e todo aviso vem com ícone e texto.
- **Selos de estado** (concluída, falhou, na fila, cancelada): sempre ícone mais texto, nunca só cor (WCAG 1.4.1).
- **Testes:** os pares novos entram no `contrast.test.ts`, com 4,5:1 para texto e 3:1 para ícone e borda, medidos nos dois temas. Um teste de classes recusa `moss` em componente de status, e o `contrast.test.ts` recusa papel de estado (`success-*`, `progress-fill`) apontando para `--color-moss-*`.

### 2. Elevação: 3 níveis mais a rebaixada

- **Níveis:** os tokens `--elevation-1…5` são substituídos por três papéis:
  - `--elevation-card`, para o cartão sobre a página;
  - `--elevation-float`, para menu, popover e tooltip;
  - `--elevation-dialog`, para diálogo e sheet.

  O `--elevation-sunken` fica para campos e poços.
- **Composição:** cada sombra tem duas partes, como no RUI cap. 6: uma grande e suave, outra pequena e escura. A parte pequena some no nível do diálogo.
- **Cartões:** ficam só com sombra, sem borda.
- **Tema escuro:** a elevação vem da superfície mais clara, com sombra mínima. Bastam os dois tokens que já existiam: `--surface` (stone-900) para o cartão e `--surface-raised` (stone-800) para o que flutua sobre ele. A escala `--surface-1/2/3` cogitada no rascunho não foi criada, porque não havia terceiro nível de superfície para pintar.
- **Testes:** o teste de classes aceita só `shadow-card`, `shadow-float`, `shadow-dialog` e `shadow-sunken`.

### 3. Espaço

A escala continua a mesma. A regra de uso do PUI vira convenção, documentada no topo de `tokens.css` e verificada na revisão de código:

- dentro de um grupo, um degrau;
- entre grupos relacionados, um degrau acima;
- entre seções, dois degraus ou mais.

Ela não tem teste automático: julgar qual espaço é intra-grupo é subjetivo, e o teste de classes já trava os valores.

### 4. Correções de CSS (CID)

Cada uma com um teste:

1. **Movimento reduzido:** já está coberto. O reset em `base.css` para o spinner, e `e2e/specs/motion.spec.ts` testa todas as telas. Fica sem mudança.
2. **Tema escuro sem script:** o fundo da página fica certo mesmo se `theme-init.js` falhar. O bloco escuro se repete sob `@media (prefers-color-scheme: dark) { :root:not([data-theme]) }`, e um teste garante que os dois blocos são idênticos. Teste: Playwright com o script bloqueado e `colorScheme: "dark"` confere a cor de fundo do `html`.
3. **Z-index:** escala nomeada só com os dois usos que existem, `--z-menu: 10` (menu de tema) e `--z-skip: 20` (pular para o conteúdo), consumida como `z-(--z-menu)`. O diálogo usa a top layer do `<dialog>` e dispensa z-index. O teste de classes recusa `z-<número>` e `z-(--x)` fora da escala.
4. **Fontes:** `preload` só da woff2 latin da Bricolage (título da primeira tela), injetado no build por um plugin do Vite, porque o nome do arquivo tem hash. O `font-display: swap` continua. Teste: `check-budget` falha se o `dist/index.html` não tiver o preload.
5. **Diálogo aberto:** `html:has(dialog[open])` ganha `overflow: hidden` e `scrollbar-gutter: stable`, sem salto de layout. Teste: Playwright confere o `overflow` do `html` com o diálogo aberto e fechado.
6. **Altura mínima:** a moldura usa `min-h-svh` no lugar de `min-h-screen`. É a unidade "small", recomendada pelo CSS in Depth 2.4.1 porque não causa relayout quando a barra de endereço aparece. O teste de classes recusa `h-screen` e `min-h-screen`.
7. **Fundo escurecido do diálogo:** usa `color-mix(in srgb, var(--overlay) 60%, transparent)`, em sRGB porque o oklch pode desviar o matiz ao misturar com transparente (CSS in Depth 13.1.2), em vez de cor com alfa fixo. O papel `--overlay` é criado nos dois temas: pedra-950 no claro, preto no escuro.

### 5. Interação

1. **Remover arquivo da Bancada:**
   - "Remover" abre um diálogo de confirmação: "Remover a.csv?".
   - Quando houver 2 arquivos ou mais, aparece "Remover todos", que pede confirmação com a contagem: "Remover os 12 arquivos?".
   - O diálogo segue a pirâmide do RUI: o botão destrutivo fica em vermelho só dentro da confirmação, o foco inicial vai para "Cancelar" e Esc cancela.
   - Ao confirmar, o foco volta para a escolha de arquivos, como já acontece hoje.
   - A remoção é só no cliente: aborta o envio em curso e tira o arquivo do lote, como o `remove` atual. O upload órfão expira sozinho no servidor. Por isso "Remover todos" não tem falha parcial.
2. **Executar com erro:** já está feito. O `ErrorSummary` recebe o foco a cada tentativa com erro, e o botão só fica `aria-disabled` durante o envio. Fica sem mudança.

## Testes (resumo)

- **Vitest:**
  - contraste da pátina;
  - classes (sombras, z-index e musgo fora de status);
  - diálogos de remoção (texto, contagem, foco inicial, Esc, foco de retorno);
  - "Remover todos" ausente com 1 arquivo;
  - igualdade dos blocos do tema escuro.
- **Playwright:**
  - travamento da rolagem;
  - tema escuro sem `theme-init.js`;
  - jornada remover 1, remover todos e cancelar;
  - axe sem violações nos dois temas.
- **CI:** o orçamento continua passando com os preloads.

## Entrega

Dois PRs, nesta ordem, depois do #42:

1. **`design-tokens`:** pátina, elevação, z-index e as correções de CSS 1 a 7.
2. **`design-interacao`:** confirmação de remoção e "Remover todos", com o e2e da rolagem travada.

## Riscos

- **Pátina perto do musgo.** A pátina pode ficar próxima demais do musgo em alguns monitores. Mitigação: diferença de matiz de pelo menos 30° e conferência visual nos dois temas antes do merge.
- **Bloco escuro duplicado.** As duas cópias do bloco escuro podem divergir. Mitigação: o teste de igualdade quebra o build na primeira diferença.
