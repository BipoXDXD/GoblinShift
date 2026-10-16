# ADR 038. Elenco em 3D na abertura: uma goblin por vez, sob demanda, vista de todos os ângulos

- **Estado:** Aceito (2026-10-16)
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (Apresentação), [3.6](../GoblinShiftPlan.md#s3-6) (CSP da SPA)
- **Relacionado:** complementa o [ADR 030](030-motor-e-asset-do-3d-da-abertura.md) (motor, modelo e arte) e o [ADR 027](027-3d-na-abertura.md) (condições do 3D). Substitui o item 4 do 030 na parte do giro (só horizontal) e do canvas fora do Tab; o resto do 030 e as condições 1 a 4 do 027 continuam valendo.

## Contexto

O usuário pediu "os modelos em 3D como a Rúbia na página inicial, para o usuário ter uma visão de todos os ângulos". Até aqui só a Rúbia tinha 3D, com giro horizontal por arrasto e o canvas fora do teclado (ADR 030, item 4). As outras quatro goblins do elenco (Fagulha, Ferrugem, Jade e Nyx) só existiam como imagens 2D nos estados.

Dois problemas: **peso**, porque cada GLB pesa de 1,5 a 3,3 MB e a abertura já carrega ~3,5 MB de 3D; e **ângulos**, porque o giro só horizontal não deixa ver de cima nem de baixo, e o teclado não chegava ao modelo.

### Como apresentar cinco modelos

| Opção | Prós | Contras |
|---|---|---|
| As cinco no palco ao mesmo tempo | Mostra o elenco de uma vez | ~10 MB de GLB e cinco malhas na GPU na abertura; num celular, inviável; cada goblin fica minúscula |
| Cinco palcos, um por goblin, cada um com carga preguiçosa ao rolar | Cada goblin grande | Cinco contextos WebGL (o navegador derruba os mais antigos a partir de ~16), cinco chunks de shader compilados, e quem rola a página baixa tudo sem pedir |
| **Um palco, uma goblin por vez, com seletor; o GLB da escolhida baixa sob demanda** | A abertura continua baixando só a Rúbia; um renderer e um contexto WebGL para o elenco todo; troca de modelo libera o anterior da GPU | Ver outra goblin custa a espera do GLB dela (1,5 a 2,2 MB); a imagem estática dela cobre essa espera |

### Como girar e inclinar

| Opção | Prós | Contras |
|---|---|---|
| `OrbitControls` do three | Pronto | Zoom com roda e pinça sequestra a rolagem da página no celular; desligar o zoom e limitar a inclinação ainda deixa o arrasto vertical com um dedo preso na cena |
| **Pose própria (giro livre + inclinação de -15° a +35°), arrasto por Pointer Events** | ~100 linhas puras e testadas (`pose.ts`, `gestures.ts`); com um dedo o arrasto vertical continua rolando a página (`touch-action: pan-y`); dois dedos inclinam | Código nosso a manter |

## Decisão

1. **Seletor do elenco no herói:** uma goblin por vez no palco e uma grade de cinco botões (`aria-pressed`) com miniatura e nome, na ordem dos créditos; a Rúbia é a inicial. Trocar de goblin mostra na hora a imagem estática dela e baixa **só o GLB dela**; trocar de novo antes de chegar aborta a carga anterior (`AbortSignal` e a ordem das trocas em `show-order.ts`, em que só a mais recente vale), e voltar à goblin que já está no palco não baixa nada. Um renderer só: o modelo anterior sai da GPU (malha, materiais, texturas e mapas de sombra) antes do novo entrar. A miniatura é a imagem estática de 192 px, recortada no rosto por CSS.
2. **Todos os ângulos:** giro livre de 360° na horizontal e inclinação da câmera de **-15° a +35°**, numa órbita em volta do centro da goblin (abaixo de -15° a câmera passaria do plano dos pés). Mouse e caneta: arrasto horizontal gira, vertical inclina. Toque: **um dedo só gira** (`touch-action: pan-y`, a página continua rolando no arrasto vertical); **dois dedos** subindo ou descendo juntos inclinam, e só nesse gesto o `touchmove` cancela a rolagem. Sem zoom.
3. **Teclado e acessibilidade:** com a cena pronta, o palco entra no Tab como `role="application"` com nome ("Nyx em 3D") e instruções visíveis ligadas por `aria-describedby`. Setas giram (15° por toque) e inclinam (10°), Home volta à pose inicial. O canvas continua num contêiner `aria-hidden`, e a imagem estática com o texto alternativo continua no DOM. Botões **Girar** (`aria-pressed`, uma volta a cada 12 s, liga e desliga, o que atende a WCAG 2.2.2) e **Pose inicial**. A entrada de 3,5 s toca a cada goblin nova e para sozinha. Os controles e o arrasto só valem com a cena mostrada: enquanto a próxima goblin carrega ou se o modelo dela falhou, ficam a imagem e o seletor.
4. **Movimento reduzido:** continua a condição 2 do 027 e o item 3 do 030: com `prefers-reduced-motion: reduce`, economia de dados ou sem WebGL 2 acelerado, **nenhuma cena carrega**; o seletor troca a imagem estática. Por isso o Girar nem aparece. Se a preferência mudar depois da carga (o herói assina o `change` do `matchMedia`), o Girar some, o giro para, a entrada que estiver tocando termina na pose de repouso e as goblins seguintes chegam sem entrada. Se o navegador tirar o contexto WebGL, a cena é liberada na hora e volta a imagem.
5. **Pipeline:** `scripts/arte/otimizar-glb.sh` e `gerar-imagens.sh` passam a receber IDs do `elenco.tsv` e geram as do elenco com **a mesma configuração da Rúbia** (weld, simplify com atributos até erro 0,0015, texturas de 2048 px em WebP, Meshopt com UV em 14 bits; imagem estática com o render realista, `--yaw=20`, em 192, 384 e 640 px). O GLB da Rúbia ficou o que já estava no repositório. `ALVO=cena` gera só as imagens da cena, sem mexer nas dos estados.
6. **Tetos no CI** (`web/scripts/check-budget.ts`):

   | Item | Medido | Teto |
   |---|---|---|
   | GLB da Rúbia (inicial) | 3283 KB (~240 mil triângulos) | 3400 KB (ADR 030) |
   | GLB da Jade | 2247 KB (146 mil) | 2400 KB por goblin |
   | GLB da Ferrugem | 1864 KB (124 mil) | 2400 KB |
   | GLB da Fagulha | 1531 KB (112 mil) | 2400 KB |
   | GLB da Nyx | 1492 KB (101 mil) | 2400 KB |
   | Chunk do 3D | 158 KB gzip | 300 KB gzip (ADR 030) |
   | Imagem estática, cada | 26 a 42 KB (640 px, AVIF) | 90 KB (ADR 030) |
   | **3D inicial**: chunk + GLB da Rúbia + imagem de 640 px dela + as miniaturas | **~3521 KB** (3529 KB medidos com uma miniatura a mais, que pesava ~8 KB) | **3600 KB** |

   O 3D inicial só conta a Rúbia: os outros quatro GLBs (7,0 MB somados) vêm só quando a pessoa os escolhe. O `check-budget` falha se houver modelos no build sem o da Rúbia.
7. **CSP: não muda.** Os GLBs são da própria origem, carregados pelo mesmo `fetch` e o mesmo decoder Meshopt do 030; nenhuma origem, Worker ou diretiva nova.

## Consequências

- Quem tem GPU e não pediu movimento reduzido baixa o mesmo que antes na abertura, mais ~47 KB das miniaturas; ver todas custa ~10 MB ao todo, só para quem as escolhe uma a uma.
- A home ganha cinco paradas de Tab (o seletor) sempre, e mais três (palco, Girar, Pose inicial) com a cena montada. O `focus.spec.ts` foi atualizado.
- A cena exige agora a pose de dois eixos, a troca de modelo e o descarte por modelo; `pose.ts` e `gestures.ts` são puros e testados no Vitest, e a cena expõe a pose (`data-giro`, `data-inclinacao`, `data-movimento`, `data-goblin`) e a memória da GPU (`data-geometrias`, `data-texturas`) para o E2E.
- Os closes no three (frente, costas, lado, de cima e de baixo, rosto e coxas) das quatro goblins novas não mostraram os defeitos que a Rúbia teve antes do simplify com atributos (íris corroída, bochecha facetada, risco na coxa).
- **Trade-off de acessibilidade, pendente de decisão de design do usuário.** O `touch-action: pan-y` no palco e o `preventDefault` do `touchmove` com dois dedos, que deixam a rolagem com um dedo e a inclinação com dois, também bloqueiam a pinça de zoom da página quando ela começa sobre o palco (WCAG 1.4.4, Resize Text: a pessoa precisa começar a pinça fora dele). E a inclinação só tem gestos de arrasto (mouse e dois dedos) e as setas do teclado; falta uma alternativa de toque único sem arrasto, como botões, para quem não consegue arrastar (WCAG 2.5.7, Dragging Movements). O giro tem o botão Girar; a inclinação não tem equivalente. Fica registrado, sem implementação, até o usuário decidir o desenho.
- O E2E com GPU simulada roda em SwiftShader: sob carga alta na máquina local, cliques podem esperar mais que 5 s; as specs da cena esperam a entrada parar antes de mexer e usam 15 s por ação.

## Compliance

- `web/src/oficina3d/pose.test.ts` e `gestures.test.ts`: limites da inclinação (on e off point), giro livre, teclas, arrasto por tipo de ponteiro, dois dedos e o cancelamento da rolagem só com dois dedos.
- `web/src/oficina3d/OficinaHero.test.tsx`: seletor, carga só da escolhida, aborto na troca, falha por goblin (sem controles nem arrasto), teclado, Girar, Pose inicial, movimento reduzido ligado com o giro em andamento e perda do contexto WebGL (a cena é liberada uma vez).
- `web/src/oficina3d/show-order.test.ts`: só a troca mais recente vale (Nyx segurada e Fagulha escolhida; volta à goblin do palco; troca vencida não se registra).
- `web/scripts/check-budget.test.ts`: teto por modelo e do 3D inicial.
- `web/e2e/specs/oficina-3d.spec.ts`: a abertura pede só o GLB da Rúbia; trocar pede o GLB certo e só ele, com a imagem dela enquanto carrega; setas e Home dentro dos limites; arrasto com mouse; Girar, que desligado não mexe mais nos quadros seguintes; movimento reduzido ligado depois da carga (sem Girar e sem entrada); trocar de goblin e voltar devolve a GPU ao mesmo número de geometrias e texturas (`data-geometrias`, `data-texturas`, de `renderer.info.memory`); movimento reduzido e sem WebGL trocam a imagem sem pedir chunk nem GLB; axe com a cena montada.

## Elenco

A Lua saiu do site em 2026-10-16, por decisão do usuário.
