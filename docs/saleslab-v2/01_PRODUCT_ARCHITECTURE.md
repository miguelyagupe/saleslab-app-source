**PRODUCT ARCHITECTURE BRIEF**

**SalesLab V2**

_Da análise gerada por IA a um sistema verificável de inteligência financeira para PMEs_

| **Campo**       | **Definição**                                                                 |
| --------------- | ----------------------------------------------------------------------------- |
| Estado          | Fonte de verdade inicial - v0.2                                               |
| Público inicial | PMEs com dados recorrentes em Excel/ERP e sem analista financeiro interno     |
| Âmbito          | Arquitetura de produto, princípios, componentes, agentes e migração           |
| Base            | Auditoria dos quatro módulos, saleslab-api e agentes financeiros da Anthropic |

**DECISÃO CENTRAL** A reconstrução substitui o cérebro analítico do SalesLab, preservando a infraestrutura funcional de autenticação, workspaces, billing, base de dados e deploy.

# **1\. Contexto e problema**

O SalesLab atual provou que consegue receber dados, chamar um modelo avançado, apresentar análises e exportar relatórios. Contudo, os testes aos módulos de Vendas, Eficiência, Custos e Financeiro mostraram que a qualidade depende excessivamente do raciocínio livre do modelo e da estrutura do input.

O teto atual é baixo porque a aplicação avança frequentemente de um sinal para uma explicação causal e, depois, para uma recomendação, sem construir uma cadeia verificável de dados, cálculos, reconciliações e limitações.

**PROBLEMA-RAIZ** O sistema ainda não determina rigidamente o que os dados permitem afirmar antes de a IA começar a interpretar e recomendar.

# **2\. Público e proposta de valor**

## **2.1 Público inicial**

- Empresas com aproximadamente 10-250 colaboradores.
- Dados mensais ou trimestrais em Excel, CSV ou exportados de ERP.
- Operação com vendas, custos e recursos recorrentes.
- Sem analista financeiro ou equipa de business intelligence interna.
- Comprador principal: proprietário, direção financeira ou direção de operações.
- Necessidade: perceber o que mudou, onde existe perda de valor e o que investigar ou executar.

## **2.2 Posicionamento**

A IA é infraestrutura, não a promessa central. O SalesLab deve vender transformação de dados em diagnóstico, decisão e acompanhamento.

| **Não é**                                   | **Passa a ser**                                                |
| ------------------------------------------- | -------------------------------------------------------------- |
| Um formulário que envia dados ao Claude     | Um pipeline analítico com contratos, regras e auditoria        |
| Um gerador de relatórios                    | Um sistema recorrente de diagnóstico e decisão                 |
| Uma caixa negra de recomendações            | Uma cadeia de evidência, cálculo, hipótese e ação              |
| Uma solução genérica para qualquer ficheiro | Uma plataforma que declara cobertura, limites e dados em falta |

# **3\. Princípios não negociáveis**

| **Princípio**                  | **Regra**                                                                                               |
| ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| Deterministic first            | Parsing, validação, métricas, reconciliação e políticas devem usar código e regras sempre que possível. |
| Evidence before interpretation | Nenhuma conclusão material existe sem evidência e período identificados.                                |
| No silent guessing             | Ambiguidade de moeda, unidade, dimensão ou período exige confirmação ou bloqueio.                       |
| Capability follows data        | A cobertura dos dados controla as análises e recomendações permitidas.                                  |
| One metric, one definition     | As métricas são centralizadas, versionadas e reutilizadas por todos os módulos.                         |
| Independent review             | O componente que cria a interpretação não é o único que a valida.                                       |
| Human authority                | A plataforma recomenda e explica; decisões materiais continuam sob responsabilidade humana.             |
| One report source              | Aplicação e PDF renderizam o mesmo objeto aprovado.                                                     |
| Safe migration                 | O pipeline V2 nasce em paralelo ao legacy, com comparação e rollback.                                   |

# **4\. O que será preservado**

