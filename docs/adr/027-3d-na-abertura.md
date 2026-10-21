# ADR 027. Cena 3D na abertura da Oficina, carregada depois do conteúdo

- **Estado:** Aceito (fatia 7, 2026-10-14). As condições 5 (teto do GLB) e 6 (CSP) foram substituídas pelo [ADR 030](030-motor-e-asset-do-3d-da-abertura.md), que também escolhe o motor (three.js, no lugar do `<model-viewer>`) e o modelo.
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (Apresentação), [3.6](../GoblinShiftPlan.md#s3-6) (CSP da SPA)
- **Relacionado:** plano de design web da fatia 7 (D14, P8)

## Contexto

A regra de apresentação do plano (2.2) dizia "oficina 2D em HTML/CSS, com cartões e goblins discretos; sem canvas/engine". O plano de design da fatia 7 recomendou manter isso na v1 (D14, opção 1): usar o modelo 3D a partir de fontes editoriais registradas só como fonte de renders 2D. O motivo era o custo medido:

- **Peso:** o `@google/model-viewer` 4.4, com o three embutido, tem cerca de 289 KB gzip, e um goblin quantizado com textura WebP de 1024 px tem 250 a 450 KB. A home sem 3D fica perto de 400 KB na primeira visita; o 3D quase dobra a página.
- **Celular:** WebGL em GPU fraca esquenta, gasta bateria e disputa o LCP.
- **Acessibilidade:** o canvas é opaco ao leitor de tela; os controles de câmera capturam setas e arrasto; `auto-rotate` é movimento automático (WCAG 2.2.2).
- **CSP:** GLB com Draco, KTX2 ou Meshopt pede decoder em WASM, por padrão baixado de `www.gstatic.com`, rodando num Worker criado de Blob. Isso exigiria `'wasm-unsafe-eval'` e `worker-src blob:`, fora da CSP de 3.6.

Alternativas consideradas:

| Opção | Prós | Contras |
|---|---|---|
| 3D só como fonte de renders 2D (a recomendação) | Página leve, CSP intacta | Não mostra o 3D ao vivo, que é o que o usuário quer no portfólio |
| "Ver em 3D" sob demanda, numa rota ou diálogo próprio | Home leve; 3D só para quem pede | O 3D não aparece na abertura; poucas pessoas clicam |
| **3D na abertura, carregado depois do conteúdo, com imagem estática no lugar** | Impacto visual na primeira tela, com o conteúdo e a ação chegando antes | Todos os custos acima na página mais visitada, contidos pelas condições abaixo |

**O usuário decidiu pela terceira opção, contra a recomendação.** Este ADR registra a decisão e as condições que ela precisa cumprir.

## Decisão

A abertura da Oficina (home) ganha uma cena 3D interativa no lugar da ilustração de destaque, com estas condições, todas obrigatórias:

1. **Carrega depois do conteúdo principal.** O título, as receitas e o resto da página não esperam o 3D. O código do 3D fica num chunk próprio, importado por `import()` só depois do evento `load` da página e de um momento ocioso; nunca entra no JS inicial nem ganha `modulepreload` no `index.html`.
2. **Imagem estática no lugar, que também é o fallback.** O ponto de montagem mostra primeiro o render 2D da cena (AVIF com WebP, `width` e `height` fixos, sem layout shift). Sem WebGL, com erro de carga ou de decodificação, a imagem fica e nada quebra.
3. **Respeita `prefers-reduced-motion`.** Com `reduce`, o 3D nem é carregado: fica a imagem estática. Sem a preferência, todo movimento automático (giro, animação de entrada) para sozinho em até 5 s ou tem botão de pausa visível (WCAG 2.2.2).
4. **Nada essencial dentro do 3D.** Nenhum texto, link, ação ou informação existe só na cena. Ela é decorativa: `aria-hidden`, fora da ordem de Tab, sem capturar teclado nem a rolagem da página no toque.
5. **Orçamento de peso medido no CI.** O `web/scripts/check-budget.ts` roda depois do `vite build` no job `web` e falha se passar dos tetos: chunk do 3D até 300 KB gzip e fora do carregamento inicial, cada modelo GLB até 500 KB, imagem estática até 90 KB. Os tetos do resto da página continuam valendo sem o 3D.
6. **CSP ajustada só no mínimo, nunca com CDN.** O modelo deve usar só `KHR_mesh_quantization` com texturas WebP embutidas, que dispensa decoder e cabe na CSP de 3.6 (o GLB vem por `connect-src 'self'` e as texturas viram `blob:`, já permitido em `img-src`). Se a compressão Draco, KTX2 ou Meshopt for indispensável para caber no orçamento, os decoders ficam auto-hospedados em `/assets/` (apontados por `ModelViewerElement.dracoDecoderLocation` e equivalentes), e a CSP ganha só `'wasm-unsafe-eval'` em `script-src` e `worker-src 'self' blob:`, numa mudança própria e registrada aqui. Nenhum domínio de terceiro entra na CSP.

Nesta fatia **o 3D não é implementado**, porque o asset ainda não existe. Ficam definidos o ponto de montagem (`data-oficina-3d` na home) e o orçamento no CI.

## Consequências

- A regra 2.2 do plano passa a ter uma exceção nomeada: a cena 3D da abertura, nas condições deste ADR.
- Mais uma dependência grande (`@google/model-viewer`, que fixa a versão do three) para manter e atualizar.
- A home fica mais pesada para quem não tem `reduce`: até cerca de 800 KB a mais, carregados depois do conteúdo.
- O teste de CSP do Playwright (frente F6) precisa cobrir a home com o 3D carregado: os estilos do shadow DOM do model-viewer usam folhas construídas, e nenhuma violação de `style-src` pode aparecer.
- A arte 2D da cena continua necessária: é a imagem estática, o fallback e o que aparece com movimento reduzido.

## Compliance

- `web/scripts/check-budget.ts` no job `web` do CI: tetos do chunk do 3D, dos GLB e da imagem estática; falha se o chunk do 3D aparecer no `index.html`.
- Testes de navegador da frente F6: com `emulateMedia({ reducedMotion: "reduce" })` nenhum pedido do chunk do 3D nem do GLB; sem a preferência, `document.getAnimations()` termina em até 5 s; nenhum `securitypolicyviolation`; o 3D fora da ordem de Tab.
- Revisão de código: qualquer mudança na CSP do `docker/nginx/snippets/spa-headers.conf` cita este ADR.
