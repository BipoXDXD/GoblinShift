// Inicialização do Swagger UI da /docs. Fica num arquivo, e não num <script> inline, porque a CSP
// da página só aceita script da própria origem (api_docs.py). Mesma configuração padrão do FastAPI.
window.ui = SwaggerUIBundle({
  url: "/openapi.json",
  dom_id: "#swagger-ui",
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
});
