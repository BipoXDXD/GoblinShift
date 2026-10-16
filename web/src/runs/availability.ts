import { formatDateTime } from "../format";
import { TEXT } from "../text";
import type { RunResults } from "./commands";

const T = TEXT.run.results;

type Availability = RunResults["artifacts"][number]["availability"];

/**
 * Por que uma referência do /results não pode ser baixada, ou null se pode. Valor que a spec
 * ganhar antes de ter texto próprio cai em "indisponível": nunca vira link de download.
 */
export function unavailableReason(
  availability: Availability,
  expiresAt: string | null,
): string | null {
  switch (availability) {
    case "available":
      return null;
    case "expired":
      return expiresAt === null ? T.fileExpired : T.fileExpiredOn(formatDateTime(expiresAt));
    case "deleted":
      return T.fileDeleted;
    case "missing":
      return T.fileMissing;
    default:
      return T.fileUnavailable;
  }
}
