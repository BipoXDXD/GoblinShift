"""`executor-ctl`: o único processo com acesso ao Docker (docs/GoblinShiftPlan.md, 3.6; ADR 004).

Serviço systemd do host, só com a biblioteca padrão do Python do sistema (3.12 no Ubuntu
24.04). Expõe ao grupo `gs-exec`, por HTTP sobre socket Unix, quatro operações sobre o container
de uma tentativa: start, status, stop e remove. Deriva sozinho nome, caminhos e opções do
`docker run` a partir do `attempt_id`; do cliente só aceita a imagem, conferida contra a allowlist.
"""
