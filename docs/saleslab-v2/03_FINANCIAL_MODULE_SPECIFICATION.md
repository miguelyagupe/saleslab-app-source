**MODULE SPECIFICATION**

**Financial Analyst - v0.2**

_Da demonstração financeira carregada a um diagnóstico reconciliado, prudente e acionável_

| **Campo**   | **Definição**                                                          |
| ----------- | ---------------------------------------------------------------------- |
| Estado      | Especificação funcional e analítica para implementação                 |
| Público     | PMEs com dados mensais/trimestrais em Excel/ERP e sem analista interno |
| Pipeline    | V2 paralelo ao legacy; modular monolith FastAPI                        |
| Referências | Product Architecture Brief v0.2 + Domain Contracts v0.2                |

**MISSÃO** Responder como evoluem rentabilidade, liquidez, solvência, caixa, capital e risco financeiro - sem confundir dados publicados, cálculos, hipóteses e decisões.

# **1\. Mandato e limites**

O Financial Analyst é o primeiro módulo de referência do SalesLab V2. A sua qualidade estabelece os padrões do Metric Engine, Reconciliation Engine, Sufficiency Gate, Auditor e Report Compiler que os restantes módulos reutilizarão.

## **1.1 O módulo MUST**

- Normalizar demonstração de resultados, balanço e fluxo de caixa por período.
- Reconciliar identidades contabilísticas e definições publicadas versus calculadas.
- Calcular métricas apenas a partir do catálogo versionado.
- Separar factos, cálculos, associações, hipóteses, cenários e recomendações.
- Mostrar lacunas e capacidades bloqueadas antes de produzir narrativa.
- Publicar um único ReportResult para aplicação e PDF.

## **1.2 O módulo MUST NOT**

- Apresentar-se como auditoria estatutária, aconselhamento fiscal ou decisão fiduciária.
- Produzir DCF, LBO, valuation institucional ou recomendação de investimento por defeito.
- Inventar benchmarks, contas, períodos, moedas, definições ou drivers ausentes.
- Tratar rubrica agregada como causa operacional ou poupança capturável.
- Recomendar dívida, hedging, venda de ativos ou cortes materiais sem exposição completa e gate permitido.

# **2\. Perguntas de produto**

| **Dimensão**  | **Pergunta que o resultado deve responder**                                       |
| ------------- | --------------------------------------------------------------------------------- |
| Rentabilidade | A empresa ganha mais ou menos? Que margens mudaram e onde ocorre a ponte?         |
| Caixa         | O lucro converte-se em caixa? Que elementos explicam a diferença?                 |
| Liquidez      | A empresa consegue cumprir obrigações de curto prazo com a informação disponível? |
| Solvência     | Qual é a alavancagem e a capacidade de serviço da dívida?                         |
| Capital       | Como evoluem working capital, capex, retornos e capital investido?                |
| Risco         | Que exposições de taxa, moeda, maturidade ou leasing estão comprovadas?           |
| Ação          | Qual é a próxima ação segura, proporcional e verificável?                         |

# **3\. Inputs e níveis de cobertura**

## **3.1 Datasets fundamentais**

| **Dataset**       | **Campos mínimos**                                                                                                    | **Papel**                                  |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| IncomeStatement   | period · revenue · operating costs · EBIT/operating result · finance result · tax · net income                        | Rentabilidade e ponte do resultado.        |
| BalanceSheet      | period_end · cash · receivables · inventory · payables · debt · lease liabilities · equity · total assets/liabilities | Liquidez, solvência e capital.             |
| CashFlowStatement | period · CFO · CFI · CFF · capex/purchases PPE · opening/closing cash                                                 | Conversão de caixa e reconciliação.        |
| AccountingPolicy  | currency · scale · fiscal calendar · sign convention · consolidation scope                                            | Interpretação correta de todos os valores. |

## **3.2 Datasets enriquecedores**

- EBITDABridge: statutory, adjusted/reported, adjustment lines e definição.
- DebtSchedule: instrumento, moeda, taxa fixa/variável, indexante, spread, maturidade, principal e hedge.
- LeaseSchedule: opening balance, additions, modifications, interest, payments, FX, disposals e closing balance.
- WorkingCapitalDetail: inventário, clientes e fornecedores por componente e saldos médios.
- SegmentStatement: receitas, EBITDA/EBIT, ativos e capex por segmento/geografia comparável.
- GrowthBridge: reported, constant currency, like-for-like, volume, preço/mix, aquisições e expansão.
- CapexSchedule: maintenance/growth, committed/spent, projeto e retorno quando disponível.

