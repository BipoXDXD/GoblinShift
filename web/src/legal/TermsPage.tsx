import type { ReactElement } from "react";

import terms from "../../../docs/legal/termos-de-uso.md?raw";
import { TEXT } from "../text";
import { LegalPage } from "./LegalPage";

/** /termos: um chunk próprio, com o texto embutido no build. */
export function TermsPage(): ReactElement {
  return <LegalPage fallbackTitle={TEXT.legal.termsTitle} markdown={terms} />;
}