- FastAPI e a base operacional do backend.
- PostgreSQL, workspaces, utilizadores e histórico de análises.
- Autenticação por password/Google, sessões JWT e magic links seguros.
- Planos, quotas atómicas, Stripe e sincronização de subscrições.
- Deploy do backend no Railway e das experiências web no Vercel.
- Fluxos de upload, interface e componentes reutilizáveis.
- Migrations, testes existentes e compatibilidade com o produto atual.

A reconstrução não começa por remover estas capacidades. O pipeline analítico atual será isolado como legacy e substituído progressivamente.

# **5\. Arquitetura analítica alvo**

| **Etapa** | **Componente**        | **Responsabilidade**                                                         |
| --------- | --------------------- | ---------------------------------------------------------------------------- |
| 1         | Ingestion Engine      | Extrai tabelas, tipos, períodos, unidades, moedas e metadados.               |
| 2         | Data Validator        | Deteta falhas estruturais, semânticas e financeiras.                         |
| 3         | Semantic Mapper       | Classifica clientes, produtos, segmentos, equipas, rubricas e métricas.      |
| 4         | Sufficiency Gate      | Define as capacidades autorizadas pela cobertura dos dados.                  |
| 5         | Metric Engine         | Executa fórmulas determinísticas sobre métricas centralizadas.               |
| 6         | Reconciliation Engine | Reconcilia totais, definições, períodos, segmentos e linguagem quantitativa. |
| 7         | Module Analyst        | Interpreta o resultado segundo a metodologia específica do módulo.           |
| 8         | Independent Critic    | Confirma ou rejeita causalidade, alertas, impacto e prioridade.              |
| 9         | Recommendation Policy | Bloqueia ações não suportadas e estrutura decisões autorizadas.              |
| 10        | Report Compiler       | Produz um objeto único para aplicação, PDF e histórico.                      |

# **6\. Modelo comum de dados e evidência**

Antes dos agentes, o V2 precisa de contratos internos partilhados. Estes contratos serão detalhados no documento seguinte, Domain Contracts.

| **Contrato**       | **Finalidade**                                                  |
| ------------------ | --------------------------------------------------------------- |
| SourceReference    | Ficheiro, folha/página, intervalo, período e origem.            |
| NormalizedDataset  | Tabelas normalizadas com dimensões, métricas, unidades e tipos. |
| MetricDefinition   | Identidade, fórmula, unidade, scope e versão da definição.      |
| MetricObservation  | Valor observado para período, dimensão e fonte específicos.     |
| Evidence           | Facto ou cálculo que suporta uma afirmação.                     |
| DataQualityIssue   | Erro, contradição, ambiguidade ou limitação.                    |
| AnalysisCapability | Ação analítica autorizada ou bloqueada.                         |
| Finding            | Conclusão ligada a evidências e alternativas.                   |
| Recommendation     | Ação, pressupostos, impacto, risco e dados em falta.            |
| AuditResult        | Aprovação, aviso, revisão obrigatória ou bloqueio.              |
| ReportResult       | Fonte única da experiência web e PDF.                           |

# **7\. Gate de suficiência**

| **Nível**    | **Permitido**                                       | **Bloqueado**                                    |
| ------------ | --------------------------------------------------- | ------------------------------------------------ |
| Completo     | Diagnóstico, cenários e recomendações quantificadas | Apenas outputs que falhem auditoria              |
| Suficiente   | Diagnóstico e recomendações condicionais            | Impacto preciso sem pressupostos                 |
| Parcial      | Resumo, anomalias, hipóteses e pedidos de dados     | Cortes, venda de ativos e decisões irreversíveis |
| Insuficiente | Mapa de dados em falta e orientação de recolha      | Causalidade, forecast e recomendações            |

# **8\. Os quatro analistas**

| **Agente**         | **Pergunta principal**                                            | **Núcleo de dados**                                                                   |
| ------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Sales Analyst      | Onde mudou a receita e que dimensões explicam a variação?         | Cliente, produto, canal, mercado, vendedor, volume, preço e mix.                      |
| Efficiency Analyst | Que recursos produzem output e onde existe perda de capacidade?   | Horas, pessoas, unidades, energia, tempo, qualidade, capacidade e desperdício.        |
| Cost Analyst       | Que custos mudaram, porquê e que redução é executável?            | Natureza, unidade, driver, fornecedor, fixo/variável, controlabilidade e recorrência. |
| Financial Analyst  | Como evoluíram rentabilidade, liquidez, solvência, caixa e risco? | Demonstrações, capital circulante, dívida, leasing, capital e definições ajustadas.   |

