import type { ReactElement } from "react";
import { Link } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";
import { PATHS } from "./paths";

export function NotFoundPage(): ReactElement {
  return (
    <main>
      <FocusHeading>{TEXT.notFound.title}</FocusHeading>
      <p>
        <Link to={PATHS.home}>{TEXT.notFound.backHome}</Link>
      </p>
    </main>
  );
}
