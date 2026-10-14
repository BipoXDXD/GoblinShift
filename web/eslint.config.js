// @ts-check
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "src/api/schema.d.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      jsxA11y.flatConfigs.strict,
      reactHooks.configs.flat.recommended,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // Regras de TypeScript do projeto (seção TypeScript do fonte externa.md global e ETS).
      "@typescript-eslint/consistent-type-assertions": [
        "error",
        { assertionStyle: "as", objectLiteralTypeAssertions: "never" },
      ],
      "@typescript-eslint/consistent-type-definitions": ["error", "interface"],
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/explicit-module-boundary-types": "error",
      "@typescript-eslint/switch-exhaustiveness-check": [
        "error",
        { considerDefaultExhaustiveForUnions: false, requireDefaultForNonUnion: true },
      ],
      "@typescript-eslint/use-unknown-in-catch-callback-variable": "error",
      "@typescript-eslint/no-shadow": "error",
      "@typescript-eslint/parameter-properties": ["error", { prefer: "class-property" }],
      "@typescript-eslint/max-params": ["error", { max: 3 }],
      "@typescript-eslint/ban-ts-comment": [
        "error",
        { "ts-expect-error": "allow-with-description", "ts-ignore": true, "ts-nocheck": true },
      ],
      "no-restricted-syntax": [
        "error",
        { selector: "TSEnumDeclaration", message: "Use união de literais ou objeto `as const`." },
        { selector: "TSModuleDeclaration[kind='namespace']", message: "Sem namespace." },
      ],
      "no-restricted-properties": [
        "error",
        {
          object: "Response",
          property: "json",
          message: "Leia o corpo como unknown e faça o parse na fronteira (src/api).",
        },
      ],
      eqeqeq: "error",
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      // Testes montam respostas falsas; a checagem de tipo continua ligada.
      "@typescript-eslint/max-params": "off",
    },
  },
  {
    files: ["e2e/**/*.ts"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      // O `use` dos fixtures do Playwright não é o hook do React; a regra confunde os dois.
      "react-hooks/rules-of-hooks": "off",
    },
  },
  {
    files: ["*.js"],
    extends: [js.configs.recommended, tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },
  {
    // Scripts clássicos servidos como estão, fora do bundle (theme-init.js).
    files: ["public/*.js"],
    extends: [js.configs.recommended, tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.browser, sourceType: "script" },
  },
  prettier,
);