## **8.1 Regras comuns**

- Cada analista terá input schema, output schema, metodologia, capabilities e bloqueios próprios.
- Nenhum analista redefine métricas partilhadas.
- Cada afirmação distingue facto, cálculo, inferência, cenário e recomendação.
- Cada agente devolve dados estruturados; o texto final é uma representação controlada.
- A ausência de granularidade reduz o âmbito da análise em vez de gerar entidades fictícias.

# **9\. Agentes e workflows transversais**

| **Componente**       | **Função**                                                                |
| -------------------- | ------------------------------------------------------------------------- |
| Analysis Auditor     | Audita fontes, fórmulas, denominadores, datas, consistência e permissões. |
| Independent Critic   | Tenta refutar findings e recomendações contra os dados normalizados.      |
| Month-End Analyst    | Compara períodos, acompanha ações e mede forecast accuracy.               |
| Reconciliation Agent | Reconcilia métricas e, futuramente, diferentes fontes operacionais.       |
| Report Compiler      | Compila apenas outputs aprovados no schema comum de relatório.            |

# **10\. Orquestração e uso de IA**

Uma arquitetura de agentes não implica uma chamada ao modelo para cada etapa. O V2 deve limitar o Claude às tarefas em que interpretação e linguagem acrescentam valor.

| **Camada**                          | **Implementação dominante**             |
| ----------------------------------- | --------------------------------------- |
| Parsing e normalização              | Código determinístico                   |
| Validação e reconciliação           | Código determinístico + regras          |
| Métricas                            | Código determinístico                   |
| Sufficiency e recommendation policy | Policy engine                           |
| Interpretação por módulo            | Claude com skill e schema               |
| Critic                              | Claude + invariantes determinísticos    |
| Texto do relatório                  | Claude controlado por conteúdo aprovado |
| Aplicação e PDF                     | Renderização determinística             |

# **11\. Histórico e valor recorrente**

O V2 deve evoluir de análises isoladas para uma memória operacional normalizada. Guardar JSON de relatórios não basta: métricas e decisões precisam de identidade temporal estável.

- Atual vs. período anterior, orçamento e forecast.
- Alertas novos, persistentes, resolvidos ou agravados.
- Decisões recomendadas, aceites, rejeitadas e executadas.
- Responsável, prazo, dependências e resultado observado.
- Comparação entre impacto previsto e impacto real.
- Medição da precisão histórica do próprio SalesLab.

# **12\. Reporting e paridade**

O ReportResult aprovado será a única fonte de conteúdo. Interface e PDF não podem possuir pipelines analíticos ou seleções de secções independentes.

- Mesmas secções, ordem e severidade.
- Mesmas decisões, evidências, pressupostos, confiança e esforço.
- Sem secções vazias ou conteúdo cortado.
- PDF reconstruído para A4, não convertido em texto corrido.
- Testes de paridade por secção, contagem e campos obrigatórios.

# **13\. Estratégia de migração**

**REGRA** O produto atual permanece operacional enquanto o V2 é desenvolvido e comparado em paralelo.

