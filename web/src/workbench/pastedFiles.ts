/** Nome que o navegador dá a uma imagem colada da área de transferência (print, "Copiar imagem"). */
const GENERIC_IMAGE_NAME = /^image\.(png|jpe?g|gif|webp)$/i;

/** O mínimo de um `DataTransfer` que a colagem lê: só os arquivos. */
interface ClipboardFiles {
  readonly files: ArrayLike<File>;
}

/**
 * Os arquivos de uma colagem, na ordem da área de transferência. Toda imagem colada chega com o
 * mesmo nome genérico (`image.png`); ela vira `colado-<n>.<ext>`, com o mesmo conteúdo e tipo,
 * para duas colagens não aparecerem na lista com o mesmo nome. `nextIndex` dá o `n` e não volta
 * a 1 dentro da sessão da Bancada.
 */
export function filesFromClipboard(data: ClipboardFiles | null, nextIndex: () => number): File[] {
  if (data === null) {
    return [];
  }
  return Array.from(data.files, (file) => {
    const generic = GENERIC_IMAGE_NAME.exec(file.name);
    if (generic === null) {
      return file;
    }
    const extension = (generic[1] ?? "png").toLowerCase();
    return new File([file], `colado-${String(nextIndex())}.${extension}`, {
      type: file.type,
      lastModified: file.lastModified,
    });
  });
}
