import { CircleAlert } from "lucide-react";
import type { ReactElement } from "react";
import { Link, Navigate, NavLink, Outlet, useLocation, useSearchParams } from "react-router";

import { LOGIN_ERROR_PARAM, loginRefFrom, safeReturnTo } from "../account/signIn";
import { isSignedOut, useMe } from "../account/useMe";
import { TEXT } from "../text";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import { acceptTermsPath, PATHS, signInPath } from "./paths";

const T = TEXT.layout;
/** Alvo do "Pular para o conteúdo"; o `<main>` é o mesmo em todas as telas. */
const MAIN_ID = "conteudo";
const NAV_LINK =
  "inline-flex min-h-11 items-center font-semibold aria-[current=page]:text-fg aria-[current=page]:no-underline";
/** Valor exato com que o servidor volta de um login recusado (auth_login.py). */
const LOGIN_FAILED = "failed";
/**
 * Telas abertas a quem ainda não aceitou os termos vigentes: ler e aceitar os textos e, como na
 * API (ADR 029), reduzir o próprio acesso pelo Acesso (sair, revogar chaves, excluir a conta).
 */
const TERMS_EXEMPT_PATHS: ReadonlySet<string> = new Set([
  PATHS.acceptTerms,
  PATHS.terms,
  PATHS.privacy,
  PATHS.credits,
  PATHS.access,
  PATHS.signIn,
  PATHS.accountDeleted,
]);

/**
 * Moldura de todas as telas (D8): faixa de acento em latão, barra no topo com a marca, o Histórico,
 * o Acesso (ou Entrar, sem sessão) e o seletor de tema, e o conteúdo centrado. Conta que ainda não
 * aceitou os termos vigentes vai para o aceite antes de qualquer outra tela.
 */
export function Layout(): ReactElement {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href={`#${MAIN_ID}`}
        className="sr-only rounded-lg bg-surface-raised px-4 py-2 font-semibold shadow-3 focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-20"
      >
        {T.skipToContent}
      </a>
      <div aria-hidden="true" className="h-1 bg-accent-band" />
      <header className="border-b border-line-soft bg-surface">
        {/* Com Histórico, Acesso e o tema, a barra não cabe em 400 px: a navegação desce para uma
            segunda linha em vez de empurrar a página para o lado (WCAG 1.4.10). */}
        <div className="mx-auto flex min-h-16 w-full max-w-5xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2 md:px-8">
          <Link
            to={PATHS.home}
            aria-label={T.brandLink}
            className="inline-flex min-h-11 items-center font-display text-xl font-semibold text-fg no-underline"
          >
            {T.brand}
          </Link>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <nav aria-label={T.navLabel} className="flex items-center gap-4">
              <NavLink to={PATHS.history} className={NAV_LINK}>
                {TEXT.history.navLink}
              </NavLink>
              <AccountLink />
            </nav>
            <ThemeSwitcher />
          </div>
        </div>
      </header>
      {/* min-h-screen: o rodapé começa abaixo da primeira dobra, para não pular (CLS) quando a
          tela carrega e cresce. */}
      <main
        id={MAIN_ID}
        className="mx-auto min-h-screen w-full max-w-5xl flex-1 px-4 py-8 md:px-8 md:py-12"
      >
        <LoginFailedAlert />
        <TermsGate />
      </main>
      {/* Aviso de IA discreto em toda tela (docs/art/LICENSE-ART.md); o detalhe fica nos créditos. */}
      <footer className="border-t border-line-soft">
        <p className="mx-auto w-full max-w-5xl px-4 py-6 text-sm text-fg-muted md:px-8">
          {T.aiArt}{" "}
          <Link to={PATHS.credits} className="font-semibold">
            {T.creditsLink}
          </Link>
        </p>
      </footer>
    </div>
  );
}

/**
 * Acesso, ou Entrar quando o /me diz que não há sessão. Enquanto o /me carrega, fica "Acesso": o
 * lugar do link é o mesmo nos dois casos, para o cabeçalho não pular quando a resposta chega.
 */
function AccountLink(): ReactElement {
  const me = useMe();
  const { pathname, search } = useLocation();
  if (isSignedOut(me.error)) {
    return (
      <NavLink to={signInPath(safeReturnTo(pathname, search))} className={NAV_LINK}>
        {TEXT.account.signIn.navSignIn}
      </NavLink>
    );
  }
  return (
    <NavLink to={PATHS.access} className={NAV_LINK}>
      {TEXT.account.signIn.navAccess}
    </NavLink>
  );
}

/** A tela da rota, ou o aceite dos termos se a conta ainda não aceitou a versão vigente. */
function TermsGate(): ReactElement {
  const me = useMe();
  const { pathname, search } = useLocation();
  const mustAccept =
    me.data?.terms.acceptance_required === true && !TERMS_EXEMPT_PATHS.has(pathname);
  if (mustAccept) {
    return <Navigate replace to={acceptTermsPath(safeReturnTo(pathname, search))} />;
  }
  return <Outlet />;
}

/**
 * O servidor volta para `/?login_error=failed#ref=<request_id>` em toda recusa, sem dizer o motivo
 * (auth_login.py). O código serve para a pessoa citar no pedido de suporte ou no recurso.
 */
function LoginFailedAlert(): ReactElement | null {
  const [params] = useSearchParams();
  const { hash } = useLocation();
  if (params.get(LOGIN_ERROR_PARAM) !== LOGIN_FAILED) {
    return null;
  }
  const ref = loginRefFrom(hash);
  return (
    <div
      role="alert"
      className="mb-8 flex gap-3 rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 text-danger-fg"
    >
      <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      <div className="flex max-w-prose flex-col gap-1">
        <p className="font-semibold">{TEXT.account.signIn.failed}</p>
        {ref !== null && <p className="font-mono text-sm">{TEXT.failure.requestId(ref)}</p>}
      </div>
    </div>
  );
}
