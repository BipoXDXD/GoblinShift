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

Checks de PR têm fila independente. Produção preserva pendências com queue:max e mantém
cancel-in-progress:false. Um SHA superado encerra com eligible=false antes de preparar o envio
ou antes de transmiti-lo à VPS; não publica e não gera falha artificial. O novo SHA tem sua própria
execução após o CI aprovado. Cancelamentos administrativos e o limite de100 pendências continuam possíveis.

O uso de workflow_run tem risco de execução privilegiada e foi revisado explicitamente.
A única exceção inline dangerous-triggers cobre esse gatilho: o job aceita apenas sucesso
de push da main do mesmo repositório, faz checkout da main confiável e confirma pela API
SHA, tentativa e jobs obrigatórios. Não executa código nem consome artefatos de PR/fork;
as credenciais de produção só são utilizadas depois de eligible=true. O scanner zizmor
permanece fixado e ativo para todos os workflows, sem desativação global de regra.

As imagens deste projeto já são publicadas pela CI. Repetições reutilizam as referências do commit;
não dependem do número da tentativa do workflow de release. Digests, plataforma, scanners e assets
continuam sujeitos às validações existentes. Não há artefato intermediário do Duora a copiar aqui.

A aplicação usa o recurso nativo do Coolify. O receptor verifica propriedade e imagem executada,
sem fixar a etiqueta informativa coolify.version; o executor de observador do Duora não se aplica.
Outras incompatibilidades ou intents pendentes continuam bloqueadas para inspeção. O receptor
compartilhado da VPS permanece inalterado.

queue:max é aceito pelo GitHub e consta em sua documentação oficial. Actionlint1.7.12 ainda não
conhece a propriedade; validar o parser real e os checks do GitHub sem retirar gates existentes.
