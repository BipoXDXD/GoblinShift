# ADR 058. A Lua volta e o elenco de seis vem do Hi3D Quality

- **Estado:** Aceito (2026-11-22, por decisão do dono)
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2) (Apresentação)
- **Relacionado:** atualiza o [ADR 030](030-motor-e-asset-do-3d-da-abertura.md) (item 8) e o [ADR 038](038-elenco-em-3d-na-abertura.md) (elenco do seletor); `docs/art/LICENSE-ART.md`; `web/src/assets/art/PROVENANCE.json`; `docs/superpowers/plans/2026-11-22-goblins-grandes-e-elenco.md`

## Contexto

A Lua saiu do site em 2026-10-16, por decisão do dono, e o elenco ficou em cinco: Rúbia, Fagulha, Ferrugem, Jade e Nyx. O vazio mostrava só a Jade.

Em 2026-11-22 o dono revisou as lacunas do produto e pediu a Lua de volta, com goblins maiores. Os seis modelos foram refeitos no Hi3D v3.0 em `high_quality` e aprovados por ele no mesmo dia, por volta das 15:45. A redução e os pesos de cada um estão no ADR 030, seção "Modelos do Hi3D Quality (2026-11-22)".

Os ADRs aceitos não são reescritos: este registra a decisão e o que muda nos dois anteriores.

## Decisão

1. **Elenco de seis.** A Lua volta ao site em 2026-11-22. Só a Ada continua excluída (`excluded` no `PROVENANCE.json`).
2. **Modelos.** As seis goblins usam os modelos do Hi3D Quality aprovados em 2026-11-22. Cada geração (`<id>-3d-quality`) cita `upstream: "source-005"` e `rights: "rights-005"`: os direitos são do dono, por declaração dele. A arte 2D (imagem estática da cena e ilustração dos estados) sai do render realista (`render_glb.py --realista`, Cycles) do GLB do site, igual nas seis.
3. **Vazio com Lua e Jade.** O estado vazio volta a ter duas goblins, `["lua", "jade"]`, na rotação estável do ADR 030 (FNV-1a de 32 bits sobre a chave da situação, módulo 2). A mesma situação mostra sempre a mesma goblin.
4. **Ordem do seletor e dos créditos:** Rúbia, Fagulha, Ferrugem, Jade, Lua e Nyx. A Rúbia continua a inicial. A 360 px os seis botões ficam em duas linhas de três, com alvo de 44 px e sem rolagem lateral.
5. **Texto alternativo da Lua:** "Lua, goblin de cabelo preto, com um sininho dourado no pescoço." O sininho é o traço que a distingue.

## O que muda nos ADRs anteriores

- **ADR 030, item 8:** o vazio passa a "Lua e Jade"; são seis goblins para quatro estados, e os créditos apresentam as seis. Os tetos, as larguras e a receita dos modelos já foram atualizados no próprio 030 em 2026-11-22 (itens 2, 5, 7 e 8).
- **ADR 038:** o seletor tem seis botões, na ordem do item 4 acima. O pipeline do item 5 e os tetos do item 6 do 038 (192, 384 e 640 px; 2400 KB por goblin; 3600 KB de 3D inicial) dão lugar aos do ADR 030 de 2026-11-22: imagem estática de 192 a 960 px, ilustrações dos estados de 256 a 1280 px, GLB da Rúbia até 8192 KB, das outras até 6144 KB e 3D inicial até 8512 KB. O resto do 038 (uma goblin por vez, carga sob demanda, pose, teclado e movimento reduzido) continua valendo.

## Consequências

- Ver as seis no palco custa ~34 MB de GLB (35.520.536 bytes somados), baixados só para quem escolhe cada uma. A abertura continua baixando só a Rúbia.
- A home ganha uma parada de Tab a mais no seletor. O `focus.spec.ts` e o E2E do seletor a 360 px cobrem a mudança.
- A Lua não tem exceção de proveniência: a cadeia dela chega a `rights-001`, `rights-003` e `rights-004`, e o modelo novo a `rights-005`. As exceções da Jade e da Fagulha continuam registradas.

## Compliance

- `web/src/oficina3d/cast.test.ts`: a ordem dos seis ids.
- `web/src/art/GoblinIllustration.test.tsx`: a rotação do vazio entre Lua e Jade.
- `web/src/legal/CreditsPage.test.tsx` e `web/src/oficina3d/OficinaHero.test.tsx`: os seis nos créditos e no seletor.
- `web/scripts/check-provenance.test.ts`: toda geração do Hi3D Quality declara `source-005` e `rights-005`.
- `web/e2e/specs/oficina-3d.spec.ts`, `goblins.spec.ts` e `focus.spec.ts`: o seletor de seis, a 360 px e no Tab.
