# Declaração de Acessibilidade do GoblinShift

- **Última revisão:** 2026-11-28
- **Endereço do serviço:** https://goblinshift.dev

## 1. Compromisso e alvo

O GoblinShift tem como alvo a WCAG 2.2 (Diretrizes de Acessibilidade para Conteúdo Web), nível
AA, em todas as telas do site.

**Estado: parcialmente conforme.** O que está abaixo é medido a cada mudança, mas ainda não houve
auditoria externa nem teste manual completo com leitor de tela. Por isso não declaramos
conformidade total.

## 2. O que o CI mede a cada mudança

Cada pull request só entra com estas verificações verdes. Os arquivos estão no repositório público
do projeto.

- `web/e2e/specs/contrast.spec.ts`: o axe com as tags `wcag2a`, `wcag2aa`, `wcag21a`,
  `wcag21aa` e `wcag22aa`, contraste incluído, nos temas claro e escuro, em toda tela da matriz
  `web/e2e/support/screens.ts`.
- `web/e2e/specs/focus.spec.ts`: ordem do Tab e foco visível (critérios 2.4.3, 2.4.7 e 2.4.11),
  com o anel de foco medido na tela.
- `web/e2e/specs/reflow.spec.ts`: reflow a 320 px de largura, sem rolagem lateral (1.4.10).
- `web/e2e/specs/text-spacing.spec.ts`: nenhum texto cortado com o espaçamento de texto ampliado
  (1.4.12).
- `web/e2e/specs/motion.spec.ts`: com movimento reduzido pedido pelo sistema, nenhuma animação e
  nenhuma cena 3D (2.3.3).
- `web/e2e/specs/controls.spec.ts`: tamanho mínimo dos alvos de toque e de clique (2.5.8) e a
  aparência dos controles nos dois temas.
- `web/e2e/specs/layout-shift.spec.ts`: a página não pula enquanto carrega.
- O axe em cada componente, nos testes de unidade (`web/src/test/axe.ts`).
- Os pares de cores da paleta, com o contraste calculado (`web/src/styles/contrast.test.ts`).

## 3. Alternativas oferecidas

- Soltar arquivos na área de envio tem um botão equivalente para escolher os arquivos (2.5.7).
- A ordem das páginas do PDF e dos arquivos pode ser mudada por botões, sem arrastar.
- Todo estado (sucesso, aviso, falha) aparece em texto e com ícone, nunca só pela cor.

## 4. Limitações conhecidas

- A cena 3D da abertura é decorativa e tem texto alternativo; com movimento reduzido, ela nem é
  carregada.
- O PDF não é desenhado na tela: as páginas são escolhidas pelo número.
- A interface só existe em português do Brasil.

## 5. Contato

Encontrou uma barreira de acesso? Escreva para contato@goblinshift.dev, dizendo a tela e o que
aconteceu. Respondemos em até 3 dias úteis, o mesmo prazo das denúncias nos Termos de Uso.
