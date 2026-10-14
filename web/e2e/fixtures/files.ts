// Entradas sintéticas e pequenas (decisão da fatia 5): nada de dado real no repositório.

export interface InputFile {
  readonly name: string;
  readonly mimeType: string;
  readonly buffer: Buffer;
}

function text(name: string, mimeType: string, content: string): InputFile {
  return { name, mimeType, buffer: Buffer.from(content, "utf8") };
}

// PNG 1×1 válidos (um pixel de cor diferente em cada), para a API real também aceitar.
const RED_PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const BLUE_PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

export const CSV_FILES = [
  text("clientes-a.csv", "text/csv", "nome,email\n Ana ,ana@example.com\nBeto,beto@example.com\n"),
  text("clientes-b.csv", "text/csv", "nome,email\nCaio,caio@example.com\n Ana ,ana@example.com\n"),
] as const;

export const PNG_FILES = [
  { name: "vermelho.png", mimeType: "image/png", buffer: Buffer.from(RED_PIXEL_PNG, "base64") },
  { name: "azul.png", mimeType: "image/png", buffer: Buffer.from(BLUE_PIXEL_PNG, "base64") },
] as const;

export const DOWNLOADS_FILES = [
  text("notas.txt", "text/plain", "lista de compras\n"),
  text("planilha.csv", "text/csv", "a,b\n1,2\n"),
] as const;
