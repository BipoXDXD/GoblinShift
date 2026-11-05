# Notas internas dos textos legais

Material de revisão dos [Termos de Uso](termos-de-uso.md) e do
[Aviso de Privacidade](aviso-de-privacidade.md). Nada daqui entra no build da SPA: as páginas
`/termos` e `/privacidade` importam só os dois textos públicos (`web/src/legal/TermsPage.tsx` e
`PrivacyPage.tsx`), e `web/src/legal/LegalPage.test.tsx` e `web/e2e/specs/legal.spec.ts` falham se
algo deste arquivo voltar para eles.

Os textos públicos estão na versão `v5`, vigente desde 2026-11-05, sem marcações pendentes. O
id de cada decisão do levantamento da fatia 8 (J-n) e as fontes de cada afirmação técnica ficam
aqui.

## Revisão de 2026-11-05

Revisado em 2026-11-05, por decisão do dono do serviço. Cada item abaixo resolve um ponto que
estava em aberto no rascunho `v4-rascunho`, sempre pela opção mais protetora para o serviço e
para os titulares. Conferido contra o código e contra a produção na mesma data.

### Controlador

- **J-16, identificação.** O controlador passa a ser Return to Monkey Corporation, CNPJ em
  registro, de Curitiba (PR), com o canal `privacidade@goblinshift.dev`. O art. 9º, III, da LGPD
  pede identificação e contato do controlador. Assim que o CNPJ sair, ele substitui "CNPJ em
  registro" nos dois textos, em versão nova. O teste `test_nao_publica_cpf` continua valendo.
- **J-17, encarregado.** Dispensado. Agente de pequeno porte (Resolução CD/ANPD nº 2/2022, art.
  2º) não é obrigado a indicar encarregado (art. 11) e precisa manter canal de comunicação com o
  titular (art. 11, § 1º), que é `privacidade@`. A exclusão do art. 3º não se aplica enquanto a
  receita estiver dentro dos limites legais. O tratamento não é de alto risco (art. 4º): sem
  larga escala e sem tratamento que afete significativamente interesses e direitos, e o serviço
  não usa decisão automatizada nem coleta dado sensível; o conteúdo dos arquivos é lido só pela
  receita. Se o enquadramento mudar, a dispensa cai.
- **Registro das operações (art. 37).** A Resolução nº 2/2022 (art. 9º) admite registro
  simplificado. Os itens 3 a 7 do Aviso, os LIAs e as tabelas desta nota fazem esse papel.

### Bases legais e LIAs

- **J-22, aceite.** Incisos V e VI do art. 7º juntos: o aceite integra o contrato e serve de
  prova dele.
- **J-23, auditoria e antiabuso.** Legítimo interesse (inciso IX) mantido. LIA concluída, com
  90 dias depois da exclusão considerados proporcionais.
- **J-24, telemetria.** Legítimo interesse mantido. LIA concluída; `user.hash` é tratado como
  dado pessoal.
- **J-25, backup.** A base passou para execução de contrato (inciso V), porque só assim o art.
  33, IX, cobre a transferência: esse inciso alcança os incisos II, V e VI do art. 7º, e não o
  legítimo interesse. A LIA fica como registro de proporcionalidade.
- **Mensagens por e-mail.** Sem legítimo interesse, pelo mesmo motivo: contato sobre o serviço
  no inciso V, inclusive procedimentos preliminares a pedido do titular; pedidos de titulares no
  inciso II; denúncias no inciso VI e, quando houver, no II.
- **Cloudflare.** Inciso V: é por ela que o site é entregue.
- **Bloqueio.** Inciso VI enquanto a conta bloqueada existir.

### Transferência internacional

- **J-19.** Mecanismo adotado: art. 33, IX, para Cloudflare, Backblaze, GitHub Actions e Zoho. Os
  provedores de login são controladores independentes, em relação direta com o titular.
- Foi adotado esse inciso porque o controlador não comprovou cláusulas-padrão da Resolução
  CD/ANPD nº 19/2024 com nenhum fornecedor. Se elas forem incorporadas, entram como reforço.
- A localização do GitHub Actions não é controlável, e o Aviso diz isso.

### Divergências encontradas em produção em 2026-11-05

