"""Nomes e atributos dos cookies da identidade web (docs/GoblinShiftPlan.md, 3.6; ADR 025).

Os nomes ficam aqui porque o `app.py` instala o cookie de login. `set_session_cookies` é chamada
pelo callback do login (Frente A), e `clear_session_cookies`, pelo logout.
"""

from starlette.responses import Response

from goblinshift.identity import IssuedSession

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

# Logout (3.6): o navegador apaga os cookies e o armazenamento da origem.
CLEAR_SITE_DATA = '"cookies", "storage"'

# Os atributos são escritos aqui, e não pelo `set_cookie` do Starlette, para que o texto seja
# exatamente o de 3.6 (`SameSite=Lax`, sem `Domain`, `Expires` nem `Max-Age` na sessão). `__Host-`
# exige `Secure` e `Path=/` também ao apagar. Os valores são base64url, seguros num cookie.
_SESSION_ATTRIBUTES = "Path=/; Secure; HttpOnly; SameSite=Lax"
_CSRF_ATTRIBUTES = "Path=/; Secure; SameSite=Strict"
_EXPIRED = "Max-Age=0"


def set_session_cookies(response: Response, issued: IssuedSession) -> None:
    """Grava os cookies da sessão nova e do seu token CSRF (callback do login)."""
    response.headers.append("set-cookie", f"{SESSION_COOKIE}={issued.token}; {_SESSION_ATTRIBUTES}")
    response.headers.append("set-cookie", f"{CSRF_COOKIE}={issued.csrf_token}; {_CSRF_ATTRIBUTES}")


def clear_session_cookies(response: Response) -> None:
    """Apaga os dois cookies, com os mesmos atributos com que foram gravados."""
    response.headers.append("set-cookie", f"{SESSION_COOKIE}=; {_SESSION_ATTRIBUTES}; {_EXPIRED}")
    response.headers.append("set-cookie", f"{CSRF_COOKIE}=; {_CSRF_ATTRIBUTES}; {_EXPIRED}")
