# ADR 032. Paleta "Oficina de taverna" substitui musgo e latão

- **Estado:** aceito (2026-10-15), decidido pelo usuário entre três propostas
- **Relacionado:** [spec da revisão de design](../superpowers/specs/2026-10-15-revisao-design-livros-design.md), [ADR 031](031-aviso-global-no-bundle.md)

## Contexto

A paleta da interface era musgo (primária), latão (acento, foco e aviso) e pedra (neutros), com brasa para perigo e pátina para sucesso. O tema da Oficina de taverna pedia madeira e metal, e o verde do musgo competia com o verde das goblins na arte. Foram comparadas três propostas em OKLCH, todas com as mesmas regras de papel e os mesmos pares de contraste do `contrast.test.ts`:

| Alternativa                                                                         | Prós                                                                                    | Contras                                                                                       |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Taverna escura:** madeira quase preta e cobre polido, âmbar só no foco e no aviso | Verde fica só nas goblins; cobre é a única cor do que se clica; escuro com mais caráter | Primária e destrutiva ficam próximas (ΔEok ≈ 0,10)                                            |
| Pergaminho: creme, tinta sépia e âmbar dourado                                      | Claro mais acolhedor                                                                    | Primária de croma baixo, menos legível como ação; faixa do herói em âmbar disputa com o aviso |
| Lareira: cobre em brasa sobre madeira avermelhada                                   | A mais quente                                                                           | Cobre de croma alto ainda mais perto da brasa do destrutivo                                   |
| Manter musgo e latão                                                                | Nada a mudar                                                                            | Verde da primária disputa com as goblins; não conversa com o tema                             |

## Decisão

- **Famílias cruas:** `wood` (neutros, matiz de 75° a 46°), `copper` (primária, 70° a 42°), `amber` (foco, aviso e seleção, 90° a 50°), `ember` (perigo, brasa carmim de 16° a 22°) e `patina` (sucesso, matiz fixo em 190°). Saem `moss`, `brass` e `stone`. Cada tom tem o croma cortado ao que cabe no sRGB, para o navegador desenhar a cor medida.
- **Papéis:** no claro, fundo `wood-100`, texto `wood-900`, primária `copper-700`, foco `amber-600`; no escuro, fundo `wood-950`, texto `wood-50`, primária `copper-400`, foco `amber-400`. A faixa de acento no topo usa `copper-500` nos dois temas. O mapeamento completo está em `web/src/styles/tokens.css`.
- **Verde só nas goblins.** A pátina foi a 190°, mais azulada que o verde da arte (120° a 130°) e longe do cobre.
- **Âmbar só no foco e no aviso.** O fio da faixa de aviso global, que usava a faixa de acento, ganhou papel próprio, `--warning-accent` (`amber-600` no claro, `amber-400` no escuro), para não herdar o cobre, que é cor de ação.
- **As sombras** passam a usar o tom de `wood-950` no lugar de `stone-950`.

## Contrastes medidos

Medidos pelo `contrast.test.ts` (WCAG 2.2: 4,5:1 para texto, 3:1 para borda, foco e componente), sem nenhuma falha:

| Tema   | Menor par de texto                 | Menor par não-texto                 | `fg × hero-band` | `fg-muted × hero-band` | `on-primary × primary` |
| ------ | ---------------------------------- | ----------------------------------- | ---------------- | ---------------------- | ---------------------- |
| Claro  | `link × surface-sunken` = 5,24     | `ring × surface-sunken` = 3,14      | 13,55            | 7,74                   | 6,70                   |
| Escuro | `fg-muted × surface-raised` = 5,60 | `border-ui × surface-raised` = 3,17 | 16,42            | 6,94                   | 6,22                   |

O fio do aviso mede 3,75 (claro) e 7,01 (escuro) contra o fundo da faixa.

## Consequências

- **Primária e destrutiva próximas.** A distância entre `--primary` e `--destructive` é ΔEok ≈ 0,101 no claro e 0,095 no escuro: cobre e brasa são vizinhos no círculo. É aceitável porque o botão destrutivo só aparece dentro do diálogo de confirmação, sempre com texto que nomeia a ação ("Remover"), e nunca ao lado de um botão primário fora dele. A cor nunca é o único sinal (WCAG 1.4.1). Se um dia uma ação destrutiva for para fora da confirmação, este ponto precisa ser revisto.
- O `ring` do claro fica com a menor folga do sistema (3,14 sobre `surface-sunken`); um tom de fundo novo mais escuro que `wood-200` exige recalcular o foco.
- Arte 2D ou 3D que use cores próprias segue sem depender dos tons crus.

## Como verificar

- `web/src/styles/contrast.test.ts`: as cinco famílias com 11 tons no sRGB, todos os pares nos dois temas (incluindo `fg × hero-band`, `fg-muted × hero-band` e os fios das faixas), primária no cobre, foco e aviso no âmbar, faixa no `copper-500`, nenhum papel de estado (`success-*`, `progress-fill`) no cobre, e o fallback escuro idêntico ao bloco escuro.
- `web/src/styles/classScale.test.ts`: nenhum componente usa tom cru (`wood`, `copper`, `amber`, `ember`, `patina`).
- `web/e2e`: contraste, axe e tema medidos no navegador, nos dois temas.
