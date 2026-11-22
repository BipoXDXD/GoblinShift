# Arte do site: condições de uso e registro

As ilustrações e os modelos do elenco, a marca e os ícones têm registro arquivo a arquivo em
[`PROVENANCE.json`](../../web/src/assets/art/PROVENANCE.json). O verificador confere os hashes,
a cadeia de origem, o direito aplicável, as exclusões e as decisões excepcionais. Os documentos
completos de origem e termos ficam no registro externo `goblinshift-editorial-20261021`, cujo índice
vincula os identificadores neutros aos originais e seus hashes. Esta edição não altera esses fatos
nem amplia os direitos concedidos.

## Elenco

O elenco tem seis goblins. Rúbia aparece na cena 3D da abertura, na imagem estática e nos estados de
carregamento; Fagulha alterna com Rúbia no carregamento; Lua e Jade alternam no estado vazio; Nyx
representa erros; Ferrugem, sucesso. As seis aparecem no seletor da abertura e nos créditos, nesta
ordem: Rúbia, Fagulha, Ferrugem, Jade, Lua e Nyx. A Lua voltou em 22/11/2026, por decisão do usuário
([ADR 058](../adr/058-lua-volta-e-elenco-hi3d-quality.md)). Só a Ada permanece excluída, conforme a
decisão registrada.

Desde 22/11/2026, os modelos 3D das seis são gerações do Hi3D v3.0 em `high_quality`, aprovadas pelo
usuário nessa data. Cada geração parte do render anterior da própria goblin e cita `source-005` e
`rights-005`: os direitos são do dono do projeto, por declaração dele. As imagens 2D saem do render
desses modelos. Origem de cada modelo (task_id do Hi3D):

| Goblin | Geração | task_id |
|---|---|---|
| Rúbia | `rubia-3d-quality` | `b831b240f7cd4e4caa0363f370c78662` |
| Fagulha | `fagulha-3d-quality` | `4e795634d7ee4a49857fb05aff6f8ae2` |
| Ferrugem | `ferrugem-3d-quality` | `aceeb3d4676f4590898a577f1bcfc799` |
| Jade | `jade-3d-quality` | `14e21db4788b4e72ba1e0d8de61d5a15` |
| Lua | `lua-3d-quality` | `4cab2fcdf9934d518e0699801f19eb73` |
| Nyx | `nyx-3d-quality` | `71091b9b119f4946968b60742ce42ce5` |

Bytes, sha256 e redução de cada modelo estão no
[ADR 030](../adr/030-motor-e-asset-do-3d-da-abertura.md), seção "Modelos do Hi3D Quality (2026-11-22)".

## Marca reservada

O emblema do cabeçalho e o selo da oficina mostram a Rúbia. O ícone da aba usa uma versão própria em
SVG. O nome GoblinShift, o emblema, o selo, o ícone da aba e os nomes dos personagens não são licenciados,
inclusive as contribuições de edição sobre eles. Não podem ser usados de modo que sugira vínculo ou
endosso. A licença da seção seguinte não alcança a marca.

## Licença da contribuição

A contribuição de seleção, renders, enquadramento, recorte e otimização fica sob
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), **exceto Jade, Fagulha e a marca**. A concessão
se limita a essa contribuição: não afirma autoria exclusiva sobre arquivos de origem nem garante sua
proteção autoral ou exclusividade, e não concede direitos pertencentes a terceiros.

Crédito sugerido: “Arte do GoblinShift (https://goblinshift.dev), CC BY 4.0”.

## Jade e Fagulha

As exceções permanecem registradas em `exceptions`, com responsável, data e motivo:

- Jade deriva de um modelo idêntico, byte a byte, ao modelo associado a uma imagem de terceiros trazida
  como referência. O usuário assumiu o risco de publicação em 15/10/2026. Se o titular pedir retirada,
  o material deve sair do site após análise do pedido.
- A imagem de entrada da origem inicial da Fagulha não está registrada, portanto sua origem não pode
  ser verificada. O usuário autorizou a publicação em 15/10/2026.

**Nenhum arquivo derivado de Jade ou Fagulha entra na concessão CC BY 4.0.** Pedidos de retirada:
abuso@goblinshift.dev, identificando o material e o direito afetado, conforme o item 9 dos Termos.

## Ressalvas preservadas

A cadeia completa de cessão e repasse ao usuário não foi verificada. Permanecem pendentes a definição
precisa da categoria paga, a retroatividade de assinaturas ou mudanças de plano, o alcance dos direitos
sobre as fontes, indenizações, limites de responsabilidade e jurisdição. Assinatura paga e saldo de API
não equivalem, por si, a comprovação de cessão. O registro externo conserva os termos, datas de leitura,
cláusulas, planos informados e essas pendências para revisão jurídica.

## Código e fontes tipográficas

O código e os scripts de `scripts/arte/` seguem a [Apache License 2.0](../../LICENSE). Essa licença não
cobre `web/src/assets/art/`, os ícones e a imagem de compartilhamento em `web/public/`, `docs/art/` nem
os modelos 3D, conforme o [`NOTICE`](../../NOTICE). As fontes tipográficas mantêm suas licenças próprias.