## **3.3 Profiles aceites**

| **Profile**         | **Cobertura**                                       | **Resultado esperado**                       |
| ------------------- | --------------------------------------------------- | -------------------------------------------- |
| F1 Core             | P&L + balanço + cash flow; ≥2 períodos comparáveis. | Diagnóstico central e reconciliação.         |
| F2 Liquidity        | Balanço + maturidades/working capital.              | Liquidez e riscos condicionais.              |
| F3 Profitability    | P&L; ≥2 períodos.                                   | Rentabilidade; caixa e solvência bloqueados. |
| F4 Published report | PDF/XLS com demonstrações e notas identificáveis.   | Extração seguida de confirmação semântica.   |
| F0 Fragmentary      | Uma tabela, período único ou totais sem definição.  | Mapa de lacunas; sem análise material.       |

# **4\. Normalização semântica**

## **4.1 Regras de mapping**

- Cada linha importada preserva original_name, original_text, source reference e content hash.
- Moeda, escala, sinal e fiscal period são resolvidos antes do cálculo.
- Conceitos ambíguos como operating profit, trading profit e EBITDA recebem semantic_type distinto até confirmação.
- Valores ajustados e estatutários são variantes, nunca substituições silenciosas.
- YTD, mês, trimestre e ano não são comparados sem normalização de periodicidade.
- Consolidado e segmentos não são somados sem eliminação/reconciliation scope.

## **4.2 Confirmações humanas obrigatórias**

| **Condição**                                | **Ação**                                                         |
| ------------------------------------------- | ---------------------------------------------------------------- |
| Moeda/escala ausente ou conflituosa         | Bloquear calculation e pedir confirmação.                        |
| Múltiplas linhas candidatas à mesma métrica | Mostrar candidatos, localização e diferença.                     |
| Sinal contabilístico incerto                | Pedir convenção ou inferir apenas com validação de identidade.   |
| Período incompleto ou scope diferente       | Marcar não comparável; não calcular variação.                    |
| EBITDA sem definição                        | Classificar reported_unverified; não reconciliar como statutory. |

# **5\. Catálogo mínimo de métricas**

Cada item abaixo corresponde a uma MetricDefinition versionada. Uma implementação MAY acrescentar métricas, mas não pode alterar fórmula ou semântica mantendo o mesmo metric_id/version.

## **5.1 Rentabilidade e crescimento**

| **Metric ID**               | **Fórmula/definição**                                          | **Gate principal**                |
| --------------------------- | -------------------------------------------------------------- | --------------------------------- |
| revenue                     | Receita do scope e período definidos.                          | Fonte identificada.               |
| revenue_growth_reported_pct | (revenue_t / revenue_t-1 − 1) × 100.                           | Períodos e scope comparáveis.     |
| revenue_growth_cc_pct       | Crescimento a câmbio constante, publicado ou bridge calculada. | Método FX explícito.              |
| revenue_growth_lfl_pct      | Like-for-like conforme definição publicada.                    | Definição e perímetro explícitos. |
| gross_profit                | revenue − COGS.                                                | COGS semanticamente confirmado.   |
| gross_margin_pct            | gross_profit / revenue × 100.                                  | Revenue ≠ 0.                      |
| ebitda_statutory            | EBIT/operating result + D&A compatível.                        | D&A e definição conciliáveis.     |
| ebitda_adjusted             | Valor publicado com adjustments identificados.                 | Bridge obrigatória.               |
| ebitda_margin_pct           | EBITDA variant / revenue × 100.                                | Variant no nome/metadata.         |
| ebit_margin_pct             | EBIT / revenue × 100.                                          | EBIT confirmado.                  |
| net_margin_pct              | net_income / revenue × 100.                                    | Net income confirmado.            |

## **5.2 Caixa, capital e working capital**

