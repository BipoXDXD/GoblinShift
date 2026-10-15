import { RETURN_PARAM } from "../app/paths";
import { safeReturnTo } from "./signIn";

/**
 * O `?volta=` da tela atual como caminho local seguro, ou `/`. O valor vem da URL, então passa
 * pelo mesmo padrão do servidor antes de virar link.
 */
export function returnPathFrom(search: string): string {
  const raw = new URLSearchParams(search).get(RETURN_PARAM);
  if (raw === null) {
    return "/";
  }
  const [pathname = "", query = ""] = raw.split(/\?(.*)/s, 2);
  return safeReturnTo(pathname, query === "" ? "" : `?${query}`);
}
