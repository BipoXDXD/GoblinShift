// Seleção dos casos do sabotage.ts pelo filtro da linha de comando. Um filtro que não seleciona
// nada é erro: com zero casos o script dizia que "os 0 defeitos" foram pegos e saía verde.

/** Palavras mais curtas ("do", "no") casam com quase todo nome e não ajudam a sugerir. */
const MIN_WORD_LENGTH = 3;

type Selection<T> =
  | { readonly found: true; readonly cases: readonly T[] }
  | { readonly found: false; readonly message: string };

export function selectCases<T extends { readonly name: string }>(
  cases: readonly T[],
  filter: string,
): Selection<T> {
  const selected = cases.filter((sabotage) => sabotage.name.includes(filter));
  if (selected.length > 0) {
    return { found: true, cases: selected };
  }
  const similar = cases.filter((sabotage) => sharesWord(sabotage.name, filter));
  const heading = similar.length > 0 ? "Os mais parecidos" : "Os casos disponíveis";
  const names = (similar.length > 0 ? similar : cases).map((sabotage) => `  ${sabotage.name}`);
  return {
    found: false,
    message: `Nenhum caso de sabotagem contém "${filter}". ${heading}:\n${names.join("\n")}`,
  };
}

function sharesWord(name: string, filter: string): boolean {
  const nameWords = new Set(words(name));
  return words(filter).some((word) => nameWords.has(word));
}

function words(text: string): string[] {
  return text
    .toLocaleLowerCase("pt-BR")
    .split(/[^\p{L}\p{N}]+/u)
    .filter((word) => word.length >= MIN_WORD_LENGTH);
}
