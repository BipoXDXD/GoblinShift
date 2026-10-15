import { ChevronLeft } from "lucide-react";
import type { ReactElement } from "react";
import { Link } from "react-router";

import { PATHS } from "../app/paths";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";

const T = TEXT.account.deletion;

/** Depois do 202 do DELETE /me: a sessão já acabou, e a purga corre em até 24 h. */
export function AccountDeletedPage(): ReactElement {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <FocusHeading>{T.doneTitle}</FocusHeading>
      <p className="max-w-prose">{T.doneBody}</p>
      <p>
        <Link to={PATHS.home} className="inline-flex min-h-11 items-center gap-1 font-semibold">
          <ChevronLeft aria-hidden="true" className="size-4" />
          {T.backHome}
        </Link>
      </p>
    </div>
  );
}
