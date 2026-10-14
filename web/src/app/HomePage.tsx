import type { ReactElement } from "react";
import { Link } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";
import { workbenchPath } from "./paths";

// Lugar da Oficina (2.2): receitas, presets e execuções recentes entram com o design (D1, D6, D8).
export function HomePage(): ReactElement {
  return (
    <main>
      <FocusHeading>{TEXT.home.title}</FocusHeading>
      <p>
        <Link to={workbenchPath("csv-janitor")}>
          {TEXT.home.openWorkbench(TEXT.csvJanitor.title)}
        </Link>
      </p>
    </main>
  );
}
