**TECHNICAL SPECIFICATION**

**SalesLab V2 - Domain Contracts**

_Objetos, estados, gates e invariantes para uma análise financeira verificável_

| **Campo**              | **Definição**                                          |
| ---------------------- | ------------------------------------------------------ |
| Estado                 | Fonte de verdade técnica inicial - v0.2                |
| Âmbito                 | Core partilhado, quatro módulos, auditoria e relatório |
| Arquitetura            | Modular monolith FastAPI; V2 paralela ao legacy        |
| Primeira implementação | Módulo Financeiro como referência                      |

**DECISÃO CENTRAL** Nenhum agente pode inventar a estrutura crítica do resultado. Métricas, evidência, capacidade, recomendações e auditoria têm contratos versionados e validação determinística.

# **1\. Finalidade e fronteira**

Este documento converte o Product Architecture Brief em contratos implementáveis. Define o significado dos dados, o que cada etapa recebe e devolve, quando uma capacidade é permitida e quais condições tornam um resultado inválido.

## **1.1 Incluído**

- Enums e value objects comuns; proveniência e referências de origem.
- Dados normalizados, catálogo de métricas e rastreio de cálculos.
- Qualidade, cobertura, sufficiency gates e decisões de capacidade.
- Findings, previsões, recomendações, prioridade, auditoria e relatório.
- Run state machine, versionamento, persistência e ponte com o legacy.

## **1.2 Fora de âmbito nesta versão**

- Fórmulas completas e requisitos mínimos de cada um dos quatro módulos.
- Prompts finais dos agentes, UX detalhada e escolha definitiva de job queue.
- Taxonomias setoriais e reconciliação contabilística completa.

# **2\. Princípios normativos**

- Imutabilidade: fonte, hash, evidência e stage payload publicado não são alterados; uma correção cria nova versão.
- Determinismo: métricas, cobertura, severidade, capability gates e prioridade são calculados por regras, não escolhidos livremente pelo modelo.
- Rastreabilidade: cada afirmação material aponta para evidência; cada cálculo aponta para inputs e fórmula.
- Semântica explícita: ausência, zero, desconhecido, não aplicável e não confirmado são estados distintos.
- Validação por fronteira: cada estágio valida o input e o output antes de persistir ou avançar.
- Separação de papéis: analistas propõem; auditor independente aprova, condiciona ou bloqueia.
- Paridade de canais: aplicação e PDF são renderizações do mesmo ReportResult aprovado.

**REGRA DE LINGUAGEM** Os termos MUST, SHOULD e MAY significam, respetivamente, obrigatório, recomendado e opcional.

# **3\. Vocabulário controlado**

| **Enum**       | **Valores canónicos**                                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| ModuleType     | financial · sales · cost · efficiency                                                                                              |
| SourceFormat   | csv · xlsx · xls · pdf · manual                                                                                                    |
| ProvenanceType | published · user_provided · extracted · calculated · inferred · estimated                                                          |
| ClaimType      | fact · calculation · association · hypothesis · scenario · recommendation                                                          |
| QualityLevel   | complete · sufficient · partial · insufficient                                                                                     |
| Severity       | info · warning · critical · blocking                                                                                               |
| ConfidenceBand | low · medium · high                                                                                                                |
| AuditStatus    | approved · approved_with_warnings · revision_required · blocked                                                                    |
| DecisionStatus | allowed · conditional · blocked                                                                                                    |
| WorkflowStatus | received · parsing · mapping_required · validating · blocked · calculating · analyzing · auditing · compiling · completed · failed |

## **3.1 Capacidades**

| **Capability**       | **Significado**                                             |
| -------------------- | ----------------------------------------------------------- |
| summarize            | Descrever dados observados sem inferência causal.           |
| calculate            | Produzir métricas com fórmula e inputs rastreáveis.         |
| compare              | Comparar períodos ou dimensões semanticamente equivalentes. |
| diagnose             | Identificar padrões, anomalias e associações.               |
| infer_cause          | Apresentar hipótese causal claramente limitada.             |
| forecast             | Projetar valores com método, base histórica e intervalo.    |
| quantify_impact      | Estimar impacto bruto e líquido com sensibilidade.          |
| recommend_reversible | Sugerir ação pequena, reversível e testável.                |
| recommend_material   | Sugerir decisão material, irreversível ou de alto risco.    |

# **4\. Value objects fundamentais**

