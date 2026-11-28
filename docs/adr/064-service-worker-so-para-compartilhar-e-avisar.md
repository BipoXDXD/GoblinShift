# ADR 064. Service worker só para compartilhar e avisar

- **Estado:** Aceito (2026-11-28, plano `paridade-plataforma-e-50mb`, Tarefa 9; D6 = a)
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (Bancada) e [3.6](../GoblinShiftPlan.md#s3-6) (Nginx da SPA)
- **Relacionado:** #564, Tarefas 8 (aviso de término) e 10 (`share_target`)

## Contexto

O "Compartilhar" do celular (Android/Chrome) só entrega arquivos a um PWA instalado com
`share_target` no manifest. O navegador manda os arquivos num `POST` multipart para a URL do
`share_target`, e alguém precisa recebê-los. O aviso de término (Tarefa 8) mostra a notificação
pelo `registration.showNotification`, e o clique nela só chega a um service worker
(`notificationclick`).

Até aqui a SPA não tinha service worker. O `index.html` sai com `Cache-Control: no-cache`, e um
deploy novo vale na próxima navegação.

## Decisão

- **Um service worker fino** (`web/src/sw.ts`, publicado como `/sw.js`, escopo `/`):
  - intercepta só o `POST /compartilhar` da própria origem. Grava os arquivos na Cache API
    (`gs-share`, `web/src/share/inbox.ts`) e responde `303 /compartilhar?id=<id>`;
  - trata `notificationclick`: foca a aba que já mostra `data.url` ou abre uma nova. Um
    `data.url` fora deste site abre a Oficina;
  - não guarda cache de página nem de asset e não responde nenhum outro pedido. O deploy segue
    valendo na próxima navegação, como hoje. `install` chama `skipWaiting` e `activate` chama
    `clients.claim`: a versão nova não tem estado para migrar.
- **Caixa de entrada com prazo e tetos:** cada compartilhamento vale 1 h (`SHARE_TTL_MS`) e é lido
  uma vez só (`takeShared` apaga). Os vencidos são apagados a cada gravação, a cada leitura
  (`peekSharedNames`, `takeShared`) e no `activate` do service worker. Os tetos são os do
  servidor: até 100 arquivos, 50 MiB por arquivo (256 MiB para vídeo) e 512 MiB no total. Fora
  deles nada é gravado; se um `put` falha (cota), o que já foi gravado daquele id é apagado. Nos
  dois casos a resposta é o `303 /?compartilhamento=indisponivel`. Os valores repetem os de
  `files/limits.py`, e `tests/test_share_inbox_limits.py` confere que os dois lados batem. O teto
  por arquivo de 50 MiB depende do #567 (`MAX_FILE_BYTES` sobe de 32 MiB para 50 MiB): até ele
  entrar, o teste do arquivo falha, e por isso o #577 entra depois do #567. Os arquivos ficam só no
  aparelho e só vão ao servidor pelo fluxo normal da Bancada (intenções, cotas, `PUT` do conteúdo),
  depois do login.
- **Build:** o `sw.ts` tem um build próprio do Vite, em IIFE, depois do da página
  (`web/scripts/vite-service-worker.ts`), e sai como `dist/sw.js`, sem hash e sem `import`: como
  entrada do build da página, o código que divide com ela iria para um chunk importado, que um
  service worker clássico não carrega. O
  `tsconfig.sw.json` o confere com `lib: ["ES2023", "WebWorker"]`. O registro
  (`registerServiceWorker`) só roda no build de produção e só onde há `navigator.serviceWorker`.
  A falha no registro não vai ao console.
- **Nginx:** `location = /sw.js` sai sem cache e dá 404 quando o arquivo falta.
  `location = /compartilhar` serve a SPA no `GET`. O `POST` só chega ao Nginx sem service worker
  (primeira visita, registro que falhou): o 405 do estático e o 413 de um corpo acima de 64k vão
  para `303 /?compartilhamento=indisponivel`, e a Oficina explica o motivo. A CSP não muda: sem
  `worker-src`, o navegador usa o `script-src 'self' 'wasm-unsafe-eval'`, que já permite o
  `/sw.js` da própria origem.
- **Modo de exibição (D6 = a):** `"display": "minimal-ui"`. Com `"browser"` o Chrome não instala
  o PWA e o `share_target` não registra. O `minimal-ui` mantém voltar e recarregar, de que o
  vaivém do OAuth e as rotas profundas precisam.

## Ameaça: envio sem consentimento

Qualquer site pode montar um formulário multipart com `action` em `/compartilhar` e enviá-lo do
navegador da pessoa. O service worker não distingue esse POST do `share_target` (o navegador não
marca a origem do formulário de forma confiável), e os arquivos entrariam na caixa de entrada. Se a
Bancada os enviasse sozinha ao abrir, um site de fora subiria arquivos para a conta logada, gastando
cota e criando execuções que a pessoa não pediu.

**Mitigação:** o que vem do compartilhamento nunca dispara envio. A Bancada mostra os nomes em
"Arquivos recebidos" e espera o clique em "Enviar os arquivos recebidos" (ou "Descartar"); só então
os arquivos saem da caixa de entrada e entram no lote, pelo mesmo caminho do seletor de arquivos.
Até a decisão eles ficam na caixa, e recarregar a Bancada não os perde. Os tetos e o prazo da caixa de
entrada limitam o que um POST de fora consegue ocupar no aparelho.

## Alternativas descartadas

- **Workbox ou `vite-plugin-pwa`:** dependência nova e um cache de assets que precisaria de
  estratégia de atualização. Um deploy deixaria de valer na próxima navegação.
- **Receber os arquivos no servidor** (uma rota `POST /compartilhar` na API): seria outra rota de
  upload, fora do fluxo de intenções e cotas, e aceitaria arquivo de quem não tem sessão.
- **`"display": "standalone"`:** aparência de app, mas sem voltar no desktop, e o vaivém do OAuth
  fica menos claro.

## Consequências

- O iOS não suporta `share_target`, e lá o "Compartilhar" não lista o GoblinShift. A página de
  acessibilidade (Tarefa 16) diz isso.
- A Política de Privacidade precisa citar a Cache API do aparelho (Tarefa 17, no PR 6 de
  `privacidade-e-docs`).
- Mudar o `sw.ts` vale na próxima navegação depois do deploy: o navegador confere o `/sw.js`
  (sem cache) a cada navegação.