- **Cloudflare não constava em nenhum texto.** `goblinshift.dev` resolve para IPs da Cloudflare,
  os servidores de nome são dela, e a resposta traz `server: cloudflare` e os cabeçalhos `nel` e
  `report-to` apontando para `a.nel.cloudflare.com`. Portanto a Cloudflare abre o TLS e vê IP
  completo, cabeçalhos, cookies e arquivos em trânsito, e o navegador pode lhe enviar relatórios
  de falha de rede. Era a omissão mais grave: entrou nos itens 2, 3.4, 3.5, 4, 6, 7 e 9 do Aviso.
- **O e-mail não passa pelo ImprovMX.** Os MX são `mx.zoho.com`, `mx2.zoho.com` e `mx3.zoho.com`, e
  o SPF inclui `zohomail.com`. O Aviso passou a citar a Zoho e deixou de citar ImprovMX e Gmail.
- **IP visto pelo serviço.** `deploy/coolify/Caddyfile` confia em `X-Forwarded-For` só de
  `179.197.70.102` e `172.16.1.6`, sem as faixas da Cloudflare. Não foi verificado se o IP
  gravado em produção é o do usuário ou o da borda. O Aviso continua correto como limite do que
  guardamos, mas, se for o da borda, a auditoria perde utilidade antiabuso.
  Resolvido em 2026-11-05 ([ADR 041](../adr/041-ip-do-cliente-atras-da-cloudflare.md)): o IP
  gravado é o do cliente, truncado, e `179.197.70.102` saiu do `Caddyfile` versionado.
- **healthchecks.io e alertas.** Recebem só pings e o texto do alerta, sem dado pessoal
  (`deploy/backup/lib.sh`, `deploy/openobserve/provision.py`). O canal urgente ainda não está
  configurado. Discord, Telegram e Slack são só opções do README.

### Marco Civil da Internet, art. 15

- O art. 15 obriga o provedor de aplicação "constituído na forma de pessoa jurídica, que exerça
  essa atividade de forma organizada, profissionalmente e com fins econômicos" a guardar registros
  de acesso por 6 meses, sob sigilo.
- **Posição:** hoje não se aplica. O serviço é gratuito, não tem receita, anúncio nem plano pago,
  e o cadastro está fechado. Por isso o Aviso mantém o IP truncado, e nada guarda o IP completo.
- **A posição é frágil** e deve ser refeita antes da abertura: o controlador passa a ser pessoa
  jurídica, o que atende um dos requisitos, e a jurisprudência tende a ler "fins econômicos" de
  forma ampla, incluindo benefício indireto. IP truncado não cumpre o art. 15, que pede data,
  hora e IP de origem (art. 5º, VIII).
- **Gatilho:** qualquer receita, plano pago, anúncio ou patrocínio exige, antes de começar, guardar
  os registros completos por 6 meses, sob sigilo, e publicar nova versão do Aviso.

### CDC

Tratado como aplicável por cautela: o serviço gratuito não afasta a relação de consumo quando há
ganho indireto. Por isso a cláusula que afastava lucros cessantes e danos indiretos saiu (CDC, art.
51, I), e os Termos ficam só com os avisos práticos e a responsabilidade na forma da lei.

- **J-21, foro.** Curitiba (PR), ressalvado ao consumidor o foro do próprio domicílio (CDC, art.
  101, I).

### Crianças e adolescentes

- **J-18.** Idade mínima de 18 anos, por autodeclaração no aceite (tela de aceite e Termos 2).
  Motivos: o art. 14 da LGPD exige consentimento de pai, mãe ou responsável para crianças, o que o
  serviço não tem como colher; e contrato com menor de idade é anulável ou nulo pelo Código Civil.
  Conta de menor é bloqueada e excluída em até 15 dias.

### Abuso sexual infantil

- **J-20.** Comunicação sem demora à Polícia Federal e à SaferNet Brasil. Preservam-se metadados,
  hashes e o identificador da conta numa ficha cifrada fora da VPS, pelo prazo da autoridade ou,
  sem prazo, por 6 meses, por analogia com o prazo do art. 15 do Marco Civil, cujo § 2º admite
  pedido cautelar de guarda por prazo maior a qualquer provedor de aplicações.
- O conteúdo não é aberto nem copiado por iniciativa própria: a posse é crime (ECA, art. 241-B),
  e a exceção do § 2º, III, vale para o provedor só até a entrega à autoridade, com sigilo (§ 3º).
  Só se a autoridade requisitar antes das 48 horas, o arquivo é copiado cifrado e entregue.