**Period.** start_date, end_date, label, granularity, fiscal_year e comparison_basis. Datas usam ISO 8601; end_date MUST ser igual ou posterior a start_date.

**Money.** amount decimal, currency ISO 4217 e scale ∈ {raw, thousands, millions}. Conversão de escala é explícita e rastreada.

**Unit.** code, label e family. Percentagem, pontos percentuais, horas e unidades físicas não são intercambiáveis.

**DimensionRef.** dimension_type, dimension_id, label e parent_ids. Produto, cliente, canal e equipa permanecem tipos distintos.

**TypedValue.** data_type, value, null_reason e original_text. null_reason ∈ {missing, unknown, not_applicable, redacted, parse_error}.

**NÃO NEGOCIÁVEL** Percentagens MUST carregar numerador, denominador e unidade. Uma variação percentual nunca é inferida apenas de texto.

# **5\. Origem e proveniência**

## **5.1 SourceDocument**

| **Campo**            | **Obrigação**                                              |
| -------------------- | ---------------------------------------------------------- |
| source_id            | Identificador estável e único no workspace.                |
| workspace_id         | Boundary de autorização e isolamento.                      |
| filename / format    | Nome original e SourceFormat.                              |
| content_hash         | SHA-256 dos bytes recebidos.                               |
| uploaded_at          | Timestamp UTC real; não pode ser reconstruído pelo modelo. |
| parser_version       | Versão do extrator utilizado.                              |
| trust_level          | declared · verified · untrusted.                           |
| supersedes_source_id | Opcional; liga uma nova versão sem mutar a anterior.       |

## **5.2 SourceReference**

Representa a localização exata de uma evidência: source_id, sheet_name ou page_number, cell_range/row/column, location_label, excerpt_hash e optional excerpt. O trecho é limitado; o hash garante integridade.

# **6\. Dados normalizados e mapping**

**FieldDefinition.** field_id, original_name, semantic_type, data_type, unit, currency, mapping_status, mapping_confidence e user_confirmed.

**MappingStatus.** auto_confirmed · needs_confirmation · user_confirmed · rejected. Um campo ambíguo não pode ser promovido silenciosamente.

**NormalizedObservation.** observation_id, semantic_field_id, period, dimensions, typed_value, source_reference, provenance e validation_status.

**NormalizedDataset.** dataset_id, schema_version, source_ids, periods, dimensions, field_definitions, observation_count, module_suitability e created_at.

## **6.1 Regras de normalização**

- Nomes originais e valores originais MUST ser preservados.
- Moeda, escala, sinal contabilístico e granularidade temporal MUST ser resolvidos antes do Metric Engine.
- Mappings com ambiguidade material MUST acionar mapping_required.
- Datas sintéticas ou defaults silenciosos são proibidos.
- Duplicados não são removidos sem uma regra identificada e um issue associado.

# **7\. Catálogo e observações de métricas**

**MetricDefinition.** metric_id, version, label, description, formula_expression, input_metric_ids, unit_family, aggregation_rule, scope_rules, allowed_modules e source_priority.

**MetricObservation.** metric_observation_id, metric_id/version, value, period, dimensions, provenance, source/evidence ids, calculation_trace_id e status.

**MetricVariant.** qualifier obrigatório quando há definições legítimas concorrentes, por exemplo EBITDA statutory vs adjusted. Variantes nunca partilham o mesmo identificador.

## **7.1 CalculationTrace**

| **Campo**            | **Regra**                                          |
| -------------------- | -------------------------------------------------- |
| formula_expression   | Expressão canónica da MetricDefinition.            |
| inputs               | Lista ordenada de IDs, valores, unidade e período. |
| output               | Valor antes e depois do arredondamento.            |
| rounding_policy      | Precisão e modo aplicados.                         |
| engine_version       | Versão executável que produziu o cálculo.          |
| reconciliation_delta | Diferença face a valor publicado, se existir.      |

**EXEMPLO** gross_margin_pct = (revenue − cogs) / revenue × 100. Se revenue = 0 ou estiver ausente, o resultado é inválido; não é 0%.

# **8\. Evidência**

**Evidence.** evidence_id, evidence_type, description, source_reference_ids, metric_observation_ids, strength, supports_claim_ids e contradicts_claim_ids.

