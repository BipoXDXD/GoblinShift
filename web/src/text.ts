// Textos fixos da interface, num lugar só (D12: só pt-BR; traduzir fica restrito a este arquivo).
// Voz (D11): literal em erros, rótulos e botões; uma frase leve sobre os goblins só no vazio, na
// espera e no sucesso, sempre depois da informação.
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
      TERMS_NOT_ACCEPTED: "Para continuar, aceite a versão atual dos termos de uso.",
      NOT_FOUND: "Não encontramos o que você pediu. Ele pode ter sido removido.",
      METHOD_NOT_ALLOWED: "Esta ação não é aceita aqui.",
      REQUEST_TIMEOUT: "O envio demorou demais e foi interrompido. Tente de novo.",
      STATE_CONFLICT: "O estado mudou enquanto você agia. Atualize a página e tente de novo.",
      IDEMPOTENCY_IN_PROGRESS:
        "O pedido anterior ainda está sendo processado. Aguarde e tente de novo.",
      ACTIVE_RUN_LIMIT:
        "Você já tem uma execução em andamento. Espere ela terminar para começar outra.",
      GONE: "Isto expirou e não está mais disponível.",
      PRECONDITION_FAILED: "Isto foi alterado em outro lugar. Atualize a página e tente de novo.",
      BODY_TOO_LARGE: "O arquivo ou o pedido passa do tamanho permitido.",
      UNSUPPORTED_MEDIA_TYPE: "O tipo de conteúdo enviado não é aceito.",
      VALIDATION_FAILED: "Alguns valores foram recusados. Confira os erros indicados.",
      IDEMPOTENCY_KEY_REUSED:
        "Este envio conflita com um pedido anterior. Recarregue a página e tente de novo.",
      INPUT_NOT_AVAILABLE:
        "Algum arquivo de entrada não está mais disponível. Envie os arquivos de novo.",
      RECIPE_VERSION_RETIRED:
        "Esta versão da receita foi retirada. Recarregue a página para usar a atual.",
      PRECONDITION_REQUIRED: "O pedido foi recusado. Recarregue a página e tente de novo.",
      QUOTA_EXCEEDED: "Você atingiu a cota da conta. Libere espaço ou espere a cota renovar.",
      RATE_LIMITED: "Muitos pedidos em pouco tempo. Aguarde um pouco e tente de novo.",
      INTERNAL_ERROR: "Erro interno no servidor. Tente de novo em instantes.",
      QUEUE_FULL: "A fila de execuções está cheia. Tente de novo mais tarde.",
      SERVICE_UNAVAILABLE: "O serviço está indisponível no momento. Tente de novo mais tarde.",
    },
    retryAfter: (seconds: number) => `Tente de novo em ${String(seconds)} s.`,
    retryAfterMinutes: (minutes: number) =>
      minutes === 1 ? "Tente de novo em 1 minuto." : `Tente de novo em ${String(minutes)} minutos.`,
  },
  locale: "pt-BR",
  theme: {
    summary: "Tema",
    legend: "Tema da interface",
    options: { system: "Sistema", light: "Claro", dark: "Escuro" },
  },
  layout: {
    navLabel: "Principal",
    skipToContent: "Pular para o conteúdo",
    brand: "GoblinShift",
    aiArt: "Ilustrações geradas com IA e editadas.",
    creditsLink: "Créditos",
    footerNavLabel: "Rodapé",
  },
  pageTitles: {
    workbench: "Bancada",
    acceptTerms: "Aceite dos termos",
  },
  notice: {
    // O tipo escrito acompanha a cor da faixa: o aviso nunca depende só dela (WCAG 1.4.1).
    kinds: {
      incident: "Incidente de segurança",
      shutdown: "Encerramento do serviço",
      "terms-change": "Mudança nos termos",
    },
    regionLabel: (kind: string) => `Aviso: ${kind.toLowerCase()}`,
    dismiss: "Fechar",
  },
  home: {
    title: "Arquivos em lote, limpos pelos goblins da oficina.",
    lead: "Escolha uma receita para começar.",
    recipesHeading: "Receitas",
    openWorkbench: "Abrir a Bancada",
    // Completa o nome acessível do link sem repetir o título do cartão na tela.
    openWorkbenchOf: (recipe: string) => `da ${recipe}`,
    summaries: {
      "csv-janitor":
        "Limpa tabelas CSV: espaços nas pontas, duplicatas, colunas, caixa e filtros, sem adivinhar o significado dos dados.",
      "image-forge":
        "Reduz e converte imagens em lote, mantendo a proporção, e gera miniaturas se você quiser.",
      "downloads-detox":
        "Organiza uma pasta bagunçada: tira os repetidos, separa por tipo e renomeia em ordem.",
      "pdf-press":
        "Junta, separa ou escolhe páginas de PDFs, com o mapa de cada página, sem abrir o arquivo no navegador.",
    },
  },
  notFound: { title: "Página não encontrada", backHome: "Voltar à Oficina" },
  // Texto alternativo das goblins (ADR 030): diz quem é e como é; o estado já está no texto ao lado.
  art: {
    hero: "Rúbia, goblin da oficina, de maria-chiquinhas vermelhas e vestido preto de renda.",
    goblins: {
      lua: { name: "Lua", alt: "Lua, goblin de cabelo preto, com um sininho dourado no pescoço." },
      ada: { name: "Ada", alt: "Ada, goblin inventora, de óculos e jaqueta de couro." },
      nyx: { name: "Nyx", alt: "Nyx, goblin de cabelo preto com pontas rosa e vestido preto." },
      ferrugem: {
        name: "Ferrugem",
        alt: "Ferrugem, goblin de cabelo laranja volumoso e roupa de couro preto.",
      },
      jade: { name: "Jade", alt: "Jade, goblin de cabelo castanho, blusa mostarda e calça jeans." },
      fagulha: {
        name: "Fagulha",
        alt: "Fagulha, goblin ruiva de cabelo cacheado, com espartilho de couro e luvas pretas.",
      },
    },
  },
  workbench: {
    title: (recipe: string) => `Bancada: ${recipe}`,
    backHome: "Oficina",
    filesHeading: "Arquivos de entrada",
    chooseFiles: "Escolher arquivos",
    chooseFilesHint: "Pode escolher vários de uma vez, ou soltar os arquivos aqui.",
    dropHere: "Solte para adicionar.",
    filesChosen: (count: number) =>
      count === 0
        ? "Nenhum arquivo escolhido"
        : count === 1
          ? "1 arquivo escolhido"
          : `${String(count)} arquivos escolhidos`,
    orderLabel: "Ordem de processamento",
    noFilesYet: "A bancada está vazia",
    noFilesYetHint:
      "Os arquivos aparecem aqui, na ordem em que serão processados. Escolha os arquivos e os goblins começam.",
    limitsHint: (maxInputs: number, maxTotal: string | null) =>
      maxTotal === null
        ? `Até ${String(maxInputs)} arquivos por execução.`
        : `Até ${String(maxInputs)} arquivos por execução, somando no máximo ${maxTotal}.`,
    parametersHeading: "Parâmetros",
    run: "Executar",
    creatingRun: "Criando a execução.",
    loadingCatalog: "Carregando a receita.",
    recipeUnavailable:
      "Esta receita não está disponível no momento. Recarregue a página ou tente mais tarde.",
    upload: {
      waiting: "Na fila de envio",
      sending: (percent: number) => `Enviando: ${String(percent)}%`,
      progressLabel: (name: string) => `Envio de ${name}`,
      confirming: "Confirmando o recebimento",
      ready: "Enviado",
      canceled: "Cancelado",
      retry: "Enviar de novo",
      remove: "Remover",
      announceReady: (name: string) => `${name} enviado.`,
      announceFailed: (name: string) => `Falha ao enviar ${name}.`,
    },
    removal: {
      titleOne: (name: string) => `Remover ${name}?`,
      bodyOne:
        "O arquivo sai do lote e é excluído do servidor. Para usá-lo de novo, escolha-o outra vez.",
      titleAll: (count: number) => `Remover os ${String(count)} arquivos?`,
      bodyAll:
        "Todos os arquivos saem do lote e são excluídos do servidor. Para usá-los de novo, escolha-os outra vez.",
      confirm: "Remover",
      confirmAll: "Remover todos",
      cancel: "Cancelar",
      removeAll: "Remover todos",
      deletionFailedOne: (name: string) => `Não foi possível excluir ${name} do servidor.`,
      deletionFailedMany: (count: number) =>
        `Não foi possível excluir ${String(count)} arquivos do servidor.`,
      deletionFailedHintOne:
        "O arquivo já saiu do lote e não entra na execução. Sem a exclusão, o servidor o apaga sozinho em até 24 horas.",
      deletionFailedHintMany:
        "Os arquivos já saíram do lote e não entram na execução. Sem a exclusão, o servidor os apaga sozinho em até 24 horas.",
      retryDeletion: "Tentar excluir de novo",
    },
    serverFieldErrors: {
      missing: "Campo obrigatório.",
      string_too_short: "Preencha este campo.",
      too_short: "Informe pelo menos um item.",
      too_long: "Há itens demais neste campo.",
      string_too_long: "O texto passa do tamanho permitido.",
      greater_than_equal: "O valor fica abaixo do mínimo permitido.",
      less_than_equal: "O valor passa do máximo permitido.",
      int_type: "Informe um número inteiro.",
      bool_type: "Escolha sim ou não.",
      string_pattern_mismatch: "O valor não está no formato pedido.",
      enum: "Escolha uma das opções.",
      literal_error: "Escolha uma das opções.",
      extra_forbidden: "Este campo não é aceito.",
      fallback: "O servidor recusou este valor.",
    },
    errors: {
      noFiles: "Escolha pelo menos um arquivo.",
      uploading: "Espere o envio dos arquivos terminar.",
      failedFiles: "Remova ou envie de novo os arquivos que falharam.",
      tooManyFiles: (max: number) => `Use no máximo ${String(max)} arquivos.`,
      tooManyBytes: (total: string, max: string) =>
        `Os arquivos somam ${total}; o máximo por execução é ${max}.`,
    },
  },
  run: {
    title: "Execução",
    loading: "Carregando a execução.",
    recipe: "Receita",
    status: "Estado",
    phase: "Fase",
    attempt: "Tentativa",
    progress: "Arquivos processados",
    progressValue: (processed: number, total: number) => `${String(processed)} de ${String(total)}`,
    queueAhead: "Execuções à frente na fila",
    stepsLabel: "Etapas da execução",
    // Rótulos curtos das etapas; a frase da fase atual aparece grande, acima delas.
    steps: {
      inspecting: "Inspecionar",
      processing: "Processar",
      packaging: "Empacotar",
      finalizing: "Finalizar",
    },
    stepDone: "(concluída)",
    failureReason: "Motivo",
    newRun: "Nova execução nesta receita",
    items: {
      heading: "Arquivos da execução",
      listLabel: "Arquivos, na ordem de entrada",
      entry: (position: number) => `Arquivo ${String(position)}`,
      summary: (parts: {
        inputs: string;
        ok: string;
        rejected: string;
        duplicates: string;
        outputs: string;
      }) =>
        `${parts.inputs}: ${parts.ok}, ${parts.rejected}, ${parts.duplicates}. ${parts.outputs}.`,
      nouns: {
        inputs: ["arquivo", "arquivos"],
        ok: ["processado", "processados"],
        rejected: ["rejeitado", "rejeitados"],
        duplicates: ["repetido", "repetidos"],
        outputs: ["arquivo gerado", "arquivos gerados"],
      },
      statuses: {
        pending: "Aguardando",
        ok: "Processado",
        rejected: "Rejeitado",
        duplicate: "Repetido",
      },
      okWithWarnings: "Processado com aviso",
      unknownStatus: "Estado desconhecido",
      unknownRejection: (code: string) => `Motivo desconhecido (${code}).`,
      unknownWarning: (code: string) => `Aviso desconhecido (${code}).`,
      rejections: {
        UNSUPPORTED_FORMAT: "O formato do arquivo não é aceito por esta receita.",
        UNSUPPORTED_BIT_DEPTH: "A profundidade de cor da imagem não é aceita.",
        UNSUPPORTED_COLOR_PROFILE: "O perfil de cor da imagem não é aceito; só sRGB e Display P3.",
        IMAGE_TOO_LARGE: "A imagem passa de 24 megapixels ou de 8192 px num dos lados.",
        CSV_STRUCTURE_INVALID: "A estrutura do CSV é inválida (aspas, colunas ou codificação).",
        FIELD_LIMIT_EXCEEDED:
          "A tabela passa do limite de registros, de colunas ou do tamanho de um campo.",
      },
      warnings: {
        CSV_ALL_RECORDS_FILTERED: "Os filtros tiraram todos os registros desta tabela.",
        CSV_FORMULA_CELLS:
          "Há células que parecem fórmulas de planilha. Abra a saída com cuidado, ou use a opção de prefixar.",
        CSV_FORMULA_HEADER: "O cabeçalho tem nomes que parecem fórmulas de planilha.",
        INPUT_NOT_SELECTED: "Nenhuma página deste PDF foi escolhida; ele não entrou no resultado.",
      },
      notices: {
        IMAGE_P3_CONVERTED: "A imagem estava em Display P3 e foi convertida para sRGB.",
      },
      unknownNotice: (code: string) => `Nota desconhecida (${code}).`,
      duplicateOf: (position: number) =>
        `Igual ao arquivo ${String(position)}; não gerou outra saída.`,
      counts: {
        records_read: (n: string) => `${n} registros lidos`,
        filtered_out: (n: string) => `${n} filtrados`,
        duplicates_removed: (n: string) => `${n} duplicatas removidas`,
        written: (n: string) => `${n} gravados`,
        blank_lines_ignored: (n: string) => `${n} linhas vazias ignoradas`,
      },
    },
    cancel: {
      open: "Cancelar execução",
      title: "Cancelar esta execução?",
      body: "A execução para e não publica resultados. Os arquivos enviados continuam na sua conta até expirarem.",
      confirm: "Cancelar execução",
      keepGoing: "Continuar a execução",
      requested:
        "Cancelamento pedido. A execução para assim que o trabalho em curso for interrompido.",
      canceled: "Execução cancelada.",
      alreadyFinished: "A execução já tinha terminado; não havia o que cancelar.",
    },
    backToWorkbench: (recipe: string) => `Bancada da ${recipe}`,
    flavor: {
      queued: "Os goblins pegam este lote assim que uma bancada vagar.",
      succeeded: "Pronto! Os goblins terminaram o lote.",
    },
    statuses: {
      queued: "Na fila",
      running: "Em andamento",
      stopping: "Cancelando",
      succeeded: "Concluída",
      succeeded_with_warnings: "Concluída com avisos",
      canceled: "Cancelada",
      failed: "Falhou",
    },
    phases: {
      inspecting: "Inspecionando as entradas",
      processing: "Processando os arquivos",
      packaging: "Empacotando os resultados",
      finalizing: "Finalizando",
      retry_wait: "Aguardando nova tentativa",
    },
    results: {
      heading: "Resultados",
      loading: "Carregando os resultados.",
      download: (fileName: string) => `Baixar ${fileName}`,
      otherFiles: "Arquivos avulsos",
      roles: {
        output: "arquivo processado",
        report: "relatório",
        manifest: "manifesto",
        package: "pacote com todos os arquivos",
      },
      availableUntil: (date: string) => `Disponíveis até ${date}.`,
      empty: "Nenhum arquivo para baixar",
      emptyHint: "Esta execução não publicou arquivos, ou eles já expiraram.",
    },
  },
  recipeFields: {
    invalidOption: "Escolha uma das opções.",
  },
  csvJanitor: {
    title: "CSV Janitor",
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
    },
  },
  imageForge: {
    title: "Image Forge",
    maxWidth: "Largura máxima, em pixels",
    maxHeight: "Altura máxima, em pixels",
    sideHint: (min: number, max: number) =>
      `De ${String(min)} a ${String(max)}. A imagem é reduzida para caber, mantendo a proporção; nunca é ampliada.`,
    outputFormat: "Formato de saída",
    outputFormats: {
      preserve: "Manter o formato (PNG continua PNG; JPEG continua JPEG)",
      jpeg: "JPEG",
      png: "PNG",
    },
    jpegQuality: "Qualidade do JPEG",
    jpegQualityHint: (min: number, max: number) =>
      `De ${String(min)} a ${String(max)}. Vale só para as saídas em JPEG.`,
    jpegBackground: "Cor de fundo da transparência no JPEG",
    jpegBackgroundHint: "No formato #RRGGBB, como #FFFFFF para branco.",
    makeThumbnails: "Gerar miniaturas",
    makeThumbnailsHint: (box: number) => `Uma miniatura de até ${String(box)} pixels por imagem.`,
    errors: {
      integerBetween: (min: number, max: number) =>
        `Informe um número inteiro de ${String(min)} a ${String(max)}.`,
      hexColor: "Use o formato #RRGGBB, como #FFFFFF.",
    },
  },
  downloadsDetox: {
    title: "Downloads Detox",
    deduplicate: "Remover arquivos repetidos",
    deduplicateHint: "Arquivos com o mesmo conteúdo ficam uma vez só, na primeira ocorrência.",
    groupBy: "Organização em pastas",
    groupByOptions: {
      kind: "Separar por tipo (imagens, tabelas, textos e arquivos compactados)",
      none: "Tudo numa pasta só",
    },
    nameMode: "Nomes dos arquivos",
    nameModes: {
      preserve: "Manter os nomes originais",
      indexed: "Numerar na ordem de entrada",
    },
    namePrefix: "Prefixo da numeração",
    namePrefixHint: (max: number) =>
      `Até ${String(max)} caracteres. Com o prefixo arquivo, os nomes ficam arquivo-001, arquivo-002 e assim por diante.`,
    errors: {
      prefixRequired: "Informe o prefixo.",
      prefixTooLong: (max: number) => `Use no máximo ${String(max)} caracteres.`,
      prefixSeparator: "O prefixo não pode ter / nem \\.",
      prefixInvisible: "O prefixo não pode ter caracteres invisíveis ou de controle.",
      prefixDot: "O prefixo não pode começar com ponto.",
    },
  },
  pdfPress: {
    title: "PDF Press",
    operation: "O que fazer com os PDFs",
    operations: {
      merge: "Juntar todos num PDF só, na ordem da lista",
      split: "Separar cada PDF em partes",
      select: "Montar um PDF com as páginas escolhidas",
    },
    mergeHint:
      "A ordem dos arquivos na lista é a ordem no PDF final. Use os botões Mover para cima e Mover para baixo para mudar.",
    pagesPerDocument: "Páginas por parte",
    pagesPerDocumentHint: (max: number) =>
      `De 1 a ${String(max)}. Com 1, cada página vira um PDF; com 3, as partes têm 3 páginas, e a última pode ter menos.`,
    select: "Páginas escolhidas",
    selectHint: (max: number) =>
      `Cada cartão é uma página do PDF final, na ordem da lista. A mesma página pode aparecer mais de uma vez. Até ${String(max)} páginas.`,
    selectWaitingFiles: "Envie os PDFs primeiro: cada cartão escolhe uma página de um deles.",
    selectOrder: "Ordem das páginas",
    addCard: "Adicionar página",
    cardName: (position: number) => `página ${String(position)} da seleção`,
    cardTitle: (position: number) => `${String(position)}.`,
    cardFile: (position: number) => `PDF da página ${String(position)}`,
    cardPage: (position: number) => `Número da página ${String(position)} no PDF`,
    noFileChosen: "Escolha um PDF",
    removeCard: (position: number) => `Remover a página ${String(position)}`,
    preview: {
      heading: "Mapa de páginas previsto",
      empty: "O mapa aparece quando houver PDFs enviados ou páginas escolhidas.",
      allPages: (name: string) => `${name}, todas as páginas`,
      groups: (size: string) => `partes de ${size} página(s), na ordem`,
      page: (name: string, page: string) => `${name}, página ${page}`,
      noFile: "PDF não escolhido",
    },
    errors: {
      integerBetween: (min: number, max: number) =>
        `Informe um número inteiro de ${String(min)} a ${String(max)}.`,
      chooseFile: "Escolha um dos PDFs enviados.",
      noPages: "Adicione ao menos uma página.",
      tooManyPages: (max: number) => `Escolha no máximo ${String(max)} páginas.`,
    },
  },
  history: {
    title: "Histórico",
    navLink: "Histórico",
    filters: "Filtros",
    recipe: "Receita",
    anyRecipe: "Todas as receitas",
    status: "Estado",
    anyStatus: "Todos os estados",
    period: "Período",
    anyPeriod: "Qualquer data",
    periods: { "7d": "Últimos 7 dias", "30d": "Últimos 30 dias", "90d": "Últimos 90 dias" },
    apply: "Aplicar filtros",
    clear: "Limpar filtros",
    loading: "Carregando as execuções.",
    listLabel: "Execuções",
    createdAt: (date: string) => `Criada em ${date}`,
    // Começa pela vírgula: o espaço inicial de um texto sr-only se perde (HomePage).
    linkDetail: (date: string) => `, criada em ${date}`,
    loadMore: "Carregar mais",
    loadingMore: "Carregando mais execuções.",
    loadedMore: (count: number) =>
      count === 1 ? "Mais 1 execução na lista." : `Mais ${String(count)} execuções na lista.`,
    restarted: "A lista voltou ao início, porque a página seguinte expirou.",
    empty: "Nenhuma execução ainda",
    emptyHint: "As execuções aparecem aqui depois que você roda uma receita na Bancada.",
    goToWorkshop: "Escolher uma receita na Oficina",
    noMatches: "Nenhuma execução com esses filtros",
    noMatchesHint: "Mude ou limpe os filtros para ver mais execuções.",
    recentHeading: "Execuções recentes",
    recentEmptyHint: "Escolha uma receita acima para começar.",
    seeAll: "Ver o histórico completo",
  },
  account: {
    signIn: {
      title: "Entrar no GoblinShift",
      lead: "Entre com uma conta do Google ou do GitHub. O GoblinShift não recebe nem guarda a sua senha.",
      providers: { google: "Entrar com Google", github: "Entrar com GitHub" },
      legal: "Ao entrar, você concorda com os",
      legalTerms: "Termos de Uso",
      legalAnd: "e com o",
      legalPrivacy: "Aviso de Privacidade",
      alreadyIn: (name: string) => `Você já entrou como ${name}.`,
      goToAccess: "Ver o seu acesso",
      promptTitle: "Entre para continuar",
      promptBody: "Esta parte do GoblinShift precisa de login.",
      reauthTitle: "Entre de novo para confirmar",
      reauthBody:
        "Por segurança, esta ação exige um login feito há poucos minutos. Entre de novo e volte para cá.",
      failed:
        "Não foi possível entrar. Tente de novo; se continuar, confira se a conta do provedor está liberada.",
      navSignIn: "Entrar",
      navAccess: "Acesso",
    },
    access: {
      title: "Acesso",
      loading: "Carregando o seu acesso.",
      sessionHeading: "Sessão",
      signedInAs: (name: string) => `Você entrou como ${name}.`,
      viaApiKey:
        "Este acesso é por chave de API. Sair, chaves e exclusão da conta só funcionam pela sessão do navegador.",
      signOut: "Sair",
      signingOut: "Saindo…",
      signOutAll: "Sair de todos os dispositivos",
      signOutAllTitle: "Sair de todos os dispositivos?",
      signOutAllBody:
        "Todas as sessões terminam, inclusive esta, e todas as chaves de API da conta são revogadas. Scripts que usam essas chaves param de funcionar.",
      signOutAllConfirm: "Sair de todos e revogar as chaves",
      cancel: "Cancelar",
    },
    usage: {
      heading: "Uso e cotas",
      runs: "Execuções nas últimas 24 horas",
      inputBytes: "Entrada nas últimas 24 horas",
      storage: "Armazenamento reservado",
      ofLimit: (used: string, limit: string) => `${used} de ${limit}`,
      atLimit: "No limite",
      resetsAt: (when: string) => `A cota diária começa a liberar em ${when}.`,
      activeRun: "Execução em andamento",
      limitsHeading: "Limites por lote",
      fileBytes: "Por arquivo",
      batchFiles: "Arquivos por lote",
      batchBytes: "Por lote",
      concurrentUploads: "Envios simultâneos",
    },
    keys: {
      heading: "Chaves de API",
      intro: "Chaves deixam scripts usarem a API em seu nome, com os escopos que você escolher.",
      guideLink: "Como usar a chave: guia da API",
      loading: "Carregando as chaves.",
      empty: "Nenhuma chave de API",
      emptyHint: "Crie uma chave para usar a API em scripts.",
      newKey: "Nova chave",
      formHeading: "Nova chave de API",
      scopes: "Escopos",
      scopeLabels: {
        "files:read": "Ler arquivos",
        "files:write": "Enviar arquivos",
        "runs:read": "Ver execuções",
        "runs:write": "Criar e cancelar execuções",
        "presets:read": "Ver presets",
        "presets:write": "Salvar e excluir presets",
      },
      scopesRequired: "Escolha pelo menos um escopo.",
      days: "Validade, em dias",
      daysHint: (min: number, max: number) => `De ${String(min)} a ${String(max)} dias.`,
      daysInvalid: (min: number, max: number) =>
        `Informe um número inteiro de ${String(min)} a ${String(max)}.`,
      create: "Criar chave",
      creating: "Criando…",
      cancel: "Cancelar",
      tooMany: "A conta já tem o máximo de chaves ativas. Revogue uma antes de criar outra.",
      createdHeading: "Chave criada",
      createdWarning: "Copie a chave agora: ela não aparece de novo, nem para nós.",
      tokenLabel: "Chave de API",
      copy: "Copiar chave",
      copied: "Chave copiada.",
      copyFailed: "Não foi possível copiar. Selecione o campo e copie à mão.",
      done: "Já copiei",
      listLabel: "Chaves de API da conta",
      statuses: { active: "Ativa", expired: "Expirada", revoked: "Revogada" },
      createdAt: (date: string) => `Criada em ${date}`,
      expiresAt: (date: string) => `Vence em ${date}`,
      expiredAt: (date: string) => `Venceu em ${date}`,
      revokedAt: (date: string) => `Revogada em ${date}`,
      lastUsed: (date: string) => `Último uso em ${date}`,
      neverUsed: "Nunca usada",
      revoke: "Revogar",
      revokeOf: (publicId: string) => `a chave ${publicId}`,
      revokeTitle: (publicId: string) => `Revogar a chave ${publicId}?`,
      revokeBody: "Scripts que usam esta chave param de funcionar na hora. Não dá para desfazer.",
      revokeConfirm: "Revogar chave",
      revoked: (publicId: string) => `Chave ${publicId} revogada.`,
      notFound: "Chave não encontrada. Ela pode já ter sido revogada; a lista foi atualizada.",
    },
    deletion: {
      heading: "Excluir conta",
      body: "A exclusão revoga as sessões e as chaves na hora, cancela a execução em andamento e apaga arquivos, resultados e dados da conta em até 24 horas.",
      open: "Excluir conta…",
      dialogTitle: "Excluir a conta?",
      dialogBody: "Não dá para desfazer. Para confirmar, digite a frase abaixo.",
      phrase: "excluir minha conta",
      phraseLabel: (phrase: string) => `Digite “${phrase}”`,
      confirm: "Excluir conta",
      deleting: "Excluindo…",
      cancel: "Cancelar",
      doneTitle: "Exclusão pedida",
      doneBody:
        "A sua sessão terminou e as chaves foram revogadas. Os arquivos, resultados e dados da conta são apagados em até 24 horas.",
      backHome: "Voltar à Oficina",
    },
  },
  // Guia da API (texto literal: é página de consulta, sem a voz temática). Trechos entre crases
  // viram <code> na tela.
  apiGuide: {
    title: "Usar a API com chave",
    intro:
      "A interface usa a mesma API que um script pode usar. Com uma chave, o script envia arquivos, cria a execução, acompanha o andamento e baixa os resultados.",
    docsLink: "Abrir a documentação interativa",
    openApiLink: "Baixar o contrato OpenAPI",
    beforeHeading: "Antes de começar",
    before: [
      "Crie a chave com só os escopos de que o script precisa. O segredo aparece uma vez, na criação.",
      "Guarde a chave numa variável de ambiente, fora do código e do repositório: `read -rs GS_API_KEY && export GS_API_KEY`.",
      "A conta precisa ter aceitado a versão vigente dos termos; sem o aceite, a API responde `403` com `TERMS_NOT_ACCEPTED`.",
      "Os exemplos usam `curl` e `jq`, e um arquivo `clientes.csv` na pasta atual.",
    ],
    createKeyLink: "Criar uma chave no Acesso",
    scopesHeading: "Escopos",
    scopesCaption: "Escopos da chave",
    scopeColumns: { scope: "Escopo", allows: "O que permite", routes: "Rotas" },
    scopes: [
      {
        scope: "files:write",
        allows: "Enviar arquivos e excluí-los",
        routes:
          "POST /uploads, PUT /uploads/{id}/content, DELETE /uploads/{id}, DELETE /runs/{id}/artifacts",
      },
      {
        scope: "files:read",
        allows: "Ver uploads e baixar resultados",
        routes: "GET /uploads/{id}, GET /artifacts/{id}, GET /artifacts/{id}/download",
      },
      {
        scope: "runs:write",
        allows: "Criar e cancelar execuções",
        routes: "POST /runs, POST /runs/{id}/cancel",
      },
      {
        scope: "runs:read",
        allows: "Acompanhar execuções e listar resultados",
        routes: "GET /runs, GET /runs/{id}, GET /runs/{id}/results",
      },
    ],
    scopesNote:
      "`GET /catalog` e `GET /me` aceitam qualquer chave válida. Chaves, exclusão da conta e aceite dos termos só funcionam com a sessão da interface.",
    stepsHeading: "Passo a passo",
    scopeOfStep: (scope: string) => `Escopo: ${scope}`,
    commandOf: (title: string) => `Comando: ${title}`,
    steps: {
      intent: {
        title: "Peça a intenção de upload",
        body: "Mande o nome e o tamanho em bytes de cada arquivo, até 100 por pedido. A resposta traz, na mesma ordem, o `id` de cada upload e as instruções de envio em `content`.",
      },
      content: {
        title: "Envie o conteúdo",
        body: "Use `content.method`, `content.url` e `content.headers` exatamente como vieram; o comando repete os valores de hoje. Um envio interrompido não retoma: peça outra intenção.",
      },
      run: {
        title: "Crie a execução",
        body: "Gere a `Idempotency-Key` uma vez por execução pretendida. Se a resposta não chegar, repita a mesma chave com o mesmo corpo: a API devolve a execução já criada em vez de outra. Os parâmetros e os defaults de cada receita estão em `GET /catalog`.",
      },
      poll: {
        title: "Acompanhe",
        body: "Consulte até o `status` ser terminal (`succeeded`, `succeeded_with_warnings`, `failed` ou `canceled`), esperando o `Retry-After` de cada resposta. Decida só pelo `status`: fase e tentativa são informativas.",
      },
      results: {
        title: "Liste os resultados",
        body: "O papel `package` é o ZIP com tudo; `output`, `report` e `manifest` vêm avulsos. Os resultados ficam disponíveis por 48 horas depois do término.",
      },
      download: {
        title: "Baixe",
        body: "O download é um streaming autenticado; `-OJ` grava com o nome que o servidor manda.",
      },
    },
    limitsHeading: "Limites",
    limits: [
      "Uma execução não terminal por conta: criar outra antes de a atual terminar dá `409` com `ACTIVE_RUN_LIMIT`.",
      "`429` e `503` trazem `Retry-After`: espere esse tempo antes de repetir.",
      "Envie e baixe no máximo dois arquivos por vez; acima disso a API responde `429`.",
      "Limites por receita (arquivos, bytes, formatos) estão em `GET /catalog`; as cotas da conta e o consumo, em `GET /me`.",
      "Uploads que não entram em execução expiram em 24 horas; resultados, 48 horas depois do término.",
      "A `Idempotency-Key` tem de 16 a 64 caracteres entre letras, números, `-` e `_`, e vale por 24 horas.",
    ],
    errorsHeading: "Erros",
    errors:
      "Toda resposta de erro é `application/problem+json`, com um `code` estável e o `request_id`. Decida pelo `code`, nunca pelo texto, e cite o `request_id` ao pedir ajuda.",
  },
  terms: {
    acceptTitle: "Termos de uso",
    acceptLead: (version: string) =>
      `Para continuar, leia e aceite a versão ${version} dos Termos de Uso e do Aviso de Privacidade.`,
    readTerms: "Ler os Termos de Uso",
    readPrivacy: "Ler o Aviso de Privacidade",
    accept: "Aceitar e continuar",
    accepting: "Aceitando…",
    versionChanged:
      "A versão dos termos mudou enquanto você lia. Leia a versão nova e aceite de novo.",
    alreadyAccepted: (version: string) => `Você já aceitou a versão ${version} dos termos.`,
    continue: "Continuar",
    signOut: "Não aceito, sair",
    loading: "Carregando os termos.",
    requiredTitle: "Aceite os termos para continuar",
    requiredBody: "Os termos de uso mudaram. Leia e aceite a versão nova para continuar.",
    goToAccept: "Ler e aceitar os termos",
  },
  credits: {
    title: "Créditos",
    artHeading: "As goblins",
    art: [
      "As goblins da oficina foram geradas com IA e editadas à mão. Os conceitos em 2D saíram da Tripo (com os modelos Seedream e Banana Pro), os modelos 3D do Hyper3D Rodin, e os renders, recortes e a otimização da cena 3D foram feitos no Blender e no glTF Transform.",
      "A saída bruta de IA pode não ser protegível por direito autoral; a parte humana (seleção, renders, edição e os scripts) fica sob a licença CC BY 4.0.",
    ],
    brandHeading: "A marca",
    brand: [
      "O emblema do cabeçalho e o selo da oficina mostram a Rúbia e foram gerados com IA só a partir de texto, no GPT Image 2.5 pela Tripo, e depois limpos e recortados à mão. O ícone da aba é outro desenho: a cabeça da Rúbia desenhada à mão em SVG, sem IA.",
      "O nome GoblinShift, o emblema, o selo e os nomes dos personagens não são licenciados e não podem ser usados de modo que sugira vínculo ou endosso.",
    ],
    sealAlt:
      "Selo da oficina GoblinShift: a goblin Rúbia, de maria-chiquinhas vermelhas, com uma chave inglesa no ombro.",
    fontsHeading: "Fontes",
    fonts:
      "Bricolage Grotesque e Atkinson Hyperlegible, auto-hospedadas, sob a SIL Open Font License 1.1.",
  },
  legal: {
    termsTitle: "Termos de Uso",
    privacyTitle: "Aviso de Privacidade",
    draftTitle: "Rascunho",
    draftBody:
      "Este texto ainda não passou por revisão jurídica e pode mudar antes da abertura do serviço.",
  },
} as const;
