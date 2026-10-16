import { ChevronLeft } from "lucide-react";
import type { ReactElement } from "react";
import { Link } from "react-router";

import { GoblinIllustration } from "../art/GoblinIllustration";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";
import { PATHS } from "./paths";

/**
 * Rota inexistente. O Nginx serve o index.html com 200 para qualquer caminho da SPA; o `noindex`
 * (que o React leva ao <head> e tira ao sair da tela) evita que a página entre na busca.
 */
export function NotFoundPage(): ReactElement {
  return (
    <div className="flex flex-col gap-4">
      <meta name="robots" content="noindex" />
      <FocusHeading>{TEXT.notFound.title}</FocusHeading>
      <GoblinIllustration state="erro" rotationKey="pagina-nao-encontrada" />
      <p>
        <Link to={PATHS.home} className="inline-flex min-h-11 items-center gap-1 font-semibold">
          <ChevronLeft aria-hidden="true" className="size-4" />
          {TEXT.notFound.backHome}
        </Link>
      </p>
    </div>
  );
}
