/** O que o navegador diz sobre a pessoa e o aparelho, lido só quando a decisão precisa. */
export interface Environment3D {
  readonly prefersReducedMotion: () => boolean;
  /** Avisa quando a preferência de movimento muda; devolve o cancelamento. */
  readonly subscribeReducedMotion: (onChange: () => void) => () => void;
  /** `Save-Data` do navegador (Network Information API, só no Chromium). */
  readonly savesData: () => boolean;
  /** WebGL 2 acelerado por GPU; WebGL só em software conta como ausente. */
  readonly supportsWebGL2: () => boolean;
}

/**
 * Carrega a cena 3D só sem movimento reduzido, sem economia de dados e com WebGL 2 (ADR 027 e
 * 030); em qualquer outro caso fica a imagem estática. As preferências vêm primeiro porque o teste
 * de WebGL cria um contexto, que custa memória de GPU.
 */
export function shouldLoad3D(environment: Environment3D): boolean {
  return (
    !environment.prefersReducedMotion() && !environment.savesData() && environment.supportsWebGL2()
  );
}

interface NetworkInformationLike {
  readonly saveData?: boolean;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export const browserEnvironment: Environment3D = {
  prefersReducedMotion: () => window.matchMedia(REDUCED_MOTION_QUERY).matches,
  subscribeReducedMotion: (onChange) => {
    const query = window.matchMedia(REDUCED_MOTION_QUERY);
    query.addEventListener("change", onChange);
    return () => {
      query.removeEventListener("change", onChange);
    };
  },
  savesData: () =>
    (navigator as Navigator & { readonly connection?: NetworkInformationLike }).connection
      ?.saveData === true,
  supportsWebGL2: () => {
    const context = document
      .createElement("canvas")
      .getContext("webgl2", { failIfMajorPerformanceCaveat: true });
    if (!context) {
      return false;
    }
    const info = context.getExtension("WEBGL_debug_renderer_info");
    const renderer: unknown =
      info === null ? null : context.getParameter(info.UNMASKED_RENDERER_WEBGL);
    // Devolve a memória da GPU na hora: este contexto só respondeu à pergunta.
    context.getExtension("WEBGL_lose_context")?.loseContext();
    return !isSoftwareRenderer(typeof renderer === "string" ? renderer : null);
  },
};

const SOFTWARE_RENDERER = /swiftshader|llvmpipe|softpipe|software|basic render/i;

/**
 * Sem GPU, o navegador cai para WebGL em software (SwiftShader, llvmpipe), que trava a página por
 * segundos para desenhar a cena (medido: 2,3 s de tarefa longa no SwiftShader). A ressalva de
 * desempenho do `getContext` nem sempre o recusa, então o nome do renderizador decide; sem o nome
 * (navegador que o esconde), vale a ressalva.
 */
export function isSoftwareRenderer(renderer: string | null): boolean {
  return renderer !== null && SOFTWARE_RENDERER.test(renderer);
}
