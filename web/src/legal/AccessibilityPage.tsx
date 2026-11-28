import type { ReactElement } from "react";

import accessibility from "../../../docs/acessibilidade.md?raw";
import { TEXT } from "../text";
import { LegalPage } from "./LegalPage";

/**
 * /acessibilidade: a declaração WCAG 2.2 AA, num chunk próprio. O texto fica em docs/, fora dos
 * textos jurídicos aceitos (docs/legal/), e tests/test_accessibility_statement.py o amarra às specs
 * do CI.
 */
export function AccessibilityPage(): ReactElement {
  return <LegalPage fallbackTitle={TEXT.legal.accessibilityTitle} markdown={accessibility} />;
}
