# Licenças das fontes da interface

As três famílias da interface (decisão D5 do plano de design da fatia 7) são auto-hospedadas: os arquivos `woff2` vêm dos pacotes do Fontsource, entram no build do Vite com hash no nome e são servidos pela própria origem, nunca por CDN (a CSP de [3.6](../docs/GoblinShiftPlan.md#s3-6) só aceita `'self'`). Só os subconjuntos `latin` e `latin-ext` são declarados, em `src/styles/fonts.css`.

Todas estão sob a **SIL Open Font License 1.1** (OFL-1.1), que permite usar, embutir e redistribuir as fontes, inclusive em produto comercial, desde que a licença e os avisos de copyright acompanhem a fonte, que a fonte não seja vendida sozinha e que versões modificadas não usem nomes reservados. Nenhuma das três declara nome reservado (Reserved Font Name). Os arquivos `woff2` não são modificados.

| Família                    | Uso                                        | Pacote                                            | Versão | Projeto de origem                                                |
| -------------------------- | ------------------------------------------ | ------------------------------------------------- | ------ | ---------------------------------------------------------------- |
| Bricolage Grotesque        | Títulos de 20 px ou mais                   | `@fontsource-variable/bricolage-grotesque`        | 5.3.0  | <https://github.com/ateliertriay/bricolage>                      |
| Atkinson Hyperlegible Next | Corpo, formulários e botões                | `@fontsource-variable/atkinson-hyperlegible-next` | 5.3.0  | <https://github.com/googlefonts/atkinson-hyperlegible-next>      |
| Atkinson Hyperlegible Mono | Dados: nomes de arquivo, tamanhos, códigos | `@fontsource-variable/atkinson-hyperlegible-mono` | 5.3.0  | <https://github.com/googlefonts/atkinson-hyperlegible-next-mono> |

O teste `src/styles/fontLicenses.test.ts` confere que cada pacote instalado continua OFL-1.1 e que o aviso de copyright dele está nesta página; atualizar uma fonte com aviso novo exige atualizar este arquivo.

## Avisos de copyright

- Copyright 2022 The Bricolage Grotesque Project Authors (https://github.com/ateliertriay/bricolage)
- Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors (https://github.com/googlefonts/atkinson-hyperlegible-next) AtkinsonHyperlegibleNext-Italic[wght].ttf: Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors (https://github.com/googlefonts/atkinson-hyperlegible-next)
- Copyright 2020-2024 The Atkinson Hyperlegible Mono Project Authors (https://github.com/googlefonts/atkinson-hyperlegible-next-mono) AtkinsonHyperlegibleMono-Italic[wght].ttf: Copyright 2020-2024 The Atkinson Hyperlegible Mono Project Authors (https://github.com/googlefonts/atkinson-hyperlegible-next-mono)

## Texto da licença

O texto é o mesmo nos três pacotes.

```text
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```
