/* Validated financial workflow. All uploaded/model text is rendered as text. */
window.FinancialV2 = (() => {
  let enabled = false, hasReports = false, hasMonthlySnapshots = false, checkedToken = null, preview = null, attempt = null;
  let planningVersion = null, normalizationVersion = null;
  let parenthesesSupported = false, fiscalCalendarSupported = false, sourceMetadataSupported = false;
  let version = 0, reportOnly = false, pollTimer = null, capabilityTimer = null, reportURL = null;
  const el = id => document.getElementById(id);
  const node = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
  function build() {
    if (el("fv2")) return;
    const root = node("div"); root.id = "fv2"; root.hidden = true;
    root.innerHTML = `<div class="card"><div class="card-body">
      <h2>Financeiro · validação dos dados</h2>
      <p>Confirme as rubricas e os períodos antes de gerar. A validação é gratuita; um relatório aprovado consome uma análise.</p>
      <div id="fv2-controls">
        <button type="button" class="btn btn-ghost" id="fv2-template">↓ Modelo CSV</button>
        <button type="button" class="btn btn-ghost" id="fv2-sample">Carregar exemplo real · JM 2024</button>
        <div id="fv2-sample-source"></div>
        <div class="field"><label for="fv2-file">Demonstrações em CSV ou XLSX · uma folha · até 500 linhas / 5 MB</label><input id="fv2-file" type="file" accept=".csv,.xlsx"></div>
        <div id="fv2-import-controls" hidden><p>Para ficheiros com outras colunas, várias folhas ou cabeçalhos fora da primeira linha, prepare a importação antes de validar.</p><span id="fv2-delimiter-field"><label for="fv2-import-delimiter">Separador do CSV</label><select id="fv2-import-delimiter"><option value=";">Ponto e vírgula</option><option value=",">Vírgula</option><option value="&#9;">Tabulação</option></select></span><div class="field" id="fv2-pdf-pages-field" hidden><label for="fv2-pdf-pages">Páginas do PDF a extrair · até oito, por ordem</label><input id="fv2-pdf-pages" value="1" placeholder="1,2,5" maxlength="40"></div> <button type="button" class="btn btn-ghost" id="fv2-inspect">Preparar importação</button> <button type="button" class="btn btn-ghost" id="fv2-import-clear">Usar modelo de oito colunas</button><div id="fv2-pdf-layout"></div><div id="fv2-import-builder"></div><div id="fv2-pdf-review"></div></div>
        <div class="field"><label for="fv2-language">Idioma dos dados</label><select id="fv2-language"><option value="pt">Português</option><option value="en">English</option><option value="es">Español</option><option value="fr">Français</option></select></div>
        <div class="field"><label for="fv2-locale">Formato dos números no ficheiro</label><select id="fv2-locale"><option value="pt-PT">1.234,56 (PT)</option><option value="en-GB">1,234.56 (EN)</option><option value="es-ES">1.234,56 (ES)</option><option value="fr-FR">1 234,56 (FR)</option></select></div>
        <div id="fv2-source-metadata" hidden><div class="field"><label for="fv2-source-currency">Confirmar moeda de todo o ficheiro</label><select id="fv2-source-currency"><option value="">Não confirmado · usar apenas a moeda das linhas</option></select></div><div class="field"><label for="fv2-source-scale">Confirmar escala de todo o ficheiro</label><select id="fv2-source-scale"><option value="">Não confirmado · usar apenas a escala das linhas</option><option value="raw">Unidades da moeda</option><option value="thousands">Milhares</option><option value="millions">Milhões</option></select></div><p>Confirme na fonte apenas se a moeda ou a escala se aplicar a todo o ficheiro. A confirmação resolve campos em falta; valores contraditórios nas linhas continuam bloqueados. Os dados originais são preservados.</p></div>
        <div class="field" id="fv2-parentheses-field" hidden><label for="fv2-parentheses">Neste ficheiro, parênteses indicam valores negativos?</label><select id="fv2-parentheses"><option value="">Não confirmado · bloquear valores entre parênteses</option><option value="true">Sim · parênteses indicam negativos</option><option value="false">Não · parênteses não definem o sinal</option></select><p>A confirmação aplica-se ao ficheiro inteiro. Os valores originais são preservados.</p></div>
        <div class="field" id="fv2-fiscal-field" hidden><label for="fv2-fiscal-month">Mês de fecho do exercício fiscal neste ficheiro</label><select id="fv2-fiscal-month"><option value="">Não confirmado · usar apenas períodos de calendário explícitos</option><option value="1">Janeiro</option><option value="2">Fevereiro</option><option value="3">Março</option><option value="4">Abril</option><option value="5">Maio</option><option value="6">Junho</option><option value="7">Julho</option><option value="8">Agosto</option><option value="9">Setembro</option><option value="10">Outubro</option><option value="11">Novembro</option><option value="12">Dezembro</option></select><p>Confirme na fonte. FY 2026 é o exercício que termina em 2026; FY 2026 Q1 é o seu primeiro trimestre. A escolha aplica-se ao ficheiro inteiro e não converte automaticamente períodos de calendário em fiscais.</p></div>
        <div class="field"><label for="fv2-report-language">Idioma do relatório</label><select id="fv2-report-language"><option value="pt">Português</option><option value="en">English</option><option value="es">Español</option><option value="fr">Français</option></select></div>
        <div class="field"><label for="fv2-tolerance">Arredondamento aceite no balanço, em unidades base da moeda (0 = igualdade exata)</label><input id="fv2-tolerance" value="0" inputmode="decimal"></div>
        <div class="field" id="fv2-capital-tolerance-field" hidden><label for="fv2-capital-tolerance">Arredondamento aceite nas pontes de dívida e leasing, em unidades base da moeda (0 = igualdade exata)</label><input id="fv2-capital-tolerance" value="0" inputmode="decimal"><p>São necessários saldos inicial/final e todos os movimentos explícitos, com montantes positivos para reembolsos e pagamentos. No ficheiro, os movimentos usam debt_schedule e lease_schedule; incluem movimentos sem efeito de caixa. Saldos médios e compras usam working_capital_detail. Caixa sem restrições, créditos elegíveis e outros ativos líquidos exigem confirmação na fonte. Os prazos usam saldos médios explícitos, compras identificadas e os dias reais do período; não substituímos compras pelo custo das vendas. Dívida/EBITDA usa o EBITDA do período sem anualização. Estes rácios não comprovam capacidade de pagamento; faltam maturidades e condições contratuais para esse diagnóstico. Uma ponte de leasing verificada não prova expansão.</p></div>
        <div class="field" id="fv2-profitability-tolerance-field" hidden><label for="fv2-profitability-tolerance">Arredondamento aceite nas pontes de resultados e EBITDA, em unidades base da moeda (0 = igualdade exata)</label><input id="fv2-profitability-tolerance" value="0" inputmode="decimal"><p>As pontes exigem todos os componentes explícitos. Custo das vendas, custos operacionais e depreciações/amortizações usam montantes não negativos; outros resultados, gastos financeiros líquidos, impostos e ajustes conservam o sinal da fórmula. EBITDA genérico permanece por confirmar. Para mapear EBITDA estatutário reportado, confirme a definição EBIT + D&A na fonte. O ajustado exige ajustes líquidos explícitos.</p></div>
        <div class="field" id="fv2-cash-tolerance-field" hidden><label for="fv2-cash-tolerance">Arredondamento aceite na reconciliação de caixa, em unidades base da moeda (0 = igualdade exata)</label><input id="fv2-cash-tolerance" value="0" inputmode="decimal"><p>Separado da tolerância do balanço. A reconciliação exige caixa inicial e final, CFO, CFI, CFF e efeitos cambiais/outros explícitos; indique 0 apenas se confirmado na fonte.</p></div>
        <div class="field"><label for="fv2-period">Período atual a analisar</label><select id="fv2-period"><option value="">Detetar a partir dos dados</option></select></div>
        <div class="field" id="fv2-growth-tolerance-field" hidden><label for="fv2-growth-tolerance">Arredondamento aceite na ponte monetária de crescimento · unidades base da moeda (0 = igualdade exata)</label><input id="fv2-growth-tolerance" value="0" inputmode="decimal" maxlength="64"><p>Reportado, câmbio constante e like-for-like são bases distintas. Os dois últimos exigem receitas monetárias atual/comparativa e uma definição ou perímetro citados da fonte. Os contributos monetários da ponte usam a base reportada confirmada; não são deduzidos das diferenças entre taxas. Uma ponte verificada não autoriza chamar orgânico ao crescimento.</p></div>
        <button type="button" class="btn btn-ghost" id="fv2-validate">Validar ficheiro</button>
        <div id="fv2-preview" tabindex="0" role="region" aria-label="Linhas do ficheiro e validação" style="overflow:auto;max-height:450px;margin:16px 0"></div>
        <div class="field" id="fv2-wc-tolerance-field" hidden><label for="fv2-wc-tolerance">Arredondamento aceite no fundo de maneio operacional · unidades base da moeda (0 = igualdade exata)</label><input id="fv2-wc-tolerance" value="0" inputmode="decimal" maxlength="64"><p>Conferimos ativos operacionais menos passivos operacionais e a variação publicada entre saldos final e inicial. Use o mesmo perímetro confirmado. Variações de saldos incluem efeitos não monetários: não medem o efeito em caixa nem explicam o CFO.</p></div><div id="fv2-wc-review" hidden></div><div id="fv2-roic-review" hidden></div><div id="fv2-growth-review" hidden></div>
        <div class="field" id="fv2-debt-tolerance-field" hidden><label for="fv2-debt-tolerance">Arredondamento aceite nos totais do principal da dívida · unidades base da moeda (0 = igualdade exata)</label><input id="fv2-debt-tolerance" value="0" inputmode="decimal" maxlength="64"><p>Use montantes explícitos de principal, sem leasing, na mesma data de reporte. Fixa + variável e vencimentos até doze meses + posteriores devem reconciliar com o total. A classificação contratual antes de hedge não mede a exposição líquida nem autoriza recomendações de financiamento ou cobertura.</p></div><div id="fv2-debt-review" hidden></div>
        <div class="field" id="fv2-capex-tolerance-field" hidden><label for="fv2-capex-tolerance">Arredondamento aceite no capex pago · unidades base da moeda (0 = igualdade exata)</label><input id="fv2-capex-tolerance" value="0" inputmode="decimal" maxlength="64"><p>Confirme na fonte o total pago e a classificação manutenção/crescimento, sem parcelas deduzidas. A classificação deve reconciliar com o total e, quando presente, com a linha independente de aquisições nos fluxos de caixa. Compromissos por executar são apresentados separadamente: não são pagamentos, datas futuras ou uma previsão de caixa.</p></div><div id="fv2-capex-review" hidden></div>
        <div id="fv2-validation" style="overflow:auto;margin:16px 0" hidden></div>
        <div id="fv2-gaps" style="overflow:auto;margin:16px 0" hidden></div>
        <div id="fv2-metrics" style="overflow:auto;margin:16px 0" hidden></div>
        <button type="button" class="btn btn-primary" id="fv2-generate" disabled>Gerar relatório auditado · 1 análise</button>
      </div>
      <p id="fv2-status" role="status" aria-live="polite"></p>
      <button type="button" class="btn btn-ghost" id="fv2-history">Relatórios guardados</button>
      <div id="fv2-history-list"></div>
      <div id="fv2-result" hidden><button type="button" class="btn btn-ghost" id="fv2-new">Nova análise</button> <button type="button" class="btn btn-ghost" id="fv2-pdf">↓ PDF verificado</button><iframe id="fv2-frame" title="Relatório financeiro auditado" sandbox="" style="display:block;width:100%;height:75vh;border:0;background:white;margin-top:16px"></iframe></div>
    </div></div>`;
    el("inputCard").before(root);
    root.querySelectorAll("input,select").forEach(input => input.addEventListener("change", invalidate));
    el("fv2-inspect").onclick = inspectSource;
    el("fv2-import-clear").onclick = () => { clearImport(); clearMappings(); invalidate(); status("Importação no formato do modelo de oito colunas."); };
    el("fv2-import-delimiter").addEventListener("change", () => { clearImport(); clearMappings(); invalidate(); });
    el("fv2-pdf-pages").addEventListener("change", () => { clearImport(); clearMappings(); invalidate(); });
    buildMonthEnd();
    el("fv2-validate").onclick = validate;
    el("fv2-generate").onclick = generate;
    el("fv2-template").onclick = template;
    el("fv2-sample").onclick = loadSample;
    el("fv2-history").onclick = () => loadHistory();
    el("fv2-new").onclick = () => { reportOnly = false; clearImport(); invalidate(); clearMappings(); el("fv2-file").value = ""; updatePdfControls(); el("fv2-pdf-pages").value = "1"; el("fv2-parentheses").value = ""; el("fv2-fiscal-month").value = ""; clearSourceMetadata(); el("fv2-result").hidden = true; status("Valide um ficheiro para iniciar uma nova análise."); sync(); busy(false); };
  }
  let tabularSupported = false, sourceInspection = null, selectedTables = [];
  let pdfSupported = false, pdfLayouts = [], pdfLayoutDirty = false, pdfReview = null;
  function clearPdfReview() { pdfReview = null; if (el("fv2-pdf-review")) el("fv2-pdf-review").replaceChildren(); }
  function updatePdfControls() { const pdf = el("fv2-file")?.files[0]?.name.toLowerCase().endsWith(".pdf"); if (el("fv2-pdf-pages-field")) el("fv2-pdf-pages-field").hidden = !pdfSupported || !pdf; if (el("fv2-delimiter-field")) el("fv2-delimiter-field").hidden = Boolean(pdf); }
  function pdfInspectionOptions() {
    const pages = el("fv2-pdf-pages").value.split(",").map(v => Number(v.trim()));
    if (!pages.length || pages.length > 8 || pages.some((p,i) => !Number.isInteger(p) || p < 1 || p > 300 || (i && p <= pages[i-1]))) throw new Error("Indique até oito páginas distintas por ordem, por exemplo 1,2,5.");
    return { pages: pages.map(page => ({ page, cuts: pdfLayouts.find(p => p.page === page)?.cuts || [] })) };
  }
  function renderPdfLayout() {
    const root = el("fv2-pdf-layout"); root.replaceChildren();
    if (!sourceInspection?.pdf) return;
    root.append(node("p", `PDF com ${sourceInspection.pdf.page_count} páginas. Páginas extraídas: ${sourceInspection.pdf.pages.map(p => p.page).join(", ")}. Páginas excluídas: ${sourceInspection.pdf.excluded_pages.join(", ") || "nenhuma"}.`));
    root.append(node("p", "Defina onde terminam as colunas no texto extraído (posições de caracteres, sem contar o número da linha). Exemplo: 40,65. Prepare novamente o ficheiro após alterar os limites. A extração de texto pode diferir da página: confronte as linhas com o PDF original."));
    sourceInspection.sheets.forEach(sheet => {
      const label = node("label", `Limites das colunas · página ${sheet.page}`); label.className = "field"; const input = node("input"); input.type = "text"; input.maxLength = 160; input.dataset.pdfCuts = String(sheet.page); input.value = pdfLayouts.find(p => p.page === sheet.page).cuts.join(",");
      input.onchange = () => { clearSourceReviews(); const text = input.value.trim(); const cuts = text ? text.split(",").map(v => Number(v.trim())) : []; if (cuts.length > 31 || cuts.some((v,i) => !Number.isInteger(v) || v < 1 || v > 2000 || (i && v <= cuts[i-1]))) { status("Use limites distintos e crescentes, por exemplo 40,65."); pdfLayoutDirty = true; invalidate(); return; } pdfLayouts.find(p => p.page === sheet.page).cuts = cuts; pdfLayoutDirty = true; clearPdfReview(); clearMappings(); invalidate(); status("Prepare novamente o PDF para aplicar as novas colunas."); };
      label.append(input); root.append(label);
      const details = node("details"); details.append(node("summary", `Texto extraído · página ${sheet.page} · ${sheet.row_count} linhas`)); const pre = node("pre"); pre.style.overflow = "auto"; pre.style.maxHeight = "300px"; pre.style.maxWidth = "100%"; pre.tabIndex = 0; pre.setAttribute("role", "region"); pre.setAttribute("aria-label", `Texto extraído da página ${sheet.page}`);
      const width = Math.max(...sheet.text_lines.map(line => Array.from(line).length), 10); const ruler = Array.from({length:width},(_,i) => String((i+1)%10)).join(""); pre.textContent = `     | ${ruler}\n` + sheet.text_lines.map((line,i) => `${String(i+1).padStart(4)} | ${line}`).join("\n"); details.append(pre); root.append(details);
    });
  }
  function showPdfReview(data) {
    const previous = pdfReview?.hash === data.review_hash ? new Set([...pdfReview.checked].filter(ref => data.review_rows.some(r => r.location === ref))) : new Set();
    pdfReview = { hash: data.review_hash, rows: data.review_rows, checked: previous };
    const root = el("fv2-pdf-review"); root.replaceChildren(); root.append(node("h3", "Conferência da extração do PDF"), node("p", data.message));
    const region = node("div"); region.style.overflow = "auto"; region.style.maxHeight = "450px"; region.tabIndex = 0; region.setAttribute("role", "region"); region.setAttribute("aria-label", "Linhas do PDF a confirmar");
    data.review_rows.forEach(row => {
      const label = node("label"); label.style.display = "block"; label.style.margin = "14px 0"; const check = node("input"); check.type = "checkbox"; check.dataset.pdfRow = row.location; check.checked = previous.has(row.location); check.onchange = () => { if (check.checked) pdfReview.checked.add(row.location); else pdfReview.checked.delete(row.location); invalidate(); };
      label.append(check, document.createTextNode(` Conferi no PDF original · página ${row.page}, linha extraída ${row.pdf_line} · ${row.values.line.trim()} = ${row.values.amount.trim()} · ${row.values.period} · ${row.values.currency} · ${row.values.scale}`));
      const source = node("pre", row.extracted_text); source.style.whiteSpace = "pre-wrap"; source.style.overflowWrap = "anywhere"; label.append(source);
      label.append(node("p", Object.entries(row.values).map(([k,v]) => `${importFields[k]}: ${v}`).join(" · "))); region.append(label);
    });
    root.append(region, node("p", "Depois de confirmar todas as linhas, valide novamente para calcular as métricas e verificar as reconciliações."));
  }

  const importFields = { line: "Rubrica", amount: "Montante", period: "Período", statement_type: "Demonstração / detalhe", scope: "Perímetro", currency: "Moeda", scale: "Escala", comparison_basis: "Base de comparação" };
  function clearImport() {
    clearSourceReviews();
    sourceInspection = null; selectedTables = []; pdfLayouts = []; pdfLayoutDirty = false; clearPdfReview(); if (el("fv2-pdf-layout")) el("fv2-pdf-layout").replaceChildren();
    if (el("fv2-import-builder")) el("fv2-import-builder").replaceChildren();
  }
  function importChanged() { clearPdfReview(); el("fv2-import-confirm").checked = false; clearMappings(); invalidate(); }
  function importChoices() {
    if (!sourceInspection) { if (el("fv2-file").files[0]?.name.toLowerCase().endsWith(".pdf")) throw new Error("Prepare o PDF e confirme a extração antes de validar."); return {}; }
    if (sourceInspection.pdf && (!pdfSupported || pdfLayoutDirty)) throw new Error("Prepare novamente o PDF para aplicar as páginas e colunas escolhidas.");
    if (!sourceInspection.pdf && !tabularSupported) return {};
    if (!el("fv2-import-confirm").checked) throw new Error("Confirme os intervalos, as colunas e o conteúdo excluído antes de validar.");
    if (!selectedTables.length) throw new Error("Adicione uma tabela do ficheiro inspecionado.");
    const tables = selectedTables.map(table => {
      const fields = {};
      Object.keys(importFields).forEach(role => {
        const binding = table.fields[role];
        if (!binding || (binding.column !== undefined && !binding.column)) throw new Error(`Associe o campo ${importFields[role]} em cada tabela.`);
        fields[role] = { ...binding };
      });
      return { ...table, fields };
    });
    if (sourceInspection.pdf) return { pdf_import: { version: "1", inspection_hash: sourceInspection.inspection_hash, pages: pdfLayouts, tables, excluded_content_confirmed: true, review_hash: pdfReview?.hash || null, confirmed_rows: pdfReview ? [...pdfReview.checked] : [] } };
    return { tabular_import: { version: "1", inspection_hash: sourceInspection.inspection_hash, delimiter: sourceInspection.delimiter, tables, excluded_content_confirmed: true } };
  }
  function blankTable(sheet) {
    return { sheet: sheet.sheet, header_row: 1, first_row: 2, last_row: Math.max(2, sheet.row_count), fields: Object.fromEntries(Object.keys(importFields).map(role => [role, { column: "" }])) };
  }
  function renderImport() {
    const root = el("fv2-import-builder"); root.style.overflowWrap = "anywhere"; root.replaceChildren();
    if (!sourceInspection) return;
    root.append(node("p", "Selecione apenas os dados que pretende importar. Linhas, colunas e folhas fora das seleções não entram na análise. Para anos em colunas diferentes, duplique a tabela, escolha a coluna do montante e confirme o período correspondente."));
    const inventory = node("ul"); sourceInspection.sheets.forEach(sheet => inventory.append(node("li", `${sheet.sheet} · ${sheet.name} · ${sheet.row_count} linhas / ${sheet.column_count} colunas${sheet.hidden ? " · folha oculta" : ""} · ${sheet.formula_count} fórmulas`))); root.append(inventory);
    selectedTables.forEach((table, index) => {
      const group = node("fieldset"); group.style.margin = "16px 0"; group.style.minWidth = "0"; group.append(node("legend", `Tabela ${index + 1}`));
      const field = (label, control) => { const wrap = node("label", label); wrap.className = "field"; wrap.style.display = "block"; wrap.style.margin = "10px 0"; control.style.maxWidth = "100%"; control.style.display = "block"; wrap.append(control); group.append(wrap); };
      const sheet = sourceInspection.sheets.find(s => s.sheet === table.sheet);
      const sheets = node("select"); sheets.dataset.importSheet = String(index);
      sourceInspection.sheets.forEach(s => { const option = node("option", `${s.sheet} · ${s.name}${s.hidden ? " (oculta)" : ""}`); option.value = String(s.sheet); sheets.append(option); }); sheets.value = String(table.sheet);
      sheets.onchange = () => { Object.assign(table, blankTable(sourceInspection.sheets.find(s => s.sheet === Number(sheets.value)))); importChanged(); renderImport(); };
      field(sourceInspection.pdf ? "Página extraída" : "Folha de origem", sheets);
      [["header_row", "Linha do cabeçalho"], ["first_row", "Primeira linha de dados"], ["last_row", "Última linha de dados"]].forEach(([key, label]) => { const input = node("input"); input.type = "number"; input.min = sourceInspection.pdf && key === "header_row" ? "0" : "1"; input.max = String(sheet.row_count); input.value = String(table[key]); input.dataset.importRow = `${index}-${key}`; input.onchange = () => { table[key] = Number(input.value); importChanged(); if (key === "header_row") renderImport(); }; field(sourceInspection.pdf && key === "header_row" ? "Linha do cabeçalho (0 = sem cabeçalho)" : label, input); });
      Object.entries(importFields).forEach(([role, label]) => {
        const select = node("select"); select.dataset.importField = `${index}-${role}`;
        const empty = node("option", "Selecionar coluna"); empty.value = ""; select.append(empty);
        const header = sheet.sample_rows.find(row => row.row === table.header_row);
        for (let c = 1; c <= sheet.column_count; c++) { const column = c <= 26 ? String.fromCharCode(64 + c) : `A${String.fromCharCode(64 + c - 26)}`; const text = header?.cells.find(cell => cell.column === column)?.value || ""; const option = node("option", `${column}${text ? ` · ${text}` : ""}`); option.value = column; select.append(option); }
        if (!["line", "amount"].includes(role)) { const option = node("option", "Confirmar um valor comum nesta tabela"); option.value = "constant"; select.append(option); }
        select.value = table.fields[role].constant !== undefined ? "constant" : table.fields[role].column;
        select.onchange = () => { table.fields[role] = select.value === "constant" ? { constant: "" } : { column: select.value }; importChanged(); renderImport(); }; field(label, select);
        if (table.fields[role].constant !== undefined) {
          const choices = { statement_type: { income_statement: "Demonstração de resultados", balance_sheet: "Balanço", cash_flow_statement: "Fluxos de caixa", ...(["8", "9", "10", "11", "12", "13"].includes(planningVersion) ? { ...(["12", "13"].includes(planningVersion) ? { roic_schedule: "NOPAT e capital investido médio" } : {}), debt_schedule: "Movimentos da dívida", lease_schedule: "Movimentos de leasing", ...(["11", "12", "13"].includes(planningVersion) ? { capex_schedule: "Investimento e compromissos de ativos fixos" } : {}), working_capital_detail: "Detalhe de fundo de maneio" } : {}) }, scope: { consolidated: "Consolidado" }, scale: { raw: "Unidades da moeda", thousands: "Milhares", millions: "Milhões" }, comparison_basis: { current: "Atual", comparative: "Comparativo" } }[role];
          const value = node(choices ? "select" : "input"); value.dataset.importConstant = `${index}-${role}`;
          if (choices) { const empty = node("option", "Confirmar valor"); empty.value = ""; value.append(empty); Object.entries(choices).forEach(([key, label]) => { const option = node("option", label); option.value = key; value.append(option); }); } else { value.type = "text"; value.maxLength = role === "currency" ? 3 : 100; }
          value.value = table.fields[role].constant; value.onchange = () => { table.fields[role].constant = value.value; importChanged(); }; field(`Valor confirmado · ${label}`, value);
        }
      });
      const sample = node("details"); sample.append(node("summary", "Amostra da folha · primeiras 20 linhas")); const scroll = node("div"); scroll.style.overflow = "auto"; scroll.style.maxHeight = "250px"; scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", `Amostra da tabela ${index + 1}`); const grid = node("table"); grid.className = "rank-table";
      sheet.sample_rows.forEach(row => { const tr = node("tr"); tr.append(node("th", String(row.row))); row.cells.forEach(cell => tr.append(node("td", `${cell.column} · ${cell.value}${cell.type === "formula" ? " (fórmula)" : ""}`))); grid.append(tr); }); scroll.append(grid); sample.append(scroll); group.append(sample);
      const duplicate = node("button", "Duplicar seleção"); duplicate.type = "button"; duplicate.className = "btn btn-ghost"; duplicate.dataset.importDuplicate = String(index); duplicate.disabled = selectedTables.length >= 12; duplicate.onclick = () => { if (selectedTables.length >= 12) return; selectedTables.push(structuredClone(table)); importChanged(); renderImport(); };
      const remove = node("button", "Remover tabela"); remove.type = "button"; remove.className = "btn btn-ghost"; remove.dataset.importRemove = String(index); remove.onclick = () => { selectedTables.splice(index, 1); importChanged(); renderImport(); }; group.append(duplicate, remove); root.append(group);
    });
    const add = node("button", "Adicionar tabela / período"); add.type = "button"; add.className = "btn btn-ghost"; add.id = "fv2-import-add"; add.disabled = selectedTables.length >= 12; add.onclick = () => { if (selectedTables.length >= 12) return; selectedTables.push(blankTable(sourceInspection.sheets[0])); importChanged(); renderImport(); }; root.append(add);
    const label = node("label"); label.style.display = "block"; label.style.margin = "16px 0"; const confirm = node("input"); confirm.type = "checkbox"; confirm.id = "fv2-import-confirm"; confirm.onchange = () => invalidate(); label.append(confirm, document.createTextNode(" Confirmo os intervalos e as associações; revi as folhas, linhas e colunas excluídas. Os valores comuns foram confirmados na fonte.")); root.append(label);
  }
  async function inspectSource() {
    const token = currentToken, epoch = version;
    try {
      busy(true); status("A inspecionar folhas e células…");
      const file = el("fv2-file").files[0]; if (!file) throw new Error("Selecione um ficheiro CSV ou XLSX.");
      const body = new FormData(); body.append("file", file); if (file.name.toLowerCase().endsWith(".pdf")) { if (!pdfSupported) throw new Error("Este servidor ainda não permite preparar PDFs."); body.append("pdf_layout", JSON.stringify(pdfInspectionOptions())); } if (file.name.toLowerCase().endsWith(".csv")) body.append("delimiter", el("fv2-import-delimiter").value);
      const response = await request("/inspect", { method: "POST", body }); const data = await response.json();
      if (token !== currentToken || epoch !== version) return;
      clearMappings(); clearPdfReview(); invalidate(); sourceInspection = data; pdfLayouts = data.pdf ? structuredClone(data.pdf.pages) : []; pdfLayoutDirty = false; selectedTables = [blankTable(data.sheets[0])]; renderPdfLayout(); renderImport(); status("Confirme as tabelas e as colunas; depois valide os dados financeiros.");
    } catch (error) { if (token === currentToken && epoch === version) status(error.message); }
    finally { if (token === currentToken) busy(false); }
  }

  function clearSourceMetadata() { clearSourceReviews(); el("fv2-wc-tolerance").value = "0"; el("fv2-capex-tolerance").value = "0"; el("fv2-debt-tolerance").value = "0"; el("fv2-growth-tolerance").value = "0"; el("fv2-capital-tolerance").value = "0"; el("fv2-profitability-tolerance").value = "0"; el("fv2-cash-tolerance").value = "0"; el("fv2-source-currency").value = ""; el("fv2-source-scale").value = ""; }
  function configureSourceMetadata(data) {
    sourceMetadataSupported = data.source_metadata_supported === true;
    el("fv2-source-metadata").hidden = !sourceMetadataSupported;
    const currency = el("fv2-source-currency"), previous = currency.value;
    const empty = node("option", "Não confirmado · usar apenas a moeda das linhas"); empty.value = ""; currency.replaceChildren(empty);
    if (sourceMetadataSupported) (Array.isArray(data.source_currencies) ? data.source_currencies : []).filter(code => typeof code === "string" && /^[A-Z]{3}$/.test(code)).forEach(code => { const option = node("option", code); option.value = code; currency.append(option); });
    currency.value = [...currency.options].some(option => option.value === previous) ? previous : "";
    if (!sourceMetadataSupported) clearSourceMetadata();
  }
  function clearMappings() {
    clearSourceReviews();
    el("fv2-preview").replaceChildren();
    el("fv2-sample-source").replaceChildren();
    const option = node("option", "Detetar a partir dos dados"); option.value = "";
    el("fv2-period").replaceChildren(option);
  }
  function invalidate(event) { if (event?.target?.dataset?.location || ["fv2-file", "fv2-language", "fv2-locale", "fv2-parentheses", "fv2-fiscal-month", "fv2-source-currency", "fv2-source-scale"].includes(event?.target?.id)) clearSourceReviews(); clearCloseResult(); if (el("fv2-close")) el("fv2-close").hidden = true; version++; preview = null; attempt = null; ["fv2-metrics", "fv2-validation", "fv2-gaps"].forEach(id => { if (el(id)) { el(id).replaceChildren(); el(id).hidden = true; } }); if (event?.target?.id && ["fv2-language", "fv2-locale", "fv2-parentheses", "fv2-fiscal-month", "fv2-source-currency", "fv2-source-scale"].includes(event.target.id)) clearPdfReview(); if (event?.target?.id === "fv2-file") { clearImport(); el("fv2-pdf-pages").value = "1"; updatePdfControls(); clearMappings(); el("fv2-parentheses").value = ""; el("fv2-fiscal-month").value = ""; clearSourceMetadata(); } if (el("fv2-generate")) el("fv2-generate").disabled = true; }
  function status(message) { el("fv2-status").textContent = message; }
  async function loadSample() {
    invalidate(); const token = currentToken, epoch = version;
    try {
      busy(true); status("A carregar o exemplo público…");
      const response = await request("/sample"), data = await response.json();
      if (token !== currentToken || epoch !== version) return;
      clearImport(); clearMappings();
      const files = new DataTransfer(); files.items.add(new File([data.content], data.filename, { type: "text/csv" })); el("fv2-file").files = files.files; updatePdfControls(); el("fv2-pdf-pages").value = "1";
      el("fv2-language").value = data.choices.source_language; el("fv2-locale").value = data.choices.format_locale; el("fv2-tolerance").value = "0";
      el("fv2-parentheses").value = ""; el("fv2-fiscal-month").value = ""; clearSourceMetadata();
      const proposals = node("div"); proposals.append(node("p", "Mapeamentos propostos para este exemplo. Reveja-os e valide o ficheiro."));
      data.source_facts.forEach(fact => {
        const label = node("label", `${fact.published_label} (${fact.period}) `), select = node("select"); select.dataset.location = fact.location;
        const original = node("option", "Manter rubrica original"); original.value = "";
        const mapped = node("option", fact.mapped_label); mapped.value = data.choices.mappings[fact.location]; select.append(original, mapped); select.value = mapped.value; select.onchange = invalidate;
        label.append(select); proposals.append(label, node("br"));
      });
      el("fv2-preview").append(proposals);
      const credits = node("p", `${data.entity}. Dados públicos selecionados e adaptados ao modelo CSV; não são demonstrações completas. Fontes: `);
      data.sources.forEach(source => { const url = new URL(source.url); if (url.protocol !== "https:") return; const link = node("a", source.title); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; credits.append(link, document.createTextNode(" · ")); });
      el("fv2-sample-source").append(credits);
      el("fv2-sample-source").append(node("p", data.basis_note));
      status("Exemplo carregado. Valide e confirme os dados antes de gerar. Apenas a geração aprovada consome uma análise.");
    } catch (error) { if (token === currentToken) status(error.message); }
    finally { if (token === currentToken) busy(false); }
  }
  function sync() {
    build();
    const show = currentView === "analysis" && currentReportType === "financial" && (enabled || hasReports || hasMonthlySnapshots || reportOnly);
    if (currentReportType === "financial" && (enabled || hasReports || hasMonthlySnapshots || reportOnly)) {
      el("heroBadge").textContent = "Financeiro validado";
      el("heroTitle").textContent = "Análise financeira com dados verificados";
      el("heroDesc").textContent = enabled ? "Valide o ficheiro, confirme as rubricas e consulte um relatório auditado com as métricas suportadas pelos seus dados." : hasMonthlySnapshots && !hasReports ? "Consulte as preparações mensais guardadas e a respetiva evidência verificada." : "Consulte os relatórios financeiros aprovados e os respetivos dados verificados.";
    }
    el("fv2").hidden = !show;
    if (currentView === "analysis") el("inputCard").style.display = show ? "none" : "block";
    el("fv2-controls").hidden = reportOnly || !enabled;
    el("fv2-new").hidden = !enabled;
  }
  async function request(path, options = {}) {
    const token = currentToken;
    const response = await fetch(`${BACKEND_URL}/financial-v2${path}`, { ...options, headers: { ...options.headers, "X-Token": token } });
    if (token !== currentToken) throw new Error("A sessão mudou.");
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const failure = new Error(typeof error.detail === "string" ? error.detail : "Não foi possível concluir o pedido.");
      failure.status = response.status; throw failure;
    }
    return response;
  }
  async function login() {
    build();
    if (!currentToken || currentToken === checkedToken) return;
    clearTimeout(capabilityTimer);
    const token = currentToken; checkedToken = token;
    try { const response = await request("/capabilities"); const data = await response.json(); if (token !== currentToken) return; enabled = data.enabled; monthEndSupported = data.month_end_preview_versions?.includes("1") === true; configureCloseCatalog(data.month_end_reconciliation_catalog, monthEndSupported); if (!monthEndSupported) { clearCloseResult(); el("fv2-close").hidden = true; } hasReports = data.reports_available; hasMonthlySnapshots = data.month_end_snapshots_available === true; closeSaveSupported = data.month_end_save_versions?.includes("1") === true; closeHistorySupported = data.month_end_snapshot_versions?.includes("1") === true; el("fv2-close-save").hidden = !closeSaveSupported; el("fv2-close-history").hidden = !closeHistorySupported; tabularSupported = data.tabular_import_versions?.includes("1") === true; pdfSupported = data.pdf_import_versions?.includes("1") === true && data.normalization_versions?.includes("2") === true; el("fv2-file").accept = pdfSupported ? ".csv,.xlsx,.pdf" : ".csv,.xlsx"; updatePdfControls(); document.querySelector('label[for="fv2-file"]').textContent = pdfSupported ? "Demonstrações em CSV, XLSX ou PDF textual · até 500 linhas financeiras / 5 MB" : tabularSupported ? "Demonstrações em CSV ou XLSX · até 500 linhas financeiras / 5 MB" : "Demonstrações em CSV ou XLSX · uma folha · até 500 linhas / 5 MB"; el("fv2-import-controls").hidden = !(tabularSupported || pdfSupported); if ((!tabularSupported && !pdfSupported) || (sourceInspection?.pdf && !pdfSupported)) clearImport(); normalizationVersion = data.normalization_versions?.includes("2") ? "2" : null; planningVersion = data.planning_versions?.includes("13") && data.normalization_versions?.includes("2") ? "13" : data.planning_versions?.includes("12") && data.normalization_versions?.includes("2") ? "12" : data.planning_versions?.includes("11") && data.normalization_versions?.includes("2") ? "11" : data.planning_versions?.includes("10") && data.normalization_versions?.includes("2") ? "10" : data.planning_versions?.includes("9") && data.normalization_versions?.includes("2") ? "9" : data.planning_versions?.includes("8") ? "8" : data.planning_versions?.includes("7") ? "7" : data.planning_versions?.includes("6") ? "6" : data.planning_versions?.includes("5") ? "5" : data.planning_versions?.includes("4") ? "4" : data.planning_versions?.includes("3") ? "3" : data.planning_versions?.includes("2") ? "2" : null; el("fv2-wc-tolerance-field").hidden = planningVersion !== "13"; if (planningVersion !== "13") { clearWorkingCapitalReview(); el("fv2-wc-tolerance").value = "0"; } if (!["12", "13"].includes(planningVersion)) clearRoicReview(); parenthesesSupported = data.accounting_parentheses_supported === true; el("fv2-parentheses-field").hidden = !parenthesesSupported; if (!parenthesesSupported) el("fv2-parentheses").value = ""; fiscalCalendarSupported = data.fiscal_calendar_supported === true; el("fv2-fiscal-field").hidden = !fiscalCalendarSupported; if (!fiscalCalendarSupported) el("fv2-fiscal-month").value = ""; configureSourceMetadata(data); el("fv2-capex-tolerance-field").hidden = !["11", "12", "13"].includes(planningVersion); if (!["11", "12", "13"].includes(planningVersion)) { clearCapexReview(); el("fv2-capex-tolerance").value = "0"; } el("fv2-debt-tolerance-field").hidden = !["10", "11", "12", "13"].includes(planningVersion); if (!["10", "11", "12", "13"].includes(planningVersion)) { clearDebtReview(); el("fv2-debt-tolerance").value = "0"; } el("fv2-growth-tolerance-field").hidden = !["9", "10", "11", "12", "13"].includes(planningVersion); if (!["9", "10", "11", "12", "13"].includes(planningVersion)) { clearSourceReviews(); el("fv2-growth-tolerance").value = "0"; } el("fv2-capital-tolerance-field").hidden = !["8", "9", "10", "11", "12", "13"].includes(planningVersion); if (!["8", "9", "10", "11", "12", "13"].includes(planningVersion)) el("fv2-capital-tolerance").value = "0"; el("fv2-profitability-tolerance-field").hidden = !["7", "8", "9", "10", "11", "12", "13"].includes(planningVersion); if (!["7", "8", "9", "10", "11", "12", "13"].includes(planningVersion)) el("fv2-profitability-tolerance").value = "0"; el("fv2-cash-tolerance-field").hidden = !["6", "7", "8", "9", "10", "11", "12", "13"].includes(planningVersion); if (!["6", "7", "8", "9", "10", "11", "12", "13"].includes(planningVersion)) el("fv2-cash-tolerance").value = "0"; sync();
      closeCsvSupported = data.month_end_checklist_csv_versions?.includes("1") === true;
      const job = localStorage.getItem("sl_financial_job"); if (enabled && job) poll(job, token);
    } catch (error) {
      if (token !== currentToken) return;
      checkedToken = null; enabled = false; sync();
      if (![401, 403].includes(error.status)) capabilityTimer = setTimeout(() => { if (token === currentToken) login(); }, 10000);
    }
  }
  function options() {
    const mappings = {};
    el("fv2-preview").querySelectorAll("select[data-location]").forEach(select => { if (select.value) mappings[select.dataset.location] = select.value; });
    return { ...importChoices(), ...(planningVersion === "13" ? { working_capital_rounding_tolerance: el("fv2-wc-tolerance").value, ...(Object.keys(workingCapitalDefinitions).length ? { working_capital_definitions: workingCapitalDefinitions } : {}) } : {}), ...(["12", "13"].includes(planningVersion) && Object.keys(roicSources).length === 6 && Object.values(roicSources).every(Boolean) ? { roic_sources: roicSources } : {}), ...(["11", "12", "13"].includes(planningVersion) ? { capex_rounding_tolerance: el("fv2-capex-tolerance").value, ...(Object.keys(capexDefinitions).length ? { capex_definitions: capexDefinitions } : {}) } : {}), ...(["10", "11", "12", "13"].includes(planningVersion) ? { debt_rounding_tolerance: el("fv2-debt-tolerance").value, ...(Object.keys(debtDefinitions).length ? { debt_definitions: debtDefinitions } : {}) } : {}), ...(["9", "10", "11", "12", "13"].includes(planningVersion) ? { growth_rounding_tolerance: el("fv2-growth-tolerance").value, ...(Object.keys(growthDefinitions).length ? { growth_definitions: growthDefinitions } : {}) } : {}), ...(["8", "9", "10", "11", "12", "13"].includes(planningVersion) ? { capital_rounding_tolerance: el("fv2-capital-tolerance").value } : {}), ...(["7", "8", "9", "10", "11", "12", "13"].includes(planningVersion) ? { profitability_rounding_tolerance: el("fv2-profitability-tolerance").value } : {}), ...(["6", "7", "8", "9", "10", "11", "12", "13"].includes(planningVersion) ? { cash_rounding_tolerance: el("fv2-cash-tolerance").value } : {}), ...(normalizationVersion ? { normalization_version: normalizationVersion } : {}), ...(sourceMetadataSupported && el("fv2-source-currency").value ? { source_currency: el("fv2-source-currency").value } : {}), ...(sourceMetadataSupported && el("fv2-source-scale").value ? { source_scale: el("fv2-source-scale").value } : {}), ...(fiscalCalendarSupported && el("fv2-fiscal-month").value !== "" ? { fiscal_year_end_month: Number(el("fv2-fiscal-month").value) } : {}), ...(parenthesesSupported && el("fv2-parentheses").value !== "" ? { accounting_parentheses: el("fv2-parentheses").value === "true" } : {}), ...(planningVersion ? { planning_version: planningVersion } : {}), source_language: el("fv2-language").value, format_locale: el("fv2-locale").value,
      report_language: el("fv2-report-language").value, rounding_tolerance: el("fv2-tolerance").value,
      target_period: el("fv2-period").value, mappings };
  }
  function form(values) {
    const file = el("fv2-file").files[0]; if (!file) throw new Error("Selecione um ficheiro CSV ou XLSX.");
    const data = new FormData(); data.append("file", file); data.append("options", JSON.stringify(values)); return data;
  }
  function busy(value) {
    el("fv2-controls").querySelectorAll("input,select,button").forEach(control => { control.disabled = value; });
    el("fv2-generate").disabled = value || !preview?.ready;
    if (el("fv2-close-download")) el("fv2-close-download").disabled = value || !closeResult;
    if (el("fv2-close-save")) el("fv2-close-save").disabled = value || !closeResult || !closeSaveSupported || closeResult.persisted;
  }
  async function validate() {
    const token = currentToken, epoch = version;
    try { busy(true); status("A validar os dados e as reconciliações…");
      const values = options(); const response = await request("/preview", { method: "POST", body: form(values) }); const data = await response.json();
      if (token !== currentToken || epoch !== version) return;
      if (data.schema === "financial-working-capital-review-v1") { preview = null; attempt = null; invalidate(); showWorkingCapitalReview(data); status(data.message); return; }
      if (data.schema === "financial-roic-review-v1") { preview = null; attempt = null; invalidate(); showRoicReview(data); status(data.message); return; }
      if (data.schema === "financial-capex-review-v1") { preview = null; attempt = null; invalidate(); showCapexReview(data); status(data.message); return; }
      if (data.schema === "financial-debt-review-v1") { preview = null; attempt = null; invalidate(); showDebtReview(data); status(data.message); return; }
      if (data.schema === "financial-growth-review-v1") { preview = null; attempt = null; invalidate(); showGrowthReview(data); status(data.message); return; }
      if (data.schema === "financial-pdf-review-v1") { preview = null; attempt = null; showPdfReview(data); invalidate(); status(data.message); return; }
      preview = data; attempt = null;
      const periods = el("fv2-period"); periods.replaceChildren(node("option", "Selecione o período atual")); periods.firstChild.value = "";
      data.periods.forEach(period => { const option = node("option", period); option.value = period; periods.append(option); }); periods.value = data.target_period;
      const table = node("table"); table.className = "rank-table";
      const header = node("tr"); ["Linha original", "Rubrica confirmada", "Valor normalizado", "Período", "Validação"].forEach(text => header.append(node("th", text))); table.append(header);
      data.rows.forEach(row => { const tr = node("tr"); const source = node("td"); source.append(node("span", row.original_line));
        if (row.original_input) {
          source.append(node("p", `Valor original: ${row.original_input.amount} · ${row.original_input.currency} · escala: ${row.original_input.scale}`));
          source.append(node("p", `Período original: ${row.original_input.period} · ${row.location}`));
          if (row.original_input.working_capital_definition_quote) source.append(node("p", "Perímetro operacional citado da fonte: " + row.original_input.working_capital_definition_quote));
          if (row.original_input.capex_definition_quote) source.append(node("p", "Definição do capex citada da fonte: " + row.original_input.capex_definition_quote));
          if (row.original_input.debt_definition_quote) source.append(node("p", "Definição da dívida citada da fonte: " + row.original_input.debt_definition_quote));
          if (row.original_input.growth_definition_quote) source.append(node("p", "Definição citada da fonte: " + row.original_input.growth_definition_quote));
          if (row.original_input.source_cells) { const details = node("details"); details.append(node("summary", `Células de origem · ${row.original_input.sheet}`)); Object.entries(row.original_input.source_cells).forEach(([role, origin]) => details.append(node("p", `${importFields[role] || role}: ${origin.cell ? `${origin.cell}${origin.span ? ` · página ${origin.page}, linha extraída ${origin.line}${origin.span[0] < origin.span[1] ? ` · caracteres ${origin.span[0] + 1} a ${origin.span[1]}` : " · campo vazio"}` : ""}` : `valor comum confirmado · ${origin.constant}`}`))); source.append(details); }
        }
        tr.append(source); const td = node("td"), select = node("select"); select.dataset.location = row.location;
        const empty = node("option", "Manter rubrica original"); empty.value = ""; select.append(empty);
        data.mapping_options.filter(option => option.statement_type === row.statement_type).forEach(option => { const n = node("option", option.label); n.value = option.concept_id; select.append(n); });
        select.value = values.mappings[row.location] || ""; select.onchange = invalidate; td.append(select); tr.append(td);
        tr.append(node("td", row.value === null ? "—" : `${row.value} ${row.currency || row.unit || ""}`), node("td", row.period?.label || "—"));
        const validation = node("td", row.normalization_status === "normalized" ? "Identificada" : "Rever dados / rubrica");
        if (row.validation_guidance?.length) {
          const details = node("details"); details.dataset.rowGuidance = row.location;
          details.append(node("summary", "O que rever nesta linha"));
          row.validation_guidance.forEach(issue => { details.append(node("p", issue.message), node("p", `${issue.field} · ${issue.code}`)); });
          validation.append(details);
        }
        tr.append(validation); table.append(tr);
      });
      el("fv2-preview").replaceChildren(table);
      if (data.source_import?.sheet_coverage) {
        const scope = node("details"); scope.append(node("summary", "Âmbito importado e exclusões"));
        data.source_import.sheet_coverage.forEach(sheet => scope.append(node("p", `${sheet.sheet} · ${sheet.name} · intervalos importados: ${sheet.selected_intervals.map(r => r.join("–")).join(", ") || "nenhum"} · linhas excluídas: ${sheet.excluded_row_ranges.map(r => r.join("–")).join(", ") || "nenhuma"} · colunas excluídas: ${sheet.excluded_columns.join(", ") || "nenhuma"}`)));
        el("fv2-preview").prepend(scope);
      }
      const metrics = el("fv2-metrics"); metrics.replaceChildren();
      if (data.calculated_metrics?.length) {
        metrics.append(node("h3", "Métricas calculadas · pré-visualização"));
        metrics.append(node("p", "Cálculos preliminares dos dados validados. Não constituem um relatório auditado. A geração continua dependente da validação completa."));
        const values = node("table"); values.className = "rank-table";
        const head = node("tr"); ["Métrica", "Valor", "Período", "Estado"].forEach(label => head.append(node("th", label))); values.append(head);
        data.calculated_metrics.forEach(metric => {
          const row = node("tr");
          const period = metric.period ? `${metric.period.start_date} / ${metric.period.end_date}` : "—";
          row.append(node("td", metric.label), node("td", metric.display_value ?? "—"), node("td", period), node("td", metric.status === "calculated" ? "Calculada" : "Indisponível · reveja os dados"));
          values.append(row);
        });
        metrics.append(values);
        data.calculated_metrics.forEach(metric => {
          if (metric.status !== "calculated" || !metric.calculation_evidence) return;
          const evidence = metric.calculation_evidence, details = node("details");
          details.dataset.calculation = metric.key;
          details.style.overflowWrap = "anywhere";
          details.append(node("summary", `Conferir cálculo · ${metric.label}`));
          details.append(node("p", `Fórmula: ${evidence.canonical_expression}`));
          details.append(node("p", `Resultado antes do arredondamento de apresentação: ${evidence.display_exact_output}`));
          details.append(node("p", evidence.rounding_policy ? `Valor apresentado: ${metric.display_value}. O arredondamento de apresentação não altera os valores usados no cálculo.` : "Apresentação sem arredondamento adicional."));
          if (evidence.division_policy) details.append(node("p", evidence.division_policy === "decimal-significant-digits-v1:100" ? "Divisões calculadas com precisão decimal de 100 algarismos significativos." : "Precisão da divisão definida pelo motor de cálculo."));
          const operandScroll = node("div"); operandScroll.style.overflowX = "auto"; operandScroll.tabIndex = 0; operandScroll.setAttribute("role", "region"); operandScroll.setAttribute("aria-label", "Valores e fontes do cálculo");
          const operands = node("table"); operands.className = "rank-table"; operands.style.minWidth = "640px"; operands.style.overflowWrap = "normal";
          const header = node("tr"); ["Parâmetro da fórmula", "Valor usado", "Período", "Rubrica de origem"].forEach(label => header.append(node("th", label))); operands.append(header);
          evidence.inputs.forEach(input => {
            const row = node("tr"), sources = node("td"); sources.style.overflowWrap = "anywhere";
            input.sources.forEach(source => sources.append(node("p", `${source.line} · ${source.amount} ${source.currency} · escala: ${source.scale} · período: ${source.period} · ${source.location}`)));
            if (!input.sources.length) sources.append(node("span", input.source_references.join(" · ") || "Origem indisponível"));
            row.append(node("td", input.role), node("td", input.display_value), node("td", `${input.period[0]} / ${input.period[1]}`), sources); operands.append(row);
          });
          operandScroll.append(operands); details.append(operandScroll); metrics.append(details);
        });
      }
      metrics.hidden = !data.calculated_metrics?.length;
      renderValidation(data);
      renderGaps(data);
      renderMonthEndRows(data);
      status(data.ready ? "Dados validados para as métricas disponíveis. Confirme os valores e gere o relatório." : `Validação incompleta. Corrija o ficheiro ou confirme as rubricas e volte a validar. ${data.issues.map(i => i.code).join(" · ")}`);
    } catch (error) { if (token === currentToken) { invalidate(); status(error.message); } }
    finally { if (token === currentToken) busy(false); }
  }
  function renderGaps(data) {
    const panel = el("fv2-gaps"); panel.replaceChildren();
    const missing = data.metric_availability?.filter(metric => metric.status !== "calculated") || [];
    panel.hidden = !missing.length; if (panel.hidden) return;
    panel.append(node("h3", "Métricas indisponíveis e dados em falta"));
    panel.append(node("p", "A ausência de um cálculo não equivale a um valor zero. A geração continua dependente da validação completa."));
    const table = node("table"); table.className = "rank-table";
    const head = node("tr"); ["Métrica", "O que falta ou rever"].forEach(label => head.append(node("th", label))); table.append(head);
    const states = { missing: "Não identificado no período selecionado", needs_confirmation: "Requer confirmação dos dados", ambiguous: "Há várias rubricas candidatas", current_revenue_required: "Confirme primeiro a receita do período atual" };
    missing.forEach(metric => {
      const row = node("tr"), gaps = node("td");
      if (metric.status === "period_required") gaps.append(node("p", "Selecione o período atual."));
      else if (metric.status === "blocked") gaps.append(node("p", "Cálculo bloqueado. Reveja os dados e as validações acima."));
      else if (metric.requirements.length) {
        const list = node("ul"); metric.requirements.forEach(requirement => list.append(node("li", `${requirement.label} · ${states[requirement.status] || requirement.status}${requirement.source_references.length ? " · " + requirement.source_references.join(" · ") : ""}`))); gaps.append(list);
      } else gaps.append(node("p", "Métrica não planeada. Reveja os dados e o período selecionado."));
      row.append(node("td", metric.label), gaps); table.append(row);
    }); panel.append(table);
  }
  function reconciliationName(check) {
    return ({ "REC-WC-CLOSING-001": "Fundo de maneio operacional final versus componentes", "REC-WC-OPENING-001": "Fundo de maneio operacional inicial versus componentes", "REC-WC-CHANGE-001": "Variação publicada do fundo de maneio entre saldos", "REC-CAPEX-001": "Classificação do capex pago entre manutenção e crescimento", "REC-CAPEX-002": "Conferência do capex com as aquisições nos fluxos de caixa", "REC-DEBT-RATE-001": "Composição contratual da dívida a taxa fixa e variável", "REC-DEBT-MATURITY-001": "Distribuição do principal pelos vencimentos após a data de reporte", "REC-GROWTH-001": "Ponte monetária do crescimento reportado", "REC-BS-001": "Identidade do balanço", "REC-CF-001": "Reconciliação de caixa", "REC-DEBT-001": "Ponte da dívida sem leasing", "REC-LEASE-001": "Ponte dos passivos de leasing", "REC-PL-001": "Ponte dos resultados", "REC-EBITDA-001": "Ponte EBITDA estatutário e ajustado" })[check.rule_id] || check.rule_id;
  }
  function renderReconciliationEvidence(check, panel, states) {
    const evidence = check.reconciliation_evidence;
    if (!evidence || check.status === "not_evaluated") return;
    const details = node("details"); details.dataset.reconciliation = check.rule_id; details.style.overflowWrap = "anywhere";
    details.append(node("summary", `Conferir reconciliação · ${reconciliationName(check)}`));
    details.append(node("p", "Valores usados na verificação preliminar. As linhas de origem mantêm os valores e escalas do ficheiro; os valores usados estão em unidades base. A geração continua sujeita à validação e auditoria."));
    details.append(node("p", `Tolerância absoluta de arredondamento: ${evidence.policy.display_rounding_absolute}. Esta tolerância aplica-se apenas a esta reconciliação; não altera os valores de origem.`));
    evidence.equations.forEach(equation => {
      details.append(node("p", `Fórmula: ${equation.canonical_expression}`));
      details.append(node("p", `Esperado: ${equation.display_expected} · Observado: ${equation.display_observed} · Diferença absoluta: ${equation.display_absolute_delta} · ${states[equation.status] || equation.status}`));
      details.append(node("p", `Diferença com sinal (observado − esperado): ${equation.display_signed_delta}`));
    });
    const scroll = node("div"); scroll.style.overflowX = "auto"; scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", "Valores e fontes da reconciliação");
    const table = node("table"); table.className = "rank-table"; table.style.minWidth = "640px"; table.style.overflowWrap = "normal";
    const head = node("tr"); ["Componente", "Valor usado", "Período", "Rubrica de origem"].forEach(text => head.append(node("th", text))); table.append(head);
    evidence.inputs.forEach(input => {
      const row = node("tr"), component = node("td", input.label), sources = node("td"); sources.style.overflowWrap = "anywhere";
      component.append(node("p", input.role));
      if (input.calculation_trace_hash) component.append(node("p", "Valor proveniente de um cálculo rastreável."));
      input.sources.forEach(source => sources.append(node("p", `${source.line} · ${source.amount} ${source.currency} · escala: ${source.scale} · período: ${source.period} · ${source.location}`)));
      if (!input.sources.length) sources.append(node("p", input.source_references.join(" · ") || "Origem indisponível"));
      row.append(component, node("td", input.display_value), node("td", `${input.period[0]} / ${input.period[1]}`), sources); table.append(row);
    });
    scroll.append(table); details.append(scroll); panel.append(details);
  }
  function renderValidation(data) {
    const panel = el("fv2-validation"); panel.replaceChildren(); panel.hidden = !data.validation_summary;
    if (!data.validation_summary) return;
    const summary = data.validation_summary;
    const quality = { complete: "Completa no âmbito avaliado", partial: "Parcial", insufficient: "Insuficiente", mapping_required: "Requer confirmação de rubricas" };
    const states = { pass: "Verificada", rounding_pass: "Aceite dentro da tolerância", warning: "Requer revisão", critical: "Diferença crítica", blocking: "Bloqueada", not_evaluated: "Não verificada" };
    panel.append(node("h3", "Validação e limites dos dados"), node("p", `Cobertura: ${quality[summary.quality_level] || summary.quality_level}. Refere-se apenas às capacidades avaliadas; não certifica demonstrações completas.`));
    panel.append(node("p", data.ready ? "Validação pronta para gerar. O relatório ainda terá de passar pela auditoria." : "Há dados a rever antes de gerar o relatório."));
    panel.append(node("h4", "Reconciliações"));
    if (!summary.reconciliations.length) panel.append(node("p", "Nenhuma identidade contabilística foi verificada com estes dados."));
    else {
      const table = node("table"); table.className = "rank-table";
      const head = node("tr"); ["Verificação", "Valor esperado", "Valor observado", "Diferença absoluta", "Estado"].forEach(text => head.append(node("th", text))); table.append(head);
      summary.reconciliations.forEach(check => {
        const row = node("tr");
        row.append(node("td", reconciliationName(check)), node("td", check.display_expected ?? "—"), node("td", check.display_observed ?? "—"), node("td", check.display_delta ?? "—"), node("td", states[check.status] || check.status)); table.append(row);
      }); panel.append(table);
      summary.reconciliations.forEach(check => renderReconciliationEvidence(check, panel, states));
    }
    if (data.cash_reconciliation && !summary.reconciliations.some(check => check.rule_id === "REC-CF-001")) {
      panel.append(node("h4", "Reconciliação de caixa · não verificada"));
      panel.append(node("p", "São necessários os seis componentes explícitos, no mesmo perímetro e moeda. O saldo inicial usa o período imediatamente anterior com base current da análise; não se usa um comparativo automaticamente. Selecione o período dos fluxos."));
      const states = { missing: "Em falta", ambiguous: "Mais de uma linha candidata", needs_confirmation: "Requer confirmação" };
      const list = node("ul");
      data.cash_reconciliation.requirements.forEach(r => list.append(node("li", `${r.label} · ${states[r.status] || r.status}${r.source_references.length ? " · " + r.source_references.join(" · ") : ""}`)));
      panel.append(list);
    }
    (data.profitability_reconciliations || []).filter(check => !summary.reconciliations.some(actual => actual.rule_id === check.rule_id)).forEach(check => {
      panel.append(node("h4", `${reconciliationName(check)} · não verificada`));
      panel.append(node("p", "Os componentes em falta não são zero. As pontes de EBITDA comparam valores calculados com montantes reportados independentes; os valores genéricos sem definição não substituem essas fontes."));
      if (check.status === "period_required") panel.append(node("p", "Selecione o período atual."));
      const list = node("ul"), states = { missing: "Em falta", ambiguous: "Mais de uma linha candidata", needs_confirmation: "Requer confirmação" };
      check.requirements.forEach(r => list.append(node("li", `${r.label} · ${states[r.status] || r.status}${r.source_references.length ? " · " + r.source_references.join(" · ") : ""}`)));
      panel.append(list);
    });
    (data.capital_reconciliations || []).filter(check => !summary.reconciliations.some(actual => actual.rule_id === check.rule_id)).forEach(check => {
      panel.append(node("h4", `${reconciliationName(check)} · não verificada`));
      panel.append(node("p", "São necessários o saldo inicial adjacente, o saldo final e todos os movimentos explícitos do período. O saldo inicial deve estar na base current desta análise; não usamos comparativos automaticamente e não assumimos movimentos zero."));
      if (check.status === "period_required") panel.append(node("p", "Selecione o período atual."));
      const list = node("ul"), states = { missing: "Em falta", ambiguous: "Mais de uma linha candidata", needs_confirmation: "Requer confirmação" };
      check.requirements.forEach(r => list.append(node("li", `${r.label} · ${states[r.status] || r.status}${r.source_references.length ? " · " + r.source_references.join(" · ") : ""}`)));
      panel.append(list);
    });
    (data.working_capital_reconciliations || []).filter(check => !summary.reconciliations.some(actual => actual.rule_id === check.rule_id)).forEach(check => { panel.append(node("h4", reconciliationName(check)), node("p", "Dados em falta para conferir o fundo de maneio. Saldos iniciais devem preceder o período; variação de saldo não equivale a efeito em caixa.")); (check.requirements || []).forEach(r => panel.append(node("p", r.label))); });
    (data.capex_reconciliations || []).filter(check => !summary.reconciliations.some(actual => actual.rule_id === check.rule_id)).forEach(check => { panel.append(node("h4", reconciliationName(check)), node("p", "Dados em falta para conferir o capex pago. Não deduzimos parcelas nem usamos compromissos como pagamentos.")); (check.requirements || []).forEach(r => panel.append(node("p", r.label))); });
    (data.debt_reconciliations || []).filter(check => !summary.reconciliations.some(actual => actual.rule_id === check.rule_id)).forEach(check => { const box = node("article"); box.className = "card"; box.append(node("h4", reconciliationName(check)), node("p", "Dados em falta para reconciliar o principal da dívida.")); (check.requirements || []).forEach(r => box.append(node("p", r.label))); panel.append(box); });
    (data.growth_reconciliations || []).filter(check => !summary.reconciliations.some(actual => actual.rule_id === check.rule_id)).forEach(check => {
      panel.append(node("h4", "Ponte monetária do crescimento reportado · não verificada"), node("p", "São necessários todos os contributos monetários assinados na mesma moeda, período e definição. Componentes em falta não são zero; taxas LFL e câmbio constante não substituem contributos da ponte."));
      const list = node("ul"); check.requirements.forEach(r => list.append(node("li", `${r.label} · ${r.status}${r.source_references.length ? " · " + r.source_references.join(" · ") : ""}`))); panel.append(list);
    });
    const capabilities = { summarize: "Resumir demonstrações", calculate: "Calcular métricas", compare: "Comparar períodos", diagnose: "Diagnosticar variações", infer_cause: "Inferir causas", forecast: "Elaborar previsões", quantify_impact: "Quantificar impacto", recommend_reversible: "Propor ações reversíveis", recommend_material: "Propor compromissos materiais" };
    const decisions = { allowed: "Requisitos satisfeitos", conditional: "Condicional", blocked: "Requisitos não satisfeitos" };
    const details = node("details"); details.append(node("summary", "Requisitos de dados por capacidade"));
    details.append(node("p", "São decisões sobre a suficiência dos dados. A geração e a publicação continuam sujeitas à validação e auditoria."));
    const table = node("table"); table.className = "rank-table";
    const head = node("tr"); ["Capacidade", "Estado", "Requisitos em falta"].forEach(text => head.append(node("th", text))); table.append(head);
    summary.capabilities.forEach(decision => {
      const row = node("tr"); row.append(node("td", capabilities[decision.capability] || decision.capability), node("td", decisions[decision.status] || decision.status), node("td", decision.requirements.map(r => r.label).join(" · ") || "—")); table.append(row);
    }); details.append(table); panel.append(details);
  }
  async function generate() {
    const token = currentToken;
    try { if (!preview?.ready) throw new Error("Valide o ficheiro antes de gerar."); busy(true);
      attempt ||= crypto.randomUUID(); status("A enviar para análise e auditoria…");
      const response = await request("/generate", { method: "POST", body: form({ ...options(), preview_hash: preview.preview_hash, attempt_key: attempt }) });
      const job = await response.json(); if (token !== currentToken) return;
      localStorage.setItem("sl_financial_job", job.job_id); poll(job.job_id, token);
    } catch (error) { if (token === currentToken) { status(error.message); busy(false); } }
  }
  async function poll(job, token) {
    clearTimeout(pollTimer);
    if (token !== currentToken) return;
    busy(true);
    try { const response = await request(`/jobs/${encodeURIComponent(job)}`); const result = await response.json(); if (token !== currentToken) return;
      if (result.status === "pending") { status("Em fila ou em processamento. Pode sair e regressar a esta sessão; o trabalho fica guardado."); pollTimer = setTimeout(() => poll(job, token), 2500); return; }
      localStorage.removeItem("sl_financial_job");
      if (result.analyses_remaining !== undefined) { analysesLeft = result.analyses_remaining; updatePlanUI(); }
      if (result.status === "completed") { hasReports = true; busy(false); status("Relatório aprovado e guardado. Foi consumida uma análise."); await open(result.run_id); }
      else { attempt = null; status(result.message || "A análise não foi aprovada. Não foi consumida quota. Reveja os dados antes de uma nova tentativa."); busy(false); }
    } catch (error) { if (token === currentToken) {
      if ([401, 403, 404].includes(error.status)) { localStorage.removeItem("sl_financial_job"); status(error.message); busy(false); return; }
      status(`${error.message} Voltaremos a consultar o trabalho.`); pollTimer = setTimeout(() => poll(job, token), 10000);
    } }
  }
  async function open(runId) {
    const token = currentToken, epoch = ++version;
    if (currentReportType !== "financial") setModule("financial", el("mod-financial"));
    if (currentView !== "analysis") switchView("analysis");
    build(); reportOnly = true; el("fv2-result").hidden = true; sync(); el("report").classList.remove("on"); el("chatBtn").classList.remove("show");
    try { const response = await request(`/reports/${encodeURIComponent(runId)}/html`); const blob = await response.blob();
      if (token !== currentToken || epoch !== version) return;
      if (reportURL) URL.revokeObjectURL(reportURL); reportURL = URL.createObjectURL(blob); el("fv2-frame").src = reportURL; el("fv2-result").hidden = false; status("Relatório aprovado. Pode consultar os dados e descarregar o PDF.");
      el("fv2-pdf").onclick = async () => { try { const pdf = await request(`/reports/${encodeURIComponent(runId)}/pdf`); download(await pdf.blob(), `saleslab-financial-${runId}.pdf`); } catch (error) { status(error.message); } };
    } catch (error) { if (token === currentToken && epoch === version) { reportOnly = false; sync(); busy(false); status(error.message); } }
  }
  async function loadHistory(before = null) {
    const token = currentToken;
    try {
      const response = await request("/history" + (before ? `?before=${encodeURIComponent(before)}` : ""));
      const data = await response.json(); if (token !== currentToken) return;
      const list = el("fv2-history-list"); if (!before) list.replaceChildren();
      list.querySelector("[data-next-page]")?.remove();
      if (!before && !data.reports.length) list.append(node("p", "Ainda não há relatórios financeiros aprovados."));
      data.reports.forEach(report => {
        const button = node("button", `${report.period} · ${new Date(report.created_at).toLocaleDateString("pt-PT")}`);
        button.type = "button"; button.className = "btn btn-ghost"; button.onclick = () => open(report.run_id); list.append(button);
      });
      if (data.next_cursor) { const next = node("button", "Carregar anteriores"); next.type = "button"; next.className = "btn btn-ghost"; next.dataset.nextPage = "true"; next.onclick = () => loadHistory(data.next_cursor); list.append(next); }
    } catch (error) { if (token === currentToken) status(error.message); }
  }
  let workingCapitalDefinitions = {};
  function clearWorkingCapitalReview() { workingCapitalDefinitions = {}; if (el("fv2-wc-review")) { el("fv2-wc-review").replaceChildren(); el("fv2-wc-review").hidden = true; } }
  function showWorkingCapitalReview(data) {
    const root = el("fv2-wc-review"); root.hidden = false; root.replaceChildren(node("h3", "Confirmar o perímetro operacional do fundo de maneio"), node("p", data.message));
    data.rows.forEach(row => {
      const box = node("fieldset"); box.style.minWidth = "0"; box.style.overflowWrap = "anywhere"; box.style.border = "1px solid rgba(201,168,76,.22)"; box.style.borderRadius = "8px"; box.style.padding = "14px"; box.style.margin = "14px 0"; box.append(node("legend", row.location), node("p", `Rubrica original: ${row.line}`), node("p", `Montante original: ${row.amount} · ${row.currency} · escala ${row.scale} · ${row.period} · ${row.comparison_basis}`));
      const label = node("label", "Copie a definição comum do perímetro e da variação de saldos da rubrica original · mínimo 10 caracteres"); label.className = "field"; const input = node("textarea"); input.rows = 3; input.maxLength = 2000; input.dataset.wcLocation = row.location; input.value = workingCapitalDefinitions[row.location] || row.working_capital_definition_quote || ""; input.oninput = () => { if (input.value) workingCapitalDefinitions[row.location] = input.value; else delete workingCapitalDefinitions[row.location]; invalidate(); }; label.append(input); box.append(label); root.append(box);
    });
  }
  let roicSources = {};
  function clearRoicReview() { roicSources = {}; if (el("fv2-roic-review")) { el("fv2-roic-review").replaceChildren(); el("fv2-roic-review").hidden = true; } }
  function showRoicReview(data) {
    const root = el("fv2-roic-review"); root.hidden = false; root.replaceChildren(node("h3", "Confirmar as bases do ROIC"), node("p", data.message));
    const roles = [
      ["nopat", "NOPAT reportado após impostos operacionais", "nopat_with_explicit_tax_assumptions", "nopat_definition"],
      ["tax_assumptions", "Pressupostos fiscais do NOPAT", null, "tax_assumptions"],
      ["average_capital", "Capital investido médio do período", "average_invested_capital", "average_capital_definition"]
    ];
    roles.forEach(([key, title, concept, quoteKey]) => {
      const box = node("fieldset"); box.style.minWidth = "0"; box.style.overflowWrap = "anywhere"; box.style.border = "1px solid rgba(201,168,76,.22)"; box.style.borderRadius = "8px"; box.style.padding = "14px"; box.style.margin = "18px 0"; const legend = node("legend", title); legend.style.padding = "0 6px"; legend.style.fontSize = ".9rem"; box.append(legend);
      const label = node("label", "Linha original da fonte"); label.className = "field";
      const select = node("select"); select.dataset.roicRole = key; const blank = node("option", "Escolher a linha confirmada"); blank.value = ""; select.append(blank);
      data.rows.filter(row => !concept || row.concept_id === concept).forEach(row => { const option = node("option", `${row.location} · ${row.line} · ${row.amount} ${row.currency} · ${row.period}`); option.value = row.location; select.append(option); });
      select.value = roicSources[key + "_location"] || "";
      const original = node("p"); original.style.margin = "12px 0"; original.style.lineHeight = "1.5";
      const quoteLabel = node("label", "Citação exata da definição na rubrica original · mínimo 10 caracteres"); quoteLabel.className = "field";
      const input = node("textarea"); input.maxLength = 2000; input.rows = 3; input.dataset.roicQuote = quoteKey; input.value = roicSources[quoteKey] || "";
      function displayOriginal() { const row = data.rows.find(r => r.location === select.value); original.textContent = row ? `Rubrica original: ${row.line} · ${row.amount} ${row.currency} · escala ${row.scale} · ${row.period} · ${row.comparison_basis}` : ""; }
      select.onchange = () => { roicSources[key + "_location"] = select.value; delete roicSources[quoteKey]; input.value = ""; displayOriginal(); invalidate(); };
      input.oninput = () => { roicSources[quoteKey] = input.value; invalidate(); };
      displayOriginal(); label.append(select); quoteLabel.append(input); box.append(label, original, quoteLabel); root.append(box);
    });
  }
  let capexDefinitions = {};
  function clearCapexReview() { capexDefinitions = {}; if (el("fv2-capex-review")) { el("fv2-capex-review").replaceChildren(); el("fv2-capex-review").hidden = true; } }
  function clearSourceReviews() { clearWorkingCapitalReview(); clearRoicReview(); clearGrowthReview(); clearDebtReview(); clearCapexReview(); }
  function showCapexReview(data) {
    const root = el("fv2-capex-review"); root.hidden = false; root.replaceChildren(node("h3", "Confirmar o capex pago e os compromissos por executar"), node("p", data.message));
    data.rows.forEach(row => { const box = node("fieldset"); box.style.minWidth = "0"; box.style.overflowWrap = "anywhere"; box.style.border = "1px solid rgba(201,168,76,.22)"; box.style.borderRadius = "8px"; box.style.padding = "14px"; box.style.margin = "14px 0"; box.append(node("legend", row.location), node("p", `Rubrica original: ${row.line}`), node("p", `Montante original: ${row.amount} · ${row.currency} · escala ${row.scale} · ${row.period} · ${row.comparison_basis}`));
      const label = node("label", "Copie da rubrica original a definição dos pagamentos/classificação ou dos compromissos · mínimo 10 caracteres"); label.className = "field"; const input = node("input"); input.type = "text"; input.maxLength = 2000; input.dataset.capexLocation = row.location; input.value = capexDefinitions[row.location] || row.capex_definition_quote || ""; input.oninput = () => { if (input.value) capexDefinitions[row.location] = input.value; else delete capexDefinitions[row.location]; invalidate(); }; label.append(input); box.append(label); root.append(box); });
  }
  let debtDefinitions = {};
  function clearDebtReview() { debtDefinitions = {}; if (el("fv2-debt-review")) { el("fv2-debt-review").replaceChildren(); el("fv2-debt-review").hidden = true; } }
  function showDebtReview(data) {
    const root = el("fv2-debt-review"); root.hidden = false; root.replaceChildren(node("h3", "Confirmar a base do principal e os vencimentos da dívida"), node("p", data.message));
    data.rows.forEach(row => { const box = node("article"); box.className = "card"; box.append(node("h4", row.line), node("p", row.location + " · " + row.amount + " " + row.currency + " · " + row.period));
      const label = node("label", "Copie da rubrica original a definição comum do principal, leasing e data de referência · mínimo 10 caracteres"); label.className = "field"; const input = node("input"); input.type = "text"; input.maxLength = 2000; input.dataset.debtLocation = row.location; input.value = debtDefinitions[row.location] || row.debt_definition_quote || ""; input.oninput = () => { if (input.value) debtDefinitions[row.location] = input.value; else delete debtDefinitions[row.location]; invalidate(); }; label.append(input); box.append(label); root.append(box); });
  }
  let growthDefinitions = {};
  function clearGrowthReview() { growthDefinitions = {}; if (el("fv2-growth-review")) { el("fv2-growth-review").replaceChildren(); el("fv2-growth-review").hidden = true; } }
  function showGrowthReview(data) {
    const root = el("fv2-growth-review"); root.hidden = false; root.replaceChildren(node("h3", "Confirmar definições e perímetros do crescimento"), node("p", data.message));
    data.rows.forEach(row => { const box = node("fieldset"); box.style.minWidth = "0"; box.style.overflowWrap = "anywhere"; box.style.border = "1px solid rgba(201,168,76,.22)"; box.style.borderRadius = "8px"; box.style.padding = "14px"; box.style.margin = "14px 0"; box.append(node("legend", row.location), node("p", `Rubrica original: ${row.line}`), node("p", `Montante original: ${row.amount} · ${row.currency} · escala ${row.scale} · ${row.period} · ${row.comparison_basis}`));
      const label = node("label", "Copie a definição metodológica ou o perímetro, exatamente como consta da rubrica original · mínimo 10 caracteres"); label.className = "field"; const input = node("input"); input.type = "text"; input.maxLength = 2000; input.dataset.growthLocation = row.location; input.value = growthDefinitions[row.location] || row.growth_definition_quote || ""; input.oninput = () => { if (input.value) growthDefinitions[row.location] = input.value; else delete growthDefinitions[row.location]; invalidate(); }; label.append(input); box.append(label); root.append(box); });
    const clear = node("button", "Limpar definições de crescimento"); clear.type = "button"; clear.className = "btn btn-ghost"; clear.onclick = () => { clearGrowthReview(); invalidate(); }; root.append(clear);
  }
  let monthEndSupported = false, closeResult = null, closeEpoch = 0, closeRows = [];
  let closeCsvSupported = false;
  let closeSaveSupported = false, closeHistorySupported = false, closeSaveAttempt = null, closeHistoryEpoch = 0;
  const closeRelations = { month_on_month: "Mês anterior", month_year_on_year: "Mês homólogo", ytd_year_on_year: "YTD homólogo" };
  function clearCloseResult() { closeEpoch++; closeResult = null; closeSaveAttempt = null; if (el("fv2-close-save")) el("fv2-close-save").disabled = true; if (el("fv2-close-result")) el("fv2-close-result").replaceChildren(); if (el("fv2-close-download")) el("fv2-close-download").disabled = true; }
  let closeCatalogRules = [], closeCatalogState = "waiting", closeCatalogLimit = 20, closeCatalogSignature = null, closeCatalogEpoch = 0;
  const closeCatalogLabels = new Map();
  const legacyCloseRules = [
    ["REC-BS-001", "Balanço", "Balance sheet", "Balance", "Bilan"],
    ["REC-CF-001", "Movimentos de caixa", "Cash movement", "Movimientos de efectivo", "Mouvements de trésorerie"],
    ["REC-PL-001", "Ponte do resultado", "Profit and loss bridge", "Puente del resultado", "Rapprochement du résultat"],
    ["REC-EBITDA-001", "Ponte de EBITDA", "EBITDA bridge", "Puente de EBITDA", "Rapprochement de l’EBITDA"],
    ["REC-DEBT-001", "Movimentos da dívida", "Debt roll-forward", "Movimientos de deuda", "Mouvements de dette"],
    ["REC-LEASE-001", "Movimentos de leasing", "Lease roll-forward", "Movimientos de arrendamientos", "Mouvements des locations"]
  ].map(([rule_id, ...labels]) => ({ rule_id, versions: [], labels: Object.fromEntries(["pt", "en", "es", "fr"].map((lang, i) => [lang, labels[i]])), label_fallback: false }));
  function normalizeCloseCatalog(raw) {
    const invalid = () => { throw new Error("O catálogo de reconciliações não é válido. Atualize-o antes de preparar o fecho."); };
    if (!raw || raw.schema !== "financial-month-end-reconciliation-catalog-v1" || raw.selection !== "explicit" || raw.result_period_requirement !== "target_month_current_currency_scope" || !Number.isInteger(raw.max_required_reconciliations) || raw.max_required_reconciliations < 1 || raw.max_required_reconciliations > 20 || !Array.isArray(raw.rules) || !raw.rules.length || raw.rules.length > 100) invalid();
    const seen = new Set();
    const rules = raw.rules.map(rule => {
      if (!rule || typeof rule.rule_id !== "string" || rule.rule_id.length > 100 || !/^REC-[A-Z0-9]+(?:-[A-Z0-9]+)*$/.test(rule.rule_id) || seen.has(rule.rule_id) || !Array.isArray(rule.versions) || !rule.versions.length || rule.versions.length > 20 || new Set(rule.versions).size !== rule.versions.length || rule.versions.some(v => typeof v !== "string" || !/^[A-Za-z0-9._-]{1,32}$/.test(v)) || typeof rule.label_fallback !== "boolean") invalid();
      const labels = {};
      for (const lang of ["pt", "en", "es", "fr"]) { const text = rule.labels?.[lang]; if (typeof text !== "string" || !text.trim() || text.length > 400) invalid(); labels[lang] = text; }
      seen.add(rule.rule_id);
      return { rule_id: rule.rule_id, versions: [...rule.versions].sort((a,b) => a.localeCompare(b, "en", { numeric: true })), labels, label_fallback: rule.label_fallback };
    }).sort((a,b) => a.rule_id.localeCompare(b.rule_id));
    return { rules, limit: raw.max_required_reconciliations };
  }
  function configureCloseCatalog(raw, supported) {
    let state, rules = [], limit = 20;
    if (!supported) state = "unavailable";
    else if (raw === undefined) { state = "legacy"; rules = legacyCloseRules; }
    else { try { const value = normalizeCloseCatalog(raw); state = "ready"; rules = value.rules; limit = value.limit; } catch (_) { state = "invalid"; } }
    const signature = JSON.stringify({ state, rules, limit });
    if (closeCatalogSignature !== null && signature !== closeCatalogSignature) clearCloseResult();
    closeCatalogState = state; closeCatalogRules = rules; closeCatalogLimit = limit; closeCatalogSignature = signature;
    for (const rule of rules) closeCatalogLabels.set(rule.rule_id, rule.labels);
    renderCloseRuleChoices();
  }
  function renderCloseRuleChoices() {
    const root = el("fv2-close-rules"); if (!root) return;
    const selected = new Set(Array.from(root.querySelectorAll("input:checked"), input => input.value));
    const language = el("fv2-report-language")?.value || "pt";
    root.replaceChildren(node("legend", "Reconciliações exigidas · selecione explicitamente"));
    const message = node("p"); message.id = "fv2-close-catalog-status"; message.setAttribute("role", "status");
    message.textContent = closeCatalogState === "ready" ? `Catálogo do backend · escolha até ${closeCatalogLimit} requisitos da sua política. Nenhum é selecionado automaticamente.` : closeCatalogState === "legacy" ? "Backend antigo: catálogo básico com as seis regras iniciais. Requisitos já escolhidos continuam visíveis até serem revistos." : closeCatalogState === "loading" ? "A atualizar o catálogo; a preparação está suspensa." : closeCatalogState === "unavailable" ? "A preparação mensal não está disponível neste backend." : "Catálogo indisponível ou inválido. Atualize-o antes de preparar o fecho.";
    root.append(message, node("p", "A escolha de uma regra não confirma a sua execução. A evidência tem de corresponder ao mês, moeda e âmbito da política. Uma conferência de saldo inicial pode pertencer ao mês anterior."));
    const button = node("button", "Atualizar catálogo"); button.id = "fv2-close-catalog-refresh"; button.type = "button"; button.className = "btn btn-ghost"; button.disabled = closeCatalogState === "loading"; button.onclick = refreshCloseCatalog; root.append(button);
    function append(rule, missing) {
      const label = node("label"); label.style.display = "block"; label.style.overflowWrap = "anywhere";
      const input = node("input"); input.type = "checkbox"; input.value = rule.rule_id; input.checked = selected.has(rule.rule_id); input.dataset.closeCatalogRule = "true";
      const text = rule.labels?.[language] || closeCatalogLabels.get(rule.rule_id)?.[language] || rule.rule_id;
      label.append(input, document.createTextNode(` ${text} · ${rule.rule_id}`));
      const hint = node("span", missing ? "Fora do catálogo atual. Este requisito não será removido automaticamente; reveja-o antes de o retirar da política." : rule.versions.length ? "Versões disponíveis no motor: " + rule.versions.join(", ") + (rule.label_fallback ? " · rótulo canónico sem tradução aprovada" : "") : "Versão não anunciada pelo backend antigo"); hint.style.display = "block"; hint.style.fontSize = ".8rem"; hint.style.opacity = ".8"; label.append(hint);
      if (missing) label.dataset.closeCatalogUnavailable = "true";
      root.append(label);
    }
    for (const rule of closeCatalogRules) append(rule, false);
    for (const id of selected) if (!closeCatalogRules.some(rule => rule.rule_id === id)) append({ rule_id: id, versions: [] }, true);
  }
  function closeRuleSelection() {
    if (!["ready", "legacy"].includes(closeCatalogState)) throw new Error("Atualize o catálogo de reconciliações antes de preparar o fecho.");
    const selected = Array.from(el("fv2-close-rules").querySelectorAll("input:checked"), input => input.value);
    if (!selected.length || selected.length > closeCatalogLimit) throw new Error(`Selecione entre uma e ${closeCatalogLimit} reconciliações da política.`);
    const permitted = new Set(closeCatalogRules.map(rule => rule.rule_id));
    if (selected.some(id => !permitted.has(id))) throw new Error("Há requisitos fora do catálogo atual. Reveja-os explicitamente antes de preparar o fecho.");
    return selected;
  }
  async function refreshCloseCatalog() {
    const token = currentToken, epoch = ++closeCatalogEpoch;
    clearCloseResult(); closeCatalogState = "loading"; renderCloseRuleChoices();
    try {
      const response = await request("/capabilities"); const data = await response.json();
      if (token !== currentToken || epoch !== closeCatalogEpoch) return;
      monthEndSupported = data.month_end_preview_versions?.includes("1") === true;
      configureCloseCatalog(data.month_end_reconciliation_catalog, monthEndSupported);
      if (!monthEndSupported && el("fv2-close")) el("fv2-close").hidden = true;
    } catch (_) { if (token === currentToken && epoch === closeCatalogEpoch) { closeCatalogState = "invalid"; closeCatalogSignature = null; renderCloseRuleChoices(); } }
  }
  function buildMonthEnd() {
    const panel = node("details"); panel.id = "fv2-close"; panel.hidden = true; panel.style.overflowWrap = "anywhere"; panel.style.minWidth = "0";
    panel.innerHTML = `<style>#fv2-close fieldset{min-width:0;border:1px solid rgba(201,168,76,.22);border-radius:8px;padding:14px;margin:14px 0}#fv2-close legend{font-weight:600;padding:0 6px;font-size:.9rem}#fv2-close fieldset>label{margin:8px 0}#fv2-close summary{cursor:pointer;padding:12px 0;font-weight:600}#fv2-close-result li{margin:14px 0}#fv2-close-result details{font-size:.85rem;opacity:.85}</style><summary>Preparar revisão do fecho mensal · sem consumir análises</summary>
      <p>Defina uma política aprovada e associe as linhas de origem. O resultado prepara uma revisão: não atesta o fecho nem cria lançamentos. Pode descarregar a preparação. Quando disponível, guarde explicitamente um snapshot imutável no histórico do workspace após rever a fonte e a política.</p>
      <div class="field"><label for="fv2-close-month">Mês a rever</label><input id="fv2-close-month" type="month"></div>
      <div class="field"><label for="fv2-close-policy">Identificador da política</label><input id="fv2-close-policy" maxlength="100"></div>
      <div class="field"><label for="fv2-close-policy-version">Versão da política</label><input id="fv2-close-policy-version" maxlength="100"></div>
      <div class="field"><label for="fv2-close-currency">Moeda da revisão · ISO</label><input id="fv2-close-currency" maxlength="3" placeholder="EUR"></div>
      <div class="field"><label for="fv2-close-start">Mês de início fiscal · 1 a 12, coerente com a fonte</label><input id="fv2-close-start" type="number" min="1" max="12"></div>
      <div class="field"><label for="fv2-close-absolute">Limiar absoluto · unidades da moeda, decimal com ponto</label><input id="fv2-close-absolute" inputmode="decimal" maxlength="64"></div>
      <div class="field"><label for="fv2-close-percentage">Limiar percentual · pontos percentuais, decimal com ponto</label><input id="fv2-close-percentage" inputmode="decimal" maxlength="64"></div>
      <div class="field"><label for="fv2-close-basis">Fundamento dos limiares aprovados</label><input id="fv2-close-basis" maxlength="100"></div>
      <fieldset id="fv2-close-statements"><legend>Demonstrações exigidas · selecione explicitamente</legend></fieldset>
      <fieldset id="fv2-close-rules"><legend>Reconciliações exigidas · selecione explicitamente</legend></fieldset>
      <fieldset id="fv2-close-relations"><legend>Comparações exigidas · selecione explicitamente</legend></fieldset>
      <div class="field"><label for="fv2-close-tasks">Tarefas exigidas · uma por linha, até 20</label><textarea id="fv2-close-tasks" rows="3" maxlength="2020"></textarea></div>
      <button type="button" class="btn btn-ghost" id="fv2-close-task-build">Preparar confirmações das tarefas</button><div id="fv2-close-task-rows"></div>
      <p>Associe cada comparação às duas linhas correspondentes. O motor verifica conceito, período, moeda e comparabilidade; não soma meses para inventar YTD.</p>
      <button type="button" class="btn btn-ghost" id="fv2-close-add-comparison">Adicionar comparação</button><div id="fv2-close-comparisons"></div>
      <p>Movimentos esperados são declarações explícitas sobre valores ou saldos líquidos; não comprovam atividade transacional.</p>
      <button type="button" class="btn btn-ghost" id="fv2-close-add-movement">Adicionar movimento esperado</button><div id="fv2-close-movements"></div>
      <button type="button" class="btn btn-primary" id="fv2-close-prepare">Preparar checklist</button>
      <button type="button" class="btn btn-ghost" id="fv2-close-save" hidden disabled>Guardar preparação revista · sem consumir análises</button>
      <button type="button" class="btn btn-ghost" id="fv2-close-download" disabled>↓ Snapshot JSON com evidência</button>
      <div id="fv2-close-result" role="region" aria-label="Checklist do fecho mensal" style="overflow-wrap:anywhere"></div>`;
    panel.prepend(panel.querySelector("summary"));
    el("fv2-generate").before(panel);
    function checks(id, entries) { Object.entries(entries).forEach(([value, text]) => { const label = node("label"); label.style.display = "block"; const input = node("input"); input.type = "checkbox"; input.value = value; label.append(input, document.createTextNode(" " + text)); el(id).append(label); }); }
    checks("fv2-close-statements", { income_statement: "Demonstração de resultados", balance_sheet: "Balanço", cash_flow_statement: "Fluxos de caixa" });
    renderCloseRuleChoices();
    el("fv2-report-language").addEventListener("change", renderCloseRuleChoices);
    checks("fv2-close-relations", closeRelations);
    panel.addEventListener("input", clearCloseResult); panel.addEventListener("change", clearCloseResult);
    el("fv2-close-tasks").addEventListener("input", () => el("fv2-close-task-rows").replaceChildren());
    el("fv2-close-task-build").onclick = buildCloseTasks;
    el("fv2-close-add-comparison").onclick = () => addCloseBinding(false);
    el("fv2-close-add-movement").onclick = () => addCloseBinding(true);
    el("fv2-close-prepare").onclick = prepareClose;
    el("fv2-close-save").onclick = saveClose;
    const history = node("section"); history.id = "fv2-close-history"; history.hidden = true; history.style.overflowWrap = "anywhere"; history.append(node("h3", "Preparações mensais guardadas"), node("p", "Snapshots imutáveis da fonte, política e verificações. Guardar uma preparação não atesta o fecho nem publica um relatório auditado."));
    const reload = node("button", "Consultar preparações guardadas"); reload.type = "button"; reload.className = "btn btn-ghost"; reload.onclick = () => loadCloseHistory(); history.append(reload);
    const list = node("div"); list.id = "fv2-close-history-list"; const saved = node("div"); saved.id = "fv2-close-saved-result"; history.append(list, saved); el("fv2-history-list").after(history);
    el("fv2-close-download").onclick = () => { if (closeResult) download(new Blob([JSON.stringify(closeResult, null, 2)], { type: "application/json" }), `saleslab-month-end-${closeResult.close_choices.target_month}.json`); };
  }
  function closeSelect(labelText, values, role, multiple = false) {
    const label = node("label", labelText); label.className = "field"; label.style.display = "block";
    const select = node("select"); select.dataset.closeRole = role; select.multiple = multiple; select.style.maxWidth = "100%"; select.style.width = "100%";
    if (!multiple) { const empty = node("option", "Selecione explicitamente"); empty.value = ""; select.append(empty); }
    values.forEach(([value, text]) => { const option = node("option", text); option.value = value; select.append(option); });
    label.append(select); return label;
  }
  function closeSourceOptions() { return closeRows.map(row => [row.location, `${row.location} · ${row.original_line} · ${row.period?.label || row.original_input?.period || "período por resolver"} · ${row.original_input?.amount || ""} · ${row.currency || "moeda por resolver"}`]); }
  function renderMonthEndRows(data) {
    clearCloseResult(); closeRows = data.rows || []; el("fv2-close").hidden = !monthEndSupported;
    ["fv2-close-comparisons", "fv2-close-movements", "fv2-close-task-rows"].forEach(id => el(id).replaceChildren());
  }
  function closeTaskNames() { const names = el("fv2-close-tasks").value.split("\n").map(s => s.trim()).filter(Boolean); if (!names.length || names.length > 20 || names.some(s => s.length > 100) || new Set(names).size !== names.length) throw new Error("Indique de uma a vinte tarefas distintas, até 100 caracteres cada."); return names; }
  function buildCloseTasks() {
    try { clearCloseResult(); const root = el("fv2-close-task-rows"); root.replaceChildren(); closeTaskNames().forEach(task => { const row = node("fieldset"); row.style.minWidth = "0"; row.dataset.closeTask = task; row.append(node("legend", task)); const label = node("label", "Confirmo que realizei esta tarefa · declaração do utilizador "); const check = node("input"); check.type = "checkbox"; label.append(check); row.append(label, closeSelect("Linhas do mês que sustentam a confirmação · seleção múltipla", closeSourceOptions(), "evidence", true)); root.append(row); }); } catch (error) { status(error.message); }
  }
  function addCloseBinding(movement) {
    const root = el(movement ? "fv2-close-movements" : "fv2-close-comparisons"); if (root.children.length >= 50) return;
    clearCloseResult(); const row = node("fieldset"); row.style.minWidth = "0"; row.dataset.closeBinding = movement ? "movement" : "comparison";
    row.append(node("legend", movement ? "Movimento esperado" : "Comparação"), closeSelect("Linha atual", closeSourceOptions(), "current"), closeSelect(movement ? "Linha comparativa · deixe vazio para valor não nulo" : "Linha comparativa", closeSourceOptions(), "prior"), closeSelect(movement ? "Expectativa" : "Relação temporal", Object.entries(movement ? { nonzero_value: "Valor não nulo", changed_from_prior_month: "Variação face ao mês anterior" } : closeRelations), "kind"));
    const remove = node("button", "Remover"); remove.type = "button"; remove.className = "btn btn-ghost"; remove.onclick = () => { row.remove(); clearCloseResult(); }; row.append(remove); root.append(row);
  }
  function closeOptions() {
    const checked = id => Array.from(el(id).querySelectorAll("input:checked"), input => input.value);
    const value = id => el("fv2-close-" + id).value;
    const bindings = (id, movement) => Array.from(el(id).children, (row, index) => { const selected = role => row.querySelector(`[data-close-role="${role}"]`).value; return { key: `${movement ? "movement" : "comparison"}-${index + 1}`, current: selected("current"), ...(movement ? { prior: selected("prior") || null, kind: selected("kind") } : { prior: selected("prior"), relation: selected("kind") }) }; });
    return { version: "1", preview_hash: preview.preview_hash, target_month: value("month"), policy_id: value("policy"), policy_version: value("policy-version"), currency: value("currency"), fiscal_year_start_month: Number(value("start")), required_statements: checked("fv2-close-statements"), required_reconciliations: closeRuleSelection(), required_comparisons: checked("fv2-close-relations"), required_tasks: closeTaskNames(), absolute_variation_threshold: value("absolute"), percentage_variation_threshold: value("percentage"), materiality_basis: value("basis"), comparisons: bindings("fv2-close-comparisons", false), movements: bindings("fv2-close-movements", true), confirmations: Array.from(el("fv2-close-task-rows").children, row => ({ task_id: row.dataset.closeTask, status: row.querySelector('input[type="checkbox"]').checked ? "complete" : "incomplete", evidence: Array.from(row.querySelector("select").selectedOptions, o => o.value) })) };
  }
  async function prepareClose() {
    if (!preview || !monthEndSupported) return;
    const token = currentToken, epoch = version; clearCloseResult(); const revision = closeEpoch;
    try { const body = form(options()); body.append("month_end", JSON.stringify(closeOptions())); busy(true); status("A preparar o checklist mensal…"); const response = await request("/month-end/preview", { method: "POST", body }); const data = await response.json(); if (token !== currentToken || epoch !== version || revision !== closeEpoch) return; closeResult = data; renderClose(data); status("Checklist preparado. Reveja as lacunas, a política e a evidência antes de descarregar."); }
    catch (error) { if (token === currentToken && epoch === version && revision === closeEpoch) status(error.message); }
    finally { if (token === currentToken) busy(false); }
  }
  async function saveClose() {
    if (!closeResult || closeResult.persisted || !closeSaveSupported) return;
    const reviewed = closeResult, token = currentToken, revision = closeEpoch, epoch = version;
    closeSaveAttempt ||= crypto.randomUUID();
    try { const body = form(reviewed.upload_choices); body.append("month_end", JSON.stringify(reviewed.close_choices)); body.append("save", JSON.stringify({ binding_hash: reviewed.binding_hash, attempt_key: closeSaveAttempt })); busy(true); status("A guardar a preparação mensal revista…");
      const response = await request("/month-end/snapshots", { method: "POST", body }); const data = await response.json();
      if (token !== currentToken || revision !== closeEpoch || epoch !== version) return;
      closeResult = data; hasMonthlySnapshots = true; renderClose(data); status("Preparação guardada. Pode reabri-la pelo histórico; o fecho continua por atestar."); await loadCloseHistory();
    } catch (error) { if (token === currentToken && revision === closeEpoch && epoch === version) status(error.message); }
    finally { if (token === currentToken) busy(false); }
  }
  async function loadCloseHistory(cursor = null) {
    if (!closeHistorySupported) return;
    const token = currentToken, epoch = ++closeHistoryEpoch, list = el("fv2-close-history-list");
    if (!cursor) list.replaceChildren();
    try { const response = await request("/month-end/snapshots" + (cursor ? "?cursor=" + encodeURIComponent(cursor) : "")); const data = await response.json(); if (token !== currentToken || epoch !== closeHistoryEpoch) return;
      list.querySelectorAll('[data-close-next]').forEach(button => button.remove());
      if (!data.snapshots.length && !cursor) list.append(node("p", "Ainda não há preparações mensais guardadas neste workspace."));
      data.snapshots.forEach(item => { const button = node("button", `${item.target_month} · ${item.policy_id} · ${item.filename}`); button.type = "button"; button.className = "btn btn-ghost"; button.dataset.closeSnapshot = item.snapshot_id; button.onclick = () => openCloseSnapshot(item.snapshot_id); list.append(button); });
      if (data.next_cursor) { const next = node("button", "Carregar preparações anteriores"); next.type = "button"; next.className = "btn btn-ghost"; next.dataset.closeNext = "true"; next.onclick = () => loadCloseHistory(data.next_cursor); list.append(next); }
    } catch (error) { if (token === currentToken && epoch === closeHistoryEpoch) status(error.message); }
  }
  async function openCloseSnapshot(id) {
    const token = currentToken, epoch = ++closeHistoryEpoch, root = el("fv2-close-saved-result"); root.replaceChildren();
    try { const response = await request("/month-end/snapshots/" + encodeURIComponent(id)); const data = await response.json(); if (token !== currentToken || epoch !== closeHistoryEpoch) return; renderClose(data, root);
      const button = node("button", closeCopy(closeLanguage(data)).saved_download); button.type = "button"; button.className = "btn btn-ghost"; button.dataset.closeSavedDownload = "true"; button.onclick = () => download(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), `saleslab-month-end-${data.close_choices.target_month}-${data.snapshot_id}.json`); root.append(button);
      if (closeCsvSupported) { const csv = node("button", closeCopy(closeLanguage(data)).csv_download); csv.type = "button"; csv.className = "btn btn-ghost"; csv.dataset.closeCsvDownload = "true";
        csv.onclick = async () => { csv.disabled = true; try { const response = await request("/month-end/snapshots/" + encodeURIComponent(id) + "/checklist.csv"); const blob = await response.blob(); if (token !== currentToken || epoch !== closeHistoryEpoch) return; download(blob, `saleslab-month-end-${id}.csv`); } catch (error) { if (token === currentToken && epoch === closeHistoryEpoch) status(error.message); } finally { csv.disabled = false; } }; root.append(csv); }
      status(closeCopy(closeLanguage(data)).reopened);
    } catch (error) { if (token === currentToken && epoch === closeHistoryEpoch) { root.replaceChildren(); status(error.message); } }
  }
  const closeMessages = {
  "pt": {
    "incomplete": "Preparação incompleta",
    "review_required": "Variações ou avisos exigem revisão",
    "ready_for_review": "Preparação pronta para revisão",
    "limits": "Fecho não atestado. Sem lançamentos contabilísticos. Desempenho não classificado.",
    "saved": "Preparação guardada; esta leitura verificou a integridade e recalculou os resultados.",
    "unsaved": "Preparação ainda não guardada no histórico.",
    "source": "Fonte",
    "source_policy": "Identificação da fonte e da política",
    "source_hash": "SHA-256 da fonte",
    "policy": "Política",
    "version": "versão",
    "pending": "Pendente",
    "review": "Rever",
    "satisfied": "Verificado pelo motor",
    "confirmed_by_user": "Confirmado pelo utilizador",
    "income_statement": "Demonstração de resultados",
    "balance_sheet": "Balanço",
    "cash_flow_statement": "Fluxos de caixa",
    "evidence": "Evidência e identificação da verificação",
    "rounding": "Valores apresentados com duas casas decimais. O snapshot conserva os valores completos; os limiares são verificados com os valores exatos.",
    "month_on_month": "Mês anterior",
    "month_year_on_year": "Mês homólogo",
    "ytd_year_on_year": "YTD homólogo",
    "delta": "variação",
    "percentage_unavailable": "percentagem indisponível (base zero)",
    "threshold_reached": "limiar de revisão atingido",
    "below_threshold": "abaixo dos limiares declarados",
    "missing": "Dados e verificações em falta",
    "fallback_reason": "Reveja as lacunas e os avisos da validação financeira de origem.",
    "saved_download": "↓ Snapshot guardado com evidência",
    "csv_download": "↓ Checklist CSV para Excel",
    "reopened": "Preparação guardada reaberta com a evidência verificada.",
    "language_fallback": "Idioma guardado não suportado. Apresentação em inglês; a evidência guardada mantém-se inalterada.",
    "reason.statement_present": "Há dados da demonstração no mês e âmbito escolhidos.",
    "reason.statement_missing": "Faltam dados desta demonstração para o mês e âmbito escolhidos.",
    "reason.reconciliation_pass": "A identidade contabilística foi reconciliada.",
    "reason.reconciliation_rounding_pass": "A identidade reconciliou dentro da tolerância de arredondamento declarada.",
    "reason.reconciliation_warning": "A reconciliação tem um aviso que exige revisão.",
    "reason.reconciliation_critical": "A reconciliação tem uma diferença crítica.",
    "reason.reconciliation_blocking": "A reconciliação está bloqueada.",
    "reason.reconciliation_missing_or_ambiguous": "Falta uma reconciliação única para este mês, moeda e âmbito.",
    "reason.comparable": "Os períodos são comparáveis e a variação fica abaixo dos limiares declarados.",
    "reason.comparison_not_configured": "Associe as linhas de origem desta comparação exigida.",
    "reason.material_variation": "A variação atinge um dos limiares declarados e exige revisão.",
    "reason.zero_comparison_base": "A base comparativa é zero: a variação absoluta está disponível, mas a percentagem não pode ser calculada.",
    "reason.calculation_blocked": "Resolva os bloqueios da validação financeira antes de calcular comparações.",
    "reason.concept_mismatch": "As duas linhas representam rubricas ou demonstrações diferentes.",
    "reason.expected_movement_absent": "O valor ou movimento líquido esperado não foi observado; verifique a fonte.",
    "reason.expected_movement_present": "O valor ou movimento líquido esperado foi observado; não comprova transações individuais.",
    "reason.metric_unsupported": "Esta rubrica não tem uma definição de comparação suportada.",
    "reason.movement_evidence_missing_or_incompatible": "Falta evidência comparável para verificar o movimento esperado.",
    "reason.periods_or_values_incompatible": "Os períodos ou valores não são comparáveis.",
    "reason.source_ambiguous": "Existem várias linhas candidatas; reveja o mapeamento da fonte.",
    "reason.source_incompatible": "A linha não está validada ou tem moeda, âmbito ou unidade incompatíveis.",
    "reason.source_missing": "A linha de origem não está disponível.",
    "reason.target_month_mismatch": "A linha atual não corresponde ao mês escolhido.",
    "reason.task_unconfirmed": "Confirme a tarefa com evidência válida do mês escolhido.",
    "reason.task_user_confirmed": "Tarefa declarada como realizada pelo utilizador, com evidência do mês.",
    "reason.ytd_cutoff_mismatch": "O início fiscal ou o corte YTD não corresponde à política escolhida.",
    "REC-BS-001": "Balanço",
    "REC-CF-001": "Movimentos de caixa",
    "REC-PL-001": "Ponte do resultado",
    "REC-EBITDA-001": "Ponte de EBITDA",
    "REC-DEBT-001": "Movimentos da dívida",
    "REC-LEASE-001": "Movimentos de leasing",
    "REC-GROWTH-001": "Ponte do crescimento reportado",
    "REC-DEBT-RATE-001": "Composição da dívida por taxa contratual",
    "REC-DEBT-MATURITY-001": "Distribuição dos vencimentos da dívida",
    "REC-CAPEX-001": "Classificação do capex pago",
    "REC-CAPEX-002": "Capex versus aquisições nos fluxos de caixa",
    "REC-WC-CLOSING-001": "Fundo de maneio: componentes finais",
    "REC-WC-OPENING-001": "Fundo de maneio: componentes iniciais",
    "REC-WC-CHANGE-001": "Fundo de maneio: variação de saldos",
    "category.statement": "Dados da demonstração",
    "category.reconciliation": "Reconciliação",
    "category.comparison": "Comparação",
    "category.movement": "Movimento esperado",
    "category.task": "Confirmação da tarefa",
    "category.quality": "Qualidade dos dados",
    "result_label": "Checklist do fecho mensal"
  },
  "en": {
    "incomplete": "Preparation incomplete",
    "review_required": "Variations or warnings require review",
    "ready_for_review": "Preparation ready for review",
    "limits": "Close not attested. No accounting entries. Performance not assessed.",
    "saved": "Saved preparation; this read verified integrity and recalculated the results.",
    "unsaved": "Preparation not yet saved in history.",
    "source": "Source",
    "source_policy": "Source and policy identification",
    "source_hash": "Source SHA-256",
    "policy": "Policy",
    "version": "version",
    "pending": "Pending",
    "review": "Review required",
    "satisfied": "Verified by the engine",
    "confirmed_by_user": "Confirmed by the user",
    "income_statement": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow_statement": "Cash flows",
    "evidence": "Evidence and check identification",
    "rounding": "Values are displayed to two decimal places. The snapshot retains full values; thresholds are checked against exact values.",
    "month_on_month": "Previous month",
    "month_year_on_year": "Same month last year",
    "ytd_year_on_year": "Year-to-date versus prior year",
    "delta": "change",
    "percentage_unavailable": "percentage unavailable (zero base)",
    "threshold_reached": "review threshold reached",
    "below_threshold": "below the declared thresholds",
    "missing": "Missing data and checks",
    "fallback_reason": "Review the gaps and warnings in the source financial validation.",
    "saved_download": "↓ Saved snapshot with evidence",
    "csv_download": "↓ CSV checklist for Excel",
    "reopened": "Saved preparation reopened with verified evidence.",
    "language_fallback": "Unsupported saved language. Labels are shown in English; saved evidence is unchanged.",
    "reason.statement_present": "Statement data exists for the selected month and scope.",
    "reason.statement_missing": "Statement data is missing for the selected month and scope.",
    "reason.reconciliation_pass": "The accounting identity reconciled.",
    "reason.reconciliation_rounding_pass": "The identity reconciled within the declared rounding allowance.",
    "reason.reconciliation_warning": "The reconciliation has a warning requiring review.",
    "reason.reconciliation_critical": "The reconciliation has a critical difference.",
    "reason.reconciliation_blocking": "The reconciliation is blocked.",
    "reason.reconciliation_missing_or_ambiguous": "A unique reconciliation is missing for this month, currency and scope.",
    "reason.comparable": "The periods are comparable and the change is below the declared thresholds.",
    "reason.comparison_not_configured": "Bind the source rows for this required comparison.",
    "reason.material_variation": "The change reaches a declared threshold and requires review.",
    "reason.zero_comparison_base": "The comparison base is zero: the absolute change is available, but the percentage cannot be calculated.",
    "reason.calculation_blocked": "Resolve financial validation blockers before calculating comparisons.",
    "reason.concept_mismatch": "The two rows represent different concepts or statements.",
    "reason.expected_movement_absent": "The expected value or net balance change was not observed; check the source.",
    "reason.expected_movement_present": "The expected value or net balance change was observed; this does not prove individual transactions.",
    "reason.metric_unsupported": "This concept has no supported comparison definition.",
    "reason.movement_evidence_missing_or_incompatible": "Comparable evidence is missing to verify the expected movement.",
    "reason.periods_or_values_incompatible": "The periods or values are not comparable.",
    "reason.source_ambiguous": "Multiple source rows are candidates; review source mapping.",
    "reason.source_incompatible": "The row is not validated or has incompatible currency, scope or unit.",
    "reason.source_missing": "The source row is unavailable.",
    "reason.target_month_mismatch": "The current row does not match the selected month.",
    "reason.task_unconfirmed": "Confirm the task with valid evidence for the selected month.",
    "reason.task_user_confirmed": "Task declared complete by the user, with evidence for the month.",
    "reason.ytd_cutoff_mismatch": "The fiscal start or year-to-date cutoff does not match the selected policy.",
    "REC-BS-001": "Balance sheet",
    "REC-CF-001": "Cash movement",
    "REC-PL-001": "Profit and loss bridge",
    "REC-EBITDA-001": "EBITDA bridge",
    "REC-DEBT-001": "Debt roll-forward",
    "REC-LEASE-001": "Lease roll-forward",
    "REC-GROWTH-001": "Reported growth bridge",
    "REC-DEBT-RATE-001": "Contractual debt rate composition",
    "REC-DEBT-MATURITY-001": "Debt maturity distribution",
    "REC-CAPEX-001": "Cash capex classification",
    "REC-CAPEX-002": "Capex versus cash-flow purchases",
    "REC-WC-CLOSING-001": "Working capital: closing components",
    "REC-WC-OPENING-001": "Working capital: opening components",
    "REC-WC-CHANGE-001": "Working capital: balance change",
    "category.statement": "Statement data",
    "category.reconciliation": "Reconciliation",
    "category.comparison": "Comparison",
    "category.movement": "Expected movement",
    "category.task": "Task confirmation",
    "category.quality": "Data quality",
    "result_label": "Monthly close checklist"
  },
  "es": {
    "incomplete": "Preparación incompleta",
    "review_required": "Las variaciones o advertencias requieren revisión",
    "ready_for_review": "Preparación lista para revisión",
    "limits": "Cierre no certificado. Sin asientos contables. Desempeño no evaluado.",
    "saved": "Preparación guardada; esta lectura verificó la integridad y recalculó los resultados.",
    "unsaved": "Preparación aún no guardada en el historial.",
    "source": "Fuente",
    "source_policy": "Identificación de la fuente y la política",
    "source_hash": "SHA-256 de la fuente",
    "policy": "Política",
    "version": "versión",
    "pending": "Pendiente",
    "review": "Revisión necesaria",
    "satisfied": "Verificado por el motor",
    "confirmed_by_user": "Confirmado por el usuario",
    "income_statement": "Cuenta de resultados",
    "balance_sheet": "Balance",
    "cash_flow_statement": "Flujos de efectivo",
    "evidence": "Evidencia e identificación de la verificación",
    "rounding": "Los valores se muestran con dos decimales. El snapshot conserva los valores completos; los umbrales se verifican con valores exactos.",
    "month_on_month": "Mes anterior",
    "month_year_on_year": "Mismo mes del año anterior",
    "ytd_year_on_year": "Acumulado frente al año anterior",
    "delta": "variación",
    "percentage_unavailable": "porcentaje no disponible (base cero)",
    "threshold_reached": "umbral de revisión alcanzado",
    "below_threshold": "por debajo de los umbrales declarados",
    "missing": "Datos y verificaciones pendientes",
    "fallback_reason": "Revise las carencias y advertencias de la validación financiera de origen.",
    "saved_download": "↓ Snapshot guardado con evidencia",
    "csv_download": "↓ Checklist CSV para Excel",
    "reopened": "Preparación guardada reabierta con evidencia verificada.",
    "language_fallback": "Idioma guardado no compatible. Presentación en inglés; la evidencia guardada no cambia.",
    "reason.statement_present": "Hay datos del estado financiero para el mes y ámbito elegidos.",
    "reason.statement_missing": "Faltan datos de este estado financiero para el mes y ámbito elegidos.",
    "reason.reconciliation_pass": "La identidad contable se ha conciliado.",
    "reason.reconciliation_rounding_pass": "La identidad se concilió dentro de la tolerancia de redondeo declarada.",
    "reason.reconciliation_warning": "La conciliación tiene una advertencia que requiere revisión.",
    "reason.reconciliation_critical": "La conciliación tiene una diferencia crítica.",
    "reason.reconciliation_blocking": "La conciliación está bloqueada.",
    "reason.reconciliation_missing_or_ambiguous": "Falta una conciliación única para este mes, moneda y ámbito.",
    "reason.comparable": "Los períodos son comparables y la variación está por debajo de los umbrales declarados.",
    "reason.comparison_not_configured": "Asocie las filas de origen de esta comparación obligatoria.",
    "reason.material_variation": "La variación alcanza un umbral declarado y requiere revisión.",
    "reason.zero_comparison_base": "La base comparativa es cero: la variación absoluta está disponible, pero no se puede calcular el porcentaje.",
    "reason.calculation_blocked": "Resuelva los bloqueos de la validación financiera antes de calcular comparaciones.",
    "reason.concept_mismatch": "Las dos filas representan conceptos o estados financieros distintos.",
    "reason.expected_movement_absent": "No se observó el valor o movimiento neto esperado; compruebe la fuente.",
    "reason.expected_movement_present": "Se observó el valor o movimiento neto esperado; no demuestra transacciones individuales.",
    "reason.metric_unsupported": "Este concepto no tiene una definición de comparación compatible.",
    "reason.movement_evidence_missing_or_incompatible": "Falta evidencia comparable para verificar el movimiento esperado.",
    "reason.periods_or_values_incompatible": "Los períodos o valores no son comparables.",
    "reason.source_ambiguous": "Hay varias filas candidatas; revise el mapeo de la fuente.",
    "reason.source_incompatible": "La fila no está validada o tiene moneda, ámbito o unidad incompatibles.",
    "reason.source_missing": "La fila de origen no está disponible.",
    "reason.target_month_mismatch": "La fila actual no corresponde al mes elegido.",
    "reason.task_unconfirmed": "Confirme la tarea con evidencia válida del mes elegido.",
    "reason.task_user_confirmed": "Tarea declarada completada por el usuario, con evidencia del mes.",
    "reason.ytd_cutoff_mismatch": "El inicio fiscal o el corte acumulado no corresponde a la política elegida.",
    "REC-BS-001": "Balance",
    "REC-CF-001": "Movimientos de efectivo",
    "REC-PL-001": "Puente del resultado",
    "REC-EBITDA-001": "Puente de EBITDA",
    "REC-DEBT-001": "Movimientos de deuda",
    "REC-LEASE-001": "Movimientos de arrendamientos",
    "REC-GROWTH-001": "Puente del crecimiento publicado",
    "REC-DEBT-RATE-001": "Composición de deuda por tipo contractual",
    "REC-DEBT-MATURITY-001": "Distribución de vencimientos de deuda",
    "REC-CAPEX-001": "Clasificación del capex pagado",
    "REC-CAPEX-002": "Capex frente a adquisiciones en flujos de caja",
    "REC-WC-CLOSING-001": "Circulante: componentes finales",
    "REC-WC-OPENING-001": "Circulante: componentes iniciales",
    "REC-WC-CHANGE-001": "Circulante: variación de saldos",
    "category.statement": "Datos del estado financiero",
    "category.reconciliation": "Conciliación",
    "category.comparison": "Comparación",
    "category.movement": "Movimiento esperado",
    "category.task": "Confirmación de tarea",
    "category.quality": "Calidad de datos",
    "result_label": "Checklist de cierre mensual"
  },
  "fr": {
    "incomplete": "Préparation incomplète",
    "review_required": "Variations ou avertissements à examiner",
    "ready_for_review": "Préparation prête à examiner",
    "limits": "Clôture non attestée. Aucune écriture comptable. Performance non évaluée.",
    "saved": "Préparation enregistrée ; cette lecture a vérifié l’intégrité et recalculé les résultats.",
    "unsaved": "Préparation pas encore enregistrée dans l’historique.",
    "source": "Source",
    "source_policy": "Identification de la source et de la politique",
    "source_hash": "SHA-256 de la source",
    "policy": "Politique",
    "version": "version",
    "pending": "En attente",
    "review": "À examiner",
    "satisfied": "Vérifié par le moteur",
    "confirmed_by_user": "Confirmé par l’utilisateur",
    "income_statement": "Compte de résultat",
    "balance_sheet": "Bilan",
    "cash_flow_statement": "Flux de trésorerie",
    "evidence": "Preuves et identification du contrôle",
    "rounding": "Les valeurs sont affichées avec deux décimales. Le snapshot conserve les valeurs complètes ; les seuils sont vérifiés sur les valeurs exactes.",
    "month_on_month": "Mois précédent",
    "month_year_on_year": "Même mois de l’année précédente",
    "ytd_year_on_year": "Cumul annuel comparé à l’année précédente",
    "delta": "variation",
    "percentage_unavailable": "pourcentage indisponible (base nulle)",
    "threshold_reached": "seuil de révision atteint",
    "below_threshold": "sous les seuils déclarés",
    "missing": "Données et contrôles manquants",
    "fallback_reason": "Examinez les lacunes et avertissements de la validation financière source.",
    "saved_download": "↓ Snapshot enregistré avec preuves",
    "csv_download": "↓ Liste de contrôle CSV pour Excel",
    "reopened": "Préparation enregistrée rouverte avec preuves vérifiées.",
    "language_fallback": "Langue enregistrée non prise en charge. Libellés en anglais ; preuves enregistrées inchangées.",
    "reason.statement_present": "Des données de l’état financier existent pour le mois et le périmètre choisis.",
    "reason.statement_missing": "Des données de cet état financier manquent pour le mois et le périmètre choisis.",
    "reason.reconciliation_pass": "L’identité comptable a été rapprochée.",
    "reason.reconciliation_rounding_pass": "L’identité a été rapprochée dans la tolérance d’arrondi déclarée.",
    "reason.reconciliation_warning": "Le rapprochement comporte un avertissement à examiner.",
    "reason.reconciliation_critical": "Le rapprochement présente un écart critique.",
    "reason.reconciliation_blocking": "Le rapprochement est bloqué.",
    "reason.reconciliation_missing_or_ambiguous": "Il manque un rapprochement unique pour ce mois, cette devise et ce périmètre.",
    "reason.comparable": "Les périodes sont comparables et la variation est sous les seuils déclarés.",
    "reason.comparison_not_configured": "Associez les lignes sources de cette comparaison requise.",
    "reason.material_variation": "La variation atteint un seuil déclaré et doit être examinée.",
    "reason.zero_comparison_base": "La base de comparaison est nulle : la variation absolue est disponible, mais le pourcentage ne peut pas être calculé.",
    "reason.calculation_blocked": "Résolvez les blocages de validation financière avant de calculer les comparaisons.",
    "reason.concept_mismatch": "Les deux lignes représentent des concepts ou des états financiers différents.",
    "reason.expected_movement_absent": "La valeur ou le mouvement net attendu n’a pas été observé ; vérifiez la source.",
    "reason.expected_movement_present": "La valeur ou le mouvement net attendu a été observé ; cela ne prouve pas des transactions individuelles.",
    "reason.metric_unsupported": "Ce concept n’a pas de définition de comparaison prise en charge.",
    "reason.movement_evidence_missing_or_incompatible": "Il manque des preuves comparables pour vérifier le mouvement attendu.",
    "reason.periods_or_values_incompatible": "Les périodes ou valeurs ne sont pas comparables.",
    "reason.source_ambiguous": "Plusieurs lignes sources sont candidates ; examinez leur correspondance.",
    "reason.source_incompatible": "La ligne n’est pas validée ou sa devise, son périmètre ou son unité est incompatible.",
    "reason.source_missing": "La ligne source est indisponible.",
    "reason.target_month_mismatch": "La ligne actuelle ne correspond pas au mois choisi.",
    "reason.task_unconfirmed": "Confirmez la tâche avec des preuves valides pour le mois choisi.",
    "reason.task_user_confirmed": "Tâche déclarée réalisée par l’utilisateur, avec des preuves du mois.",
    "reason.ytd_cutoff_mismatch": "Le début de l’exercice ou la date de clôture du cumul ne correspond pas à la politique choisie.",
    "REC-BS-001": "Bilan",
    "REC-CF-001": "Mouvements de trésorerie",
    "REC-PL-001": "Rapprochement du résultat",
    "REC-EBITDA-001": "Rapprochement de l’EBITDA",
    "REC-DEBT-001": "Mouvements de dette",
    "REC-LEASE-001": "Mouvements des locations",
    "REC-GROWTH-001": "Rapprochement de la croissance publiée",
    "REC-DEBT-RATE-001": "Composition de dette par taux contractuel",
    "REC-DEBT-MATURITY-001": "Répartition des échéances de dette",
    "REC-CAPEX-001": "Classification du capex payé",
    "REC-CAPEX-002": "Capex et acquisitions dans les flux de trésorerie",
    "REC-WC-CLOSING-001": "Fonds de roulement : composantes finales",
    "REC-WC-OPENING-001": "Fonds de roulement : composantes initiales",
    "REC-WC-CHANGE-001": "Fonds de roulement : variation des soldes",
    "category.statement": "Données de l’état financier",
    "category.reconciliation": "Rapprochement",
    "category.comparison": "Comparaison",
    "category.movement": "Mouvement attendu",
    "category.task": "Confirmation de tâche",
    "category.quality": "Qualité des données",
    "result_label": "Liste de contrôle de clôture mensuelle"
  }
};
  function closeCopy(language) { return closeMessages[Object.hasOwn(closeMessages, language) ? language : "en"]; }
  function closeReason(code, language) { const text = closeCopy(language), key = "reason." + String(code).replace(/^month_end\./, ""); return Object.hasOwn(text, key) ? text[key] : text.fallback_reason + " (" + String(code) + ")"; }
  function closeLanguage(data) { return data.result.language || data.upload_choices?.report_language || "pt"; }
  function renderClose(data, root = el("fv2-close-result")) {
    const result = data.result, language = closeLanguage(data), text = closeCopy(language);
    root.replaceChildren(); root.lang = Object.hasOwn(closeMessages, language) ? language : "en";
    root.setAttribute("aria-label", text.result_label);
    if (!Object.hasOwn(closeMessages, language)) root.append(node("p", text.language_fallback + " (" + language + ")"));
    root.append(node("h3", Object.hasOwn(text, result.preparation_readiness) ? text[result.preparation_readiness] : result.preparation_readiness), node("p", text.limits + " " + (data.persisted ? text.saved : text.unsaved)), node("p", `${text.source}: ${data.filename}`));
    const source = node("details"); source.append(node("summary", text.source_policy), node("p", `${text.source_hash}: ${data.source_hash}`), node("p", `${text.policy}: ${result.policy.policy_id} · ${text.version} ${result.policy.version} · ${result.policy.materiality_basis}`)); root.append(source);
    const list = node("ul"); result.checklist.forEach(item => {
      const key = item.item_id.slice(item.category.length + 1);
      const title = item.category === "statement" || item.category === "reconciliation" ? (Object.hasOwn(text, key) ? text[key] : item.title) : item.category === "quality" ? text["category.quality"] : item.title;
      const row = node("li"); row.append(node("strong", `${Object.hasOwn(text, item.status) ? text[item.status] : item.status} · ${title}`), node("p", closeReason(item.reason_code, language)));
      const details = node("details"); details.append(node("summary", text.evidence), node("p", `${item.title} · ${item.item_id} · ${item.reason_code}`));
      item.evidence.forEach(e => details.append(node("p", typeof e === "string" ? e : `${e.location} · SHA-256: ${e.content_hash}`))); row.append(details); list.append(row);
    }); root.append(list);
    root.append(node("p", text.rounding));
    result.comparisons.forEach(c => { const display = data.presentation?.comparisons?.find(p => p.key === c.key); root.append(node("p", `${Object.hasOwn(text, c.relation) ? text[c.relation] : c.relation} · ${c.key}: ${display?.current_value || c.current_value} / ${display?.prior_value || c.prior_value}; ${text.delta} ${display?.delta || c.delta}; ${c.percentage === null ? text.percentage_unavailable : display?.percentage || c.percentage + "%"}; ${c.material ? text.threshold_reached : text.below_threshold}`)); });
    if (result.missing_data_requests.length) { root.append(node("h4", text.missing)); result.missing_data_requests.forEach(gap => root.append(node("p", closeReason(gap.reason_code, language)))); }
  }

  function download(blob, name) { const url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  function template() {
    const text = "line;amount;period;statement_type;scope;currency;scale;comparison_basis\nreceita;1000;2026;income_statement;consolidated;EUR;raw;current\nresultado líquido;100;2026;income_statement;consolidated;EUR;raw;current\ntotal do ativo;100;2026;balance_sheet;consolidated;EUR;raw;current\ntotal do passivo;60;2026;balance_sheet;consolidated;EUR;raw;current\ncapital próprio;40;2026;balance_sheet;consolidated;EUR;raw;current\n";
    download(new Blob(["\ufeff", text], { type: "text/csv;charset=utf-8" }), "saleslab-financial-template.csv");
  }
  function reset() { closeCatalogEpoch++; closeCatalogRules = []; closeCatalogState = "waiting"; closeCatalogLimit = 20; closeCatalogSignature = null; closeCatalogLabels.clear(); closeHistoryEpoch++; closeSaveSupported = false; closeHistorySupported = false; hasMonthlySnapshots = false; clearSourceReviews(); monthEndSupported = false; clearCloseResult(); closeRows = []; clearImport(); tabularSupported = false; pdfSupported = false; enabled = false; hasReports = false; planningVersion = null; normalizationVersion = null; parenthesesSupported = false; fiscalCalendarSupported = false; sourceMetadataSupported = false; checkedToken = null; reportOnly = false; invalidate(); clearTimeout(pollTimer); clearTimeout(capabilityTimer); if (reportURL) URL.revokeObjectURL(reportURL); reportURL = null; if (el("fv2")) el("fv2").remove(); }
  function moduleChanged() { reportOnly = false; if (el("fv2-result")) el("fv2-result").hidden = true; sync(); login(); }
  return { login, sync, reset, open, moduleChanged, active: () => enabled && currentReportType === "financial" };
})();