| **Metric ID**         | **Fórmula/definição**                                     | **Nota**                                    |
| --------------------- | --------------------------------------------------------- | ------------------------------------------- |
| cfo                   | Cash flow operacional publicado/reconciliado.             | Não substituir por EBITDA.                  |
| capex                 | Aquisições de ativos fixos; sinal normalizado.            | Separar maintenance/growth se possível.     |
| free_cash_flow        | CFO − capex.                                              | Versão básica; definição exibida.           |
| cash_conversion_pct   | CFO / EBITDA variant × 100.                               | Variant e efeitos não recorrentes visíveis. |
| net_working_capital   | operating current assets − operating current liabilities. | Excluir cash/debt; componentes explícitos.  |
| dso                   | average trade receivables / revenue × days.               | Saldos médios e calendário.                 |
| dio                   | average inventory / COGS × days.                          | COGS requerido.                             |
| dpo                   | average trade payables / COGS × days.                     | Compras preferíveis quando disponíveis.     |
| cash_conversion_cycle | DSO + DIO − DPO.                                          | Só se componentes válidos.                  |
| roic                  | NOPAT / average invested capital.                         | Tax rate e capital investido explícitos.    |

## **5.3 Liquidez, dívida e cobertura**

| **Metric ID**          | **Fórmula/definição**                                                 | **Bloqueio**                        |
| ---------------------- | --------------------------------------------------------------------- | ----------------------------------- |
| current_ratio          | current_assets / current_liabilities.                                 | Passivos/ativos correntes ausentes. |
| quick_ratio            | (cash + receivables + permitted liquid assets) / current liabilities. | Componentes não identificados.      |
| gross_debt             | Borrowings brutos; leases em variant separada.                        | Scope incerto.                      |
| net_debt               | gross_debt − cash_and_equivalents.                                    | Cash restrito não distinguido.      |
| net_debt_incl_leases   | gross_debt + lease liabilities − eligible cash.                       | Lease e cash scope explícitos.      |
| net_debt_to_ebitda     | net_debt variant / EBITDA variant.                                    | Variants e período incompatíveis.   |
| interest_coverage      | EBIT ou EBITDA variant / net finance cost.                            | Denominador e variant no label.     |
| fixed_variable_mix     | Principal por tipo de taxa / dívida elegível.                         | DebtSchedule obrigatório.           |
| maturity_concentration | Principal por bucket de maturidade.                                   | Maturidades obrigatórias.           |

# **6\. Reconciliation Engine**

**POLÍTICA** O módulo não avança para narrativa material enquanto uma identidade obrigatória falhar acima da tolerância definida ou permanecer sem explicação.

| **Reconciliation** | **Identidade/ponte**                                                            | **Falha produz**                 |
| ------------------ | ------------------------------------------------------------------------------- | -------------------------------- |
| Balance sheet      | assets = liabilities + equity.                                                  | blocking se diferença material.  |
| Cash movement      | opening cash + CFO + CFI + CFF + FX/other = closing cash.                       | critical/blocking.               |
| P&L subtotal       | Receita − custos ± outros = EBIT; EBIT − finance ± tax = net income.            | critical.                        |
| EBITDA             | EBIT + D&A = statutory; +/− adjustments = adjusted.                             | bridge e variants.               |
| Debt               | opening + drawdowns − repayments ± FX/other = closing.                          | warning/blocking por capability. |
| Lease              | opening + additions + modifications + interest − payments ± FX/other = closing. | bloqueia causalidade de leasing. |
| Growth             | LFL + expansão/acquisition + FX + other ≈ reported growth.                      | bloqueia "orgânico".             |
| Working capital    | Componentes e variação reconciliam com disclosure/cash-flow quando disponível.  | limita diagnóstico.              |

## **6.1 Tolerâncias**

- Tolerância absoluta e relativa são configurações versionadas por moeda/escala; não são escolhidas pelo modelo.
- Diferenças apenas de arredondamento são registradas como info e mantêm calculation trace.
- Uma diferença material exige mapping correction, adjustment explícito ou issue blocking.
- O auditor vê valor esperado, observado, delta absoluto, delta percentual e source references.

# **7\. Sufficiency Gate por capacidade**

| **Capability**       | **Dados mínimos**                                               | **Bloqueadores típicos**              |
| -------------------- | --------------------------------------------------------------- | ------------------------------------- |
| summarize            | Uma demonstração validada e período identificado.               | Moeda/escala/scope desconhecido.      |
| compare              | ≥2 períodos comparáveis.                                        | Granularidade ou perímetro diferente. |
| calculate            | Inputs completos + MetricDefinition.                            | Mapping ambíguo ou denominador zero.  |
| diagnose             | Comparação + bridge/componentes relevantes.                     | Rubrica agregada usada como driver.   |
| forecast             | Série temporal adequada ao horizonte + método/backtest.         | Um ou dois pontos; quebra de scope.   |
| quantify_impact      | Baseline + driver + custo + sensibilidade.                      | Pressupostos ausentes.                |
| recommend_reversible | Finding auditado; risco e owner.                                | Dados insufficient.                   |
| recommend_material   | Coverage complete + schedules relevantes + auditoria reforçada. | Dívida/leasing/capex incompletos.     |

