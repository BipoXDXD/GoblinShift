import { ChevronLeft } from "lucide-react";
import { useEffect, type ReactElement } from "react";
import { Link, useRouteError } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { ProblemAlert } from "../components/feedback/ProblemAlert";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import { documentTitle } from "./pageTitle";
import { PATHS } from "./paths";

const T = TEXT.routeFailure;

/**
 * Tela que não abriu, dentro da moldura. O detalhe vai só para o console: um `lazy` rejeitado chega
 * aqui sem passar pelo `componentDidCatch`, e sem este registro não deixaria rastro.
 */
export function RouteFailure(): ReactElement {
  const error = useRouteError();
  useEffect(() => {
    console.error("Falha ao abrir a tela:", error);
  }, [error]);
  // Efeito passivo: roda depois do título da rota (efeito de layout da moldura) e o substitui, para a
  // aba não dizer que a tela abriu (WCAG 2.4.2).
  useEffect(() => {
    document.title = documentTitle(T.pageTitle);
  }, []);
  return (
    <div className="flex flex-col gap-4">
      <FocusHeading>{T.title}</FocusHeading>
      <ProblemAlert failure={{ kind: "internal" }} />
      <div className="flex flex-wrap items-center gap-4">
        <Button
          type="button"
          variant="primary"
          onClick={() => {
            window.location.reload();
          }}
        >
          {T.reload}
        </Button>
        <Link to={PATHS.home} className="inline-flex min-h-11 items-center gap-1 font-semibold">
          <ChevronLeft aria-hidden="true" className="size-4" />
          {T.backHome}
        </Link>
      </div>
    </div>
  );
}

/** O mesmo aviso quando a própria moldura quebrou: sem ela, a página fica só com o `<main>`. */
export function RootFailure(): ReactElement {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 md:px-8 md:py-12">
      <RouteFailure />
    </main>
  );
}
