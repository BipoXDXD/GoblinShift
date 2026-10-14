"""Nomes e atributos dos cookies da identidade web (docs/GoblinShiftPlan.md, 3.6; ADR 024).

Os nomes ficam aqui porque o `app.py` instala o cookie de login; a Frente B acrescenta as
funções que gravam e apagam os cookies de sessão e de CSRF.
"""

# Sessão: HttpOnly, Secure, SameSite=Lax, Path=/, sem Domain e sem Max-Age (o prazo vale no
# servidor). `Strict` quebraria a volta do Google e do GitHub.
SESSION_COOKIE = "__Host-gs_session"
# CSRF: lido pela SPA (sem HttpOnly), Secure, SameSite=Strict, Path=/, sem Domain.
CSRF_COOKIE = "__Host-gs_csrf"
CSRF_HEADER = "X-CSRF-Token"

# Marcador do estado do login (Authlib), assinado; o conteúdo fica em `login_state`. `Path=/auth`
# faz o cookie nem chegar às rotas da API.
LOGIN_COOKIE = "__Secure-gs_login"
LOGIN_COOKIE_PATH = "/auth"
LOGIN_COOKIE_MAX_AGE_S = 600