| **EvidenceType** | **Uso**                                                 |
| ---------------- | ------------------------------------------------------- |
| direct           | Valor ou afirmação diretamente presente na fonte.       |
| calculated       | Resultado determinístico com CalculationTrace.          |
| corroborating    | Elemento independente que reforça uma conclusão.        |
| contradictory    | Elemento que limita ou contradiz a conclusão.           |
| missing          | Ausência relevante que impede ou condiciona capacidade. |

# **9\. Qualidade, cobertura e sufficiency gate**

**DataQualityIssue.** issue_id, code, severity, category, message, affected_refs, expected, observed, remediation e blocked_capabilities.

**CoverageAssessment.** module, required_inputs, present_inputs, missing_inputs, ambiguous_inputs, quality_level, score_components, assessment_rule_version e assessed_at.

## **9.1 Categorias de issue**

| **Categoria** | **Exemplos**                                                     |
| ------------- | ---------------------------------------------------------------- |
| structural    | Ficheiro ilegível, colunas partidas, cabeçalhos duplicados.      |
| semantic      | Receita confundida com volume; cliente confundido com produto.   |
| financial     | Sinal, moeda ou escala inconsistente; subtotais não reconciliam. |
| temporal      | Períodos misturados, lacunas, datas futuras indevidas.           |
| provenance    | Valor sem origem, documento substituído ou referência perdida.   |

## **9.2 Matriz mínima de capacidades**

| **Qualidade** | **Permitido por defeito**                                             | **Bloqueado por defeito**                       |
| ------------- | --------------------------------------------------------------------- | ----------------------------------------------- |
| complete      | Todas as capacidades, sujeitas a regras específicas.                  | Nenhuma.                                        |
| sufficient    | Resumo, cálculo, comparação, diagnóstico e recomendações reversíveis. | Decisão material sem auditoria reforçada.       |
| partial       | Resumo, cálculo local e hipóteses claramente marcadas.                | Forecast, impacto material e causalidade forte. |
| insufficient  | Qualidade, lacunas e pedido de dados.                                 | Análise, ranking, forecast e recomendações.     |

**AnalysisCapabilityDecision.** capability, decision_status, reason_codes, supporting_issue_ids, requirements_to_unlock e rule_version.

**PRECEDÊNCIA** Um issue blocking vence o quality_level agregado. O gate decide antes de o analista ser invocado e é revalidado pelo auditor.

# **10\. Contrato de Finding**

| **Grupo**  | **Campos**                                                  |
| ---------- | ----------------------------------------------------------- |
| Identidade | finding_id · run_id · module · title                        |
| Afirmação  | claim_type · statement · causal_status                      |
| Base       | metric_observation_ids · evidence_ids · counterevidence_ids |
| Limites    | missing_data_ids · assumptions · scope                      |
| Avaliação  | confidence_score/band · materiality_score · analyst_version |
| Estado     | draft · audited · rejected                                  |

## **10.1 CausalStatus**

- observed: descrição direta sem mecanismo causal.
- associated: duas variáveis movem-se juntas; não implica causa.
- hypothesis: mecanismo plausível, explicitamente condicionado.
- reconciled_driver: efeito quantitativo reconciliado com identidade ou bridge definida.

**REGRA** Um título forte como "custos de pessoal causaram a queda da margem" exige reconciled_driver. Caso contrário, a linguagem MUST permanecer associação ou hipótese.

# **11\. Forecast, impacto e recomendação**

**Forecast.** forecast_id, method, horizon, target_metric_id, training_periods, scenarios, assumptions, prediction_interval, backtest_summary, capability_decision_id e status.

**ImpactEstimate.** baseline, gross_impact, implementation_cost, net_impact, currency/unit, formula, range, sensitivity_inputs e calculation_trace_id.

**Recommendation.** recommendation_id, finding_ids, action, action_type, evidence_ids, assumptions, impact_estimate_id, timeframe, owner_role, dependencies, risks, confidence, capability_decision_id e audit_status.

## **11.1 ActionType**

| **Tipo**              | **Exemplo**                                         | **Gate mínimo**                                      |
| --------------------- | --------------------------------------------------- | ---------------------------------------------------- |
| reversible_experiment | Teste de preço limitado ou controlo de desperdício. | sufficient; partial apenas sem impacto quantificado. |
| operational_change    | Alterar processo, escala ou política operacional.   | sufficient + audit.                                  |
| material_commitment   | Investimento, despedimento, financiamento ou fecho. | complete + auditoria reforçada + decisão humana.     |

## **11.2 Prioridade transparente**