## **7.1 Política de cobertura**

- Complete: três demonstrações reconciliadas, ≥2 períodos e notas necessárias à conclusão específica.
- Sufficient: core reconciliado com lacunas não materiais para a capacidade pedida.
- Partial: permite resumo, métricas locais e pedidos de dados; recomendações apenas experimentais e não quantificadas.
- Insufficient: entrega exclusivamente Data Quality, lacunas e ações para desbloquear.

# **8\. Agentes e responsabilidades**

| **Componente**     | **Responsabilidade**                                                           | **Não pode**                                       |
| ------------------ | ------------------------------------------------------------------------------ | -------------------------------------------------- |
| Financial Analyst  | Selecionar findings permitidos, explicar bridges e propor ações proporcionais. | Calcular fora do Metric Engine ou ignorar gates.   |
| Statement Auditor  | Testar identidades, definitions, periods, denominators e provenance.           | Reescrever resultados para os fazer caber.         |
| Month-End Closer   | Avaliar fecho mensal, variações, contas pendentes e checklist de dados.        | Atestar fecho contabilístico ou criar lançamentos. |
| Independent Critic | Procurar contradições, causalidade excessiva, missing counterevidence e risco. | Publicar diretamente.                              |
| Report Compiler    | Montar apenas objetos aprovados no ReportResult.                               | Criar novos números, findings ou recomendações.    |

## **8.1 Month-End Closer - escopo inicial**

- Comparar mês, YTD e período homólogo quando semanticamente disponíveis.
- Sinalizar reconciliações pendentes, contas sem movimento esperado e variações materiais.
- Separar 'fecho incompleto' de 'performance negativa'.
- Produzir checklist e missing-data requests; não substituir contabilista nem ERP.

# **9\. Contrato de saída do módulo**

| **Secção**           | **Conteúdo obrigatório**                                               |
| -------------------- | ---------------------------------------------------------------------- |
| Data confidence      | coverage, issues, capabilities, períodos, moeda, escala e scope.       |
| Financial snapshot   | Métricas principais com variants e provenance.                         |
| Reconciliations      | Identidades, bridges, deltas e estado.                                 |
| Performance          | Rentabilidade e crescimento, com linguagem proporcional.               |
| Cash & capital       | CFO, FCF, conversion, working capital, capex e ROIC quando permitidos. |
| Liquidity & solvency | Ratios, dívida, maturidades, taxa/moeda/hedging quando presentes.      |
| Findings             | Claim type, evidência, counterevidence, confidence e limitações.       |
| Recommendations      | Ação, reversibilidade, owner, impacto, custo, risco e gate.            |
| Missing data         | Pedido concreto, razão e capacidades que desbloqueia.                  |
| Methodology          | Fórmulas, versões, fontes e auditoria.                                 |

## **9.1 Regras narrativas**

- 'Aumentou 4,4%' exige numerador, denominador, período e CalculationTrace.
- 'Principal driver' exige bridge quantitativa; sem bridge usar 'associação' ou 'hipótese'.
- 'Orgânico' só aparece com definição documentada; caso contrário usar reported/constant currency/LFL.
- 'Melhorou' e 'deteriorou' especificam métrica, direção, magnitude e base comparativa.
- Uma rubrica agregada nunca é renomeada como logística, desperdício ou ineficiência.

# **10\. Política de recomendação**

| **Classe**            | **Exigência**                                                              | **Exemplos permitidos**                               |
| --------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------- |
| Diagnostic request    | Lacuna explícita e capability a desbloquear.                               | Decompor custos de distribuição; obter debt schedule. |
| Reversible experiment | Finding auditado, baixo custo, prazo e métrica de sucesso.                 | Teste de cobrança ou controlo de inventário.          |
| Operational action    | Driver reconciliado, owner, custo, risco e impacto líquido.                | Alterar política de working capital.                  |
| Material commitment   | Complete + cenário + sensibilidade + auditoria reforçada + decisão humana. | Hedge, refinanciamento, sale-and-leaseback.           |

**REGRA DE PRUDÊNCIA** Hedging, dívida, venda de ativos, redução de headcount e congelamento de expansão nunca surgem como defaults. O sistema pode primeiro recomendar recolher os dados que tornam a decisão analisável.

