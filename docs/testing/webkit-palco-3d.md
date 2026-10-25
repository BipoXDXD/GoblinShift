# Regressão do palco 3D em WebKit móvel

A suíte `npm run e2e:webkit` usa o WebKit fixado pelo Playwright do lockfile, o perfil iPhone 13
(viewport CSS de 390 × 664, toque e escala de dispositivo 3), a API falsa e o build local com a CSP
de produção. Reaproveita as specs do palco em vez de duplicar jornadas da aplicação. A configuração
normal continua com Chromium desktop/mobile e o job de doze minutos; a suíte com aviso e a VPS
não recebem novos projetos.

## Executar

Em `web/`, com Node 24:

```sh
npm ci
npx playwright install webkit
npm run build
npm run e2e:webkit
```

Para investigar só os contratos de entrada, acrescente `-- e2e/webkit/oficina-3d-mobile.spec.ts`.
A configuração rejeita `E2E_BASE_URL` e `E2E_NOTICE`: esta suíte usa apenas o build normal local.
`E2E_PORT` permite escolher outra porta ao executar ao lado de outra suíte local.

O workflow `e2e-webkit.yml` roda em alterações de `web/`, dos cabeçalhos da SPA ou do próprio
workflow, em PR e push na main, além de permitir execução manual. É independente do CI principal,
usa macOS 15, um worker, zero retries, teto de quatro minutos para os testes e oito para o job
inteiro. Relatório HTML, capturas, diagnósticos e traces de falhas ficam no artefato
`playwright-webkit-stage` por sete dias. Cada execução cancela a anterior do mesmo ref.

## WebGL é capacidade medida

Antes de cada cenário com cena, o auxiliar pede um contexto WebGL 2 com
`failIfMajorPerformanceCaveat: true`, registra renderer e user agent em `webgl-capability` e devolve
esse contexto de sondagem. Se não houver WebGL 2 ou o renderer indicar software, o cenário **falha
com diagnóstico**. Não há skip de GPU nem substituição da cena, modelo ou renderer de WebKit.
O nome do renderer pode ser ocultado pelo navegador; nesse caso a ressalva de desempenho do
contexto decide, como na aplicação.

Somente os cenários de fallback forçam a indisponibilidade de WebGL ou um nome de renderer
software para verificar a rejeição. O Chromium mantém o auxiliar existente que permite desenhar
em SwiftShader; isso não é usado no caminho de cena WebKit. Caso um runner deixe de oferecer
WebGL adequado, investigar o artefato e o ambiente, sem declarar que os cenários de cena passaram.

## Cobertura e limites

| Cenário                       | WebKit automatizado                                                                                           | Limite da garantia                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Carregamento inicial          | Chunk após `load`, só Rúbia, mesma origem e CSP; entrada termina                                              | Não mede desempenho de iPhone                                                                        |
| Troca de personagem           | Só GLB escolhido; imagem durante a espera; nome acessível; volta ao mesmo número de geometrias/texturas       | Não é perfil de memória de longo prazo                                                               |
| Fallback e movimento reduzido | Nenhum chunk/GLB; seleção das cinco imagens sem WebGL; controles 3D ausentes; preferência alterada após carga | Falta de GPU e software são forçados nos casos de fallback                                           |
| Inclinação acessível          | Tap, Enter/Espaço, alvos de 44 px, foco visível, ordem de navegação, limites e reset                          | No WebKit/macOS a navegação entre botões usa Option+Tab, respeitando a preferência padrão do sistema |
| Teclado, mouse e axe          | Setas/Home, arrasto, rotação que para e axe WCAG A/AA                                                         | Axe não substitui VoiceOver                                                                          |
| Permissão para pan/zoom       | `touch-action` do palco e ancestrais; viewport permite ampliar; `touchmove` com 1–3 dedos não é cancelado     | TouchEvent sintético não executa a pinça nativa                                                      |
| Rolagem vertical              | PageDown com palco focado rola a página sem girar/inclinar                                                    | Não simula swipe; `mouse.wheel` não é suportado no WebKit móvel do Playwright                        |
| Cancelamento de toques        | `pointercancel` e `lostpointercapture` removem dois ponteiros; novo dedo gira sem inclinar                    | PointerEvents sintéticos; apenas a captura de IDs sintéticos é substituída                           |
| Perda de captura real         | Entrada do mouse, captura nativa liberada, arrasto encerrado e retomado                                       | Não comprova quando o reconhecedor iOS decide assumir pan/pinça                                      |
| Pinça nativa                  | Caso existente roda no Chromium móvel via CDP e verifica aumento de `visualViewport.scale`, giro e rolagem    | Único skip explícito no WebKit: CDP é exclusivo de Chromium                                          |

Diferenças reproduzidas na preparação da suíte:

- WebKit no macOS não disponibilizou `requestIdleCallback`. A aplicação já possui fallback por
  timer; o auxiliar de espera dos testes passou a respeitar esse caminho.
- Tab simples pulou botões no WebKit/macOS. Um exemplo mínimo confirmou que Option+Tab alcança o
  próximo botão; a spec usa essa entrada nativa sem mudar `tabindex` nem a interface.
- WebKit expôs `TouchEvent` mas recusou `new Touch`. O teste cria um TouchEvent
  com listas de toques sintéticas; isso serve para testar cancelamento, sem fingir um gesto nativo.

O protocolo público de toque do Playwright oferece apenas tap. Não usamos APIs privadas de
WebKit ou `dispatchEvent` para alegar que uma pinça real mudou `visualViewport.scale`.
Referências: [Touchscreen](https://playwright.dev/docs/api/class-touchscreen),
[WebKit no Playwright](https://playwright.dev/docs/browsers#webkit) e
[comportamento de Tab no WebKit](https://bugs.webkit.org/show_bug.cgi?id=199671).

## Homologação ainda necessária em dispositivos físicos

Safari/iOS físico precisa confirmar pinça iniciada sobre o palco (amplia e permite ler a página),
arrasto vertical com um dedo (rola), giro horizontal, retomada após pan/pinça cancelados pelo
navegador, tap nos botões de inclinação e troca das cinco goblins. Repetir com VoiceOver, movimento
reduzido e orientação/tamanho de texto alterados; avaliar estabilidade e aquecimento do aparelho.
Fazer também a conferência Android prevista no ADR 038.

A inclinação com dois dedos continua oportunista: se o navegador assumir pan/zoom, a navegação
nativa tem prioridade. Os botões continuam sendo a alternativa acessível previsível. Uma execução
verde de WebKit automatizado não equivale a essa homologação física.
