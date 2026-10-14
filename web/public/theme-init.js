// Aplica o tema antes da primeira pintura, para a página não piscar no tema errado. É arquivo, e
// não script inline, por causa da CSP (script-src 'self'). Mesma regra de src/theme/theme.ts:
// "light" e "dark" fixam o tema; qualquer outro valor, ou nenhum, segue o sistema.
(() => {
  let preference = null;
  try {
    preference = localStorage.getItem("gs-theme");
  } catch {
    // Armazenamento bloqueado: segue o sistema.
  }
  const dark =
    preference === "dark" ||
    (preference !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
})();
