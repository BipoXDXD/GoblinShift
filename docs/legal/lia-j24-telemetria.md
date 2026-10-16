# LIA J-24: telemetria e registros técnicos

> **RASCUNHO para o advogado, não publicar.** Avaliação de legítimo interesse (LGPD, art. 7º,
> IX, e art. 10) redigida a partir do que o sistema faz (código e plano técnico), não por
> advogado(a). Fora do build da SPA, como a J-23. Quatro fases do guia orientativo da ANPD
> (2024) e perguntas ao revisor no fim. Id do levantamento: J-24.

## 0. O tratamento avaliado

| | |
|---|---|
| Titulares | Pessoas com conta e qualquer visitante que faça requisições ao serviço |
| Eventos amplos (spans) | Um por requisição HTTP, um por tentativa de processamento, um por término de execução e um por volta da manutenção (este sem dado de pessoa) |
| Dados nos eventos | Rota, método, código de resposta, tempos, bytes, códigos de erro do catálogo, identificadores internos de execução e tentativa, receita e versão, contagens. A conta só como `user.hash`, um HMAC-SHA256 do identificador interno com segredo do servidor. IP truncado (/24 ou /48) |
| Nunca entram | Conteúdo, nomes de arquivo, valores de parâmetros (filtros de CSV são dados do usuário), query string, cabeçalhos (inclusive User-Agent e Referer), tokens, IP completo |
| Registros (logs) | Mensagens técnicas da aplicação; registro de acesso do Nginx com IP truncado e caminho sem query string |
| Onde | OpenObserve na própria VPS (Brasil), sem envio a terceiros; logs dos containers no disco da VPS |
| Prazo | Telemetria: 30 dias (expiração no OpenObserve). Logs: rotação por tamanho, no máximo 50 MiB por serviço, cerca de 14 dias |
| Quem acessa | Só o administrador, pelo OpenObserve (porta só em `127.0.0.1`, acesso por túnel SSH) |
| Fontes técnicas | `docs/GoblinShiftPlan.md` 3.7; ADR 013; `src/goblinshift/platform/telemetry.py`, `entrypoints/api/wide_event.py`, `runs/finished.py`; `tests/test_nginx_logs.py`, `tests/test_telemetry.py` |

## 1. Finalidade

- **Interesse:** medir se o serviço funciona para quem o usa (SLOs de aceitação, execução e
  download), diagnosticar falhas, ser avisado de causas conhecidas (disco cheio, fila cheia,
  execução presa, exclusão de conta atrasada) e planejar capacidade.
- **Situação concreta:** um serviço aberto, de uma pessoa, numa VPS de capacidade finita; sem
  telemetria, uma falha só seria percebida por reclamação, e o prazo de 24 h da exclusão de
  conta (1.8) não teria como ser vigiado.

## 2. Necessidade

- Pseudonimização por HMAC: agrupa os eventos de uma conta (para ver se uma falha atinge
  muitas contas ou uma só, e para o roteiro de abuso) sem guardar o identificador. Sem o
  segredo do servidor, o hash não volta à conta. Alternativa descartada: nenhuma marca de
  conta, que tornaria impossível distinguir "uma conta abusando" de "o serviço falhando".
- IP truncado e sem cabeçalhos: o bastante para ver uma rede ou um provedor, não a pessoa.
- Sem amostragem, porque o volume de uma VPS cabe inteiro e o SLO precisa de todos os
  eventos; em troca, o prazo é curto (30 dias, a janela dos SLOs).
- Dados ficam na VPS: nenhum fornecedor de observabilidade recebe nada.

## 3. Balanceamento

- **Expectativa legítima:** medir disponibilidade e erros é esperado de qualquer serviço
  online; o Aviso descreve o que entra e o que nunca entra (itens 3.4 e 5).
- **Impacto:** baixo. Nenhum uso para perfil, publicidade, decisão automatizada ou venda. O
  `user.hash` só volta a uma conta por ação deliberada do administrador (`gs-admin find-user`),
  no roteiro de abuso.
- **Risco residual:** quem tiver o banco **e** o segredo do HMAC consegue ligar eventos a
  contas. Os dois ficam na mesma VPS; o segredo fica fora do repositório e do banco.

## 4. Salvaguardas

- Regras de conteúdo testadas: canários de IP e de query string nos logs do Nginx
  (`tests/test_nginx_logs.py`), atributos da instrumentação ASGI reescritos antes de exportar
  (`tests/test_telemetry.py`), erro do banco sem valores no span.
- Retenção explícita de 30 dias no OpenObserve, sem depender do padrão da versão.
- OpenObserve sem porta pública; acesso só por túnel SSH com a chave do administrador.
- Oposição: pelo `privacidade@`, caso a caso; o `user.hash` não permite apagar só os eventos
  de uma conta sem o segredo, mas eles expiram sozinhos em 30 dias.

## Perguntas ao revisor

1. O `user.hash` é dado pseudonimizado (art. 13, § 4º) e, por isso, ainda dado pessoal?
   Proposta: tratar como dado pessoal e manter esta avaliação.
2. Visitantes sem conta (requisições ao site e ao login) também precisam de menção no Aviso,
   além do que o item 3.4 já diz?
3. O prazo de logs "cerca de 14 dias, por tamanho" é aceitável como redação, ou o Aviso deve
   trazer um teto fixo em dias?
