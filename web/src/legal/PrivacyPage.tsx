import type { ReactElement } from "react";

import privacy from "../../../docs/legal/aviso-de-privacidade.md?raw";
import { TEXT } from "../text";
import { LegalPage } from "./LegalPage";

/** /privacidade: um chunk próprio, com o texto embutido no build. */
export function PrivacyPage(): ReactElement {
  return <LegalPage fallbackTitle={TEXT.legal.privacyTitle} markdown={privacy} />;
}