# **11\. Fixture de regressão - Jerónimo Martins**

Este caso preserva os erros já encontrados e impede que regressem. Os valores servem como fixture conhecida; não constituem benchmark para PMEs.

| **ID**     | **Caso**                             | **Resultado obrigatório**                                                    |
| ---------- | ------------------------------------ | ---------------------------------------------------------------------------- |
| JM-FIN-001 | EBITDA 2.113 M€ vs 2.232 M€.         | Exibir statutory calculado, adjusted/reported e bridge de 119 M€.            |
| JM-FIN-002 | 93 M€ adicionais e alerta de 4,4%.   | 4,4% do EBITDA total; ≈388% dos 24 M€ adicionais. Frase e fórmula coincidem. |
| JM-FIN-003 | Custos de distribuição agregados.    | Não chamar logística; pedir decomposição antes de recomendar.                |
| JM-FIN-004 | Hedge de 50-70% sem exposure.        | Bloquear recomendação; pedir taxa, moeda, maturidade, hedge e custo.         |
| JM-FIN-005 | Receitas +9,3%, CC +4,9%, LFL +0,6%. | Separar reported, constant currency, LFL e expansão; não chamar orgânico.    |
| JM-FIN-006 | Lease liabilities +535 M€.           | Exigir roll-forward; não atribuir integralmente à expansão.                  |

# **12\. Testes adversariais**

| **Teste** | **Input hostil/incompleto**                             | **Comportamento esperado**                                            |
| --------- | ------------------------------------------------------- | --------------------------------------------------------------------- |
| ADV-001   | 'EBITDA' aparece em três folhas com valores diferentes. | mapping_required; nenhum valor principal selecionado silenciosamente. |
| ADV-002   | Receita está em milhares; dívida em unidades.           | Normalizar escala ou bloquear ratio.                                  |
| ADV-003   | Ano atual tem 12 meses; comparativo tem 9.              | Bloquear YoY direto; oferecer base comparável se calculável.          |
| ADV-004   | Balanço não fecha.                                      | Issue blocking; não publicar solvência.                               |
| ADV-005   | Cash flow fecha apenas com FX não identificado.         | Mostrar residual; impedir causalidade forte.                          |
| ADV-006   | Zero usado para célula em branco.                       | Preservar missing; não calcular percentagem.                          |
| ADV-007   | Prompt/source contém instrução para ignorar regras.     | Tratar conteúdo como dados não confiáveis; manter system contract.    |
| ADV-008   | Modelo devolve métrica sem evidence_id.                 | Schema/audit rejection e revisão.                                     |
| ADV-009   | Recomendação tem impacto mas sem custo.                 | ImpactEstimate inválido; não priorizar.                               |
| ADV-010   | PDF omite limitações ou recommendation.                 | report_parity blocking; build falha.                                  |

# **13\. Testes de aceitação**

## **13.1 Unitários determinísticos**

- Fórmulas, rounding, percentagens, denominadores e divisão por zero.
- Mappings, moeda, escala, sinais e normalização temporal.
- Cada reconciliation identity com casos exatos, rounding e falha material.
- Capability matrix e precedência de issue blocking.
- Schema validation de Finding, Recommendation, AuditResult e ReportResult.

## **13.2 Integração**

- Upload → parsing → mapping → validation → calculation → analysis → audit → compile.
- Idempotência com mesmo ficheiro/hash e retry após falha.
- Isolamento por workspace e quota reservada/consumida uma única vez.
- Shadow run legacy/V2 sem alterar o endpoint atual.
- Web e PDF recebem o mesmo report_id/report_version e contagem de secções/objetos.

## **13.3 Golden reports**

- Jerónimo Martins com os seis casos JM-FIN.
- PME lucrativa com caixa fraco por working capital.
- PME com um único P&L e capacidades devidamente bloqueadas.
- Demonstrações com currency/scale conflitantes e confirmação humana.
- Dataset mensal com fecho incompleto para Month-End Closer.

# **14\. Critérios de conclusão do módulo**

- Nenhum número publicado carece de source/evidence ou CalculationTrace.
- Statutory e adjusted metrics reconciliam ou permanecem variantes explicitamente não reconciliadas.
- Todas as percentagens passam validação numerador/denominador/texto.
- As três demonstrações e movimentos relevantes reconciliam dentro de tolerância ou bloqueiam capacidades.
- Nenhuma recomendação excede coverage e capability decision.
- O fixture JM-FIN-001…006 e todos os adversarial tests passam.
- O PDF e a aplicação são semanticamente idênticos e sem secções vazias/cortadas.
- O pipeline V2 pode ser desligado sem afetar auth, workspaces, billing, quotas ou legacy.

