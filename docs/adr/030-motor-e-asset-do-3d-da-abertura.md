# ADR 030. Motor, modelo e arte da cena 3D da abertura e das goblins nos estados

- **Estado:** Aceito (2026-10-15); atualizado no mesmo dia com a entrada da Jade e da Fagulha por decisão do usuário (item 10), e de novo com a correção dos defeitos da Rúbia, a troca dela pela regeração de 1 milhão de triângulos e a luz realista (itens 2, 3, 5 e 7; seção "Defeitos da primeira versão"). Substitui em parte o [ADR 027](027-3d-na-abertura.md): a condição 5 (teto do GLB) e a condição 6 (CSP). As condições 1 a 4 do 027 continuam valendo.
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (Apresentação, "sem canvas/engine"), [3.6](../GoblinShiftPlan.md#s3-6) (CSP da SPA)
- **Complementado por:** [ADR 038](038-elenco-em-3d-na-abertura.md), que leva as cinco goblins ao palco, uma por vez e sob demanda, com inclinação e teclado (substitui a parte do item 4 sobre giro só horizontal e canvas fora do Tab).
- **Relacionado:** [ADR 027](027-3d-na-abertura.md); `docs/art/LICENSE-ART.md`; `web/src/assets/art/PROVENANCE.json`

## Contexto

O usuário decidiu ter **3D na abertura** (ADR 027), contra a recomendação de deixar fora da v1, e contra a regra 2.2 do plano ("oficina 2D em HTML/CSS [...] sem canvas/engine"). O 027 fixou as condições, mas não o motor nem o asset, porque a arte não existia. Agora o elenco está fechado em sete goblins aprovadas, e a favorita do usuário, a **Rúbia** (fonte externa `e1c79f67`), vai para a cena. As outras entram como imagens 2D nos estados (decisão D6: goblins só nos estados, cena só na home).

O modelo bruto do fonte externa tem 30,5 MB: 500 mil triângulos e três texturas PNG de 2048 px (cor, normal, metal/rugosidade). A meta do usuário é a abertura inteira abaixo de 2 a 3 MB.

### Motor

| Opção | Prós | Contras |
|---|---|---|
| `<model-viewer>` 4.4, auto-hospedado | Pronto e mantido; poster, carga preguiçosa e controles de câmera de graça | ~289 KB gzip, o dobro do three sozinho; aponta os decoders Draco e KTX2 para `www.gstatic.com` por padrão (precisa reconfigurar para não ir a CDN); controles de câmera capturam setas e arrasto, e desligá-los tira quase tudo o que ele oferece; estilos em shadow DOM a conferir contra `style-src 'self'` |
| **three.js mínimo** (`WebGLRenderer`, `GLTFLoader`, `RoomEnvironment`) | 162 KB gzip medidos no build, já com o decoder Meshopt embutido; nenhuma URL de terceiro; controle total do que mexe (entrada de 3,5 s, arrasto horizontal, render só sob demanda) e do descarte | ~200 linhas nossas: laço de render, redimensionamento, perda de contexto, descarte de GPU; mais uma dependência grande para atualizar |
| Vídeo ou imagem pré-renderizada (giro em MP4/WebM, ou só o PNG) | Nenhum JS de 3D; funciona em qualquer aparelho; CSP intacta | Não é 3D: nada de girar com o dedo, que é o que o usuário pediu; vídeo em laço é movimento automático além de 5 s (WCAG 2.2.2) ou precisa de botão de pausa; um giro de 3 s em boa qualidade pesa 1 a 2 MB, como o GLB |

### Redução e compressão do modelo

As UVs do fonte externa são picotadas em milhares de ilhas pequenas, e isso decide o resto. Medido na Rúbia:

| Opção | Peso | Qualidade | CSP |
|---|---|---|---|
| Decimate do Blender para 30 mil triângulos + quantização + WebP | 912 KB | **Pintas brancas e pretas** no cabelo e na pele: triângulos atravessam as costuras e amostram o vão entre as ilhas | Só `blob:` em `connect-src` |
| Malha de 30 a 50 mil triângulos com UVs novas e textura reassada da original (Cycles, "selected to active") | ~1 MB (estimado) | Pior: manchas pretas onde os raios acertam camadas de cabelo e o rosto perde definição | Só `blob:` em `connect-src` |
| `simplify` do meshoptimizer (para em ~120 mil triângulos, porque respeita as costuras) + quantização + WebP, sem Meshopt | 4,8 MB | Igual à bruta no tamanho exibido | Só `blob:` em `connect-src` |
| **O mesmo, com compressão Meshopt (`EXT_meshopt_compression`)** | **1,87 MB** | **Igual à bruta no tamanho exibido** | `blob:` em `connect-src` e `'wasm-unsafe-eval'` em `script-src` |
| O mesmo, com Draco ou KTX2 | menor | Igual | `'wasm-unsafe-eval'` e `worker-src 'self' blob:` (decoder num Worker de Blob) |

Texturas de 2048 px custam +350 KB sem diferença na tela (a cena aparece com no máximo 320 × 400 px CSS). *Superado pela seção abaixo: de perto, 2048 px fazem diferença.*

### Defeitos da primeira versão

O usuário viu no site três defeitos que o bruto aprovado não tem: a íris vermelha do olho esquerdo quase some, a bochecha tem uma deformação e há um risco vermelho atrás da coxa esquerda. Diagnóstico com closes no three (a mesma luz e câmera para todos) e no Blender, isolando cada passo do `optimize`:

| Variante | Olho esquerdo | Bochecha | Coxa |
|---|---|---|---|
| Só textura de 1024 px (com e sem WebP) | igual | igual | igual |
| Só quantização (12 bits de UV, 14 de posição) | igual | igual | igual |
| Só Meshopt (quantização incluída) | igual | igual | igual |
| **Só `simplify`** | **íris corroída** | **facetada** | **risco vermelho** |

A causa dos três é o `simplify` do gltf-transform, que só mede o desvio de **posição** (erro de 1% do raio). Ele respeita as costuras de UV, mas dentro de cada ilha colapsa o que é plano: a bochecha vira poucos triângulos grandes, cujas normais interpoladas fazem facetas; a malha do olho perde vértices e o UV da íris escorrega; e na coxa um triângulo esticado passa a cobrir, no espaço de UV, um canto côncavo da ilha onde está pintado o cabelo. Travar as bordas (`--lock-border`) não muda nada (121.524 triângulos nos dois casos), e baixar o erro só de posição até 0,0001 ainda deixava facetas na bochecha.

A correção é o simplify **com atributos** do meshoptimizer (`simplifyWithAttributes`), que soma ao erro o desvio da normal (peso 1) e do UV (peso 16): `web/scripts/simplificar-glb.ts`, com teste que reproduz o modo de falha num plano (o simplify só de posição distorce o UV em mais de 0,02; o novo fica abaixo de 0,002). Os closes da Rúbia nova (1 milhão de triângulos), comparados ao bruto pela SSIM (dissimilaridade, menor é melhor):

| Erro aceito | Triângulos | GLB | Rosto | Olho de frente | Olho em 3/4 | Coxa |
|---|---|---|---|---|---|---|
| 0,01, só posição, textura 1024 (antigo) | — | 2,66 MB | 0,106 | 0,098 | 0,096 | 0,062 |
| 0,003 | 198 mil | 3,08 MB | 0,056 | 0,063 | 0,058 | 0,027 |
| 0,002 | 219 mil | 3,22 MB | 0,050 | 0,058 | 0,054 | 0,021 |
| **0,0015** | **241 mil** | **3,36 MB** | **0,032** | **0,039** | **0,037** | **0,018** |
| 0,001 | 285 mil | 3,65 MB | 0,026 | 0,035 | 0,032 | 0,014 |

A textura pesa: na Rúbia antiga, sem mudar a malha, 2048 px em vez de 1024 px baixam a dissimilaridade dos closes de 0,034 para 0,021, por ~320 KB. A fonte nova tem texturas de 2048 px, então o site usa 2048 px (4096 px seria só aumentar a imagem). O UV vai em 14 bits em vez de 12 (+3 KB), para não dar passos de texel se um dia vier textura de 4096 px.

## Decisão

1. **Motor: three.js mínimo** (`three@0.186.1`), num chunk próprio (`assets/oficina-3d-*.js`) importado por `import()` depois do `load` e de um momento ocioso (condição 1 do 027). `<model-viewer>` sai porque custa o dobro, chama CDN por padrão e traz controles que o 027 manda desligar; o vídeo sai porque não entrega o 3D que o usuário escolheu.
2. **Modelo: só a Rúbia**, na regeração de 1 milhão de triângulos (fonte externa `9f030f3c`, Gen-2.5-High, malha Raw, `texture_delight=false`, mesma imagem de referência da `e1c79f67`), que o usuário escolheu em 2026-10-15. Otimizada por `scripts/arte/otimizar-glb.sh`, versionado: weld, simplify **com atributos** até erro de 0,0015 (~240 mil triângulos), texturas de 2048 px em WebP, malha quantizada com UV em 14 bits e **compressão Meshopt**: **3,36 MB** (3283 KB), com olho, bochecha e coxa iguais ao bruto nos closes. O decoder Meshopt é o do próprio three (`meshopt_decoder.module.js`), um WASM embutido no chunk e servido da própria origem; ele roda na thread principal, sem Worker. **Sem Draco nem KTX2**, que pediriam Worker de `blob:`.
3. **Quando carrega:** só sem `prefers-reduced-motion: reduce`, sem `Save-Data` e com **WebGL 2 acelerado por GPU**. WebGL em software (SwiftShader, llvmpipe) conta como ausente: medido no Chromium sem GPU, ele trava a thread principal por 2,3 s na montagem e ~70 ms por quadro. A decisão lê a preferência antes de criar qualquer contexto WebGL. Os shaders compilam em paralelo (`compileAsync`) antes do primeiro quadro.
   **Luz e cor:** a do render realista que o usuário aprovou (`render_glb.py --realista`: Cycles, três luzes de área e tom Standard). No three: sem tone mapping (o Standard do Blender), ambiente de cor única cinza a 0,2 e as três luzes como direcionais nos mesmos ângulos (principal a −40° e 35° de altura, preenchimento a 60° e 10°, recorte a 170° e 40°), com a irradiância convertida de cada luz de área (potência / 9π a três raios) vezes 0,6. A principal faz sombra (mapa de 2048 px, PCF), que devolve o volume do cabelo sobre o rosto e do braço sobre o corpo. A versão anterior (AgX, exposição 0,4, `RoomEnvironment` a 25%) escurecia a pele para um verde-oliva fechado e achatava o volume; o usuário achou a Rúbia do site bem menos realista. Medido na pele (matiz de 55° a 85°): matiz 65° contra 64,6° do render realista e brilho (V do HSV) 0,78 contra 0,76.
4. **Movimento:** a entrada gira a goblin uma vez em 3,5 s e para (abaixo dos 5 s da WCAG 2.2.2, sem botão de pausa); depois disso nada se mexe sozinho e o canvas só redesenha quando a pessoa arrasta. O arrasto é só horizontal (`touch-action: pan-y`), para não prender a rolagem no celular. O canvas fica num contêiner `aria-hidden`, fora do Tab; a imagem estática continua no DOM com o texto alternativo.
5. **Imagem estática** (placeholder e fallback): render da Rúbia em 4:5 com a luz realista (`render_glb.py --realista`, a mesma que a cena imita; `scripts/arte/gerar-imagens.sh rubia`), AVIF e WebP de 192, 384 e 640 px (41 KB o maior AVIF), `fetchpriority="high"` porque é o provável LCP. Com erro de chunk, de GLB, de WebGL ou perda de contexto, ela fica.
6. **CSP: `'wasm-unsafe-eval'` em `script-src` e `blob:` em `connect-src`, nada mais.** O `'wasm-unsafe-eval'` deixa compilar WebAssembly (o decoder Meshopt) e não libera `eval` nem `new Function` de JS; o WASM só pode vir de script da própria origem, porque `script-src` continua `'self'`. O `blob:` vem do `GLTFLoader`, que lê as texturas embutidas no GLB com `fetch()` de uma URL `blob:` criada pela própria página (o 027 supunha que elas passassem por `img-src`, o que só vale no Safari anterior ao 17 e no Firefox anterior ao 98, em que o three usa `<img>`). Uma URL `blob:` só existe se um script da própria origem a criou, então a abertura não dá acesso a outra origem. A alternativa sem mudar a CSP era trocar o carregador de texturas do `GLTFLoader` por um campo interno, comportamento não documentado do three. Nenhum domínio de terceiro entra na CSP. O usuário aprovou o `'wasm-unsafe-eval'` em 2026-10-15, em troca do modelo limpo de 1,87 MB.
7. **Tetos no CI** (`web/scripts/check-budget.ts`): GLB até **3400 KB** (era 500 KB no 027 e 2048 KB na primeira versão; a Rúbia nova mede 3283 KB, e um erro de simplificação maior já tirava relevo do rosto), chunk do 3D até 300 KB gzip e fora do `index.html`, imagem estática até 90 KB, e cada ilustração de goblin até 64 KB (era 20 KB com a caixa de 128 × 160; subiu com as goblins em destaque, item 8). Total da abertura com o 3D: ~3,5 MB (chunk 162 KB gzip + GLB 3283 KB + imagem 41 KB), acima da meta inicial de 2 a 3 MB: é o preço de a Rúbia ficar igual ao bruto, e tudo chega depois do `load`.
8. **Goblins nos estados**, como imagens 2D (render no Blender de cada GLB bruto, `scripts/arte/gerar-imagens.sh`), AVIF e WebP de 256, 384, 512 e 768 px de largura com `srcset` e `sizes`, numa caixa 4:5 reservada de 192 × 240 px no celular e 256 × 320 px a partir de 768 px (em 2026-10-15 o usuário achou as de 128 × 160 "muito pequeninas" e pediu destaque), com texto alternativo que diz quem é a goblin (o estado já está no texto ao lado). Cada estado tem as suas goblins, para a pessoa associar a personagem à situação:

   | Estado | Goblins | Onde aparece |
   |---|---|---|
   | Vazio | Jade (casual, de jeans; a Lua saiu em 2026-10-16, por decisão do usuário) | Bancada sem arquivos; Histórico vazio |
   | Carregando | Rúbia (a mascote da oficina, mesmo render da imagem estática da abertura) e Fagulha (couro e luvas de oficina) | Execução na fila, em andamento ou cancelando |
   | Erro | Nyx (sombria) | Execução que falhou; página não encontrada |
   | Sucesso | Ferrugem (a mais expansiva) | Execução concluída, com ou sem avisos |

   No máximo uma goblin por tela: na Oficina é a Rúbia, e o vazio das execuções recentes fica só em texto. Alertas de formulário e de leitura (`role="alert"`) não ganham goblin. São cinco goblins para quatro estados, e a página de créditos apresenta as cinco com o nome: onde há duas, a escolha é uma **rotação estável** (FNV-1a de 32 bits sobre uma chave da situação, módulo o número de goblins do estado). A chave é o id da execução, a receita da Bancada ou um nome fixo da tela (Histórico, 404). A mesma situação mostra sempre a mesma goblin, sem trocar a cada render nem a cada polling, e situações diferentes se espalham entre as duas.
9. **Proveniência travada no CI** (`web/scripts/check-provenance.ts`): todo arquivo de `web/src/assets/art/` tem entrada no `PROVENANCE.json`, com sha256 conferido, a cadeia de gerações até a ferramenta resolvida e um direito registrado; nada pode derivar de uma goblin excluída, e geração de origem de terceiros ou não verificável (`third_party`) só passa com uma decisão em `exceptions` que diga quem decidiu, quando e por quê.
10. **Direitos e exclusões.** Os termos do fonte externa fonte externa e da fonte externa permitem publicar as saídas num site público nos planos pagos, e o usuário é assinante dos dois, além de ter saldo pago na API da fonte externa (informado em 2026-10-15); com isso, a dúvida sobre a carteira da API contar como "Paid User" deixa de importar (detalhe e links em `docs/art/LICENSE-ART.md`). As publicadas de cadeia própria (Rúbia, Ferrugem e Nyx) partem só de texto no fonte externa e de renders nossos. As outras duas entram **por decisão do usuário em 2026-10-15**, registrada em `exceptions` no `PROVENANCE.json`:
    - a **Jade** (`3c7aa397`) deriva do modelo `cab576c8`, que é byte a byte o 3D gerado da `referencias-usuario/ref2-jeans.png`, imagem de terceiros; o usuário assume o risco de publicá-la;
    - a **Fagulha** (`0d84776f`) deriva do `f37f6132`, gerado direto no site do fonte externa sem registro da imagem de entrada; o usuário a libera mesmo sem a proveniência verificável.

    A primeira versão deste ADR as deixava fora do site; essa parte foi substituída pela decisão acima.

    A **Ada** (`417f7fe0`) saiu do site em 2026-10-15, por decisão do usuário, que não gostou do resultado; ela está em `excluded` no `PROVENANCE.json`, e o `check-provenance` recusa qualquer arquivo que volte a derivar dela.

## Consequências

- A home pesa ~3,5 MB a mais para quem tem GPU e não pediu movimento reduzido, tudo depois do `load`; quem não tem GPU, pede movimento reduzido ou economiza dados baixa só a imagem de 41 KB.
- `three` vira dependência de produção, fixada na versão exata, com `@types/three` na mesma minor.
- A CSP ganha `'wasm-unsafe-eval'` em `script-src` e `blob:` em `connect-src`; o teste do Nginx (`tests/test_nginx_spa.py`), o snippet e a `csp.spec.ts` (que aceita `'wasm-unsafe-eval'` e continua recusando `'unsafe-eval'`) mudam juntos.
- Se um dia a malha vier sem UVs picotadas (outro gerador, ou retopologia manual), a decimação volta a ser possível e o Meshopt pode sair, junto com o `'wasm-unsafe-eval'`.
- O E2E do Chromium do CI não tem GPU: as specs da cena simulam uma (`pretendGpu`) para provar o caminho com 3D; as demais specs veem o caminho sem 3D, que é o de qualquer aparelho sem GPU.
- O aviso de chunk grande do Vite sobe para 650 KB; quem manda no peso é o `check-budget`.
- Para trocar o modelo, rode os scripts de `scripts/arte/` e atualize o `PROVENANCE.json` (o CI recusa arquivo sem entrada).

## Compliance

- `web/scripts/check-budget.ts` e `web/scripts/check-provenance.ts` no job `web` do CI.
- `web/scripts/simplificar-glb.test.ts` (no `npm test` do CI): o simplify mantém o UV e as normais de um plano dentro da tolerância, e o teste mostra que o simplify só de posição, o dos defeitos, não mantém. A comparação de render contra o bruto não roda no CI: o bruto (47 MB) fica fora do Git e o render pede GPU. Ela é o passo manual ao trocar o modelo (closes de olho, bochecha e coxa, como na tabela acima).
- `web/e2e/specs/oficina-3d.spec.ts`: com movimento reduzido, sem WebGL e com WebGL em software, nenhum pedido do chunk nem do GLB; com GPU, a cena chega depois do `load`, só da própria origem, sem violação de CSP, fora do Tab e do leitor de tela, sem violações do axe e com a entrada parada em até 5 s; GLB que falha deixa a imagem.
- `web/e2e/specs/motion.spec.ts` e `contrast.spec.ts` percorrem as telas com as goblins dos estados.
- Revisão de código: mudança na CSP do `docker/nginx/snippets/spa-headers.conf` cita este ADR.
