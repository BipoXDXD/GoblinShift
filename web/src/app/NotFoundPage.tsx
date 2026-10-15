import { ChevronLeft } from "lucide-react";
import type { ReactElement } from "react";
import { Link } from "react-router";

import { GoblinIllustration } from "../art/GoblinIllustration";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";
import { PATHS } from "./paths";

export function NotFoundPage(): ReactElement {
  return (
    <div className="flex flex-col gap-4">
      <FocusHeading>{TEXT.notFound.title}</FocusHeading>
      <GoblinIllustration state="erro" />
      <p>
        <Link to={PATHS.home} className="inline-flex min-h-11 items-center gap-1 font-semibold">
          <ChevronLeft aria-hidden="true" className="size-4" />
          {TEXT.notFound.backHome}
        </Link>
      </p>
    </div>
  );
}