- O código não tem retenção legal (*legal hold*): a preservação é manual, em
  `docs/runbooks/abuso.md`, seção "Abuso sexual infantil (CSAM)".

### Direitos do titular

- **J-26, bloqueio e exclusão.** Conta bloqueada não entra e não usa `DELETE /me`. O comando
  `gs-admin request-deletion` pede a exclusão pelo mesmo caminho de domínio e serve aos pedidos
  aceitos, às contas de menores e à revisão anual (`docs/runbooks/abuso.md`). Sem pedido, os dados
  ficam enquanto o bloqueio durar. O HMAC de `issuer` + `subject` proposto antes não foi implementado:
  depois de uma exclusão, nenhum identificador fica, por minimização.
- **J-27, restauração.** O diário de exclusões não foi implementado, e a auditoria restaurada só
  vai até a data da cópia. O site publica essa data, e quem excluiu a conta depois dela repete o
  pedido; o que ainda houver de registro fora da cópia é refeito antes de reabrir.
- **J-28, verificação e prazo.** Identidade pelo login no provedor ou pelo código da tela de login
  recusado. Resposta simplificada imediata e declaração completa em até 15 dias (art. 19). O prazo
  em dobro do agente de pequeno porte (Resolução nº 2/2022, art. 14) não é usado.

### Fornecedores

Hostinger, Cloudflare, Backblaze, GitHub Actions e Zoho são operadores. Google e GitHub, como
login, são controladores independentes. O Aviso declara isso no item 6.

### Direitos sobre a arte e a marca

As ressalvas ficam em `docs/art/LICENSE-ART.md`, seção "Ressalvas preservadas", e os documentos
completos no registro externo `goblinshift-editorial-20261021`. Os Termos 9 só descrevem essas
condições, sem garantia de proteção ou exclusividade sobre os arquivos de origem.

### Versão

`v5`, e não `v3`: `v3-rascunho` e `v4-rascunho` já foram usadas. A troca exige novo aceite de
toda conta do login (ADR 029). Com o cadastro fechado, só as contas do dono são atingidas, e os
15 dias de antecedência dos textos não se aplicam. O aviso de rascunho saiu das páginas.

### Antes de abrir o cadastro

Depende do dono, fora do repositório:

1. Trocar "CNPJ em registro" pelo número, em versão nova dos textos.
2. Confirmar se a caixa da Zoho encaminha para outra caixa. Se encaminhar para o Gmail, o Google
   volta aos itens 3.6, 6 e 7 do Aviso.
3. Conferir se o plano da Cloudflare e o da Zoho têm contrato de tratamento de dados aceito, e
   incorporar as cláusulas-padrão da ANPD onde houver.
4. Conferir os canais de denúncia da Polícia Federal e da SaferNet indicados no roteiro.
5. Testar os três endereços de e-mail, envio e resposta.
6. Acrescentar rotação de logs ao `proxy` do gateway, numa janela própria, e ajustar o item 5 do Aviso.

### Riscos residuais

Recomendam revisão por advogado(a) se o serviço crescer ou passar a ter fins econômicos:

- **Controlador sem CNPJ registrado.** Até o registro, "Return to Monkey Corporation" não tem
  personalidade jurídica: quem responde é a pessoa natural por trás do serviço, e o texto
  identifica o controlador de forma incompleta (LGPD, art. 9º, III). Também não foi feita busca
  de nome empresarial nem de marca.
- **Marco Civil, art. 15.** A posição acima pode não resistir, e o IP truncado não atende à
  guarda exigida.
- **Art. 33, IX, como mecanismo único.** A ANPD pode entender que a Cloudflare e o backup não são
  estritamente necessários ao contrato. O reforço são as cláusulas-padrão.
- **Idade por autodeclaração.** A Lei nº 15.211/2025 (ECA Digital), em vigor desde 2026-03-17,
  vale para serviço de acesso provável por menores (art. 1º, parágrafo único) e veda autodeclaração
  para conteúdo ou serviço impróprio ou proibido a menores (art. 9º, § 1º). Posição: o GoblinShift
  não é esse caso, porque é ferramenta de arquivos sem conteúdo adulto, e os 18 anos são limite
  contratual. Um limite próprio de 18 anos, porém, pode ser lido como "proibido a menores".