- Preservar \`/analyze\` como pipeline legacy durante a transição.
- Criar \`/v2/analyze\` ou feature flag equivalente por workspace.
- Executar legacy e V2 sobre os mesmos fixtures de avaliação.
- Migrar um módulo de cada vez.
- Começar pelo Financeiro, seguido de Vendas, Custos e Eficiência.
- Exigir auditor e critic antes de disponibilizar qualquer módulo V2.
- Manter rollback simples até o V2 superar formalmente os critérios de aceitação.

# **14\. Fora de âmbito inicial**

- Microserviços e infraestrutura distribuída.
- Execução automática de decisões financeiras.
- Lançamentos contabilísticos ou movimentação de fundos.
- Reconciliação bancária completa entre sistemas heterogéneos.
- Benchmarks setoriais sem fonte licenciada ou metodologia validada.
- DCF, LBO, investment banking e workflows institucionais da Anthropic.
- Substituição imediata do frontend ou da infraestrutura funcional.

# **15\. Ordem dos documentos de especificação**

| **Ordem** | **Documento**                  | **Objetivo**                                            |
| --------- | ------------------------------ | ------------------------------------------------------- |
| 1         | Product Architecture Brief     | Este documento: visão, limites e arquitetura.           |
| 2         | Domain Contracts               | Schemas e invariantes partilhados.                      |
| 3         | Module Specifications          | Métodos e políticas dos quatro analistas.               |
| 4         | Agent & Workflow Specification | Prompts, skills, ferramentas, estados e falhas.         |
| 5         | Migration & Rollout Plan       | Compatibilidade, feature flags, migrations e rollback.  |
| 6         | Test & Evaluation Plan         | Fixtures, golden outputs, adversarial cases e métricas. |

# **16\. Critérios de sucesso da arquitetura**

- Nenhuma métrica partilhada apresenta definições incompatíveis sem reconciliação.
- Nenhuma recomendação material ultrapassa as capabilities permitidas pelos dados.
- Todas as percentagens materiais são rastreáveis ao numerador e denominador.
- Nenhuma entidade é usada num ranking de tipo diferente.
- Causalidade é distinguida de correlação e hipótese.
- Aplicação e PDF apresentam o mesmo conteúdo aprovado.
- Cada resultado pode ser reproduzido e auditado.
- O V2 pode ser ativado, comparado e revertido sem interromper o legacy.

# **17\. Decisões fixadas e questões em aberto**

## **17.1 Decisões fixadas**

- Público inicial definido pelo perfil de dados e ausência de analista, não por um único setor.
- Arquitetura modular monolith em FastAPI.
- Quatro analistas especializados com core determinístico partilhado.
- Data Validator, Sufficiency Gate, Auditor e Critic são componentes obrigatórios.
- Pipeline V2 paralelo ao legacy.
- Financeiro será o primeiro módulo de referência.

## **17.2 Questões para os próximos documentos**

- Schemas exatos e versionamento dos contratos.
- Política de armazenamento dos dados normalizados.
- Regras de confirmação humana para mapeamento semântico.
- Framework comum de confiança, materialidade e severidade.
- Contratos mínimos de dados por módulo.
- Critérios quantitativos para comparar legacy e V2.
- Integração progressiva do frontend e do PDF.

**PRÓXIMO PASSO** Produzir o Domain Contracts v0.2 antes de criar branches ou entregar tarefas de implementação ao Codex.

# **18\. Multilinguagem como capacidade do core**

O lançamento V2 suporta português, inglês, espanhol e francês. A arquitetura interna permanece independente do idioma: IDs, fórmulas, enums, gates, métricas e estados são canónicos; apenas labels, explicações, mensagens e renderização são localizados.

## **18.1 Separação de contextos**

| **Contexto**       | **Responsabilidade**                                                 |
| ------------------ | -------------------------------------------------------------------- |
| source_language    | Idioma predominante dos ficheiros e cabeçalhos recebidos.            |
| analysis_language  | Idioma de trabalho do pipeline linguístico, sem alterar o cálculo.   |
| report_language    | Idioma escolhido para interface, narrativa e PDF: pt · en · es · fr. |
| format_locale      | Regras regionais de números, datas, separadores e moeda.             |
| accounting_context | País, normas e terminologia contabilística aplicável.                |

## **18.2 Componentes obrigatórios**

- Canonical Terminology Registry com traduções aprovadas por conceito financeiro.
- Locale Resolver com fallback explícito e nunca silencioso para conteúdo crítico.
- Source Language Detector que preserva o texto original e a confiança da deteção.
- Translation Layer posterior ao cálculo e anterior ao Report Compiler.
- Catálogo de UI, emails, erros, billing e estados sem strings críticas hardcoded.
- Testes de invariância: os quatro idiomas produzem os mesmos números, gates, severidade, IDs e decisões.

**INVARIANTE MULTILINGUE** Alterar o idioma nunca pode alterar a semântica, a fórmula, a evidência, a prioridade ou o resultado da auditoria. Web e PDF usam o mesmo ReportResult e o mesmo locale resolvido.