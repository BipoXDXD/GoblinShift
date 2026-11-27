# Publicação automática pelo Coolify

Depois da CI bem-sucedida na main, o workflow Coolify release confere o commit exato e todos os gates. Imagens são transportadas em OCI por SSH, com digest e plataforma conferidos, para uma entrada restrita ao projeto. A VPS publica no recurso existente do Coolify e só registra sucesso após saúde real. Não há runner de PR nem token GitHub na VPS.

A chave COOLIFY_RELEASE_SSH_KEY e o known_hosts COOLIFY_RELEASE_KNOWN_HOSTS ficam no environment production, restrito à main. A variável COOLIFY_RELEASE_TARGET identifica a conta restrita. O token Coolify é local, root-only, com API limitada a IPs locais. Chaves de backup continuam separadas.

A configuração de containers permanece fixada no commit de infraestrutura revisado; releases só atualizam imagens. Volumes, bancos e assets não são removidos. preserved-assets.json bloqueia remoção ou alteração das mídias originais. Imagens antigas são retidas. Não há downgrade automático do schema; falha exige inspeção do deployment e do estado pending.json na VPS. Para rollback, verificar compatibilidade de schema e backup antes de publicar uma release anterior pelo procedimento administrativo.

Receptor: /opt/coolify-release/receiver.py; configuração /etc/coolify-release/<projeto>.json; recibos /var/lib/coolify-release/<projeto>/. Alterações do receptor no Git não atualizam automaticamente o executável root da VPS: exigem revisão e instalação administrativa.

O receptor limita o transporte OCI a 4 GiB e a expansão agregada das camadas a 16 GiB.
A trava comum da VPS serializa os receptores e intents duráveis bloqueiam novas releases após falhas.
O inventário administrativo de armazenamento e o protocolo v2 com hash exato são obrigatórios.
Instalação coordenada, reconciliação, limites e testes estão em [ISOLATION.md](ISOLATION.md).


## Fila e validação da publicação

Este projeto recebe workflow_run da CI `CI` concluída com sucesso na main. O gate consulta
novamente o push da main deste repositório, SHA e tentativa exatos, e exige todos os jobs obrigatórios.
Eventos de PR/fork, tags, CI incompleta ou com falha não liberam produção. A execução manual exige
um SHA completo e aprovado.

Checks de PR executam no workflow `CI`. Produção preserva pendências com queue:max e mantém
cancel-in-progress:false. Um SHA superado encerra com eligible=false antes de preparar o envio
ou antes de transmiti-lo à VPS; não publica e não gera falha artificial. O novo SHA tem sua própria
execução após o CI aprovado. Cancelamentos administrativos e o limite de100 pendências continuam possíveis.

O uso de workflow_run tem risco de execução privilegiada e foi revisado explicitamente.
A única exceção inline dangerous-triggers cobre esse gatilho: o job aceita apenas sucesso
de push da main do mesmo repositório, faz checkout da main confiável e confirma pela API
SHA, tentativa do evento e jobs obrigatórios. Não executa código nem consome artefatos de PR/fork;
as credenciais de produção só são utilizadas depois de eligible=true. O scanner zizmor
permanece fixado e ativo para todos os workflows, sem desativação global de regra.

Em PR e no push da `main`, os três jobs constroem OCI AMD64 e ARM64, verificam os índices e
executam Trivy nas duas plataformas em registro descartável. O check `Deploy na VPS` exercita em
fixtures o receptor e a recusa do comando legado. `Jornadas na VPS (pós-deploy)` roda as mesmas
jornadas de leitura com uma conta descartável e as imagens do CI. A produção continua a usar a
jornada pós-release com a origem e o environment restritos.

O check de promoção espera os demais jobs e verifica as três tags locais. O recibo liga os
digests validados aos IDs dos artefatos do run e à tentativa. Após o CI da `main`, somente o job
`publish-images` tem permissão `packages: write`. Ele baixa os três layouts pelos IDs do recibo
validado, confere os índices e compara as três tags `sha-*` existentes antes da primeira escrita.
Antes disso, `gh attestation verify` confere, pelo blob de cada índice, o atestado de procedência
que os jobs de imagem do `ci.yml` emitem no push da `main` (depois do scan): sem atestado desse
workflow e dessa ref, nada é gravado no GHCR.
A publicação reutiliza as tags com digest idêntico, cria as ausentes e move `:main` por digest.
Se um novo CI gerar digests incompatíveis após publicação parcial, o despacho manual aceita
`commit`, `ci_run` e `ci_attempt` para retomar com o recibo da tentativa original bem-sucedida.
Os layouts de cada tentativa conservam seus próprios IDs e precisam continuar disponíveis.
O deploy consulta por digest os índices do recibo validado, confere os bytes e as duas
plataformas e usa o manifesto AMD64 resultante no scan e no envio. A conferência de mídia e o
receptor permanecem no caminho de publicação.

A aplicação usa o recurso nativo do Coolify. O receptor verifica propriedade e imagem executada,
sem fixar a etiqueta informativa coolify.version; o executor de observador do Duora não se aplica.
Outras incompatibilidades ou intents pendentes continuam bloqueadas para inspeção. O receptor
compartilhado da VPS permanece inalterado.

queue:max é aceito pelo GitHub e consta em sua documentação oficial. Actionlint1.7.12 ainda não
conhece a propriedade; validar o parser real e os checks do GitHub sem retirar gates existentes.

## Verificação depois da release

`pos_release.sh <SHA completo>` confere, só lendo, se a release do GoblinShift ficou coerente:
sem `pending.json`, `incoming-*`, lock ou receptor ativo; `current.json`, history e allowlist do
executor de acordo com o commit; digests iguais ao manifesto linux/amd64 publicado no GHCR;
containers saudáveis com a imagem da release; `/readyz` 200; log de erros do receptor sem escrita
nova; e o commit como HEAD da main com o run `completed success`.

O destino do ssh vem de `POS_RELEASE_SSH_TARGET`, obrigatória e sem padrão. O script faz uma só
sessão ssh, que executa `pos_release_remote.sh` (enviado pelo stdin) na VPS: só leitura, com
`sudo -n`, locks lidos por `/proc/locks` (nunca adquiridos) e `pending.json` conferido de novo no
fim, para que uma release iniciada durante a leitura dê EM ANDAMENTO. O `/readyz` só é consultado
no IP da API na rede `goblinshift_internal`; sem esse IP o item fica desconhecido. Depois o script
completa os fatos com `curl`, `crane`, `gh` e `git ls-remote`, todos com limite de tempo. A coleta
só emite linhas `CHAVE<TAB>valor`; quem decide é `pos_release.py`, sem rede, que também aceita
uma coleta salva: `python3 -I pos_release.py <SHA> < fatos.tsv`.

Saída: 0 COERENTE, 1 INCOERENTE, 2 DESCONHECIDO, 3 EM ANDAMENTO. Um fato que não pôde ser lido
fica de fora da coleta e torna o item DESCONHECIDO, nunca OK. Com `pending.json` presente o
resultado é EM ANDAMENTO e nada mais é lido; repita depois. Uma falha observada vence um dado
desconhecido. Os testes (`test_pos_release.py`) usam os fatos reais da release 691dcbaf, têm
um caso de FALHA para cada um dos 25 itens e rodam a metade remota contra uma cópia local da
estrutura da VPS, sem rede nem Docker.
