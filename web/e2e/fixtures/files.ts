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

/**
 * PDF mínimo e válido, de uma página, montado aqui com o xref calculado: nenhum PDF de terceiros
 * no repositório, e a API real também o aceita (cabeçalho %PDF-, sem recurso ativo).
 */
function onePagePdf(name: string, marker: string): InputFile {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>",
    `<< /Length ${String(marker.length + 3)} >>\nstream\n% ${marker}\nendstream`,
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(body.length);
    body += `${String(index + 1)} 0 obj\n${object}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${String(objects.length + 1)}\n0000000000 65535 f \n`;
  body += offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  body += `trailer << /Root 1 0 R /Size ${String(objects.length + 1)} >>\nstartxref\n${String(xref)}\n%%EOF\n`;
  return { name, mimeType: "application/pdf", buffer: Buffer.from(body, "latin1") };
}

export const PDF_FILES = [
  onePagePdf("contrato.pdf", "contrato"),
  onePagePdf("anexo.pdf", "anexo"),
] as const;

/**
 * JPEG 8×6 de uma cor só, como o preview do Image Forge (qualidade 80): o navegador tem que
 * decodificá-lo de verdade para a spec do preview conferir a imagem na tela.
 */
export const PREVIEW_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwBKKKK+dMj/2Q==",
  "base64",
);