- **Preservação manual.** A posse temporária do material e a falta de *legal hold* no código.
- **Responsabilidade por conteúdo de terceiros.** A decisão do STF sobre o art. 19 do Marco Civil
  ampliou a responsabilidade dos provedores; os arquivos são privados, mas o alcance não foi medido.
- **Restauração.** Uma exclusão feita depois da cópia pode voltar até que a pessoa repita o
  pedido, e ela só saberá pelo aviso no site.
- **Log do proxy de borda.** `deploy/coolify/gateway.json` não define rotação para o serviço
  `proxy`, e o Aviso declara isso. O serviço é compartilhado com outro site, então a rotação fica
  para uma janela do dono.
- **Citações.** Conferidos no Planalto ou na ANPD em 2026-11-05: LGPD (arts. 7º, 9º, 13, 16, 18, 19
  e 33), Marco Civil (arts. 5º, 13 e 15), ECA (art. 241-B), Resolução nº 2/2022 e Lei nº 15.211/2025.
  Não conferidos: CDC, Código Civil, Resolução nº 19/2024 e a jurisprudência citada. O art. 241-B
  foi alterado pela Lei nº 15.487/2026, que mexeu nos §§ 1º e 4º; confirme que os §§ 2º e 3º, base
  da preservação, seguem iguais.

## Termos de Uso

### Fontes internas

Cada afirmação técnica dos Termos corresponde a um trecho do plano ou do código. "Plano" é
`docs/GoblinShiftPlan.md`. Onde a fonte é só o plano, o comportamento é o especificado para a
abertura pública. As fatias citadas no plano já estão na `main`, conferida em 2026-11-05.

| Afirmação | Fonte |
|---|---|
| Projeto de portfólio, gratuito, código público, serviço aberto | Plano, introdução da seção 1 e 1.8; ADR 012 |
| Revisão antes da abertura | Plano 1.8 (último item de Privacidade); ADR 012; seção "Revisão de 2026-11-05" destas notas |
| Aceite no primeiro login com versão e data; reaceite quando a versão muda | Plano 1.8 (Base legal e transparência) e 3.3 (`User`: versão e data dos termos); plano das fatias 7 e 8, 2.1; ADR 029; `src/goblinshift/identity/terms.py` |
| Receitas fixas, resultados privados com manifesto e relatório; catálogo mostra só o implementado | Plano, introdução da seção 1 e 1.1 |
| Sem rede no processamento, com processamento determinístico, sem scripts do cliente | Plano, introdução da seção 1; 3.6 (Sandbox: `--network=none`) |
| Login só Google (OIDC) ou GitHub (OAuth 2.0), sem senha própria; com cadastro fechado, a conta nova fica à espera de liberação | Plano 1.8 (Contas) e 3.6 (Identidade web); ADR 008; ADR 012; ADR 023 |
| Duas contas separadas para a mesma pessoa; sem vínculo por e-mail | Plano 1.8; ADR 008, Decisão e Consequências |
| Sessão: 30 min de inatividade ou 8 h absolutas; `logout-all` revoga sessões e chaves | Plano 3.6 (Sessão) |
| Até 5 chaves; exibição única; 30 dias padrão, 90 máximo; exige login recente | Plano 1.7 (Configurações), 3.6 (Chaves de API e Autenticação recente); `src/goblinshift/identity/keys.py` (validade) |
| Cotas: 1 execução não terminal, 30 execuções e 2 GiB/24 h, 4 GiB por conta, 100 presets | Plano 1.7 (tabela de limites); ADR 012 |
| Recusa imediata ao atingir limite; fila com prazo de 2 h | Plano 1.7 (Fila) e 1.8 (Recusar cedo); `src/goblinshift/runs/lifecycle.py` (`QUEUE_WINDOW = 2 h`) |
| Arquivos e resultados por 48 h após o término; upload não usado em 24 h | Plano 1.7 (Retenção); `src/goblinshift/files/limits.py` (`ARTIFACT_TTL`, `USED_INPUT_TTL`, `READY_UPLOAD_TTL`) |
| Arquivos sem backup | Plano 3.8 (Backups: "Arquivos: sem backup, por decisão") |
| Sem links públicos de compartilhamento | Plano 1.8 (Abuso e custo); ADR 012 |
| Bloqueio revoga sessões e chaves, cancela a execução e impede novo login | Plano 1.8 (Estado da conta); ADR 012; plano das fatias 7 e 8, 2.6 |
| Procedimento de CSAM | Plano das fatias 7 e 8, 2.6; `docs/runbooks/abuso.md`; J-20 nesta nota |
| Sem varredura automatizada de conteúdo nesta versão | Plano 1.8 (Abuso e custo): as medidas listadas são limites, privacidade, expiração, bloqueio e termos; nenhuma é varredura |
| Canal de contato para denúncias | Plano 1.8 (Abuso e custo); ADR 012 |
| `DELETE /me` e `gs-admin request-deletion`: revoga, cancela e remove em até 24 h; backups expiram em até 31 dias | Plano 1.8 (Exclusão), 3.6 (Autenticação recente) e 3.8 (Backups); `src/goblinshift/entrypoints/api/me_delete.py`; `src/goblinshift/entrypoints/admin_cli.py` |
| Remoção de metadados não é anonimização | Plano 1.2 (Image Forge, perfis de cor e metadados) |
| Código sob Apache-2.0; a licença não cobre a arte nem os modelos 3D | `LICENSE`, `NOTICE`, `pyproject.toml` (`license`), `web/package.json` (`license`) |
| Arte sob CC BY 4.0 na parte humana, exceto Jade e Fagulha | `docs/art/LICENSE-ART.md` |
| Processamento isolado sem rede; arte com registro de licenças | Plano, introdução da seção 1; `docs/art/LICENSE-ART.md`; página `/creditos` (`web/src/legal/CreditsPage.tsx`) |
| "Código da tela de login" para o recurso contra bloqueio | A tela de login recusado mostra uma referência do pedido para citar no e-mail) |
| Canal da política de divulgação em `/.well-known/security.txt` | `SECURITY.md` e `web/public/.well-known/security.txt` |