PriorityAssessment contém impact_score, urgency_score, confidence_score e ease_score, cada um com inputs e regra. O score final MUST resultar de fórmula versionada. Se um componente não puder ser medido, a prioridade é incompleta; o modelo não preenche o vazio por intuição.

## **11.3 Limites temporais**

- Recomendações futuras usam starts_on/target_date; ações já concluídas usam retrospective = true.
- Nenhum prazo pode anteceder generated_at sem marcação retrospetiva.
- Forecast MUST informar horizonte, frequência, base histórica e intervalo; um ponto isolado não é forecast.

# **12\. Auditoria independente**

**AuditCheck.** check_id, check_type, status, severity, message, related_object_ids, remediation e deterministic.

**AuditResult.** audit_id, run_id, status, checks, required_revisions, approved_object_ids, rejected_object_ids, auditor_version e audited_at.

## **12.1 Famílias de checks**

| **CheckType**  | **Pergunta obrigatória**                                            |
| -------------- | ------------------------------------------------------------------- |
| schema         | O objeto cumpre o schema e os enums?                                |
| traceability   | Cada claim e cálculo aponta para evidência válida?                  |
| reconciliation | Totais, pontes, variantes e períodos são coerentes?                 |
| capability     | O output respeita as capacidades permitidas?                        |
| causality      | A linguagem excede a força da evidência?                            |
| recommendation | Impacto, custo, risco e reversibilidade estão explícitos?           |
| report_parity  | O relatório contém apenas objetos aprovados e é idêntico por canal? |

**ONE-WRITER RULE** Após auditoria, apenas o Report Compiler escreve ReportResult. Analistas e critic não alteram diretamente o relatório final.

# **13\. ReportResult - fonte única de publicação**

| **Bloco**  | **Conteúdo**                                                               |
| ---------- | -------------------------------------------------------------------------- |
| Envelope   | report_id · run_id · schema_version · workspace_id · module · generated_at |
| Contexto   | company_profile · analysis_period · comparison_basis                       |
| Confiança  | coverage_assessment · capability_decisions · data_quality_issues           |
| Resultados | metric_observations · findings · forecasts · recommendations               |
| Controlo   | audit_result · missing_data_requests · methodology · source_index          |
| Render     | report_version · render_version · locale · display_preferences             |

## **13.1 ExecutiveSummary**

É um objeto estruturado com headline, verified_facts, material_findings, limitations e next_actions. Não é um texto livre que possa introduzir números, causas ou recomendações inexistentes nos objetos aprovados.

## **13.2 Paridade**

- A aplicação web e o PDF recebem o mesmo report_id e report_version.
- O renderer pode alterar paginação e apresentação, nunca omitir conteúdo material.
- Qualquer diferença de objetos ou valores entre canais é erro blocking.

# **14\. AnalysisRun e state machine**

**AnalysisRun.** run_id, workspace_id, module, pipeline_version, schema_version, input_source_ids/hashes, status, stage_results, started_at, completed_at, failure, legacy_comparison_id e idempotency_key.

| **Estado**       | **Próximos estados válidos**             | **Condição**                                            |
| ---------------- | ---------------------------------------- | ------------------------------------------------------- |
| received         | parsing · failed                         | Pedido autenticado, quota reservada, hashes registados. |
| parsing          | mapping_required · validating · failed   | Extração concluída ou intervenção necessária.           |
| mapping_required | validating · failed                      | Confirmações humanas resolvidas.                        |
| validating       | blocked · calculating · failed           | Issues e capability decisions persistidos.              |
| blocked          | validating · failed                      | Novos dados ou correções explícitas.                    |
| calculating      | analyzing · failed                       | Métricas e traces válidos.                              |
| analyzing        | auditing · failed                        | Outputs dos analistas validados por schema.             |
| auditing         | analyzing · compiling · blocked · failed | Revisão, aprovação ou bloqueio.                         |
| compiling        | completed · failed                       | ReportResult aprovado e persistido.                     |
| completed        | -                                        | Estado terminal imutável.                               |
| failed           | received                                 | Nova tentativa cria run novo ou retry_count versionado. |

## **14.1 StageResult**

Cada etapa persiste stage_name, stage_version, input_hashes, output_schema_version, output_hash, started_at, completed_at, status e error. Repetir uma etapa com o mesmo idempotency_key e inputs MUST devolver o mesmo resultado ou uma incompatibilidade explícita.

# **15\. Versionamento e reprodutibilidade**

