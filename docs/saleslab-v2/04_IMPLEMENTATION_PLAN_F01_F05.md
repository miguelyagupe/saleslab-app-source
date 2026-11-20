**IMPLEMENTATION PLAN**

**SalesLab V2 - F-01 a F-05**

_Fundação técnica do módulo Financeiro, em paralelo ao produto atual_

| **Campo**        | **Definição**                                                                 |
| ---------------- | ----------------------------------------------------------------------------- |
| Estado           | Plano executável sujeito a readiness read-only no workspace Codex             |
| Repositório alvo | miguelyagupe-cyber/saleslab-api · branch base a confirmar                     |
| Referência       | anthropics/financial-services · padrões, não código a copiar                  |
| Âmbito           | F-01 schemas · F-02 mapping · F-03 metrics · F-04 reconciliation · F-05 gates |
| Idiomas          | Português · inglês · espanhol · francês                                       |

**RESULTADO** Ao concluir F-05, o SalesLab terá um core financeiro determinístico e testado, mas ainda sem substituir o endpoint legacy nem publicar relatórios V2 a clientes.

# **1\. Evidência verificada no repositório**

A inspeção foi read-only sobre \`master\`. Os paths e comportamentos abaixo foram confirmados; qualquer branch diferente deve ser novamente comparada antes de implementar.

| **Path atual**   | **Responsabilidade confirmada**                                                       | **Tratamento**                               |
| ---------------- | ------------------------------------------------------------------------------------- | -------------------------------------------- |
| main.py          | FastAPI, parsing, cálculos, prompts, Anthropic, histórico, chat, Stripe e Resend.     | Preservar; extrair V2 gradualmente.          |
| models.py        | User, Workspace, WorkspaceMember, Analysis, UsageCounter e entidades de billing/auth. | Não quebrar; V2 usa tabelas aditivas.        |
| auth.py          | JWT, Google, magic links, membership e quotas atómicas.                               | Reutilizar sem duplicar.                     |
| database.py      | SQLAlchemy async/Postgres; startup usa Base.metadata.create_all.                      | Migrar com cuidado para workflow versionado. |
| requirements.txt | FastAPI, Anthropic, SQLAlchemy, asyncpg, Alembic, Stripe, Resend e parsing XLSX.      | Adicionar apenas dependências justificadas.  |
| Procfile         | Railway executa uvicorn main:app.                                                     | Não alterar nos slices F-01-F-05.            |

## **1.1 Riscos confirmados**

- \`main.py\` mistura domínio, infraestrutura, API e apresentação; qualquer alteração direta tem blast radius elevado.
- \`parse_numeric_value\` e \`format_amount\` estão orientados a convenções portuguesas e usam float.
- O prompt obriga European Portuguese e mistura política linguística com schema analítico.
- \`Analysis.result_json\` é um envelope legacy opaco; não deve receber payloads V2 sem versionamento.
- \`init_db()\` usa create_all; migrations e rollback precisam de ser validados antes de Railway.
- Quota é consumida após sucesso na mesma transação do Analysis legacy; V2 deve preservar atomicidade e idempotência.

## **1.2 Não verificado - bloquear suposições**

- Branch que contém alterações experimentais e divergência exata face a \`master\`.
- Estrutura atual de Alembic/migrations e comando efetivamente executado no deploy.
- Suite de testes existente na branch de trabalho; não foi localizada em \`master\` por paths comuns.
- Contrato atual do frontend e ownership do renderer PDF.
- Políticas reais de retenção dos uploads e limites de observabilidade Railway.

# **2\. Padrões aproveitados da Anthropic**

| **Padrão**           | **Referência observada**                                                        | **Aplicação SalesLab**                                               |
| -------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Reader isolado       | Statement Auditor e Month-End Closer tratam documentos externos como untrusted. | Parser não recebe ferramentas de escrita nem controla orchestration. |
| Auditor independente | Statement Auditor faz tie-out e recomenda pass/hold.                            | Reconciliation e AuditResult separados do analista.                  |
| One writer           | Model Builder: apenas builder escreve; auditor revalida.                        | Só Report Compiler publicará ReportResult em F-08.                   |
| Handoff tipado       | orchestrate.py valida allowlist e payload schema.                               | Stage transitions e reason codes, nunca texto livre executável.      |
| Human sign-off       | No posting/distribution sem aprovação.                                          | Material commitments permanecem decisões humanas.                    |
| Artefacto rastreável | Outputs citam inputs e fórmulas.                                                | CalculationTrace e SourceReference obrigatórios.                     |

**LIMITE** Não adotar \`/v1/agents\`, tool topology ou estrutura de plugins da Anthropic nesta fase. O SalesLab permanece um modular monolith FastAPI.

# **3\. Estrutura-alvo do código**

Criar um package aditivo \`saleslab_v2/\`. Os nomes abaixo são paths de implementação propostos; o readiness inicial pode ajustar apenas quando existir conflito real demonstrado.

| **Path novo**                           | **Responsabilidade**                                                                          |
| --------------------------------------- | --------------------------------------------------------------------------------------------- |
| saleslab_v2/domain/enums.py             | Enums canónicos: módulo, proveniência, qualidade, severidade, capability, workflow e idiomas. |
| saleslab_v2/domain/value_objects.py     | Period, Money/Decimal, Unit, DimensionRef, LanguageContext e source references.               |
| saleslab_v2/domain/financial.py         | Schemas financeiros, observations, findings e contratos de estágio.                           |
| saleslab_v2/domain/quality.py           | Issues, coverage e capability decisions.                                                      |
| saleslab_v2/i18n/catalog.py             | TerminologyEntry, aliases, fallback e resolução pt/en/es/fr.                                  |
| saleslab_v2/i18n/catalogs/\*.json       | Quatro catálogos versionados, sem fórmulas nem IDs traduzidos.                                |
| saleslab_v2/normalization/              | Locale parser, semantic mapper, period/scope/scale/sign normalization.                        |
| saleslab_v2/metrics/                    | MetricDefinition registry, calculation engine e traces.                                       |
| saleslab_v2/reconciliation/             | Rules, tolerances, checks e results.                                                          |
| saleslab_v2/quality/                    | Coverage evaluator e capability gate.                                                         |
| saleslab_v2/persistence/models.py       | Entidades SQLAlchemy V2, sempre com workspace boundary.                                       |
| saleslab_v2/persistence/repositories.py | Persistência idempotente e queries por workspace/run.                                         |
| saleslab_v2/api/schemas.py              | Requests/responses V2; sem acoplar domínio ao Form legacy.                                    |
| saleslab_v2/api/router.py               | Router sob feature flag; wiring público posterior.                                            |
| tests/v2/                               | Unit, integration, fixtures, golden e i18n invariance.                                        |

## **3.1 Alterações mínimas em ficheiros atuais**

| **Path**             | **Mudança permitida até F-05**                                                             |
| -------------------- | ------------------------------------------------------------------------------------------ |
| main.py              | Importar/incluir router V2 apenas quando feature flag ativa; nenhum rewrite.               |
| database.py          | Suportar migration workflow e importar metadata V2 de forma explícita; preservar sessions. |
| models.py            | Nenhuma mudança estrutural obrigatória; relações V2 são opcionais.                         |
| auth.py              | Reutilizar get_current_session e quota; não alterar regras/plans.                          |
| requirements.txt     | Adicionar dependência runtime apenas se necessária e compatível.                           |
| requirements-dev.txt | Novo: pytest, pytest-asyncio, httpx e ferramentas de cobertura.                            |

# **4\. Regras globais de implementação**

- Nenhum slice altera a resposta de \`/analyze\`, \`/history\`, \`/chat\`, auth, Stripe ou Resend.
- \`SALESLAB_V2_ENABLED\` tem default false; desligar remove o wiring V2 sem rollback destrutivo.
- Todos os IDs persistidos usam UUID; valores financeiros usam Decimal/Numeric, nunca float.
- Toda tabela V2 contém workspace_id diretamente ou por FK não ambígua a AnalysisRun.
- Dados de origem e stage payloads publicados são imutáveis; correções criam nova versão/run.
- Enums persistidos usam códigos canónicos em inglês; mensagens são localizadas na apresentação.
- Nenhuma chamada a modelo é adicionada em F-01-F-05.
- Cada slice termina em commit independente, migration reversível e testes verdes.

# **5\. F-01 - Schemas, enums e fixtures**

**OBJETIVO** Criar o vocabulário executável do domínio e uma AnalysisRun V2 vazia, sem análise por IA e sem expor nova funcionalidade ao cliente.

## **5.1 Entregas**

- Package \`saleslab_v2\` e módulos domain/i18n/persistence.
- Enums e value objects do Domain Contracts v0.2.
- LanguageCode pt/en/es/fr, LanguageContext e parsing de LocaleTag validado.
- AnalysisRun, StageResult, SourceDocument metadata e idempotency contract.
- Fixtures canónicas: Jerónimo Martins metadata, PME mínima e quatro locale variants.

## **5.2 Persistência e migration**

| **Tabela**          | **Campos nucleares**                                                                                                                    |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| analysis_runs_v2    | id · workspace_id · created_by · module · status · schema/pipeline versions · language context · idempotency_key · timestamps · failure |
| stage_results_v2    | id · run_id · stage_name/version · input/output hashes · payload JSONB · status · timestamps · error                                    |
| source_documents_v2 | id · workspace_id · filename · format · content_hash · parser_version · trust_level · detected_language · created_at                    |

Migration: criar nova revisão a partir do head real encontrado no workspace. Se o repositório não tiver Alembic operacional, estabelecer \`alembic.ini\`/env antes de qualquer deploy e impedir que \`create_all\` seja o mecanismo de produção para tabelas V2.

## **5.3 Testes e gate de saída**

- Serialização/deserialização de todos os enums e value objects.
- Money rejeita float e currency inválida; Period rejeita end < start.
- LanguageContext aceita pt/en/es/fr e separa format_locale.
- Idempotency key única por workspace/purpose; outro workspace não colide.
- Upgrade e downgrade da migration em Postgres de teste.

# **6\. F-02 - Mapping e normalização financeira**

**OBJETIVO** Transformar inputs heterogéneos em observations canónicas preservando origem, ambiguidade, idioma, escala, sinal, período e scope.

## **6.1 Componentes**

| **Componente**      | **Path**                   | **Contrato**                                                        |
| ------------------- | -------------------------- | ------------------------------------------------------------------- |
| LocaleNumberParser  | normalization/numbers.py   | String + format_locale → Decimal ou issue; sem guessing silencioso. |
| PeriodNormalizer    | normalization/periods.py   | Labels/YTD/month/quarter → Period + comparability.                  |
| SemanticMapper      | normalization/mapping.py   | Original field → semantic_type + confidence/status.                 |
| FinancialNormalizer | normalization/financial.py | P&L/BS/CF rows → NormalizedObservation.                             |
| TerminologyRegistry | i18n/catalog.py            | Aliases pt/en/es/fr → concept_id versionado.                        |

## **6.2 Persistência**

| **Tabela**                 | **Campos nucleares**                                                                                                    |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| normalized_datasets_v2     | id · run_id · source_ids · schema_version · periods · dimensions · suitability · created_at                             |
| field_definitions_v2       | id · dataset_id · original_name · semantic_type · datatype · unit/currency · mapping status/confidence · user_confirmed |
| normalized_observations_v2 | id · dataset_id · field_id · period · dimensions · Decimal value · unit/currency · source reference · status            |

## **6.3 Casos obrigatórios**

- \`1.234,56\` e \`1,234.56\` resolvem ao mesmo Decimal quando o locale é conhecido.
- Locale ausente com separador ambíguo produz mapping_required.
- Zero, missing, unknown e not_applicable permanecem distintos.
- Cabeçalhos equivalentes nos quatro idiomas mapeiam ao mesmo concept_id.
- EBITDA sem definição permanece reported_unverified; não vira statutory.
- YTD de nove meses não compara diretamente com ano completo.

# **7\. F-03 - Metric Catalog e CalculationTrace**

**OBJETIVO** Eliminar fórmulas embutidas em prompts e helpers dispersos. Toda métrica deriva de uma definição versionada e reproduzível.

## **7.1 Paths e responsabilidades**

| **Path**                | **Conteúdo**                                                          |
| ----------------------- | --------------------------------------------------------------------- |
| metrics/definitions.py  | MetricDefinition dataclass/Pydantic e registry imutável.              |
| metrics/financial_v1.py | Catálogo inicial do Financial Module Specification v0.2.              |
| metrics/engine.py       | Resolver dependências, validar units/period/scope e calcular Decimal. |
| metrics/trace.py        | Inputs ordenados, fórmula, output, rounding e engine_version.         |
| metrics/rounding.py     | Políticas explícitas; armazenamento não perde precisão.               |

## **7.2 Métricas da primeira entrega**

Revenue/growth, gross profit/margin, statutory e adjusted EBITDA, EBITDA/EBIT/net margins, CFO, capex, FCF, cash conversion, NWC, DSO/DIO/DPO/CCC, ROIC, current/quick ratio, gross/net debt variants, leverage, interest coverage, fixed/variable mix e maturity concentration.

## **7.3 Persistência e testes**

- metric_observations_v2 e calculation_traces_v2, com metric_definition_version.
- Percentagens guardam numerator/denominator IDs e valores.
- Golden test JM-FIN-001: 2.113 M€ statutory, 2.232 M€ adjusted e bridge 119 M€.
- Golden test JM-FIN-002: validar 4,4% do EBITDA total e ≈388% de 24 M€.
- Property tests: divisão por zero, sinais, escala, rounding e incompatibilidade de períodos.
- Teste de invariância pt/en/es/fr compara o payload canónico antes de labels.

# **8\. F-04 - Reconciliation Engine**

**OBJETIVO** Determinar se os números podem sustentar análise antes de gerar qualquer narrativa.

## **8.1 Rules**

| **Rule ID**    | **Identidade**                                                                  |
| -------------- | ------------------------------------------------------------------------------- |
| REC-BS-001     | assets = liabilities + equity.                                                  |
| REC-CF-001     | opening cash + CFO + CFI + CFF + FX/other = closing cash.                       |
| REC-PL-001     | Receita − custos ± outros = EBIT; EBIT − finance ± tax = net income.            |
| REC-EBITDA-001 | EBIT + D&A = statutory; ± adjustments = adjusted.                               |
| REC-DEBT-001   | opening + drawdowns − repayments ± FX/other = closing debt.                     |
| REC-LEASE-001  | opening + additions + modifications + interest − payments ± FX/other = closing. |
| REC-GROWTH-001 | LFL + expansão/aquisição + FX + other ≈ reported growth.                        |

## **8.2 Contratos e persistência**

- ReconciliationRule versionada com required inputs e severity policy.
- TolerancePolicy recebe moeda/escala/materialidade; o modelo nunca escolhe threshold.
- ReconciliationResult guarda expected, observed, delta absoluto/relativo, refs e status.
- Tabela \`reconciliation_results_v2\`; rules ficam em código versionado na primeira fase.

## **8.3 Gate de saída**

- Cada regra tem pass, rounding_pass, warning, critical e blocking fixtures.
- JM-FIN-005 impede usar 'orgânico' sem growth bridge.
- JM-FIN-006 impede atribuir +535 M€ de leasing integralmente à expansão.
- Resultados são determinísticos com mesma versão/inputs e não dependem de idioma.

# **9\. F-05 - Coverage e Capability Gates**

**OBJETIVO** Transformar qualidade de dados em permissões executáveis antes de chamar qualquer analista.

## **9.1 Componentes**

| **Path**                         | **Responsabilidade**                                  |
| -------------------------------- | ----------------------------------------------------- |
| quality/requirements.py          | RequiredInputSet por profile/capability.              |
| quality/coverage.py              | Present/missing/ambiguous e QualityLevel.             |
| quality/issues.py                | Issue codes, categories, severity e remediation keys. |
| quality/capabilities.py          | allowed/conditional/blocked + reasons_to_unlock.      |
| quality/policies/financial_v1.py | Matriz do módulo Financeiro versionada.               |

## **9.2 Persistência**

| **Tabela**              | **Conteúdo**                                                                                             |
| ----------------------- | -------------------------------------------------------------------------------------------------------- |
| data_quality_issues_v2  | code · severity · category · affected refs · expected/observed · remediation key · blocking capabilities |
| coverage_assessments_v2 | run/module · required/present/missing/ambiguous · quality · score components · rule version              |
| capability_decisions_v2 | run · capability · status · reason codes · issue refs · requirements_to_unlock · rule version            |

## **9.3 Aceitação**

- Issue blocking prevalece sobre score agregado.
- F0 fragmentary bloqueia análise, ranking, forecast e recomendação.
- Partial permite resumo/cálculo local, mas bloqueia material commitment e forecast.
- Hedge sem dívida fixa/variável, moeda, maturidade e cobertura fica blocked.
- Pedidos de dados são localizados por key nos quatro idiomas; reason codes permanecem iguais.

# **10\. Migration e Railway strategy**

## **10.1 Sequência segura**

- Confirmar branch base, Alembic head e mecanismo de deploy antes do primeiro write.
- Criar migrations aditivas; nunca renomear/remover tabelas legacy em F-01-F-05.
- Executar upgrade em base temporária com snapshot/schema equivalente.
- Validar downgrade de cada slice; dados V2 são descartáveis enquanto feature flag estiver off.
- Deploy backend com flag false; aplicar migrations; smoke-test legacy; só depois ativar ambiente interno.

## **10.2 Feature flags**

| **Flag**                        | **Default** | **Efeito**                                                 |
| ------------------------------- | ----------- | ---------------------------------------------------------- |
| SALESLAB_V2_ENABLED             | false       | Regista ou não o router V2.                                |
| SALESLAB_V2_PERSIST_STAGES      | false       | Permite persistência interna de stages.                    |
| SALESLAB_V2_INTERNAL_WORKSPACES | empty       | Allowlist de workspaces para shadow/internal.              |
| SALESLAB_V2_STRICT_I18N         | true        | Termo crítico ausente gera issue, sem fallback silencioso. |

## **10.3 Rollback**

Rollback operacional = desligar flags e voltar ao artefacto anterior; não apagar tabelas V2 no incidente. Downgrade de schema ocorre apenas após preservar evidência e confirmar que nenhuma versão ativa depende das colunas.

# **11\. Estratégia de testes**

| **Camada**  | **Suite**                                                                          |
| ----------- | ---------------------------------------------------------------------------------- |
| Contract    | Pydantic schemas, enums, JSON round-trip e backward-incompatible change detection. |
| Unit        | Parsing, mapping, formulas, rounding, reconciliations, coverage e gates.           |
| Property    | Decimal/locale/separadores, sinais, escala e invariantes matemáticas.              |
| Persistence | Workspace isolation, FK, unique/idempotency e transaction rollback.                |
| Migration   | Upgrade/downgrade em Postgres real de teste.                                       |
| Golden      | JM-FIN-001…006 + PME core/partial/insufficient/month-end.                          |
| I18n        | pt/en/es/fr semantic equivalence; source/report languages cruzados.                |
| Regression  | Endpoints legacy, auth, quota, history, chat, checkout e webhook.                  |

## **11.1 Definition of done comum**

- Lint/type/tests definidos pelo repositório passam; se não existirem, o Codex propõe baseline antes de aplicar.
- Nenhum test usa rede ou modelo real; relógio, UUID e versões são controláveis.
- Fixtures não contêm credenciais, PII ou documentos proprietários não autorizados.
- Coverage inclui branches de blocking e rollback, não apenas happy path.

# **12\. Ordem de commits e PRs**

| **PR**         | **Scope**                                                    | **Não inclui**            |
| -------------- | ------------------------------------------------------------ | ------------------------- |
| PR-0 Readiness | Relatório read-only e eventuais correções deste plano.       | Código.                   |
| PR-1 F-01      | Package, contracts, i18n core, run/source tables e fixtures. | Mapper/metrics.           |
| PR-2 F-02      | Normalization, mapping e observations.                       | Métricas.                 |
| PR-3 F-03      | Metric catalog, engine e traces.                             | Reconciliação.            |
| PR-4 F-04      | Reconciliation rules/results.                                | Agentes.                  |
| PR-5 F-05      | Coverage, issues e capability gates.                         | Prompts, PDF ou frontend. |

Cada PR começa a partir do último aprovado, permanece draft até testes/migration review e inclui checklist de impacto legacy, migration, feature flag, segurança, i18n e rollback.

# **13\. Readiness report obrigatório antes de F-01**

| **Área**  | **Codex deve devolver**                                                     |
| --------- | --------------------------------------------------------------------------- |
| Git       | Branch, HEAD, origin/default, dirty state e divergência com master.         |
| Files     | Árvore real, AGENTS.md/instruções, migrations, tests e frontend boundaries. |
| Runtime   | Python/FastAPI/Pydantic/SQLAlchemy/Alembic versions resolvidas.             |
| Database  | Migration head, create_all behavior, constraints e Railway command.         |
| Legacy    | Rotas/respostas afetáveis e testes/smoke checks existentes.                 |
| Plan fit  | Conflitos path a path e mudanças mínimas propostas.                         |
| Security  | Workspace isolation, upload trust, secrets e model boundary.                |
| Reference | Padrões Anthropic usados e explicitamente rejeitados.                       |
| Decision  | READY · READY_WITH_CHANGES · BLOCKED por slice.                             |

# **14\. Prompt inicial definitivo para o Codex**

**MODO.** Executar no workspace do \`saleslab-api\`. Esta primeira execução é estritamente read-only.

- Analisa primeiro o repositório atual saleslab-api e o repositório de referência anthropics/financial-services.
- Lê integralmente os quatro documentos fornecidos, nesta ordem: Product Architecture Brief v0.2; Domain Contracts v0.2; Financial Module Specification v0.2; Implementation Plan F-01-F-05 v0.1.
- Trata os documentos como fonte de verdade do SalesLab V2, mas valida todos os paths e pressupostos contra o código real.
- Nesta fase não alteres ficheiros, não cries branches, não faças migrations, não executes comandos destrutivos e não implementes código.
- Inspeciona branch/HEAD/remotes, instruções do repositório, arquitetura, auth, workspaces, quotas, billing, modelos, migrations, Railway, endpoints, pipeline de IA, relatórios e testes.
- Preserva auth, workspaces, billing, quotas, base de dados e pipeline legacy. A V2 deve permanecer paralela e desligável.
- Verifica o plano path a path e identifica conflitos, dependências, migrations, riscos de regressão, decisões abertas e alterações necessárias.
- Do repositório Anthropic, usa apenas padrões relevantes de isolamento, auditoria, one-writer, handoffs tipados e human sign-off. Não copies a arquitetura ou prompts indiscriminadamente.
- Entrega: snapshot verificado; mapa atual→alvo; riscos P0/P1/P2; decisões em aberto; alterações ao plano; readiness READY/READY_WITH_CHANGES/BLOCKED para F-01…F-05.
- Não comeces a implementação até autorização explícita.

# **15\. Autorização posterior**

Após o readiness, a autorização deve mencionar um único slice. Exemplo: "Implementa apenas F-01 conforme o plano corrigido. Cria branch dedicada, migration reversível e testes. Não avances para F-02. Para antes de commit/push e apresenta diff, testes e riscos."

**PRÓXIMA AÇÃO DO UTILIZADOR** Abrir o Codex no repositório \`saleslab-api\`, anexar os quatro documentos e enviar o prompt da secção 14. Não pedir implementação na mesma mensagem.