## Aviso de Privacidade

### Fontes internas

"Plano" é `docs/GoblinShiftPlan.md`; "plano 7-8" é o levantamento das fatias 7 e 8
(`planos/fatias7-8-plano.md`, fora do repositório). Onde a fonte é só o plano, o comportamento é
o especificado para a abertura pública. Os caminhos citados estão na `main`, conferida em
2026-11-05.

| Afirmação | Fonte |
|---|---|
| Sem senha própria; login Google (OIDC, escopo `openid`) ou GitHub (OAuth 2.0, sem escopo); token do GitHub descartado após uma chamada a `GET /user` | Plano 1.8 (Contas, Minimização) e 3.6 (Identidade web); ADR 008 |
| E-mail não coletado; só `issuer`, `subject` e nome de exibição | Plano 1.8 (Minimização); ADR 008, Consequências; `src/goblinshift/identity/tables.py` (`app_user` sem coluna de e-mail) |
| Identidade GitHub pelo `id` numérico; nome de exibição = `login` | Plano 1.8 (Contas, Minimização); ADR 008 |
| Estado `active`/`blocked`, data de criação | `src/goblinshift/identity/tables.py` (`status`, `created_at`) |
| Versão e data dos termos no `User` | Plano 3.3 (`User`); plano 7-8, 2.1; ADR 029 |
| Provedor de login não recebe acesso aos arquivos | Plano 3.6 (Identidade web); ADR 008, Consequências |
| Sessão: segredo aleatório com hash no banco, datas, expiração 30 min / 8 h, `Clear-Site-Data` no logout, `logout-all` | Plano 3.6 (Sessão) e 3.3 (`WebSession`) |
| Chaves: identificador público, hash, escopos, datas, último uso | Plano 3.6 (Chaves de API); `src/goblinshift/identity/tables.py` (`api_key`) |
| Auditoria: eventos listados, IP truncado /24 e /48, sem nomes nem conteúdo, 90 dias | Plano 3.6 (Auditoria), 3.3 (`AuditEvent`), 1.7 (Retenção) e 1.8 (Privacidade) |
| Estado transitório do login no PostgreSQL por poucos minutos; cookie leva só a chave | Plano 3.6 (Sessão, último parágrafo) e 1.7 (Retenção) |
| Arquivos com tamanho e SHA-256; nomes, parâmetros, resultado por arquivo; histórico; presets | Plano 3.3 (`Upload`, `Run`, `RunInput`, `StoredObject`, `Preset`) e 1.1 |
| Consumo calculado do histórico | Plano 3.3 (último parágrafo: cotas a partir de `Run` e `RunInput`) e 1.8 (Abuso e custo) |
| Conteúdo lido só pela receita, em contêiner isolado e sem rede | Plano 3.6 (Sandbox) |
| Metadados GPS em fotos como exemplo; remoção de metadados não é anonimização | Plano 1.2 (Image Forge: orientar por EXIF, remover metadados suportados) |
| Telemetria: eventos por requisição, tentativa, término; `user.hash` HMAC; o que nunca entra | Plano 3.7 (Eventos amplos); `src/goblinshift/entrypoints/api/wide_event.py` (docstring) |
| IP truncado /24 e /48 nos eventos; query string removida | `src/goblinshift/platform/telemetry.py` (`IPV4_PREFIX_BITS`, `IPV6_PREFIX_BITS`, `truncate_client_address`) |
| Cloudflare como proxy; Zoho como e-mail | Consulta de DNS e `curl -I https://goblinshift.dev` em 2026-11-05 (ver "Divergências encontradas em produção") |
| Telemetria na VPS, sem terceiros, 30 dias | Plano 3.7 (Backend de telemetria) e 1.8; ADR 013; `docker/otel-collector/config.yaml` (só exporta para o OpenObserve local) |
| Sem scripts de terceiros | Plano 3.6 (Cabeçalhos da aplicação: CSP `script-src 'self'; connect-src 'self'`) |
| Base legal execução de contrato | Plano 1.8 (Base legal e transparência) |
| Segurança como finalidade do IP truncado | Plano 1.8 ("por segurança do próprio serviço") |
| Tabela de retenção (1 h, 24 h, 48 h, 30 dias, 90 dias, 7 dias, 24 h, 30 dias) | Plano 1.7 (Retenção); `src/goblinshift/files/limits.py` (`INCOMPLETE_UPLOAD_TTL`, `READY_UPLOAD_TTL`, `ARTIFACT_TTL`, `USED_INPUT_TTL`); `src/goblinshift/runs/acceptance.py` (`IDEMPOTENCY_TTL`) |
| Arquivos sem backup | Plano 3.8 (Backups) |
| Hostinger, KVM 2, Campinas (SP) | Plano 3.8 (Servidor); ADR 011 |
| Backup: dump diário, cifrado com `age` antes do envio, chave só de escrita, até 31 dias por ciclo de vida do bucket, chave privada fora da VPS | `docs/adr/022-backup-externo.md`, `deploy/backup/pg-backup.sh`, `deploy/backup/goblinshift-pg-backup.timer`; plano 3.8; plano 7-8, 2.2 |
| Backblaze B2 nos EUA | Decisão do dono em 2026-10-15: região US East. Plano 7-8, 2.2; ADR 022 (alvo B2). A região é configuração (`BACKUP_S3_REGION`), fora do repositório |
| Eventos de segurança guardados 90 dias depois de `DELETE /me` | ADR 026, Consequências; `tests/test_schema_fatia_6.py` (apagar a conta mantém os eventos) |
| Mensagens por e-mail guardadas 12 meses depois do fim da conversa | Decisão do dono em 2026-10-15 (documento de decisões jurídicas, J-6) |
| Aviso de incidente no site | Decisão do dono em 2026-10-15 (J-15); o aviso global do site está em `web/src` |
| Restauração mensal decifrada em runner do GitHub Actions | `.github/workflows/restore-check.yml` (`runs-on: ubuntu-24.04`, `cron: "23 10 1 * *"`, serviço `postgres` efêmero) |
| healthchecks.io recebe só pings | ADR 022, Decisão 4 |
| Exclusão adiada de arquivos | Plano 1.1 (Exclusão) e 3.6 (Exclusão sempre adiada) |
| `DELETE /me` e `gs-admin request-deletion`: revoga, cancela, remove em até 24 h; backup em até 31 dias | Plano 1.8 (Exclusão), 3.6 (Autenticação recente), 3.8 (Backups); `src/goblinshift/entrypoints/admin_cli.py` |
| Acesso por `GET /me`, histórico e presets | Plano 1.8 (Acesso); `src/goblinshift/entrypoints/api/me.py` (id, nome, escopos, limites, consumo e aceite) |
| Cookies `__Host-gs_session` e `__Host-gs_csrf` e seus atributos | Plano 3.6 (Sessão, CSRF) |
| HTTPS, banco sem porta exposta, segredos em hash | Plano 3.8 (Proxy de borda, Rede) e 3.6 |
| Nome "Conta Google" para contas Google; nome do GitHub (`login`) regravado a cada login; nome sem edição | `src/goblinshift/identity/providers.py` (`GOOGLE_SCOPE`, `GOOGLE_DISPLAY_NAME`); `src/goblinshift/identity/users.py` (`ON CONFLICT ... set_ display_name`); `src/goblinshift/entrypoints/api/me.py` (só `GET /me`, sem rota de edição) |
| Cookie `__Secure-gs_login`: chave assinada, `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/auth`, 10 minutos; estado do login expira em 10 minutos | `src/goblinshift/entrypoints/api/cookies.py`; `src/goblinshift/entrypoints/api/app.py` (`SessionMiddleware` do Starlette, que sempre marca `httponly`); `src/goblinshift/identity/login_state.py` (`LOGIN_STATE_TTL`) |
| Log de acesso do Nginx: IP truncado /24 e /48, só o caminho, sem Referer nem User-Agent; Uvicorn sem log de acesso | `docker/nginx/templates/access-log.conf.template` (`log_format privacy`); `docker/nginx/templates/default.conf.template:25`; `docker/app.Dockerfile:34` (`--no-access-log`); `tests/test_nginx_logs.py`; PR #12 |
| Log de erro do Nginx com a linha da requisição e o IP da borda | PR #12 ("Fora do escopo") |
| Proxy de borda sem log de acesso | `deploy/edge/Caddyfile` e `deploy/edge/conf.d/goblinshift.caddy` (sem diretiva `log`) |
| Logs: 5 × 10 MiB por serviço, cerca de 14 dias no volume previsto, somem ao recriar o container | `compose.yaml:11-15` (`x-logging`); plano 1.7 (Retenção e "Logs dos containers"); `tests/test_container_logs.py` |