| **Versão**                | **Responsabilidade**                           |
| ------------------------- | ---------------------------------------------- |
| schema_version            | Estrutura dos contratos.                       |
| pipeline_version          | Ordem, políticas e composição das etapas.      |
| metric_definition_version | Fórmula e semântica de cada métrica.           |
| rule_version              | Qualidade, capability, prioridade e auditoria. |
| analyst_version           | Prompt/skill/model configuration do analista.  |
| render_version            | Transformação de ReportResult em web/PDF.      |

Um run reproduzível MUST reter todas as versões, hashes das fontes, configurações relevantes e outputs por estágio. Uma melhoria nunca reinterpreta silenciosamente um relatório histórico.

# **16\. Persistência e limites de API**

## **16.1 Entidades V2 recomendadas**

- analysis_runs e stage_results para execução e observabilidade.
- source_documents, source_references e normalized_datasets para origem.
- metric_definitions, metric_observations e calculation_traces para cálculo.
- quality_issues, coverage_assessments e capability_decisions para gates.
- findings, forecasts, recommendations, evidence e audit_results para análise.
- report_results para publicação imutável.

JSONB MAY armazenar payloads imutáveis de estágio, mas campos necessários a autorização, procura, auditoria, comparação e integridade SHOULD ter colunas/índices explícitos. O V2 não deve repetir o padrão de colocar toda a semântica crítica num único result_json opaco.

## **16.2 API boundary inicial**

**V2AnalysisRequest.** workspace_id, module, source_ids, locale, requested_period, comparison_basis e idempotency_key.

**AnalysisRunAccepted.** run_id, status, created_at e links de consulta. O contrato admite execução síncrona inicialmente e assíncrona depois sem mudar o domínio.

**AnalysisRunView.** status, progress_stage, non-sensitive issues, required_user_actions e report_id quando concluído.

**AUTORIZAÇÃO** Todo objeto persistido MUST estar alcançável por workspace_id. Nenhum ID recebido do cliente é confiável sem validação de pertença.

# **17\. Invariantes do domínio**

| **ID** | **Regra obrigatória**                                                        |
| ------ | ---------------------------------------------------------------------------- |
| DC-001 | Toda afirmação material referencia pelo menos uma Evidence aprovada.         |
| DC-002 | Toda MetricObservation calculated referencia uma CalculationTrace válida.    |
| DC-003 | Toda percentagem contém numerador, denominador e unidade compatíveis.        |
| DC-004 | Toda recomendação referencia findings e uma capability decision permitida.   |
| DC-005 | material_commitment é bloqueado abaixo de complete e exige decisão humana.   |
| DC-006 | MetricDefinition/version não muda dentro do mesmo run.                       |
| DC-007 | SourceReference é imutável e resolve para SourceDocument existente.          |
| DC-008 | ReportResult inclui apenas objetos aprovados pelo AuditResult.               |
| DC-009 | Web e PDF renderizam o mesmo report_id/report_version.                       |
| DC-010 | Prazo passado exige retrospective = true.                                    |
| DC-011 | Ranking e título usam a mesma DimensionType.                                 |
| DC-012 | Forecast exige base histórica e gate forecast permitido.                     |
| DC-013 | Confidence score é derivado por regra; não é escolhido pelo modelo.          |
| DC-014 | Run não pode completar com issue blocking não resolvido.                     |
| DC-015 | Causalidade forte exige reconciled_driver ou evidência equivalente definida. |
| DC-016 | Ausente, zero, desconhecido e não aplicável nunca são convertidos entre si.  |
| DC-017 | Impacto líquido = impacto bruto − custo, com fórmula e sensibilidade.        |
| DC-018 | ExecutiveSummary não introduz factos ausentes dos objetos aprovados.         |

# **18\. Ponte com o saleslab-api atual**

| **Atual**            | **V2**                            | **Estratégia de transição**                                   |
| -------------------- | --------------------------------- | ------------------------------------------------------------- |
| Analysis.result_json | ReportResult + stage payloads     | Manter leitura legacy; V2 grava envelope versionado separado. |
| data_quality         | Issues + CoverageAssessment       | Adaptador temporário; não tratar string como gate.            |
| status genérico      | WorkflowStatus                    | Mapear estados antigos apenas na API legacy.                  |
| prompt monolítico    | Analistas + contracts + auditor   | Executar V2 em rota/serviço paralelo.                         |
| PDF próprio          | Renderer de ReportResult          | Comparar paridade antes de substituir.                        |
| quota                | Reserva e consumo por AnalysisRun | Preservar billing; tornar retry idempotente.                  |