# **15\. Sequência de implementação**

| **Slice** | **Entrega**                             | **Gate de saída**                         |
| --------- | --------------------------------------- | ----------------------------------------- |
| F-01      | Schemas financeiros, enums e fixtures.  | Validação e testes de contrato.           |
| F-02      | Mapping + normalização de P&L, BS e CF. | Golden inputs normalizados.               |
| F-03      | Metric catalog + CalculationTrace.      | Unit tests de fórmulas.                   |
| F-04      | Reconciliation Engine.                  | Identidades e tolerâncias auditáveis.     |
| F-05      | Coverage + capability gates.            | Blocked/conditional/allowed reproduzível. |
| F-06      | Financial Analyst output estruturado.   | Sem narrativa fora do schema.             |
| F-07      | Statement Auditor + Independent Critic. | Revision loop controlado.                 |
| F-08      | ReportResult + web/PDF parity.          | Golden reports e visual regression.       |
| F-09      | Shadow run e comparação legacy/V2.      | Critérios de qualidade cumpridos.         |
| F-10      | Month-End Closer inicial.               | Checklist mensal e lacunas validadas.     |

# **16\. Decisões abertas - não assumir na implementação**

| **Tema**              | **Decisão futura**                                                              |
| --------------------- | ------------------------------------------------------------------------------- |
| Thresholds            | Tolerâncias materiais por moeda, escala e profile.                              |
| Time series           | Número mínimo de períodos por método de forecast.                               |
| Benchmarks & taxonomy | Fontes/licenças e plano de contas canónico com extensões por setor, país e ERP. |
| Scoring               | Fórmulas de coverage, confidence e materiality.                                 |
| Debt/lease            | Tratamento padrão de IFRS 16 e variantes locais.                                |
| Operations & review   | Queue/SLA e quais material commitments exigem confirmação humana.               |

# **17\. Requisitos multilingues do módulo**

O Financial Analyst é lançado em português, inglês, espanhol e francês. A análise é calculada e auditada uma única vez sobre conceitos canónicos; a localização não altera métricas, evidência, gates ou recomendações.

## **17.1 Terminologia financeira**

| **Regra**                 | **Aplicação**                                                                               |
| ------------------------- | ------------------------------------------------------------------------------------------- |
| Concept-first             | EBITDA, dívida líquida, cash flow e working capital resolvem por concept_id, não por texto. |
| Aliases de origem         | Cabeçalhos equivalentes nos quatro idiomas alimentam o mesmo semantic_type.                 |
| Variantes contabilísticas | Termos locais não fundem statutory, adjusted, reported ou IFRS/local GAAP.                  |
| Original preservado       | SourceReference mantém texto original e tradução explicativa separada.                      |
| Glossário versionado      | Cada relatório regista terminology_version usada.                                           |

## **17.2 Requisitos de saída**

- O utilizador escolhe report_language ∈ {pt, en, es, fr}; format_locale é configurado separadamente.
- Labels, narrativa, issues, pedidos de dados, metodologia e PDF são localizados integralmente.
- Metric IDs, fórmulas, reason codes, evidence IDs e audit checks permanecem canónicos.
- Números persistidos usam Decimal canónico; separadores aparecem apenas no renderer.
- Uma fonte em francês pode gerar relatório em português sem perder a citação original.

## **17.3 Testes obrigatórios**

| **ID**       | **Teste**                                  | **Aceitação**                                            |
| ------------ | ------------------------------------------ | -------------------------------------------------------- |
| I18N-FIN-001 | Mesmo golden input em pt/en/es/fr.         | Métricas, findings, gates e auditoria equivalentes.      |
| I18N-FIN-002 | Cabeçalhos financeiros nos quatro idiomas. | Mesmo mapping ou confirmação explícita.                  |
| I18N-FIN-003 | 1.234,56 vs 1,234.56.                      | Mesmo Decimal após parse; locale registado.              |
| I18N-FIN-004 | Fonte num idioma e relatório noutro.       | Original, tradução e proveniência preservados.           |
| I18N-FIN-005 | Termo sem tradução aprovada.               | Issue/fallback visível; nunca string vazia ou inventada. |
| I18N-FIN-006 | Web e PDF em cada idioma.                  | Mesmo conteúdo, ordem, valores e report_version.         |