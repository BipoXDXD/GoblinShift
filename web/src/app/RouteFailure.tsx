import { useEffect, type ReactElement } from "react";
import { useRouteError } from "react-router";

import { ProblemAlert } from "../components/feedback/ProblemAlert";

/**
 * Aviso de falha ao abrir uma tela, dentro da moldura. O detalhe vai só para o console: um `lazy`
 * rejeitado chega aqui sem passar pelo `componentDidCatch`, e sem este registro não deixaria rastro.
 */
export function RouteFailure(): ReactElement {
  const error = useRouteError();
  useEffect(() => {
    console.error("Falha ao abrir a tela:", error);
  }, [error]);
  return (
    <>
      <ProblemAlert failure={{ kind: "internal" }} />
      <button type="button" className="size-11" />
    </>
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
