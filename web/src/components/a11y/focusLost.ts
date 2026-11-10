/**
 * `true` quando o foco não está em nenhum controle: no `<body>`, em nada, ou num elemento que saiu
 * da página. É o que sobra quando o botão focado some numa releitura da lista (WCAG 2.4.3).
 */
export function focusIsLost(): boolean {
  const active = document.activeElement;
  return active === null || active === document.body || !active.isConnected;
}
