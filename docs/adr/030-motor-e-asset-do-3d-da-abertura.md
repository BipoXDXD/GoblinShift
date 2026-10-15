# ADR 030. Motor, modelo e arte da cena 3D da abertura e das goblins nos estados

- **Estado:** Aceito (2026-10-15). Substitui em parte o [ADR 027](027-3d-na-abertura.md): a condição 5 (teto do GLB) e a condição 6 (CSP). As condições 1 a 4 do 027 continuam valendo.
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (Apresentação, "sem canvas/engine"), [3.6](../GoblinShiftPlan.md#s3-6) (CSP da SPA)
- **Relacionado:** [ADR 027](027-3d-na-abertura.md); `docs/art/LICENSE-ART.md`; `web/src/assets/art/PROVENANCE.json`

## Contexto

O usuário decidiu ter **3D na abertura** (ADR 027), contra a recomendação de deixar fora da v1, e contra a regra 2.2 do plano ("oficina 2D em HTML/CSS [...] sem canvas/engine"). O 027 fixou as condições, mas não o motor nem o asset, porque a arte não existia. Agora o elenco está fechado em sete goblins aprovadas, e a favorita do usuário, a **Rúbia** (Rodin `e1c79f67`), vai para a cena. As outras entram como imagens 2D nos estados (decisão D6: goblins só nos estados, cena só na home).

O modelo bruto do Rodin tem 30,5 MB: 500 mil triângulos e três texturas PNG de 2048 px (cor, normal, metal/rugosidade). A meta do usuário é a abertura inteira abaixo de 2 a 3 MB.

### Motor

| Opção | Prós | Contras |
|---|---|---|
| `<model-viewer>` 4.4, auto-hospedado | Pronto e mantido; poster, carga preguiçosa e controles de câmera de graça | ~289 KB gzip, o dobro do three sozinho; aponta os decoders Draco e KTX2 para `www.gstatic.com` por padrão (precisa reconfigurar para não ir a CDN); controles de câmera capturam setas e arrasto, e desligá-los tira quase tudo o que ele oferece; estilos em shadow DOM a conferir contra `style-src 'self'` |
| **three.js mínimo** (`WebGLRenderer`, `GLTFLoader`, `RoomEnvironment`) | 162 KB gzip medidos no build, já com o decoder Meshopt embutido; nenhuma URL de terceiro; controle total do que mexe (entrada de 3,5 s, arrasto horizontal, render só sob demanda) e do descarte | ~200 linhas nossas: laço de render, redimensionamento, perda de contexto, descarte de GPU; mais uma dependência grande para atualizar |
| Vídeo ou imagem pré-renderizada (giro em MP4/WebM, ou só o PNG) | Nenhum JS de 3D; funciona em qualquer aparelho; CSP intacta | Não é 3D: nada de girar com o dedo, que é o que o usuário pediu; vídeo em laço é movimento automático além de 5 s (WCAG 2.2.2) ou precisa de botão de pausa; um giro de 3 s em boa qualidade pesa 1 a 2 MB, como o GLB |

### Redução e compressão do modelo

As UVs do Rodin são picotadas em milhares de ilhas pequenas, e isso decide o resto. Medido na Rúbia:

| Opção | Peso | Qualidade | CSP |
|---|---|---|---|
| Decimate do Blender para 30 mil triângulos + quantização + WebP | 912 KB | **Pintas brancas e pretas** no cabelo e na pele: triângulos atravessam as costuras e amostram o vão entre as ilhas | Só `blob:` em `connect-src` |
| Malha de 30 a 50 mil triângulos com UVs novas e textura reassada da original (Cycles, "selected to active") | ~1 MB (estimado) | Pior: manchas pretas onde os raios acertam camadas de cabelo e o rosto perde definição | Só `blob:` em `connect-src` |
| `simplify` do meshoptimizer (para em ~120 mil triângulos, porque respeita as costuras) + quantização + WebP, sem Meshopt | 4,8 MB | Igual à bruta no tamanho exibido | Só `blob:` em `connect-src` |
| **O mesmo, com compressão Meshopt (`EXT_meshopt_compression`)** | **1,87 MB** | **Igual à bruta no tamanho exibido** | `blob:` em `connect-src` e `'wasm-unsafe-eval'` em `script-src` |
| O mesmo, com Draco ou KTX2 | menor | Igual | `'wasm-unsafe-eval'` e `worker-src 'self' blob:` (decoder num Worker de Blob) |

Texturas de 2048 px custam +350 KB sem diferença na tela (a cena aparece com no máximo 320 × 400 px CSS).

## Decisão

1. **Motor: three.js mínimo** (`three@0.186.1`), num chunk próprio (`assets/oficina-3d-*.js`) importado por `import()` depois do `load` e de um momento ocioso (condição 1 do 027). `<model-viewer>` sai porque custa o dobro, chama CDN por padrão e traz controles que o 027 manda desligar; o vídeo sai porque não entrega o 3D que o usuário escolheu.
2. **Modelo: só a Rúbia**, otimizado por `scripts/arte/otimizar-glb.sh`, versionado: `simplify` até onde as costuras deixam (~120 mil triângulos), texturas de 1024 px em WebP, malha quantizada e **compressão Meshopt**: 1,87 MB, sem as pintas da decimação. O decoder Meshopt é o do próprio three (`meshopt_decoder.module.js`), um WASM embutido no chunk e servido da própria origem; ele roda na thread principal, sem Worker. **Sem Draco nem KTX2**, que pediriam Worker de `blob:`.
3. **Quando carrega:** só sem `prefers-reduced-motion: reduce`, sem `Save-Data` e com **WebGL 2 acelerado por GPU**. WebGL em software (SwiftShader, llvmpipe) conta como ausente: medido no Chromium sem GPU, ele trava a thread principal por 2,3 s na montagem e ~70 ms por quadro. A decisão lê a preferência antes de criar qualquer contexto WebGL. Os shaders compilam em paralelo (`compileAsync`) antes do primeiro quadro.
   **Cor:** tom AgX (`AgXToneMapping`, exposição 0,4), luz direcional de cima à esquerda e ambiente (`RoomEnvironment`) a 25%. A textura do Rodin é verde-amarelada clara (média de pele ~`#bebd7e`); o verde-oliva que o usuário aprovou vem do render do Blender com tom AgX, sol forte e ambiente escuro. Com tom Neutral e só o ambiente claro, a pele saía amarelada (média medida `rgb(207,207,144)` contra `rgb(126,123,90)` do render aprovado); com estes valores, `rgb(126,123,94)`. A textura não foi tocada.
4. **Movimento:** a entrada gira a goblin uma vez em 3,5 s e para (abaixo dos 5 s da WCAG 2.2.2, sem botão de pausa); depois disso nada se mexe sozinho e o canvas só redesenha quando a pessoa arrasta. O arrasto é só horizontal (`touch-action: pan-y`), para não prender a rolagem no celular. O canvas fica num contêiner `aria-hidden`, fora do Tab; a imagem estática continua no DOM com o texto alternativo.
5. **Imagem estática** (placeholder e fallback): render da Rúbia em 4:5, no mesmo modo dos renders aprovados (`render_glb.py` padrão: EEVEE com tom AgX; o `--realista` usa tom Standard e também deixava a pele amarelada), AVIF e WebP de 192, 384 e 640 px (36 KB o maior AVIF), `fetchpriority="high"` porque é o provável LCP. Com erro de chunk, de GLB, de WebGL ou perda de contexto, ela fica.
6. **CSP: `'wasm-unsafe-eval'` em `script-src` e `blob:` em `connect-src`, nada mais.** O `'wasm-unsafe-eval'` deixa compilar WebAssembly (o decoder Meshopt) e não libera `eval` nem `new Function` de JS; o WASM só pode vir de script da própria origem, porque `script-src` continua `'self'`. O `blob:` vem do `GLTFLoader`, que lê as texturas embutidas no GLB com `fetch()` de uma URL `blob:` criada pela própria página (o 027 supunha que elas passassem por `img-src`, o que só vale no Safari anterior ao 17 e no Firefox anterior ao 98, em que o three usa `<img>`). Uma URL `blob:` só existe se um script da própria origem a criou, então a abertura não dá acesso a outra origem. A alternativa sem mudar a CSP era trocar o carregador de texturas do `GLTFLoader` por um campo interno, comportamento não documentado do three. Nenhum domínio de terceiro entra na CSP.
7. **Tetos no CI** (`web/scripts/check-budget.ts`): GLB até **2048 KB** (era 500 KB no 027; a Rúbia mede 1911 KB), chunk do 3D até 300 KB gzip e fora do `index.html`, imagem estática até 90 KB, e cada ilustração de estado até 20 KB. Total da abertura com o 3D: ~2,1 MB (chunk 162 KB gzip + GLB 1911 KB + imagem 36 KB), dentro da meta de 2 a 3 MB.
8. **Goblins nos estados**, como imagens 2D (render no Blender de cada GLB bruto, `scripts/arte/gerar-imagens.sh`), AVIF e WebP em 1× e 2× de uma caixa de 128 × 160 px, com texto alternativo que diz quem é a goblin (o estado já está no texto ao lado). Uma goblin fixa por estado, para a pessoa associar a personagem à situação:

   | Estado | Goblin | Onde aparece |
   |---|---|---|
   | Vazio | Lua (calma, sininho) | Bancada sem arquivos; Histórico vazio |
   | Carregando | Ada (inventora, ferramentas) | Execução na fila, em andamento ou cancelando |
   | Erro | Nyx (sombria) | Execução que falhou; página não encontrada |
   | Sucesso | Ferrugem (a mais expansiva) | Execução concluída, com ou sem avisos |

   No máximo uma goblin por tela: na Oficina é a Rúbia, e o vazio das execuções recentes fica só em texto. Alertas de formulário e de leitura (`role="alert"`) não ganham goblin. São quatro goblins para quatro estados, então não há rotação; se a Fagulha ou a Jade forem liberadas (abaixo), entram por rotação estável sobre o id do recurso, sem sortear a cada render.
9. **Proveniência travada no CI** (`web/scripts/check-provenance.ts`): todo arquivo de `web/src/assets/art/` tem entrada no `PROVENANCE.json`, com sha256 conferido, a cadeia de gerações até a ferramenta resolvida e um direito registrado; nada pode derivar de uma goblin excluída.
10. **Direitos e exclusões.** Os termos do Hyper3D Rodin e da Tripo permitem publicar as saídas num site público nos planos pagos (detalhe e links em `docs/art/LICENSE-ART.md`). Das sete aprovadas, duas **não vão ao site**: a **Jade** (`3c7aa397`) deriva do modelo `cab576c8`, que é byte a byte o 3D gerado da `referencias-usuario/ref2-jeans.png`, imagem de terceiros; a **Fagulha** (`0d84776f`) deriva do `f37f6132`, gerado direto no site do Rodin sem registro da imagem de entrada. As cinco publicadas (Rúbia, Ferrugem, Ada, Nyx e Lua) partem só de texto no Seedream e de renders nossos.

## Consequências

- A home pesa ~2,1 MB a mais para quem tem GPU e não pediu movimento reduzido, tudo depois do `load`; quem não tem GPU, pede movimento reduzido ou economiza dados baixa só a imagem de 36 KB.
- `three` vira dependência de produção, fixada na versão exata, com `@types/three` na mesma minor.
- A CSP ganha `'wasm-unsafe-eval'` em `script-src` e `blob:` em `connect-src`; o teste do Nginx (`tests/test_nginx_spa.py`), o snippet e a `csp.spec.ts` (que aceita `'wasm-unsafe-eval'` e continua recusando `'unsafe-eval'`) mudam juntos.
- Se um dia a malha vier sem UVs picotadas (outro gerador, ou retopologia manual), a decimação volta a ser possível e o Meshopt pode sair, junto com o `'wasm-unsafe-eval'`.
- O E2E do Chromium do CI não tem GPU: as specs da cena simulam uma (`pretendGpu`) para provar o caminho com 3D; as demais specs veem o caminho sem 3D, que é o de qualquer aparelho sem GPU.
- O aviso de chunk grande do Vite sobe para 650 KB; quem manda no peso é o `check-budget`.
- Para trocar o modelo, rode os scripts de `scripts/arte/` e atualize o `PROVENANCE.json` (o CI recusa arquivo sem entrada).

## Compliance

- `web/scripts/check-budget.ts` e `web/scripts/check-provenance.ts` no job `web` do CI.
- `web/e2e/specs/oficina-3d.spec.ts`: com movimento reduzido, sem WebGL e com WebGL em software, nenhum pedido do chunk nem do GLB; com GPU, a cena chega depois do `load`, só da própria origem, sem violação de CSP, fora do Tab e do leitor de tela, sem violações do axe e com a entrada parada em até 5 s; GLB que falha deixa a imagem.
- `web/e2e/specs/motion.spec.ts` e `contrast.spec.ts` percorrem as telas com as goblins dos estados.
- Revisão de código: mudança na CSP do `docker/nginx/snippets/spa-headers.conf` cita este ADR.