## **18.1 Compatibilidade**

- Auth, workspaces, billing, base de dados e deploy permanecem; contratos V2 não autorizam reescrita indiscriminada.
- O endpoint legacy não muda durante a fase de shadow run.
- Um legacy_comparison_id liga resultados equivalentes para avaliação quantitativa.
- Migração de leitura só ocorre após critérios de qualidade, paridade e regressão serem cumpridos.

# **19\. Critérios de aceitação deste contrato**

- Todos os objetos críticos possuem identificador, versionamento e owner de validação.
- Nenhum módulo precisa de inventar formatos próprios para finding, recomendação ou auditoria.
- É possível explicar qualquer número desde ReportResult até fonte e fórmula.
- É possível bloquear capacidades sem invocar um modelo.
- É possível executar V2 ao lado do legacy e comparar resultados.
- É possível gerar web e PDF a partir do mesmo objeto.
- As decisões ainda abertas estão explicitamente separadas das decisões fixadas.

# **20\. Decisões abertas - não assumir**

| **Decisão**          | **O que falta definir**                                             |
| -------------------- | ------------------------------------------------------------------- |
| Relacional vs JSONB  | Split físico por entidade, volume e padrões de consulta.            |
| Execução assíncrona  | Momento de introduzir queue, worker e cancelamento.                 |
| Retenção             | Prazos para uploads, extrações, relatórios e direito ao apagamento. |
| Confirmação humana   | UX e threshold de mapping_confidence.                               |
| Scoring              | Fórmulas de confiança, materialidade, prioridade e cobertura.       |
| Contratos por módulo | Inputs obrigatórios, métricas, gates e testes de cada analista.     |
| Model routing        | Modelos, fallback, budgets e políticas por tarefa.                  |

**PRÓXIMO PASSO** Criar Module Specifications v0.1, começando pelo Financeiro: datasets mínimos, catálogo de métricas, reconciliações, gates, outputs, testes e casos adversariais.

# **21\. Contratos multilingues**

## **21.1 Enums e value objects**

| **Contrato**      | **Definição**                                                                               |
| ----------------- | ------------------------------------------------------------------------------------------- |
| LanguageCode      | pt · en · es · fr.                                                                          |
| LocaleTag         | Tag BCP 47 validada; separada do idioma e configurável por utilizador/workspace.            |
| LocalizedString   | key canónica + values por LanguageCode + fallback_policy + terminology_version.             |
| LanguageContext   | source_language · analysis_language · report_language · format_locale · accounting_context. |
| TranslationStatus | approved · machine_draft · needs_review · missing · rejected.                               |
| TerminologyEntry  | concept_id · canonical_label · traduções · aliases · prohibited_terms · version.            |

## **21.2 Extensões aos objetos**

- AnalysisRun MUST persistir LanguageContext e versões do terminology catalog/prompt locale.
- SourceDocument MAY conter vários idiomas; cada SourceReference preserva original_text e detected_language.
- MetricDefinition usa metric_id canónico e labels LocalizedString; fórmulas nunca são traduzidas.
- Finding e Recommendation guardam estrutura semântica canónica e narrative_variants localizadas.
- ReportResult inclui report_language, format_locale, terminology_version e translation_audit.
- DataQualityIssue usa code canónico; a mensagem é localizada apenas na fronteira de apresentação.

## **21.3 Invariantes adicionais**

| **ID** | **Regra obrigatória**                                                                            |
| ------ | ------------------------------------------------------------------------------------------------ |
| DC-019 | Mudar report_language não altera valores, IDs, gates, severidade ou AuditStatus.                 |
| DC-020 | Fórmulas, metric_ids, enum values e reason_codes nunca são traduzidos.                           |
| DC-021 | Toda tradução financeira resolve por concept_id e terminology_version.                           |
| DC-022 | Texto original da fonte é preservado mesmo quando a análise ocorre noutro idioma.                |
| DC-023 | Separadores regionais só afetam apresentação; valores persistidos mantêm representação canónica. |
| DC-024 | Web e PDF usam o mesmo LanguageContext e não podem divergir na língua ou completude.             |
| DC-025 | Fallback de tradução crítica é visível e gera issue; não produz mistura silenciosa de idiomas.   |
| DC-026 | Prompt injection em conteúdo traduzido continua sujeito às mesmas fronteiras de confiança.       |