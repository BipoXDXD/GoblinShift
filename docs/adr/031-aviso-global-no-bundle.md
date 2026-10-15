# ADR 031. Aviso global versionado no bundle da interface

- **Estado:** aceito (2026-10-15), pedido pelas decisões J-11, J-14 e J-15 dos textos jurídicos
- **Relacionado:** [ADR 014](014-proxy-de-borda-caddy.md), [ADR 029](029-aceite-versionado-dos-termos.md)

## Contexto

Os Termos de Uso e o Aviso de Privacidade vão prometer "aviso destacado no site" em três casos: incidente de segurança, encerramento do serviço (30 dias antes) e mudança relevante nos termos (15 dias antes). Não havia como mostrar um aviso em todas as telas, com ou sem login.

| Alternativa | Prós | Contras |
| --- | --- | --- |
| **Arquivo `web/src/notice/notice.json` no repositório, validado por Zod e embutido no bundle** | Publicar é um PR revisado, com histórico no git (DevOps 7); o aviso sai na primeira pintura, junto da moldura, sem layout shift; sem pedido extra, sem nada novo no Nginx nem na VPS | Exige o deploy do CI: uns 10 a 15 min entre o merge e o aviso no ar |
| Arquivo `/aviso.json` servido pelo Nginx de um volume da VPS, lido pela SPA em tempo de execução | Aviso no ar em segundos, sem deploy | Editado por SSH, fora de review e do histórico; chega depois da moldura e empurra o conteúdo (CLS), ou exige segurar a pintura esperando a rede; um pedido a mais em toda carga; mount e regra novos no Nginx e no `serve-dist` |

Para o incidente, a demora do deploy pesa menos do que parece: o incidente que o texto trata é o de dados pessoais (LGPD art. 48), cuja comunicação aos titulares tem prazo de dias úteis, não de minutos. Para queda do serviço, o aviso no site não ajuda, porque o site está fora.

## Decisão

- **O aviso mora em `web/src/notice/notice.json`** (`{"notice": null}` sem aviso), com `id`, `kind` (`incident`, `shutdown` ou `terms-change`), `title`, `body`, `link` opcional (caminho interno e rótulo), `from` e `until` opcional (ISO 8601 com fuso). O Zod recusa campo extra, caractere de controle, texto acima do limite, link externo e `until` que não vem depois de `from`.
- **Publicar é um PR que edita o arquivo.** O teste `siteNotice.test.ts` parseia o arquivo do repositório: um aviso inválido derruba o CI antes do merge. O deploy leva o aviso a todas as telas.
- **Janela semiaberta `[from, until)`**, avaliada com o relógio injetado nas rotas (`now`), o mesmo do Histórico e do Acesso.
- **Só `terms-change` fecha.** O id fechado fica no `localStorage` (`gs-notice-dismissed`), lido e gravado em `try/catch`; sem armazenamento, o aviso volta na próxima carga. Incidente e encerramento não fecham.
- **A faixa fica entre a barra e o conteúdo**, `role="region"` com nome, sem `alert` nem foco automático. Latão (aviso) para encerramento e mudança, brasa (perigo) para incidente, sempre com ícone e o tipo escrito.

## Consequências

- Um aviso de incidente leva o tempo do pipeline até o ar. Se um dia for preciso aviso em minutos (status de queda, por exemplo), o caminho é a segunda alternativa, num ADR que substitua este.
- Fechar vale por navegador, não por conta: quem troca de aparelho vê a mudança de termos de novo.
- O E2E constrói um segundo `dist` com o aviso de `e2e/fixtures/notice.json` (variável `GS_NOTICE_FILE`, lida só pelo `vite.config.ts`) e roda contra ele a matriz de telas e o teste de CLS.

## Como verificar

- `web/src/notice/notice.test.ts`: parse inválido (campo extra, tipo desconhecido, data sem fuso, `until` antes de `from`, link externo, caractere de controle, limites) e a janela nas bordas.
- `web/src/notice/SiteNoticeBanner.test.tsx`: aparece só na janela; fecha só `terms-change`; o fechamento sobrevive à remontagem e a um `localStorage` que lança; axe sem violações.
- `web/e2e/specs/notice.spec.ts` e `layout-shift.spec.ts` com o build do aviso: a faixa em todas as telas da matriz, fechada continua fechada depois de recarregar, e CLS abaixo de 0,05.
