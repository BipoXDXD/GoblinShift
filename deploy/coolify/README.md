# Publicação no Coolify

A publicação usa aplicações Git Compose em modo Raw, com repositório preservado e sem comando de start customizado. Os containers reais são pertencentes ao recurso no Coolify. Não há observador substituindo a aplicação.

Aplicação e infraestrutura têm recursos separados para que uma publicação da aplicação não pare o banco. As definições compartilham o nome Compose original, usam redes externas e guardam os dados em binds do host, sem volumes nomeados, e não executam remoção de órfãos. Só o gateway, um recurso próprio de nome `edge`, tem volumes externos. Os binds privados existem previamente no host; suas credenciais não ficam no repositório. Cada imagem é fixada por digest e deve estar previamente importada e validada; `pull_policy: never` impede trocar a imagem durante o corte.

O Postgres (`infra.json`) tem `stop_grace_period: 60s`, para o checkpoint final da parada terminar sem SIGKILL, e `shm_size: 128m` de `/dev/shm` para as queries paralelas. Os dois só valem no host depois de o operador reimplantar o recurso de infraestrutura (Tarefa 9 do plano de contêineres, #366).

A configuração de preparação da migração, sem escritores em segundo plano, já não existe: `app.json` traz os serviços completos, com `worker` e `maintenance`.

Cada serviço tem `container_name` fixo, então uma publicação substitui a API e o Nginx, sem duas versões lado a lado. O Coolify para os containers do recurso antes de subir a versão nova: a publicação os recria, e pode haver indisponibilidade curta durante a troca. Não há medição registrada do efeito disso em produção ([README](../../README.md#publicação-em-produção)).

Segredos de interpolação são configurados como variáveis privadas no Coolify. O modo Raw conserva o isolamento por serviço das variáveis e dos arquivos de segredo. Não adicionar env_file global a todos os serviços.

Os quatro serviços contínuos da aplicação têm healthcheck no Compose. A saúde da API e do Nginx é só liveness: a API responde `/livez` sem tocar no banco, e o Nginx consulta `/livez` pelo próprio proxy até a API. Assim uma oscilação do Postgres não marca os dois `unhealthy` nem reprova uma publicação sã; a prontidão do banco e do schema fica no gate, na regra 23 do `pos_release` (`/readyz` direto na API) e nas sondas `readiness` que a configuração do receptor no host declarar; worker e manutenção verificam o processo esperado, uma consulta somente leitura no banco e uma consulta de status ao executor-ctl, sem criar tentativas. Assim o status agregado no Coolify reflete as dependências operacionais. Migração e inicialização de armazenamento usam `restart: no` e ficam fora do agregado por serem tarefas de execução única.

Manter backups e restauração verificada antes de atualizar uma versão. Não apontar tráfego para a origem com dados antigos após o destino aceitar escritas. Uma reversão precisa primeiro preservar e transferir o estado novo.

A configuração do gateway (`docker/gateway/Caddyfile` e `docker/gateway/sites/`) não passa pela release da CI, que só troca imagens: publicar `sites/`, publicar o `Caddyfile` e atualizar a lista de faixas da Cloudflare seguem os passos do [ADR 041](../../docs/adr/041-ip-do-cliente-atras-da-cloudflare.md).
