// Textos fixos da interface, num lugar só: a voz (D11) e o idioma (D12) ainda não foram decididos, e
// trocar ou traduzir fica restrito a este arquivo. Os textos são neutros e literais de propósito.
export const TEXT = {
  field: {
    errorPrefix: "Erro: ",
  },
  reorder: {
    instructions:
      "Para mudar a ordem, use os botões Mover para cima e Mover para baixo, ou Alt + seta para cima e Alt + seta para baixo com o foco num deles. Também é possível arrastar.",
    moveUp: "Mover para cima",
    moveDown: "Mover para baixo",
    moved: (name: string, position: number, total: number) =>
      `${name} movido para a posição ${String(position)} de ${String(total)}.`,
  },
  errorSummary: {
    title: "Corrija os itens abaixo antes de continuar.",
  },
  failure: {
    network: "Não foi possível falar com o servidor. Confira a conexão e tente de novo.",
    timeout: "O servidor demorou demais para responder. Tente de novo.",
    unexpected: "O servidor respondeu de um jeito inesperado. Tente de novo em instantes.",
    internal: "Algo deu errado nesta tela. Recarregue a página e tente de novo.",
    requestId: (id: string) => `Código do pedido para suporte: ${id}`,
    problems: {
      BAD_REQUEST:
        "O pedido foi recusado por estar malformado. Recarregue a página e tente de novo.",
      JSON_INVALID:
        "O pedido foi recusado por estar malformado. Recarregue a página e tente de novo.",
      IDEMPOTENCY_KEY_INVALID: "O pedido foi recusado. Recarregue a página e tente de novo.",
      AUTHENTICATION_REQUIRED: "Sua sessão terminou. Entre de novo para continuar.",
      REAUTH_REQUIRED: "Por segurança, entre de novo para continuar.",
      INSUFFICIENT_SCOPE: "Sua credencial não tem permissão para esta ação.",
      FORBIDDEN: "Você não tem permissão para esta ação.",
      CSRF_FAILED:
        "A verificação de segurança da sessão falhou. Recarregue a página e tente de novo.",
      NOT_FOUND: "Não encontramos o que você pediu. Ele pode ter sido removido.",
      METHOD_NOT_ALLOWED: "Esta ação não é aceita aqui.",
      REQUEST_TIMEOUT: "O envio demorou demais e foi interrompido. Tente de novo.",
      STATE_CONFLICT: "O estado mudou enquanto você agia. Atualize a página e tente de novo.",
      IDEMPOTENCY_IN_PROGRESS:
        "O pedido anterior ainda está sendo processado. Aguarde e tente de novo.",
      ACTIVE_RUN_LIMIT:
        "Você já tem uma execução em andamento. Espere ela terminar para começar outra.",
      GONE: "Isto expirou e não está mais disponível.",
      BODY_TOO_LARGE: "O arquivo ou o pedido passa do tamanho permitido.",
      UNSUPPORTED_MEDIA_TYPE: "O tipo de conteúdo enviado não é aceito.",
      VALIDATION_FAILED: "Alguns valores foram recusados. Confira os erros indicados.",
      IDEMPOTENCY_KEY_REUSED:
        "Este envio conflita com um pedido anterior. Recarregue a página e tente de novo.",
      INPUT_NOT_AVAILABLE:
        "Algum arquivo de entrada não está mais disponível. Envie os arquivos de novo.",
      RECIPE_VERSION_RETIRED:
        "Esta versão da receita foi retirada. Recarregue a página para usar a atual.",
      QUOTA_EXCEEDED: "Você atingiu a cota da conta. Libere espaço ou espere a cota renovar.",
      RATE_LIMITED: "Muitos pedidos em pouco tempo. Aguarde um pouco e tente de novo.",
      INTERNAL_ERROR: "Erro interno no servidor. Tente de novo em instantes.",
      QUEUE_FULL: "A fila de execuções está cheia. Tente de novo mais tarde.",
      SERVICE_UNAVAILABLE: "O serviço está indisponível no momento. Tente de novo mais tarde.",
    },
    retryAfter: (seconds: number) => `Tente de novo em ${String(seconds)} s.`,
  },
  csvJanitor: {
    title: "CSV Janitor",
    legend: "Parâmetros da limpeza",
    inputDelimiter: "Delimitador dos arquivos de entrada",
    outputDelimiter: "Delimitador dos arquivos de saída",
    delimiters: { ",": "Vírgula (,)", ";": "Ponto e vírgula (;)", "\t": "Tabulação" },
    columnListHint: "Uma coluna por linha, com o nome exato do cabeçalho.",
    trimColumns: "Colunas sem espaços nas pontas",
    trimColumnsHint: "Remove espaços do início e do fim dos valores; os espaços internos ficam.",
    deduplicateBy: "Remover duplicatas pelas colunas",
    deduplicateByHint: "Mantém a primeira ocorrência. Deixe vazio para não remover.",
    outputColumns: "Colunas finais, na ordem",
    outputColumnsHint: "Deixe vazio para manter todas as colunas na ordem original.",
    formulaGuard: "Células que parecem fórmulas de planilha",
    formulaGuards: {
      none: "Manter o texto e só avisar",
      tab_prefix: "Prefixar com tabulação (recomendação da OWASP)",
    },
    caseRules: "Regras de caixa",
    caseRulesHint: "Converte os valores de uma coluna para minúsculas ou maiúsculas.",
    caseModes: { lower: "Minúsculas", upper: "Maiúsculas" },
    addCaseRule: "Adicionar regra de caixa",
    filters: "Filtros",
    filtersHint: "Mantém só os registros que atendem a todos os filtros, comparando como texto.",
    filterOps: {
      eq: "é igual a",
      ne: "é diferente de",
      contains: "contém",
      is_empty: "está vazio",
    },
    addFilter: "Adicionar filtro",
    column: (row: number) => `Coluna (linha ${String(row)})`,
    mode: (row: number) => `Modo (linha ${String(row)})`,
    op: (row: number) => `Condição (linha ${String(row)})`,
    value: (row: number) => `Valor (linha ${String(row)})`,
    removeRow: (row: number) => `Remover linha ${String(row)}`,
    errors: {
      columnRequired: "Informe a coluna.",
      tooManyColumns: (max: number) => `Informe no máximo ${String(max)} colunas.`,
      tooManyRows: (max: number) => `Use no máximo ${String(max)} linhas.`,
      invalidOption: "Escolha uma das opções.",
    },
  },
} as const;
