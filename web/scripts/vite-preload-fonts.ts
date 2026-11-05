import type { Plugin } from "vite";

/**
 * Injeta o preload das fontes cujo arquivo final casa com algum de `patterns`. O nome tem hash do
 * build, por isso o link não pode ir escrito no index.html.
 */
export function preloadFonts(patterns: readonly RegExp[]): Plugin {
  return {
    name: "goblinshift-preload-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, context) {
        const bundle = context.bundle ?? {};
        return Object.keys(bundle)
          .filter((fileName) => patterns.some((pattern) => pattern.test(fileName)))
          .map((fileName) => ({
            tag: "link",
            attrs: {
              rel: "preload",
              as: "font",
              type: "font/woff2",
              crossorigin: "",
              href: `/${fileName}`,
            },
            injectTo: "head-prepend" as const,
          }));
      },
    },
  };
}