### Divergências entre plano e código encontradas ao redigir

Todas as três encontradas no primeiro rascunho estão resolvidas na `main` (conferida em 2026-10-14,
`6c1c41f`), e o texto acima já descreve o comportamento atual.

1. **Logs de acesso com IP completo: resolvida pelo PR #12.** O Nginx usa o formato `privacy`
   (IP truncado em /24 no IPv4 e /48 no IPv6, só o caminho, sem query string, Referer nem
   User-Agent), e o Uvicorn roda com `--no-access-log`. Ressalva mantida no item 3.2: o log de
   **erro** do Nginx ainda inclui a linha da requisição, com a query string, quando a aplicação
   não responde; o `client:` dessa linha é o IP do proxy de borda, não o do usuário.
2. **Retenção dos logs: resolvida pelo PR #12.** A rotação passou para cada serviço do Compose
   (driver `local`, 5 arquivos de 10 MiB), e o `deploy/provision.sh` não escreve mais o
   `/etc/docker/daemon.json`, que é global na VPS compartilhada. O plano 1.7 trocou "14 dias" por
   "teto de 50 MiB por serviço, dimensionado para cerca de 14 dias no volume previsto", e o
   item 5 usa esse texto, sem prometer prazo fixo.
3. **IP na telemetria: resolvida pelo PR #16.** A truncagem do IP e a remoção da query string nos
   eventos estão na `main` (`src/goblinshift/platform/telemetry.py`, `truncate_client_address`).

