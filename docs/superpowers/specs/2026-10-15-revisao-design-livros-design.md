# Revisão do design da SPA pelos livros de UI

- **Data:** 2026-10-15
- **Status:** aprovado em conversa, aguardando revisão desta spec.
- **Fontes:** notas em `~/.claude/knowledge/notes/`:
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

- paleta musgo/latão/pedra/brasa em OKLCH;
- cinzas quentes;
- raio único de 8 px;
- fontes Bricolage Grotesque e Atkinson Hyperlegible Next (variável, 200–800);
- pesos 400 e 600;
- escala de tipo;
- alvo de toque de 44 px;
- escala de espaço atual (4–128 px);
- "Carregar mais" por cursor no histórico.

## Decisões

### 1. Cores: sucesso ganha pátina

- **Pátina:** rampa nova `--color-patina-50…950`, em OKLCH, com matiz perto de 175° (verde-azulado de cobre oxidado). O croma é ajustado para não competir com o musgo.
- **Papéis de sucesso:** `--success-bg`, `--success-fg` e o acento de sucesso, quando houver, passam a usar a pátina nos dois temas.
- **Musgo:** só para elemento interativo (botão primário, link, controle selecionado). Nenhum selo ou texto estático usa musgo. (PUI: cor primária reservada para o que é clicável.)
- **Latão:** continua como acento, foco e aviso. Ele aparece pouco como acento, e todo aviso vem com ícone e texto.
- **Selos de estado** (concluída, falhou, na fila, cancelada): sempre ícone mais texto, nunca só cor (WCAG 1.4.1).
- **Testes:** os pares novos entram no `contrast.test.ts`, com 4,5:1 para texto e 3:1 para ícone e borda, medidos nos dois temas. Um teste de classes recusa `moss` em componente de status.

### 2. Elevação: 3 níveis mais a rebaixada

- **Níveis:** os tokens `--elevation-1…5` são substituídos por três papéis:
  - `--elevation-card`, para o cartão sobre a página;
  - `--elevation-float`, para menu, popover e tooltip;
  - `--elevation-dialog`, para diálogo e sheet.

  O `--elevation-sunken` fica para campos e poços.
- **Composição:** cada sombra tem duas partes, como no RUI cap. 6: uma grande e suave, outra pequena e escura. A parte pequena some no nível do diálogo.
- **Cartões:** ficam só com sombra, sem borda.
- **Tema escuro:** a elevação vem da superfície mais clara (`--surface-1/2/3`), com sombra mínima.
- **Testes:** o teste de classes aceita só `shadow-card`, `shadow-float`, `shadow-dialog` e `shadow-sunken`.

### 3. Espaço

A escala continua a mesma. A regra de uso do PUI vira convenção, documentada no topo de `tokens.css` e verificada na revisão de código:

- dentro de um grupo, um degrau;
- entre grupos relacionados, um degrau acima;
- entre seções, dois degraus ou mais.

Ela não tem teste automático: julgar qual espaço é intra-grupo é subjetivo, e o teste de classes já trava os valores.

### 4. Correções de CSS (CID)

Cada uma com um teste:

1. **Movimento reduzido:** spinner e toda animação ou transição decorativa param com `prefers-reduced-motion: reduce`. Teste: Playwright com `reducedMotion: "reduce"` confere `animation-name: none` (ou uma duração de 0) no spinner.
2. **Tema escuro sem script:** o fundo da página fica certo mesmo se `theme-init.js` falhar. O `:root` sem `data-theme` cai em `prefers-color-scheme`. Teste: Playwright com o script bloqueado e `colorScheme: "dark"` confere a cor de fundo do `body`.
3. **Z-index:** escala nomeada (`--z-header`, `--z-float`, `--z-dialog`, `--z-toast`). O teste de classes passa a pegar também `z-(--var)` e recusa z fora da escala.
4. **Fontes:** `preload` das duas woff2 latin no `index.html`, com `crossorigin`, mantendo `font-display: swap`. Teste: `index.html` contém os dois preloads, e o orçamento continua passando.
5. **Diálogo aberto:** a rolagem da página fica travada (`overflow: hidden` no `html` enquanto o diálogo estiver aberto, sem salto de layout, via `scrollbar-gutter: stable`). Teste: Vitest confere a classe no abrir e no fechar.
6. **Altura mínima:** a moldura da página usa `min-h-dvh` no lugar de `min-h-screen`.
7. **Fundo escurecido do diálogo:** usa `color-mix(in oklch, var(--overlay) 60%, transparent)` em vez de cor com alfa fixo. O papel `--overlay` é criado nos dois temas: pedra-950 no claro, preto no escuro.

### 5. Interação

1. **Remover arquivo da Bancada:**
   - "Remover" abre um diálogo de confirmação: "Remover a.csv?".
   - Quando houver 2 arquivos ou mais, aparece "Remover todos", que pede confirmação com a contagem: "Remover os 12 arquivos?".
   - O diálogo segue a pirâmide do RUI: o botão destrutivo fica em vermelho só dentro da confirmação, o foco inicial vai para "Cancelar" e Esc cancela.
   - Ao confirmar, o foco volta para a escolha de arquivos, como já acontece hoje.
   - "Remover todos" remove em lote no cliente e no servidor. Se um item falhar, os demais continuam, e a falha aparece por item.
2. **Executar com erro:** se a validação falhar, o foco vai para um resumo de erros no topo do formulário, com links para cada campo (DI e PUI). O botão continua ativo; o `aria-disabled` só vale durante o envio.

## Testes (resumo)

- **Vitest:**
  - contraste da pátina;
  - classes (sombras, z-index e musgo fora de status);
  - travamento da rolagem;
  - diálogos de remoção (texto, contagem, foco inicial, Esc, foco de retorno);
  - "Remover todos" ausente com 1 arquivo;
  - resumo de erros recebendo o foco.
- **Playwright:**
  - movimento reduzido;
  - tema escuro sem `theme-init.js`;
  - jornada remover 1, remover todos e cancelar;
  - axe sem violações nos dois temas.
- **CI:** o orçamento continua passando com os preloads.

## Entrega

Dois PRs, nesta ordem, depois do #42:

1. **`design-tokens`:** pátina, elevação, z-index e as correções de CSS 1 a 7.
2. **`design-interacao`:** confirmação de remoção, "Remover todos" e foco no resumo de erros.

## Riscos

- **Pátina perto do musgo.** A pátina pode ficar próxima demais do musgo em alguns monitores. Mitigação: diferença de matiz de pelo menos 30° e conferência visual nos dois temas antes do merge.
- **Remoção em lote parcial.** "Remover todos" com falha parcial deixa a lista num estado misto. Mitigação: resultado por item, sem rollback, e o texto diz quantos foram removidos.