Nota operacional: nem `deploy/edge/compose.yaml` nem `deploy/coolify/gateway.json`, de
infraestrutura compartilhada, declaram rotação própria de logs. O Caddy da borda não grava log de
acesso, e o item 5 do Aviso declara as mensagens técnicas dele à parte.

## Decisões aplicadas em 2026-10-15

Tomadas pelo dono do serviço sobre o documento de decisões jurídicas de 2026-10-15
(`planos/juridico-decisoes-2026-10-15.md`, fora do repositório). As marcações correspondentes
saíram dos textos públicos, que passaram à versão `v2-rascunho`.

| Id | Decisão | Onde |
|---|---|---|
| J-4 | Versão `v2-rascunho`, vigência 2026-10-15. `CURRENT_TERMS_VERSION` subiu junto, e toda conta aceita de novo (ADR 029) | Topo dos dois textos; `src/goblinshift/identity/terms.py` |
| J-5 | Domínio `https://goblinshift.dev` | Topo dos dois textos |
| J-6 | Três aliases: `contato@`, `abuso@` e `privacidade@goblinshift.dev`. O dono cria os aliases e testa envio e resposta antes da abertura | Termos 1, 7, 8 e 9; Aviso 1, 3.6 e 8 |
| J-7 | Autorização para testar falhas comunicadas de boa-fé pelo canal de `https://goblinshift.dev/.well-known/security.txt`. | Termos 6, item 7 |
| J-8 | Opção B: um item só, sobre emprestar, vender ou publicar chaves de API e revender o serviço | Termos 6, item 9 |
| J-9 | Opção B: primeira resposta em até 3 dias úteis; abuso sexual infantil com prioridade | Termos 7 |
| J-10 | Opção A: sem aviso prévio (não há e-mail); recurso por `contato@` citando o código da tela de login e o provedor | Termos 8 |
| J-11 | Opção A: 30 dias de aviso no site antes do fim do serviço, salvo segurança, ordem legal ou caso fortuito | Termos 8 |
| J-12 | Apache-2.0; a licença não cobre `web/src/assets/art/`, `docs/art/` nem os modelos 3D (`NOTICE`) | Termos 9; `LICENSE`; `NOTICE` |
| J-13 | Opção B: marca reservada; a parte humana da arte sob CC BY 4.0, sem garantia sobre a arquivos de origem, com Jade e Fagulha fora da concessão. Os scripts de `scripts/arte/` passaram da CC BY 4.0 para a Apache-2.0, como o resto do código | Termos 9; `docs/art/LICENSE-ART.md` |
| J-14 | Opção A: 15 dias de antecedência para mudança relevante; tabela "Histórico de versões" no fim de cada texto, mais o link do Git | Termos 12; Aviso 12; fim dos dois textos |
| J-15 | Aviso destacado no site, em até 3 dias úteis, "porque não temos o seu e-mail" | Aviso 10 |
| J-16 | Controlador de Curitiba (PR), canal `privacidade@goblinshift.dev`; sem CPF nem endereço. Substituída em 2026-11-05 (ver "Revisão de 2026-11-05") | Termos 1; Aviso 1 |
| J-19 (parcial) | Região do B2: Estados Unidos (US East). O mecanismo do art. 33 foi definido em 2026-11-05 | Aviso 6 |
| Provedor de e-mail | Os aliases `contato@`, `abuso@` e `privacidade@` passam pelo ImprovMX, que encaminha para uma caixa Gmail (Google) do controlador. O endereço da caixa não entra no texto. Aviso: itens 2, 3.6, 6 (duas linhas, ImprovMX e Google) e 7 (item 3 da lista). Localização dos dois: "conforme a política do fornecedor", sem afirmar país. Versão `v3-rascunho` (o aceite é um só, então os Termos sobem junto, sem mudança de cláusula). Substituída em 2026-11-05: os MX atuais são da Zoho | Aviso 2, 3.6, 6 e 7; `src/goblinshift/identity/terms.py` |

Lacunas fora das marcações, resolvidas no mesmo dia:

- **Auditoria depois da exclusão** (ADR 026): o Aviso diz, nos itens 5 e 8, que os eventos de
  segurança ficam 90 dias depois de `DELETE /me`.
- **Dados enviados por e-mail**: item 3.6 novo do Aviso, retenção de 12 meses depois do fim da
  conversa (item 5), e os resumos dos itens 2 e 3.5 deixam de dizer que nenhum e-mail chega.
- **Processamento e arte**: o item 3 dos Termos descreve o isolamento sem rede nem scripts de cliente.
  As condições da arte permanecem em `docs/art/LICENSE-ART.md` e em `/creditos`.
- **Notificação e retirada** de material do site que viole direito de alguém: Termos 9, por
  `abuso@`.

Nesta troca de versão, a antecedência de 15 dias do J-14 não se aplicou: o cadastro está
fechado e as únicas contas são do dono.
