/* Browser integration against controlled API responses; backend integrity is
 * verified separately by the PostgreSQL suite. No live accounts or providers. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const sourceRef = (concept, period = "2026") => fixture.rows.find(row => row.concept_id === concept && row.period?.label === period).location;
const capexFixture = JSON.parse(fs.readFileSync(process.argv[9], "utf8"));
let capexMode=false, capexOptions=[];
const roicFixture = JSON.parse(fs.readFileSync(process.argv[10],"utf8"));
let roicMode=false, roicOptions=[];
const wcFixture=JSON.parse(fs.readFileSync(process.argv[11],"utf8"));
let wcMode=false,wcOptions=[];
const debtFixture = JSON.parse(fs.readFileSync(process.argv[8], "utf8"));
let debtMode=false, debtOptions=[];
const growthFixture = JSON.parse(fs.readFileSync(process.argv[7], "utf8"));
let growthMode=false, growthOptions=[];
const monthEndFixture = JSON.parse(fs.readFileSync(process.argv[6], "utf8"));
let monthEndMode = false, monthEndSupported = false, monthEndRequests = [];
let closeCatalogMode="server", closeCatalogLimit=20, closeCatalogReversed=false, closeCatalogUnsafeLabel=false;
function catalogResponse() { const value=structuredClone(monthEndFixture.reconciliation_catalog); value.max_required_reconciliations=closeCatalogLimit; if(closeCatalogReversed)value.rules.reverse(); if(closeCatalogUnsafeLabel)value.rules[0].labels.pt='<img src=x onerror="window.catalogExecuted=true">Rule label'; if(closeCatalogMode==="duplicate")value.rules.push(structuredClone(value.rules[0])); return value; }
let monthlyPresentationLanguage=null;
let monthlyCsvSupported=true, monthlyCsvFailures=1;
let monthlySnapshotSupported=false, monthlySaved=null, monthlySaveRequests=[], monthlySaveFailures=1, hidePaidHistory=false;
const publicSample = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const importFixture = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
let importMode = false, tabularSupported = true, inspectionFailures = 0;
const importOptions = [];
const pdfFixture = JSON.parse(fs.readFileSync(process.argv[5], "utf8"));
let pdfMode = false, pdfSupported = false;
const pdfOptions = [];
let generation = 0, pending = true, featureEnabled = true, jobMissing = false;
let capabilityFailures = 0, htmlFailures = 0;
let previewBlocked = false;
let includeValidationSummary = true, includeReconciliationEvidence = true, reconciliationUnavailable = false;
let includeCalculationEvidence = true;
let includeMetricAvailability = true, previewFailures = 0;
let planningVersions = ["1", "2", "3", "4", "5", "6", "7", "8"], normalizationVersions = ["1", "2"];
let parenthesesSupported = true, fiscalCalendarSupported = true, sourceMetadataSupported = true, includeRowGuidance = true;
const previewOptions = [];
const runId = "11111111-1111-4111-8111-111111111111";
const jobId = "22222222-2222-4222-8222-222222222222";
const history = [{ created_at: "2026-10-22T12:00:00Z", period: '<img src=x onerror="window.historyExecuted=true">January 2026', data_quality: "partial", result: { schema: "financial-v2", run_id: runId, kpis: [] } }];
const json = (res, value, code = 200) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(value)); };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/" || url.pathname === "/index.html") {
    const html = fs.readFileSync(path.join(root, "index.html"), "utf8").replace('"https://web-production-74559.up.railway.app"', 'window.location.origin');
    res.writeHead(200, { "Content-Type": "text/html" }); res.end(html); return;
  }
  if (url.pathname === "/financial-v2.js") { res.writeHead(200, { "Content-Type": "text/javascript" }); res.end(fs.readFileSync(path.join(root, "financial-v2.js"))); return; }
  if (url.pathname === "/auth/session") { json(res, { token: "synthetic-session", plan: "free", role: "owner", email: "owner@example.test", chatbot: true, remaining: 10 }); return; }
  if (url.pathname === "/financial-v2/capabilities") { if (capabilityFailures > 0) { capabilityFailures--; json(res, { detail: "Temporary capability failure" }, 503); return; } json(res, { enabled: featureEnabled, ...(monthEndSupported ? {month_end_preview_versions:["1"]} : {}), ...(monthEndSupported && closeCatalogMode!=="legacy" ? {month_end_reconciliation_catalog:catalogResponse()} : {}), ...(pdfSupported ? { pdf_import_versions: ["1"] } : {}), ...(tabularSupported ? { tabular_import_versions: ["1"] } : {}), reports_available: !hidePaidHistory && generation > 0, ...(monthlySnapshotSupported ? {...(monthlyCsvSupported ? {month_end_checklist_csv_versions:["1"]} : {}),month_end_snapshot_versions:["1"],month_end_save_versions:monthEndSupported ? ["1"] : [],month_end_snapshots_available:monthlySaved!==null} : {}), ...(planningVersions ? { planning_versions: planningVersions } : {}), ...(normalizationVersions ? { normalization_versions: normalizationVersions } : {}), ...(parenthesesSupported ? { accounting_parentheses_supported: true } : {}), ...(fiscalCalendarSupported ? { fiscal_calendar_supported: true } : {}), ...(sourceMetadataSupported ? { source_metadata_supported: true, source_currencies: ["EUR", "GBP", "USD"] } : {}) }); return; }
  if (url.pathname === "/financial-v2/month-end/snapshots") {
    if (req.method === "GET") { json(res,{snapshots:monthlySaved ? [{snapshot_id:monthlySaved.snapshot_id,saved_at:monthlySaved.saved_at,target_month:monthlySaved.close_choices.target_month,policy_id:monthlySaved.close_choices.policy_id,filename:'<img src=x onerror="window.monthlyHistoryExecuted=true">Statements'}] : [],next_cursor:null});return; }
    let body="";for await(const chunk of req) body+=chunk;
    const save=JSON.parse(body.match(/name="save"\r\n\r\n([^\r]+)\r\n/)[1]); const close=JSON.parse(body.match(/name="month_end"\r\n\r\n([^\r]+)\r\n/)[1]); monthlySaveRequests.push(save);
    assert.equal(save.binding_hash,monthEndFixture.result.binding_hash);assert.deepEqual(close,monthEndFixture.result.close_choices);
    if(monthlySaveRequests.length>1) assert.equal(save.attempt_key,monthlySaveRequests[0].attempt_key);
    monthlySaved=structuredClone(monthEndFixture.saved_result);
    if(monthlySaveFailures-->0){json(res,{detail:"A resposta de gravação foi interrompida; pode repetir."},503);return;}
    json(res,monthlySaved,200);return;
  }
  if (url.pathname.endsWith("/checklist.csv")) { if(monthlyCsvFailures-->0){json(res,{detail:"CSV temporariamente indisponível"},503);return;} res.writeHead(200, {"Content-Type":"text/csv"});res.end("\ufeffexport_schema;status\r\nfinancial-month-end-checklist-csv-v1;pending\r\n");return; }
  if (url.pathname.startsWith("/financial-v2/month-end/snapshots/")) {json(res,monthlyPresentationLanguage ? monthEndFixture.localized_saved_results[monthlyPresentationLanguage] : monthlySaved);return;}
  if (url.pathname === "/financial-v2/history") { json(res, { reports: history.map(h => ({ ...h, run_id: runId })), next_cursor: null }); return; }
  if (url.pathname === "/financial-v2/sample") { json(res, publicSample); return; }
  if (url.pathname.startsWith("/history/")) { json(res, { history: url.pathname.endsWith("financial") ? history : [] }); return; }
  if (url.pathname === "/financial-v2/inspect") {
    if (growthMode) {json(res,growthFixture.inspection);return;}
    if (inspectionFailures > 0) { inspectionFailures--; json(res, { detail: "Synthetic inspection failure" }, 422); return; }
    if (pdfMode) { let body = ""; for await (const chunk of req) body += chunk; const layout = JSON.parse(body.match(/name="pdf_layout"\r\n\r\n([^\r]+)\r\n/)[1]); json(res, layout.pages.every(p => p.cuts.length === 1 && p.cuts[0] === 40) ? pdfFixture.inspection : pdfFixture.raw_inspection); return; }
    const value = structuredClone(importFixture.inspection);
    value.sheets[2].name = '<img src=x onerror="window.importExecuted=true">Notas';
    json(res, value); return;
  }
  if (url.pathname === "/financial-v2/month-end/preview") {
    let body = ""; for await (const chunk of req) body += chunk;
    const selected = JSON.parse(body.match(/name="month_end"\r\n\r\n([^\r]+)\r\n/)[1]);
    monthEndRequests.push(selected);
    assert.equal(selected.preview_hash, monthEndFixture.preview.preview_hash);
    assert.equal(selected.comparisons.length,3); assert.equal(selected.movements.length,1);
    assert.equal(selected.comparisons[0].current,"csv:2"); assert.equal(selected.comparisons[0].prior,"csv:7");
    assert.equal(selected.fiscal_year_start_month,1); assert.equal(selected.absolute_variation_threshold,"10000");
    const confirmed = selected.confirmations.some(c=>c.status==="complete" && c.evidence.includes("csv:2"));
    const value = structuredClone(selected.required_reconciliations.includes("REC-WC-CLOSING-001") ? monthEndFixture.catalog_gap_result : confirmed ? monthEndFixture.result : monthEndFixture.incomplete_result);
    value.result.checklist[0].title='<img src=x onerror="window.closeExecuted=true">Source checklist';
    json(res,value); return;
  }
  if (url.pathname === "/financial-v2/preview") {
    if (previewFailures > 0) { previewFailures--; json(res, { detail: "Temporary validation failure" }, 503); return; }
    let body = ""; for await (const chunk of req) body += chunk;
    if(wcMode) {
      const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]);wcOptions.push(options);
      assert.equal(options.planning_version,"13");assert.equal(options.normalization_version,"2");
      if(Object.keys(options.mappings).length!==Object.keys(wcFixture.choices.mappings).length){json(res,Object.keys(options.mappings).length===1 ? wcFixture.partial_preview : wcFixture.unmapped_preview);return;}
      assert.deepEqual(options.mappings,wcFixture.choices.mappings);
      const defs=options.working_capital_definitions || {};
      if(Object.entries(wcFixture.choices.working_capital_definitions).every(([loc,quote])=>defs[loc]===quote))json(res,wcFixture.preview);
      else {const value=structuredClone(wcFixture.review);value.rows[0].line+=' <img src=x onerror="window.wcExecuted=true">';json(res,value);}
      return;
    }
    if (roicMode) {
      const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); roicOptions.push(options);
      assert.equal(options.planning_version,"12"); assert.equal(options.normalization_version,"2");
      if (Object.keys(options.mappings).length!==Object.keys(roicFixture.choices.mappings).length) {json(res,Object.keys(options.mappings).length===1 ? roicFixture.partial_preview : roicFixture.unmapped_preview);return;}
      assert.deepEqual(options.mappings,roicFixture.choices.mappings);
      if (options.roic_sources) {assert.deepEqual(options.roic_sources,roicFixture.choices.roic_sources);json(res,roicFixture.preview);}
      else {const value=structuredClone(roicFixture.review);value.rows[5].line+=' <img src=x onerror="window.roicExecuted=true">';json(res,value);}
      return;
    }
    if (capexMode) {
      const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); capexOptions.push(options);
      assert.equal(options.planning_version,"11"); assert.equal(options.normalization_version,"2");
      if (Object.keys(options.mappings).length!==Object.keys(capexFixture.choices.mappings).length) {json(res,capexFixture.unmapped_preview);return;}
      assert.deepEqual(options.mappings,capexFixture.choices.mappings);
      const defs=options.capex_definitions || {};
      if (Object.entries(capexFixture.choices.capex_definitions).every(([loc,quote])=>defs[loc]===quote)) json(res,capexFixture.preview);
      else {const value=structuredClone(capexFixture.review);value.rows[0].line+=' <img src=x onerror="window.capexExecuted=true">';json(res,value);}
      return;
    }
    if (debtMode) {
      const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); debtOptions.push(options);
      assert.equal(options.planning_version,"10"); assert.equal(options.normalization_version,"2");
      if (Object.keys(options.mappings).length!==Object.keys(debtFixture.choices.mappings).length) {json(res,debtFixture.unmapped_preview);return;}
      assert.deepEqual(options.mappings,debtFixture.choices.mappings);
      const defs=options.debt_definitions || {};
      if (Object.entries(debtFixture.choices.debt_definitions).every(([loc,quote])=>defs[loc]===quote)) json(res,debtFixture.preview);
      else {const value=structuredClone(debtFixture.review);value.rows[0].line+=' <img src=x onerror="window.debtExecuted=true">';json(res,value);}
      return;
    }
    if (growthMode) {
      const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); growthOptions.push(options);
      assert.equal(options.planning_version,"9"); assert.equal(options.normalization_version,"2");
      const view=options.tabular_import ? growthFixture.prepared : growthFixture;
      if (Object.keys(options.mappings).length!==Object.keys(view.choices.mappings).length) {json(res,view.unmapped_preview);return;}
      assert.deepEqual(options.mappings,view.choices.mappings);
      const defs=options.growth_definitions || {};
      if (Object.entries(view.choices.growth_definitions).every(([loc,quote])=>defs[loc]===quote)) json(res,view.preview);
      else {const value=structuredClone(view.review);value.rows[0].line+=' <img src=x onerror="window.growthExecuted=true">';json(res,value);}
      return;
    }
    if (monthEndMode) {json(res,monthEndFixture.preview);return;}
    previewOptions.push(JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]));
    if (pdfMode) { const options = previewOptions.at(-1); pdfOptions.push(options); const selected = options.pdf_import; const refs = pdfFixture.review.review_rows.map(r => r.location); if (selected?.review_hash === pdfFixture.review.review_hash && selected.confirmed_rows.length === refs.length && refs.every(ref => selected.confirmed_rows.includes(ref))) json(res, pdfFixture.preview); else { const review = structuredClone(pdfFixture.review); review.review_rows[0].extracted_text = '<img src=x onerror="window.pdfExecuted=true"> original PDF text'; json(res, review); } return; }
    if (importMode) { importOptions.push(previewOptions.at(-1)); json(res, importFixture.preview); return; }
    const value = structuredClone(fixture);
    const originalRow = value.rows.find(row => row.location === "csv:2");
    originalRow.original_line = '<img src=x onerror="window.uploadExecuted=true">';
    originalRow.original_input = { ...originalRow.original_input, amount: '<img src=x onerror="window.originalExecuted=true">(100)', currency: "EUR", scale: "raw", period: "2026" };
    originalRow.validation_guidance = [{ code: "number.parentheses_sign_ambiguous", field: "value", message: '<img src=x onerror="window.guidanceExecuted=true">Confirme os parênteses na fonte.' }];
    if (!includeRowGuidance) value.rows.forEach(row => { delete row.original_input; delete row.validation_guidance; });
    const injectedMargin = value.calculated_metrics.find(m => m.metric_id === "net_margin_pct");
    injectedMargin.label = '<img src=x onerror="window.metricExecuted=true">Receita';
    injectedMargin.calculation_evidence.inputs[0].sources[0].line = '<img src=x onerror="window.evidenceExecuted=true">Rubrica original';
    if (!includeCalculationEvidence) value.calculated_metrics.forEach(m => delete m.calculation_evidence);
    value.validation_summary.capabilities[0].requirements[0].label = '<img src=x onerror="window.validationExecuted=true">Dado necessário';
    const growthGap = value.metric_availability.find(m => m.metric_id === "revenue_growth_reported_pct");
    growthGap.requirements[0].label = '<img src=x onerror="window.gapExecuted=true">Receita homóloga';
    if (previewBlocked) {
      value.ready = false;
      const margin = value.calculated_metrics.find(m => m.metric_id === "net_margin_pct");
      Object.assign(margin, { status: "unavailable", display_value: null, exact_value: null, calculation_evidence: null });
      value.validation_summary.reconciliations[0].status = "blocking";
      value.metric_availability.find(m => m.metric_id === "net_margin_pct").status = "blocked";
    }
    const cashCheck = value.validation_summary.reconciliations.find(check => check.rule_id === "REC-CF-001");
    if (cashCheck.reconciliation_evidence) {
      const fx = cashCheck.reconciliation_evidence.inputs.find(input => input.role === "fx_other");
      fx.sources[0].line = '<img src=x onerror="window.reconciliationExecuted=true">cash foreign exchange and other movements';
    }
    if (previewBlocked) value.validation_summary.reconciliations[0].reconciliation_evidence = null;
    if (reconciliationUnavailable) { cashCheck.status = "not_evaluated"; cashCheck.reconciliation_evidence = null; }
    if (!includeReconciliationEvidence) value.validation_summary.reconciliations.forEach(check => delete check.reconciliation_evidence);
    if (!includeValidationSummary) delete value.validation_summary;
    if (!includeMetricAvailability) delete value.metric_availability;
    json(res, value); return;
  }
  if (url.pathname === "/financial-v2/generate") {
    let body = ""; for await (const chunk of req) body += chunk;
    if(wcMode) {const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]);assert.equal(options.planning_version,"13");assert.equal(options.preview_hash,wcFixture.preview.preview_hash);assert.deepEqual(options.working_capital_definitions,wcFixture.choices.working_capital_definitions);generation++;json(res,{job_id:jobId,status:"pending"},202);return;}
    if (roicMode) { const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); assert.equal(options.planning_version,"12");assert.equal(options.preview_hash,roicFixture.preview.preview_hash);assert.deepEqual(options.roic_sources,roicFixture.choices.roic_sources);generation++;json(res,{job_id:jobId,status:"pending"},202);return; }
    if (capexMode) { const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); assert.equal(options.planning_version,"11"); assert.equal(options.preview_hash,capexFixture.preview.preview_hash); assert.deepEqual(options.capex_definitions,capexFixture.choices.capex_definitions); generation++; json(res,{job_id:jobId,status:"pending"},202);return; }
    if (debtMode) { const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); assert.equal(options.planning_version,"10"); assert.equal(options.preview_hash,debtFixture.preview.preview_hash); assert.deepEqual(options.debt_definitions,debtFixture.choices.debt_definitions); generation++; json(res,{job_id:jobId,status:"pending"},202);return; }
    if (growthMode) { const options=JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); assert.equal(options.planning_version,"9"); const view=options.tabular_import ? growthFixture.prepared : growthFixture; assert.equal(options.preview_hash,view.preview.preview_hash); assert.deepEqual(options.growth_definitions,view.choices.growth_definitions); generation++;json(res,{job_id:jobId,status:"pending"},202);return; }
    if (pdfMode) { const options = JSON.parse(body.match(/name="options"\r\n\r\n([^\r]+)\r\n/)[1]); assert.equal(options.preview_hash,pdfFixture.preview.preview_hash); assert.equal(options.pdf_import.review_hash,pdfFixture.review.review_hash); assert.equal(options.pdf_import.confirmed_rows.length,5); assert.equal(Object.hasOwn(options,"tabular_import"),false); generation++; json(res,{job_id:jobId,status:"pending"},202); return; }
    assert.ok(body.includes('"preview_hash":"' + fixture.preview_hash + '"'));
    assert.ok(body.includes('"target_period":"2026"'));
    assert.ok(body.includes('"planning_version":"8"'));
    assert.ok(body.includes('"normalization_version":"2"'));
    assert.ok(body.includes('"fiscal_year_end_month":6'));
    assert.ok(body.includes('"source_currency":"EUR"'));
    assert.ok(body.includes('"source_scale":"raw"'));
    generation++; json(res, { job_id: jobId, status: "pending" }, 202); return;
  }
  if (url.pathname === "/financial-v2/jobs/" + jobId) {
    if (jobMissing) { json(res, { detail: "The upload job is unavailable." }, 404); return; }
    json(res, pending ? { status: "pending" } : { status: "completed", run_id: runId, analyses_remaining: 9 }); return;
  }
  if (url.pathname.endsWith("/html")) { if (htmlFailures > 0) { htmlFailures--; json(res, { detail: "Report temporarily unavailable." }, 503); return; } res.writeHead(200, { "Content-Type": "text/html" }); res.end("<!doctype html><title>Verified report</title><h1>Receita verificada</h1><p>1 000 EUR</p>"); return; }
  if (url.pathname.endsWith("/pdf")) { res.writeHead(200, { "Content-Type": "application/pdf" }); res.end("%PDF-1.7\nverified artifact"); return; }
  res.writeHead(404); res.end();
});

(async () => {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, acceptDownloads: true });
    await context.route("**/*", route => route.request().url().startsWith(origin) || route.request().url().startsWith("blob:") ? route.continue() : route.abort());
    const page = await context.newPage(); page.on("pageerror", error => errors.push(error.message));
    async function reloadSignedIn() { await page.reload(); await page.waitForFunction(() => document.getElementById("app").classList.contains("visible")); }
    await page.addInitScript(() => { if (window === window.top) localStorage.setItem("sl_token", "synthetic-session"); });
    await page.goto(origin);
    await page.waitForFunction(() => document.getElementById("app").classList.contains("visible"));
    await page.click("#mod-financial");
    await page.waitForFunction(() => !document.getElementById("fv2").hidden);
    assert.equal(await page.locator("#inputCard").isVisible(), false);
    assert.equal(await page.locator("#fv2-parentheses-field").isVisible(), true);
    assert.equal(await page.locator("#fv2-fiscal-field").isVisible(), true);
    assert.equal(await page.locator("#fv2-source-metadata").isVisible(), true);
    assert.equal(await page.locator("#fv2-source-currency option").count(), 4);
    await page.selectOption("#fv2-source-currency", "EUR");
    await page.selectOption("#fv2-source-scale", "raw");
    await page.selectOption("#fv2-fiscal-month", "6");
    await page.selectOption("#fv2-parentheses", "true");
    await page.click("#fv2-sample");
    await page.waitForFunction(() => document.getElementById("fv2-file").files[0]?.name === "jm-2024.csv");
    assert.equal(await page.locator("#fv2-language").inputValue(), "en");
    assert.equal(await page.locator("#fv2-locale").inputValue(), "en-GB");
    assert.equal(await page.locator("#fv2-parentheses").inputValue(), "");
    assert.equal(await page.locator("#fv2-fiscal-month").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-currency").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-scale").inputValue(), "");
    assert.equal(await page.locator("#fv2-sample-source a").count(), 2);
    assert.equal(await page.locator("#fv2-preview select").count(), 6);
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    assert.equal(generation, 0);
    previewBlocked = true;
    await page.click("#fv2-validate");
    await page.waitForFunction(() => document.getElementById("fv2-status").textContent.includes("Validação incompleta"));
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    const rowDetails = page.locator('#fv2-preview details[data-row-guidance="csv:2"]');
    await rowDetails.locator("summary").click();
    assert.ok((await rowDetails.textContent()).includes("Confirme os parênteses na fonte."));
    assert.ok((await rowDetails.textContent()).includes("number.parentheses_sign_ambiguous"));
    assert.ok((await page.locator("#fv2-preview").textContent()).includes("Valor original:"));
    assert.equal(await page.locator("#fv2-preview img").count(), 0);
    assert.equal(await page.evaluate(() => Boolean(window.originalExecuted || window.guidanceExecuted)), false);
    assert.ok((await page.locator("#fv2-metrics").textContent()).includes("Indisponível"));
    assert.equal(await page.locator('#fv2-metrics details[data-calculation="net_margin"]').count(), 0);
    assert.ok((await page.locator("#fv2-validation").textContent()).includes("Bloqueada"));
    assert.ok((await page.locator("#fv2-gaps").textContent()).includes("Cálculo bloqueado"));
    previewBlocked = false;
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "8");
    assert.equal(previewOptions.at(-1).normalization_version, "2");
    assert.ok((await page.locator("#fv2-metrics").textContent()).includes("Margem EBIT"));
    assert.equal(previewOptions.at(-1).cash_rounding_tolerance, "0");
    assert.ok((await page.locator("#fv2-validation").textContent()).includes("Reconciliação de caixa"));
    await page.locator("#fv2-cash-tolerance").fill("1");
    await page.locator("#fv2-cash-tolerance").dispatchEvent("change");
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).cash_rounding_tolerance, "1");
    const cashDetails = page.locator('#fv2-validation details[data-reconciliation="REC-CF-001"]');
    const balanceDetails = page.locator('#fv2-validation details[data-reconciliation="REC-BS-001"]');
    await cashDetails.locator("summary").click();
    await balanceDetails.locator("summary").click();
    assert.ok((await cashDetails.textContent()).includes("opening_cash+cfo+cfi+cff+fx_other=closing_cash"));
    assert.ok((await cashDetails.textContent()).includes("Tolerância absoluta de arredondamento: 0 EUR"));
    assert.ok((await cashDetails.textContent()).includes(sourceRef("cash_and_equivalents", "2025")));
    assert.ok((await cashDetails.textContent()).includes(sourceRef("cash_and_equivalents")));
    assert.ok((await cashDetails.textContent()).includes(sourceRef("cash_fx_other")));
    assert.ok((await cashDetails.textContent()).includes("cálculo rastreável"));
    assert.equal(await cashDetails.locator("tr").count(), 7);
    assert.ok((await balanceDetails.textContent()).includes("liabilities+equity=assets"));
    assert.equal(await balanceDetails.locator("tr").count(), 4);
    assert.equal(await cashDetails.locator("img").count(), 0);
    assert.equal(await page.evaluate(() => window.reconciliationExecuted), undefined);
    assert.equal(previewOptions.at(-1).profitability_rounding_tolerance, "0");
    await page.locator("#fv2-profitability-tolerance").fill("1");
    await page.locator("#fv2-profitability-tolerance").dispatchEvent("change");
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).profitability_rounding_tolerance, "1");
    const profitDetails = page.locator('#fv2-validation details[data-reconciliation="REC-PL-001"]');
    const ebitdaDetails = page.locator('#fv2-validation details[data-reconciliation="REC-EBITDA-001"]');
    await profitDetails.locator("summary").click();
    await ebitdaDetails.locator("summary").click();
    assert.ok((await profitDetails.textContent()).includes("revenue-costs+other_operating=ebit"));
    assert.ok((await profitDetails.textContent()).includes("ebit-finance-tax=net_income"));
    assert.ok((await ebitdaDetails.textContent()).includes("ebit+da=statutory_ebitda"));
    assert.ok((await ebitdaDetails.textContent()).includes("statutory_ebitda+adjustments=adjusted_ebitda"));
    assert.ok((await ebitdaDetails.textContent()).includes(sourceRef("reported_statutory_ebitda")));
    const statutoryMargin = page.locator('#fv2-metrics details[data-calculation="ebitda_margin_statutory_pct"]');
    const adjustedMargin = page.locator('#fv2-metrics details[data-calculation="ebitda_margin_adjusted_pct"]');
    await statutoryMargin.locator("summary").click();
    await adjustedMargin.locator("summary").click();
    assert.ok((await statutoryMargin.textContent()).includes("25,00 %"));
    assert.ok((await adjustedMargin.textContent()).includes("27,00 %"));
    assert.ok((await page.locator('#fv2-metrics details[data-calculation="gross_profit"]').textContent()).includes("400 EUR"));
    assert.equal(previewOptions.at(-1).capital_rounding_tolerance, "0");
    await page.locator("#fv2-capital-tolerance").fill("1");
    await page.locator("#fv2-capital-tolerance").dispatchEvent("change");
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).capital_rounding_tolerance, "1");
    const debtDetails = page.locator('#fv2-validation details[data-reconciliation="REC-DEBT-001"]');
    const leaseDetails = page.locator('#fv2-validation details[data-reconciliation="REC-LEASE-001"]');
    await debtDetails.locator("summary").click();
    await leaseDetails.locator("summary").click();
    assert.ok((await debtDetails.textContent()).includes("opening+drawdowns-repayments+fx_other=closing"));
    assert.ok((await leaseDetails.textContent()).includes("opening+additions+modifications+interest-payments+fx_other=closing"));
    assert.equal(await debtDetails.locator("tr").count(), 6);
    assert.equal(await leaseDetails.locator("tr").count(), 8);
    const cycleDetails = page.locator('#fv2-metrics details[data-calculation="cash_conversion_cycle"]');
    const dsoDetails = page.locator('#fv2-metrics details[data-calculation="dso"]');
    const dayDetails = page.locator('#fv2-metrics details[data-calculation="days_for_dso"]');
    await cycleDetails.locator("summary").click();
    await dsoDetails.locator("summary").click();
    await dayDetails.locator("summary").click();
    assert.ok((await cycleDetails.textContent()).includes("dso+dio-dpo"));
    assert.ok((await cycleDetails.textContent()).includes("63,9 days"));
    assert.ok((await dsoDetails.textContent()).includes("36,5 days"));
    assert.ok((await dayDetails.textContent()).includes("inclusive_calendar_days(period_anchor.start_date,period_anchor.end_date)"));
    assert.ok((await dayDetails.textContent()).includes("365 days"));
    assert.ok((await page.locator('#fv2-metrics details[data-calculation="net_debt"]').textContent()).includes("500 EUR"));
    const ratioDetails = page.locator('#fv2-metrics details[data-calculation="current_ratio"]');
    await ratioDetails.locator("summary").click();
    assert.ok((await ratioDetails.textContent()).includes("current_assets/current_liabilities"));
    assert.ok((await ratioDetails.textContent()).includes("current assets"));
    assert.ok((await ratioDetails.textContent()).includes("current liabilities"));
    assert.ok((await page.locator("#fv2-metrics").textContent()).includes("Liquidez corrente"));
    const ebitDetails = page.locator('#fv2-metrics details[data-calculation="ebit_margin"]');
    await ebitDetails.locator("summary").click();
    assert.ok((await ebitDetails.textContent()).includes("confirmed_ebit/revenue*100"));
    assert.ok((await ebitDetails.textContent()).includes("20,00 %"));
    const cfiDetails = page.locator('#fv2-metrics details[data-calculation="cfi"]');
    const cffDetails = page.locator('#fv2-metrics details[data-calculation="cff"]');
    await cfiDetails.locator("summary").click();
    await cffDetails.locator("summary").click();
    assert.ok((await cfiDetails.textContent()).includes("reported_cfi"));
    assert.ok((await cfiDetails.textContent()).includes("-120 EUR"));
    assert.ok((await cfiDetails.textContent()).includes("cash flow from investing"));
    assert.ok((await cffDetails.textContent()).includes("reported_cff"));
    assert.ok((await cffDetails.textContent()).includes("40 EUR"));
    const fcfDetails = page.locator('#fv2-metrics details[data-calculation="free_cash_flow"]');
    await fcfDetails.locator("summary").click();
    assert.ok((await fcfDetails.textContent()).includes("cfo+reported_capex_cash_outflow_negative"));
    assert.ok((await fcfDetails.textContent()).includes("220 EUR"));
    assert.ok((await fcfDetails.textContent()).includes("-80 EUR"));
    await page.locator("#fv2-file").setInputFiles({ name: "statements.csv", mimeType: "text/csv", buffer: Buffer.from("controlled browser upload") });
    assert.equal(await page.locator("#fv2-sample-source a").count(), 0);
    assert.equal(await page.locator("#fv2-preview select").count(), 0);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(await page.locator("#fv2-preview img").count(), 0);
    assert.equal(await page.evaluate(() => window.uploadExecuted), undefined);
    assert.ok((await page.locator("#fv2-preview").textContent()).includes("<img"));
    assert.equal(await page.locator("#fv2-metrics").isVisible(), true);
    assert.equal(previewOptions.at(-1).capital_rounding_tolerance, "0");
    assert.equal(Object.hasOwn(previewOptions.at(-1), "accounting_parentheses"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "fiscal_year_end_month"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "source_currency"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "source_scale"), false);
    for (const [currency, scale] of [["EUR", "raw"], ["USD", "thousands"], ["", ""], ["EUR", "raw"]]) {
      await page.selectOption("#fv2-source-currency", currency);
      await page.selectOption("#fv2-source-scale", scale);
      assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
      assert.equal(await page.locator("#fv2-metrics").isVisible(), false);
      await page.click("#fv2-validate");
      await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
      if (currency) { assert.equal(previewOptions.at(-1).source_currency, currency); assert.equal(previewOptions.at(-1).source_scale, scale); }
      else { assert.equal(Object.hasOwn(previewOptions.at(-1), "source_currency"), false); assert.equal(Object.hasOwn(previewOptions.at(-1), "source_scale"), false); }
    }
    for (const month of ["6", "12", ""]) {
      await page.selectOption("#fv2-fiscal-month", month);
      assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
      assert.equal(await page.locator("#fv2-metrics").isVisible(), false);
      await page.click("#fv2-validate");
      await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
      if (month) assert.equal(previewOptions.at(-1).fiscal_year_end_month, Number(month));
      else assert.equal(Object.hasOwn(previewOptions.at(-1), "fiscal_year_end_month"), false);
    }
    for (const convention of ["true", "false", "true"]) {
      await page.selectOption("#fv2-parentheses", convention);
      assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
      assert.equal(await page.locator("#fv2-metrics").isVisible(), false);
      assert.equal(await page.locator("#fv2-gaps").isVisible(), false);
      await page.click("#fv2-validate");
      await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
      assert.equal(previewOptions.at(-1).accounting_parentheses, convention === "true");
    }
    assert.equal(await page.locator("#fv2-gaps").isVisible(), true);
    assert.ok((await page.locator("#fv2-gaps").textContent()).includes("Receita homóloga"));
    assert.ok((await page.locator("#fv2-gaps").textContent()).includes("Não identificado no período selecionado"));
    assert.equal(await page.locator("#fv2-gaps img").count(), 0);
    assert.equal(await page.evaluate(() => window.gapExecuted), undefined);
    const marginDetails = page.locator('#fv2-metrics details[data-calculation="net_margin"]');
    await marginDetails.locator("summary").click();
    assert.ok((await marginDetails.textContent()).includes("net_income/revenue*100"));
    assert.ok((await marginDetails.textContent()).includes("Rubrica original"));
    assert.ok((await marginDetails.textContent()).includes("csv:3"));
    assert.equal(await marginDetails.locator("img").count(), 0);
    assert.equal(await page.evaluate(() => window.evidenceExecuted), undefined);
    assert.equal(await page.locator("#fv2-validation").isVisible(), true);
    assert.ok((await page.locator("#fv2-validation").textContent()).includes("Identidade do balanço"));
    await page.locator('#fv2-validation > details:not([data-reconciliation]) > summary').click();
    assert.equal(await page.locator("#fv2-validation img").count(), 0);
    assert.equal(await page.evaluate(() => window.validationExecuted), undefined);
    assert.ok((await page.locator("#fv2-validation").textContent()).includes("Elaborar previsões"));
    includeReconciliationEvidence = false;
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(await page.locator("#fv2-validation details[data-reconciliation]").count(), 0);
    assert.equal(await page.locator("#fv2-validation").isVisible(), true);
    includeReconciliationEvidence = true; reconciliationUnavailable = true;
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(await cashDetails.count(), 0);
    assert.equal(await balanceDetails.count(), 1);
    assert.ok((await page.locator("#fv2-validation").textContent()).includes("Não verificada"));
    reconciliationUnavailable = false;
    includeValidationSummary = false;
    includeCalculationEvidence = false;
    includeMetricAvailability = false;
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(await page.locator("#fv2-validation").isVisible(), false);
    assert.equal(await page.locator("#fv2-gaps").isVisible(), false);
    assert.equal(await page.locator("#fv2-metrics details").count(), 0);
    assert.equal(await page.locator("#fv2-metrics").isVisible(), true);
    includeValidationSummary = true;
    includeCalculationEvidence = true;
    includeMetricAvailability = true;
    previewFailures = 1;
    await page.click("#fv2-validate");
    await page.waitForFunction(() => document.getElementById("fv2-status").textContent === "Temporary validation failure");
    assert.equal(await page.locator("#fv2-gaps").isVisible(), false);
    assert.equal(await page.locator("#fv2-metrics").isVisible(), false);
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-validation").hidden);
    await page.locator('#fv2-validation > details:not([data-reconciliation]) > summary').click();
    await marginDetails.locator("summary").click();
    fs.mkdirSync(path.join(root, "tmp/product-browser"), { recursive: true });
    await ebitDetails.locator("summary").click();
    await fcfDetails.locator("summary").click();
    await cfiDetails.locator("summary").click();
    await cffDetails.locator("summary").click();
    await ratioDetails.locator("summary").click();
    await page.locator('#fv2-preview details[data-row-guidance="csv:2"] summary').click();
    await cashDetails.locator("summary").click();
    await balanceDetails.locator("summary").click();
    await profitDetails.locator("summary").click();
    await ebitdaDetails.locator("summary").click();
    await statutoryMargin.locator("summary").click();
    await adjustedMargin.locator("summary").click();
    await debtDetails.locator("summary").click();
    await leaseDetails.locator("summary").click();
    await cycleDetails.locator("summary").click();
    await dsoDetails.locator("summary").click();
    await dayDetails.locator("summary").click();
    const sourceRegion = page.getByRole("region", { name: "Linhas do ficheiro e validação" });
    assert.equal(await sourceRegion.getAttribute("tabindex"), "0");
    await page.screenshot({ path: path.join(root, "tmp/product-browser/validation-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const reconciliationRegion = cashDetails.getByRole("region", { name: "Valores e fontes da reconciliação" });
    assert.equal(await reconciliationRegion.getAttribute("tabindex"), "0");
    assert.ok(await reconciliationRegion.evaluate(node => node.scrollWidth > node.clientWidth));
    await reconciliationRegion.evaluate(node => { node.scrollLeft = node.scrollWidth; });
    assert.ok(await reconciliationRegion.evaluate(node => node.scrollLeft > 0));
    await reconciliationRegion.evaluate(node => { node.scrollLeft = 0; });
    const operandRegion = marginDetails.getByRole("region", { name: "Valores e fontes do cálculo" });
    assert.ok(await operandRegion.evaluate(node => node.scrollWidth > node.clientWidth));
    assert.ok(await operandRegion.locator("table").evaluate(node => node.getBoundingClientRect().width >= 640));
    await operandRegion.evaluate(node => { node.scrollLeft = node.scrollWidth; });
    assert.ok(await operandRegion.evaluate(node => node.scrollLeft > 0));
    await operandRegion.evaluate(node => { node.scrollLeft = 0; });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(root, "tmp/product-browser/validation-mobile.png"), fullPage: true });
    await sourceRegion.evaluate(node => { node.scrollLeft = node.scrollWidth; });
    await reconciliationRegion.screenshot({ path: path.join(root, "tmp/product-browser/reconciliation-mobile.png") });
    await sourceRegion.screenshot({ path: path.join(root, "tmp/product-browser/row-guidance-mobile.png") });
    await sourceRegion.evaluate(node => { node.scrollLeft = 0; });
    await page.setViewportSize({ width: 1440, height: 1000 });
    assert.equal(await page.locator("#fv2-metrics img").count(), 0);
    assert.equal(await page.evaluate(() => window.metricExecuted), undefined);
    assert.ok((await page.locator("#fv2-metrics").textContent()).includes("10,00 %"));
    assert.equal(generation, 0);
    await page.locator('#fv2-preview select[data-location="csv:2"]').selectOption("revenue");
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    assert.equal(await page.locator("#fv2-metrics").isVisible(), false);
    assert.equal(await page.locator("#fv2-validation").isVisible(), false);
    assert.equal(await page.locator("#fv2-gaps").isVisible(), false);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).mappings["csv:2"], "revenue");
    await page.selectOption("#fv2-fiscal-month", "6");
    await page.selectOption("#fv2-source-currency", "USD");
    await page.selectOption("#fv2-source-scale", "millions");
    await page.locator("#fv2-file").setInputFiles({ name: "different.csv", mimeType: "text/csv", buffer: Buffer.from("different controlled upload") });
    assert.equal(await page.locator("#fv2-parentheses").inputValue(), "");
    assert.equal(await page.locator("#fv2-fiscal-month").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-currency").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-scale").inputValue(), "");
    assert.equal(await page.locator("#fv2-preview select").count(), 0);
    assert.equal(await page.locator("#fv2-metrics").isVisible(), false);
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.deepEqual(previewOptions.at(-1).mappings, {});
    assert.equal(Object.hasOwn(previewOptions.at(-1), "accounting_parentheses"), false);
    await page.selectOption("#fv2-fiscal-month", "6");
    await page.selectOption("#fv2-source-currency", "EUR");
    await page.selectOption("#fv2-source-scale", "raw");
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).fiscal_year_end_month, 6);
    await page.click("#fv2-generate");
    await page.waitForFunction(() => localStorage.getItem("sl_financial_job"));
    await page.waitForFunction(() => document.getElementById("fv2-status").textContent.includes("Em fila"));
    assert.equal(await page.locator("#fv2-file").isDisabled(), true);
    assert.equal(generation, 1);
    pending = false;
    await reloadSignedIn(); // real initialization resumes the persisted job
    await page.waitForFunction(() => !document.getElementById("fv2-result").hidden);
    assert.equal(generation, 1);
    assert.equal(await page.evaluate(() => localStorage.getItem("sl_financial_job")), null);
    assert.equal(await page.locator("#analysesCounter").textContent(), "9 de 10 análises grátis");
    assert.equal(await page.locator("#fv2-frame").getAttribute("sandbox"), "");
    await page.frameLocator("#fv2-frame").locator("h1").waitFor();
    assert.equal(await page.frameLocator("#fv2-frame").locator("h1").textContent(), "Receita verificada");
    const downloadEvent = page.waitForEvent("download"); await page.click("#fv2-pdf");
    assert.equal((await downloadEvent).suggestedFilename(), `saleslab-financial-${runId}.pdf`);
    await page.click("#fv2-new");
    assert.equal(await page.locator("#fv2-fiscal-month").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-currency").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-scale").inputValue(), "");
    assert.equal(await page.locator("#fv2-controls").isVisible(), true);
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    assert.equal(await page.locator("#fv2-preview select").count(), 0);
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.click("#fv2-history");
    await page.locator("#fv2-history-list button").first().click();
    await page.waitForFunction(() => !document.getElementById("fv2-result").hidden);
    htmlFailures = 1;
    await page.locator("#fv2-history-list button").first().click();
    await page.waitForFunction(() => document.getElementById("fv2-status").textContent === "Report temporarily unavailable.");
    assert.equal(await page.locator("#fv2-controls").isVisible(), true);
    assert.equal(await page.locator("#fv2-validate").isDisabled(), false);
    assert.equal(await page.locator("#fv2-result").isVisible(), false);
    await page.locator("#fv2-history-list button").first().click();
    await page.waitForFunction(() => !document.getElementById("fv2-result").hidden);
    assert.ok((await page.locator("#fv2-status").textContent()).includes("Relatório aprovado"));
    await page.evaluate(() => switchView("dashboard"));
    await page.locator("#historyTable tbody tr").first().waitFor();
    assert.equal(await page.locator("#historyTable tbody img").count(), 0);
    assert.equal(await page.evaluate(() => window.historyExecuted), undefined);
    assert.ok((await page.locator("#historyTable tbody").textContent()).includes("<img"));
    await page.locator("#historyTable tbody tr").first().click();
    await page.waitForFunction(() => !document.getElementById("fv2-result").hidden);
    assert.equal(await page.locator("#inputCard").isVisible(), false);
    assert.equal(await page.locator("#chatBtn").isVisible(), false);
    fs.mkdirSync(path.join(root, "tmp/product-browser"), { recursive: true });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(root, "tmp/product-browser/desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.click("#fv2-new");
    assert.equal(await page.locator("#fv2-fiscal-month").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-currency").inputValue(), "");
    assert.equal(await page.locator("#fv2-source-scale").inputValue(), "");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(root, "tmp/product-browser/mobile.png"), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), JSON.stringify(await page.evaluate(() => [...document.querySelectorAll("body *")].filter(n => { const box = n.getBoundingClientRect(); return box.width && box.right > innerWidth; }).map(n => ({ id: n.id, tag: n.tagName, width: n.getBoundingClientRect().width })).slice(0, 12))));
    await page.click("#mod-sales"); assert.equal(await page.locator("#fv2").isVisible(), false);
    assert.equal(await page.locator("#inputCard").isVisible(), true);
    await page.evaluate(() => logout());
    assert.equal(await page.locator("#fv2").count(), 0);
    // A stale job from another workspace terminates polling and unlocks controls.
    jobMissing = true;
    await page.evaluate(job => { localStorage.setItem("sl_financial_job", job); }, jobId);
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.waitForFunction(() => localStorage.getItem("sl_financial_job") === null);
    assert.equal(await page.locator("#fv2-validate").isDisabled(), false);
    featureEnabled = false;
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.waitForFunction(() => !document.getElementById("fv2").hidden && document.getElementById("fv2-controls").hidden);
    await page.click("#fv2-history"); await page.locator("#fv2-history-list button").first().click();
    await page.waitForFunction(() => !document.getElementById("fv2-result").hidden);
    generation = 0;
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.waitForFunction(() => document.getElementById("fv2").hidden);
    assert.equal(await page.locator("#inputCard").isVisible(), true);
    featureEnabled = true; capabilityFailures = 1;
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.waitForFunction(() => !document.getElementById("fv2").hidden && !document.getElementById("fv2-controls").hidden);
    assert.equal(capabilityFailures, 0);
    assert.deepEqual(errors, []);
    planningVersions = ["1", "2", "3", "4", "5", "6", "7"];
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "version-seven.csv", mimeType: "text/csv", buffer: Buffer.from("version seven controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "7");
    assert.equal(Object.hasOwn(previewOptions.at(-1), "capital_rounding_tolerance"), false);
    assert.equal(await page.locator("#fv2-capital-tolerance-field").isVisible(), false);
    assert.equal(await page.locator("#fv2-profitability-tolerance-field").isVisible(), true);
    planningVersions = ["1", "2", "3", "4", "5", "6"];
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "version-six.csv", mimeType: "text/csv", buffer: Buffer.from("version six controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "6");
    assert.equal(Object.hasOwn(previewOptions.at(-1), "profitability_rounding_tolerance"), false);
    assert.equal(await page.locator("#fv2-profitability-tolerance-field").isVisible(), false);
    assert.equal(await page.locator("#fv2-cash-tolerance-field").isVisible(), true);
    planningVersions = ["1", "2", "3", "4", "5"];
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "version-five.csv", mimeType: "text/csv", buffer: Buffer.from("version five controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "5");
    assert.equal(Object.hasOwn(previewOptions.at(-1), "cash_rounding_tolerance"), false);
    assert.equal(await page.locator("#fv2-cash-tolerance-field").isVisible(), false);
    // Negotiate the older supported plan before falling back to a pre-version backend.
    planningVersions = ["1", "2", "3", "4"];
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "version-four.csv", mimeType: "text/csv", buffer: Buffer.from("version four controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "4");
    planningVersions = ["1", "2", "3"]; normalizationVersions = ["1"];
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "version-three.csv", mimeType: "text/csv", buffer: Buffer.from("version three controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "3");
    assert.equal(Object.hasOwn(previewOptions.at(-1), "normalization_version"), false);
    planningVersions = ["1", "2"];
    parenthesesSupported = false; fiscalCalendarSupported = false; sourceMetadataSupported = false; includeRowGuidance = false;
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "version-two.csv", mimeType: "text/csv", buffer: Buffer.from("version two controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(previewOptions.at(-1).planning_version, "2");
    assert.equal(await page.locator("#fv2-parentheses-field").isVisible(), false);
    assert.equal(await page.locator("#fv2-fiscal-field").isVisible(), false);
    assert.equal(await page.locator("#fv2-source-metadata").isVisible(), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "source_currency"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "source_scale"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "fiscal_year_end_month"), false);
    assert.equal(await page.locator("#fv2-preview details[data-row-guidance]").count(), 0);
    assert.equal((await page.locator("#fv2-preview").textContent()).includes("Valor original:"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "accounting_parentheses"), false);
    planningVersions = null; normalizationVersions = null;
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "legacy.csv", mimeType: "text/csv", buffer: Buffer.from("legacy controlled upload") });
    await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "planning_version"), false);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "normalization_version"), false);
    assert.equal(await page.locator("#fv2-import-controls").isVisible(), true);
    assert.equal(Object.hasOwn(previewOptions.at(-1), "tabular_import"), false);
    planningVersions = ["1", "2", "3", "4", "5", "6", "7", "8"]; normalizationVersions = ["1", "2"]; importMode = true;
    await page.setViewportSize({ width: 1280, height: 1000 });
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({ name: "erp.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from(importFixture.content, "base64") });
    await page.selectOption("#fv2-language", "en"); await page.selectOption("#fv2-locale", "en-GB");
    inspectionFailures = 1; await page.click("#fv2-inspect");
    await page.waitForFunction(() => document.getElementById("fv2-status").textContent.includes("Synthetic inspection failure"));
    await page.click("#fv2-inspect"); await page.locator("#fv2-import-confirm").waitFor();
    assert.equal(await page.locator("#fv2-import-builder img").count(), 0);
    assert.equal(await page.evaluate(() => window.importExecuted), undefined);
    assert.equal(await page.locator("#fv2-import-builder").textContent().then(t => t.includes("folha oculta")), true);
    for (let i = 0; i < importFixture.selection.tables.length; i++) {
      if (i > 0) await page.click("#fv2-import-add");
      const selected = importFixture.selection.tables[i];
      await page.selectOption(`[data-import-sheet="${i}"]`, String(selected.sheet));
      for (const key of ["header_row", "first_row", "last_row"]) {
        await page.locator(`[data-import-row="${i}-${key}"]`).fill(String(selected[key]));
        await page.locator(`[data-import-row="${i}-${key}"]`).dispatchEvent("change");
      }
      for (const [role, binding] of Object.entries(selected.fields)) {
        await page.selectOption(`[data-import-field="${i}-${role}"]`, binding.column || "constant");
        if (!binding.column) {
          const input = page.locator(`[data-import-constant="${i}-${role}"]`);
          if (await input.evaluate(n => n.tagName) === "SELECT") await input.selectOption(binding.constant);
          else { await input.fill(binding.constant); await input.dispatchEvent("change"); }
        }
      }
    }
    await page.click("#fv2-validate");
    await page.waitForFunction(() => document.getElementById("fv2-status").textContent.includes("Confirme os intervalos"));
    assert.equal(importOptions.length, 0);
    await page.check("#fv2-import-confirm"); await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-generate").disabled);
    assert.deepEqual(importOptions.at(-1).tabular_import.tables, importFixture.selection.tables.map(t => ({ ...t, fields: Object.fromEntries(Object.entries(t.fields).map(([k,b]) => [k, b.column ? {column:b.column} : {constant:b.constant}])) })));
    assert.equal(importOptions.at(-1).tabular_import.inspection_hash, importFixture.inspection.inspection_hash);
    assert.equal((await page.locator("#fv2-preview").textContent()).includes("Células de origem · Resultados"), true);
    await page.locator("#fv2-import-builder fieldset details").first().locator("summary").click();
    await page.screenshot({ path: "tmp/product-browser/import-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "tmp/product-browser/import-mobile.png", fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.locator('[data-import-row="0-first_row"]').fill("5"); await page.locator('[data-import-row="0-first_row"]').dispatchEvent("change");
    assert.equal(await page.locator("#fv2-import-confirm").isChecked(), false);
    assert.equal(await page.locator("#fv2-generate").isDisabled(), true);
    await page.click("#fv2-sample"); await page.waitForFunction(() => document.getElementById("fv2-file").files[0]?.name === "jm-2024.csv");
    assert.equal(await page.locator("#fv2-import-builder fieldset").count(), 0);
    tabularSupported = false; await reloadSignedIn(); await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-import-controls").isVisible(), false);
    assert.deepEqual(errors, []);
    pdfSupported = true; tabularSupported = true; pdfMode = true; importMode = false; jobMissing = false; htmlFailures = 0;
    await page.setViewportSize({ width: 1280, height: 1000 }); await reloadSignedIn(); await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-file").getAttribute("accept"), ".csv,.xlsx,.pdf");
    await page.locator("#fv2-file").setInputFiles({ name: "statements.pdf", mimeType: "application/pdf", buffer: Buffer.from(pdfFixture.content,"base64") });
    await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
    await page.locator("#fv2-pdf-pages").fill("1,2"); await page.locator("#fv2-pdf-pages").dispatchEvent("change");
    await page.click("#fv2-inspect"); await page.locator('[data-pdf-cuts="1"]').waitFor();
    for (const n of [1,2]) { await page.locator(`[data-pdf-cuts="${n}"]`).fill("40"); await page.locator(`[data-pdf-cuts="${n}"]`).dispatchEvent("change"); }
    await page.click("#fv2-validate"); await page.waitForFunction(() => document.getElementById("fv2-status").textContent.includes("Prepare novamente"));
    assert.equal(pdfOptions.length,0);
    await page.click("#fv2-inspect"); await page.waitForFunction(() => document.querySelector('[data-pdf-cuts="1"]').value === "40");
    for (let i=0;i<pdfFixture.selection.tables.length;i++) {
      if (i) await page.click("#fv2-import-add"); const selected=pdfFixture.selection.tables[i];
      await page.selectOption(`[data-import-sheet="${i}"]`,String(selected.sheet));
      for (const key of ["header_row","first_row","last_row"]) { await page.locator(`[data-import-row="${i}-${key}"]`).fill(String(selected[key])); await page.locator(`[data-import-row="${i}-${key}"]`).dispatchEvent("change"); }
      for (const [role,binding] of Object.entries(selected.fields)) { await page.selectOption(`[data-import-field="${i}-${role}"]`,binding.column || "constant"); if (!binding.column) { const input=page.locator(`[data-import-constant="${i}-${role}"]`); if (await input.evaluate(n=>n.tagName)==="SELECT") await input.selectOption(binding.constant); else { await input.fill(binding.constant); await input.dispatchEvent("change"); } } }
    }
    await page.check("#fv2-import-confirm"); await page.click("#fv2-validate");
    await page.locator('[data-pdf-row="p1:L3:B"]').waitFor();
    assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    assert.equal(await page.locator("#fv2-pdf-review img").count(),0);
    assert.equal(await page.evaluate(()=>window.pdfExecuted),undefined);
    await page.check('[data-pdf-row="p1:L3:B"]'); await page.click("#fv2-validate");
    await page.waitForFunction(() => !document.getElementById("fv2-validate").disabled);
    assert.equal(await page.locator('[data-pdf-row="p1:L3:B"]').isChecked(),true);
    assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    for (const row of pdfFixture.review.review_rows) await page.check(`[data-pdf-row="${row.location}"]`);
    await page.locator("#fv2-pdf-layout details summary").first().click();
    await page.screenshot({path:"tmp/product-browser/pdf-review-desktop.png",fullPage:true});
    await page.setViewportSize({width:390,height:844}); await page.screenshot({path:"tmp/product-browser/pdf-review-mobile.png",fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    assert.equal((await page.locator("#fv2-preview").textContent()).includes("p1:L3:B"),true);
    assert.equal(pdfOptions.at(-1).pdf_import.confirmed_rows.length,5);
    await page.selectOption("#fv2-locale","pt-PT"); assert.equal(await page.locator("#fv2-pdf-review input").count(),0); assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.selectOption("#fv2-locale","en-GB"); await page.click("#fv2-validate"); await page.locator('[data-pdf-row="p1:L3:B"]').waitFor();
    for (const row of pdfFixture.review.review_rows) await page.check(`[data-pdf-row="${row.location}"]`);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    pending = false; await page.click("#fv2-generate"); await page.waitForFunction(()=>!document.getElementById("fv2-result").hidden);
    await page.click("#fv2-new"); assert.equal(await page.locator("#fv2-pdf-review input").count(),0);
    pdfSupported=false; tabularSupported=false; await reloadSignedIn(); await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-file").getAttribute("accept"),".csv,.xlsx");
    monthEndMode=true; monthEndSupported=true; monthlySnapshotSupported=true; pdfMode=false; importMode=false;
    await reloadSignedIn(); await page.click("#mod-financial"); await page.setViewportSize({width:1280,height:900});
    await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(monthEndFixture.content,"base64")});
    await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    await page.locator("#fv2-close summary").click();
    assert.equal(await page.locator("#fv2-close-rules input[type=checkbox]").count(),14);
    assert.equal(await page.locator("#fv2-close-rules input:checked").count(),0);
    for (const [id,value] of Object.entries({month:"2026-10",policy:"synthetic-monthly","policy-version":"1",currency:"EUR",start:"1",absolute:"10000",percentage:"50",basis:"Synthetic test thresholds only",tasks:"review_sources"})) await page.fill("#fv2-close-"+id,value);
    for (const value of ["income_statement","balance_sheet"]) await page.check(`#fv2-close-statements input[value="${value}"]`);
    await page.check('#fv2-close-rules input[value="REC-BS-001"]');
    for (const value of ["month_on_month","month_year_on_year","ytd_year_on_year"]) await page.check(`#fv2-close-relations input[value="${value}"]`);
    await page.click("#fv2-close-task-build");
    for (const [current,prior,relation] of [["csv:2","csv:7","month_on_month"],["csv:2","csv:8","month_year_on_year"],["csv:9","csv:10","ytd_year_on_year"]]) {
      await page.click("#fv2-close-add-comparison"); const row=page.locator('#fv2-close-comparisons fieldset').last();
      await row.locator('[data-close-role="current"]').selectOption(current); await row.locator('[data-close-role="prior"]').selectOption(prior); await row.locator('[data-close-role="kind"]').selectOption(relation);
    }
    await page.click("#fv2-close-add-movement"); await page.selectOption('#fv2-close-movements [data-close-role="current"]',"csv:2"); await page.selectOption('#fv2-close-movements [data-close-role="kind"]',"nonzero_value");
    const quotaCalls=generation;
    await page.click("#fv2-close-prepare"); await page.locator("#fv2-close-result h3").waitFor();
    assert.match(await page.locator("#fv2-close-result").textContent(),/Preparação incompleta/);
    await page.check('#fv2-close-task-rows input[type="checkbox"]'); await page.selectOption('#fv2-close-task-rows select',["csv:2"]);
    assert.equal(await page.locator("#fv2-close-download").isDisabled(),true);
    await page.click("#fv2-close-prepare"); await page.locator("#fv2-close-result h3").waitFor();
    assert.match(await page.locator("#fv2-close-result").textContent(),/Preparação pronta para revisão/);
    assert.equal(await page.locator("#fv2-close-result img").count(),0); assert.equal(await page.evaluate(()=>window.closeExecuted),undefined);
    assert.equal(generation,quotaCalls); assert.equal(monthEndRequests.length,2);
    const closeDownload=page.waitForEvent("download"); await page.click("#fv2-close-download"); const snapshot=await closeDownload;
    assert.match(snapshot.suggestedFilename(),/saleslab-month-end-2026-10.json/);
    await snapshot.saveAs("tmp/product-browser/month-end-snapshot.json");
    const saved=JSON.parse(fs.readFileSync("tmp/product-browser/month-end-snapshot.json","utf8")); assert.equal(saved.persisted,false); assert.equal(saved.result.accounting_close_attested,false); assert.equal(saved.original_rows[0].amount,"1000");
    await page.locator("#fv2-close").screenshot({path:"tmp/product-browser/month-end-desktop.png"});
    await page.setViewportSize({width:390,height:844}); await page.locator("#fv2-close").screenshot({path:"tmp/product-browser/month-end-mobile.png"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    assert.equal(await page.locator("#fv2-close-save").isVisible(),true);
    await page.click("#fv2-close-save"); await page.waitForFunction(()=>!document.getElementById("fv2-close-save").disabled);
    assert.equal(monthlySaveRequests.length,1); assert.equal(generation,quotaCalls);
    await page.click("#fv2-close-save"); await page.locator("[data-close-snapshot]").waitFor();
    assert.equal(monthlySaveRequests.length,2); assert.equal(await page.locator("#fv2-close-save").isDisabled(),true);
    assert.equal(await page.locator("#fv2-close-history img").count(),0); assert.equal(await page.evaluate(()=>window.monthlyHistoryExecuted),undefined);
    await page.click("[data-close-snapshot]"); await page.locator("[data-close-saved-download]").waitFor();
    assert.match(await page.locator("#fv2-close-saved-result").textContent(),/Preparação guardada/);
    const savedDownload=page.waitForEvent("download"); await page.click("[data-close-saved-download]"); const retained=await savedDownload; await retained.saveAs("tmp/product-browser/monthly-saved.json");
    const retainedValue=JSON.parse(fs.readFileSync("tmp/product-browser/monthly-saved.json","utf8"));assert.deepEqual(retainedValue,monthEndFixture.saved_result);assert.equal(generation,quotaCalls);
    await page.click("[data-close-csv-download]"); await page.waitForFunction(()=>document.getElementById("fv2-status").textContent.includes("CSV temporariamente indisponível")); assert.equal(await page.locator("[data-close-csv-download]").isDisabled(),false);
    const languageExpectations={pt:["Preparação incompleta","Pendente","Confirme a tarefa", "Fecho não atestado"],en:["Preparation incomplete","Pending","Confirm the task", "Close not attested"],es:["Preparación incompleta","Pendiente","Confirme la tarea", "Cierre no certificado"],fr:["Préparation incomplète","En attente","Confirmez la tâche", "Clôture non attestée"]};
    for(const [language,expected] of Object.entries(languageExpectations)) {
      monthlyPresentationLanguage=language; await page.click("[data-close-snapshot]"); await page.locator("[data-close-saved-download]").waitFor();
      const rendered=await page.locator("#fv2-close-saved-result").textContent();
      for(const phrase of expected)assert.ok(rendered.includes(phrase),language+": "+phrase);
      assert.equal(await page.locator("#fv2-close-saved-result").getAttribute("lang"),language);
      if(language!=="pt")assert.ok(!rendered.includes("Fecho não atestado"));
      const unchanged=page.waitForEvent("download");await page.click("[data-close-saved-download]");const file=await unchanged;await file.saveAs(`tmp/product-browser/monthly-${language}.json`);assert.deepEqual(JSON.parse(fs.readFileSync(`tmp/product-browser/monthly-${language}.json`,"utf8")),monthEndFixture.localized_saved_results[language]);
      await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await page.locator("#fv2-close-saved-result").screenshot({path:`tmp/product-browser/monthly-${language}-mobile.png`});
      assert.equal(await page.locator("#fv2-close-saved-result img").count(),0);assert.equal(generation,quotaCalls);
    }
    monthlyPresentationLanguage=null;await page.click("[data-close-snapshot]");await page.locator("[data-close-saved-download]").waitFor();
    const csvDownload=page.waitForEvent("download"); await page.click("[data-close-csv-download]"); const csvFile=await csvDownload; await csvFile.saveAs("tmp/product-browser/monthly-checklist.csv"); assert.match(fs.readFileSync("tmp/product-browser/monthly-checklist.csv","utf8"),/financial-month-end-checklist-csv-v1/); assert.equal(generation,quotaCalls);
    await page.setViewportSize({width:1280,height:900});
    await page.locator("#fv2-close-history").screenshot({path:"tmp/product-browser/monthly-saved-desktop.png"});
    await page.setViewportSize({width:390,height:844});await page.locator("#fv2-close-history").screenshot({path:"tmp/product-browser/monthly-saved-mobile.png"});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await page.setViewportSize({width:1280,height:900});
    const catalogQuota=generation, originalSaved=structuredClone(monthlySaved);
    await page.check('#fv2-close-rules input[value="REC-WC-CLOSING-001"]');
    await page.click("#fv2-close-prepare");await page.locator("#fv2-close-result h3").waitFor();
    assert.match(await page.locator("#fv2-close-result").textContent(),/Preparação incompleta/);
    assert.deepEqual(monthEndRequests.at(-1).required_reconciliations,["REC-BS-001","REC-WC-CLOSING-001"]);
    async function refreshCatalog() {await page.click("#fv2-close-catalog-refresh");await page.waitForFunction(()=>!document.getElementById("fv2-close-catalog-refresh").disabled);}
    closeCatalogReversed=true;await refreshCatalog();
    assert.equal(await page.isChecked('#fv2-close-rules input[value="REC-WC-CLOSING-001"]'),true);
    assert.equal(await page.locator("#fv2-close-download").isDisabled(),true);
    closeCatalogUnsafeLabel=true;await refreshCatalog();
    assert.equal(await page.locator("#fv2-close-rules img").count(),0);assert.equal(await page.evaluate(()=>window.catalogExecuted),undefined);
    closeCatalogMode="duplicate";await refreshCatalog();
    const catalogRequests=monthEndRequests.length;await page.click("#fv2-close-prepare");
    assert.equal(monthEndRequests.length,catalogRequests);assert.match(await page.locator("#fv2-status").textContent(),/Atualize o catálogo/);
    assert.equal(await page.locator("#fv2-close-rules input:checked").count(),2);
    closeCatalogMode="server";closeCatalogUnsafeLabel=false;closeCatalogReversed=false;closeCatalogLimit=2;await refreshCatalog();
    await page.check('#fv2-close-rules input[value="REC-CAPEX-001"]');await page.click("#fv2-close-prepare");
    assert.equal(monthEndRequests.length,catalogRequests);assert.match(await page.locator("#fv2-status").textContent(),/uma e 2 reconciliações/);
    closeCatalogLimit=20;await refreshCatalog();await page.uncheck('#fv2-close-rules input[value="REC-CAPEX-001"]');
    closeCatalogMode="legacy";await refreshCatalog();
    assert.equal(await page.isChecked('#fv2-close-rules input[value="REC-WC-CLOSING-001"]'),true);
    assert.equal(await page.locator('[data-close-catalog-unavailable="true"]').count(),1);
    await page.click("#fv2-close-prepare");assert.equal(monthEndRequests.length,catalogRequests);
    assert.match(await page.locator("#fv2-status").textContent(),/fora do catálogo/);
    await page.uncheck('#fv2-close-rules input[value="REC-WC-CLOSING-001"]');
    await page.click("#fv2-close-prepare");await page.locator("#fv2-close-result h3").waitFor();
    assert.deepEqual(monthEndRequests.at(-1).required_reconciliations,["REC-BS-001"]);
    closeCatalogMode="server";await refreshCatalog();
    capabilityFailures=1;await refreshCatalog();const failedCatalogRequests=monthEndRequests.length;
    await page.click("#fv2-close-prepare");assert.equal(monthEndRequests.length,failedCatalogRequests);
    assert.equal(await page.isChecked('#fv2-close-rules input[value="REC-BS-001"]'),true);
    await refreshCatalog();
    await page.locator("#fv2-close-rules").screenshot({path:"tmp/product-browser/close-catalog-desktop.png",style:"header,#chatBtn{visibility:hidden!important;transition:none!important;}"});
    await page.setViewportSize({width:390,height:844});await page.locator("#fv2-close-rules").screenshot({path:"tmp/product-browser/close-catalog-mobile.png",style:"header,#chatBtn{visibility:hidden!important;transition:none!important;}"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    assert.deepEqual(monthlySaved,originalSaved);assert.equal(generation,catalogQuota);
    await page.fill("#fv2-close-absolute","9999"); assert.equal(await page.locator("#fv2-close-download").isDisabled(),true); assert.equal(await page.locator("#fv2-close-result h3").count(),0);
    for (const language of ["en","es","fr","pt"]) {
      await page.selectOption("#fv2-report-language",language);
      const rule=monthEndFixture.reconciliation_catalog.rules.find(r=>r.rule_id==="REC-BS-001");
      assert.ok((await page.locator('#fv2-close-rules label').filter({has:page.locator('input[value="REC-BS-001"]')}).textContent()).includes(rule.labels[language]));
      assert.equal(await page.isChecked('#fv2-close-rules input[value="REC-BS-001"]'),true);
    }
    await page.selectOption("#fv2-locale","pt-PT"); assert.equal(await page.locator("#fv2-close").isHidden(),true);
    monthEndSupported=false; await reloadSignedIn(); await page.click("#mod-financial"); assert.equal(await page.locator("#fv2-close").isHidden(),true);
    featureEnabled=false;hidePaidHistory=true;await reloadSignedIn();await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2").isVisible(),true);assert.equal(await page.locator("#fv2-controls").isHidden(),true);assert.equal(await page.locator("#fv2-close-save").isHidden(),true);
    await page.click("#fv2-close-history button");await page.locator("[data-close-snapshot]").waitFor();await page.click("[data-close-snapshot]");await page.locator("[data-close-saved-download]").waitFor();assert.equal(generation,quotaCalls);
    monthlyCsvSupported=false;await reloadSignedIn();await page.click("#mod-financial");await page.click("#fv2-close-history button");await page.locator("[data-close-snapshot]").waitFor();await page.click("[data-close-snapshot]");await page.locator("[data-close-saved-download]").waitFor();assert.equal(await page.locator("[data-close-csv-download]").count(),0);
    featureEnabled=true;hidePaidHistory=false;monthlySnapshotSupported=false;
    growthMode=true; monthEndMode=false; planningVersions=["1","2","3","4","5","6","7","8","9"]; normalizationVersions=["1","2"];
    await reloadSignedIn(); await page.click("#mod-financial"); await page.setViewportSize({width:1280,height:900});
    await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(growthFixture.content,"base64")});
    await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(await page.locator("#fv2-growth-tolerance-field").isVisible(),true);
    for (const [location,concept] of Object.entries(growthFixture.choices.mappings)) await page.selectOption(`#fv2-preview select[data-location="${location}"]`,concept);
    await page.click("#fv2-validate"); await page.locator('[data-growth-location="csv:8"]').waitFor();
    assert.equal(await page.locator("#fv2-generate").isDisabled(),true); assert.equal(await page.locator("#fv2-metrics").isHidden(),true);
    assert.equal(await page.locator("#fv2-growth-review img").count(),0); assert.equal(await page.evaluate(()=>window.growthExecuted),undefined);
    await page.fill('[data-growth-location="csv:8"]',growthFixture.choices.growth_definitions["csv:8"]); await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(await page.inputValue('[data-growth-location="csv:8"]'),growthFixture.choices.growth_definitions["csv:8"]);
    assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    for (const [location,quote] of Object.entries(growthFixture.choices.growth_definitions)) await page.fill(`[data-growth-location="${location}"]`,quote);
    await page.locator("#fv2-growth-review").screenshot({path:"tmp/product-browser/growth-definitions-desktop.png"});
    await page.setViewportSize({width:390,height:844}); await page.locator("#fv2-growth-review").screenshot({path:"tmp/product-browser/growth-definitions-mobile.png"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    assert.match(await page.locator("#fv2-metrics").textContent(),/4,90 %/); assert.match(await page.locator("#fv2-metrics").textContent(),/0,60 %/);
    assert.match(await page.locator("#fv2-validation").textContent(),/Ponte monetária do crescimento reportado/);
    assert.deepEqual(growthOptions.at(-1).growth_definitions,growthFixture.choices.growth_definitions);
    await page.fill("#fv2-growth-tolerance","1"); await page.dispatchEvent("#fv2-growth-tolerance","change"); assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.fill("#fv2-growth-tolerance","0"); await page.dispatchEvent("#fv2-growth-tolerance","change");
    await page.selectOption("#fv2-locale","pt-PT"); assert.equal(await page.locator("#fv2-growth-review input").count(),0); assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.selectOption("#fv2-locale","en-GB"); await page.click("#fv2-validate"); await page.locator('[data-growth-location="csv:8"]').waitFor();
    for (const [location,quote] of Object.entries(growthFixture.choices.growth_definitions)) await page.fill(`[data-growth-location="${location}"]`,quote);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    tabularSupported=true; // Exercise source-preparation changes while growth decisions already exist.
    await reloadSignedIn(); await page.click("#mod-financial");
    await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(growthFixture.content,"base64")});
    await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    for (const [location,concept] of Object.entries(growthFixture.choices.mappings)) await page.selectOption(`#fv2-preview select[data-location="${location}"]`,concept);
    await page.click("#fv2-validate"); await page.locator('[data-growth-location="csv:8"]').waitFor();
    for (const [location,quote] of Object.entries(growthFixture.choices.growth_definitions)) await page.fill(`[data-growth-location="${location}"]`,quote);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    await page.click("#fv2-inspect"); await page.locator('[data-import-row="0-first_row"]').waitFor();
    assert.equal(await page.locator("#fv2-growth-review input").count(),0);
    for (const [role,binding] of Object.entries(growthFixture.selection.tables[0].fields)) await page.selectOption(`[data-import-field="0-${role}"]`,binding.column);
    await page.check("#fv2-import-confirm"); await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(Object.hasOwn(growthOptions.at(-1),"growth_definitions"),false);
    const prepareImportedGrowth = async () => {
      for (const [location,concept] of Object.entries(growthFixture.prepared.choices.mappings)) await page.selectOption(`#fv2-preview select[data-location="${location}"]`,concept);
      await page.click("#fv2-validate"); await page.locator(`[data-growth-location="${Object.keys(growthFixture.prepared.choices.mappings)[0]}"]`).waitFor();
      for (const [location,quote] of Object.entries(growthFixture.prepared.choices.growth_definitions)) await page.fill(`[data-growth-location="${location}"]`,quote);
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    };
    await prepareImportedGrowth();
    await page.selectOption('[data-import-field="0-line"]',"B"); assert.equal(await page.locator("#fv2-growth-review input").count(),0); assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.selectOption('[data-import-field="0-line"]',"A"); await page.check("#fv2-import-confirm"); await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled); assert.equal(Object.hasOwn(growthOptions.at(-1),"growth_definitions"),false);
    await prepareImportedGrowth();
    await page.fill('[data-import-row="0-first_row"]',"3"); await page.dispatchEvent('[data-import-row="0-first_row"]',"change"); assert.equal(await page.locator("#fv2-growth-review input").count(),0);
    await page.fill('[data-import-row="0-first_row"]',"2"); await page.dispatchEvent('[data-import-row="0-first_row"]',"change"); await page.check("#fv2-import-confirm"); await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled); assert.equal(Object.hasOwn(growthOptions.at(-1),"growth_definitions"),false);
    await prepareImportedGrowth();
    pending=false; await page.click("#fv2-generate"); await page.waitForFunction(()=>!document.getElementById("fv2-result").hidden);
    await page.click("#fv2-new"); assert.equal(await page.locator("#fv2-growth-review input").count(),0);
    growthMode=false; planningVersions=["1","2","3","4","5","6","7","8"]; await reloadSignedIn(); await page.click("#mod-financial"); assert.equal(await page.locator("#fv2-growth-tolerance-field").isHidden(),true);
    debtMode=true; planningVersions=["1","2","3","4","5","6","7","8","9","10"]; await reloadSignedIn(); await page.click("#mod-financial");
    await page.setViewportSize({width:1280,height:900});
    async function startDebt() {
      await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(debtFixture.content,"base64")});
      await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
      assert.equal(Object.hasOwn(debtOptions.at(-1),"debt_definitions"),false);
      for (const [loc,concept] of Object.entries(debtFixture.choices.mappings)) await page.selectOption(`#fv2-preview select[data-location="${loc}"]`,concept);
      await page.click("#fv2-validate"); await page.locator('[data-debt-location="csv:7"]').waitFor();
    }
    await startDebt(); assert.equal(await page.locator("#fv2-debt-tolerance-field").isVisible(),true);
    assert.equal(await page.locator("#fv2-debt-review img").count(),0); assert.equal(await page.evaluate(()=>window.debtExecuted),undefined);
    const firstDebt=Object.keys(debtFixture.choices.debt_definitions)[0];
    await page.fill(`[data-debt-location="${firstDebt}"]`,debtFixture.choices.debt_definitions[firstDebt]);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(await page.inputValue(`[data-debt-location="${firstDebt}"]`),debtFixture.choices.debt_definitions[firstDebt]);
    async function confirmDebt() {
      for (const [loc,quote] of Object.entries(debtFixture.choices.debt_definitions)) await page.fill(`[data-debt-location="${loc}"]`,quote);
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    }
    for (const [loc,quote] of Object.entries(debtFixture.choices.debt_definitions)) await page.fill(`[data-debt-location="${loc}"]`,quote);
    await page.locator("#fv2-debt-review").screenshot({path:"tmp/product-browser/debt-definitions-desktop.png"});
    await page.setViewportSize({width:390,height:844}); await page.locator("#fv2-debt-review").screenshot({path:"tmp/product-browser/debt-definitions-mobile.png"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true); await page.setViewportSize({width:1280,height:900});
    await confirmDebt();
    for (const value of ["60,00 %","40,00 %","25,00 %"]) assert.ok((await page.locator("#fv2-metrics").textContent()).includes(value));
    await page.fill("#fv2-debt-tolerance","1"); await page.dispatchEvent("#fv2-debt-tolerance","change"); assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.fill("#fv2-debt-tolerance","0"); await page.dispatchEvent("#fv2-debt-tolerance","change");
    await page.selectOption("#fv2-locale","pt-PT"); assert.equal(await page.locator("#fv2-debt-review input").count(),0);
    await startDebt(); await confirmDebt();
    pending=false; await page.click("#fv2-generate"); await page.waitForFunction(()=>!document.getElementById("fv2-result").hidden);
    await page.click("#fv2-new"); assert.equal(await page.locator("#fv2-debt-review input").count(),0);
    debtMode=false; planningVersions=["1","2","3","4","5","6","7","8","9"]; await reloadSignedIn(); await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-debt-tolerance-field").isHidden(),true); assert.equal(await page.locator("#fv2-growth-tolerance-field").isVisible(),true);
    capexMode=true; planningVersions=["1","2","3","4","5","6","7","8","9","10","11"]; await reloadSignedIn(); await page.click("#mod-financial");
    await page.setViewportSize({width:1280,height:900});
    async function startCapex() {
      await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(capexFixture.content,"base64")});
      await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
      assert.equal(Object.hasOwn(capexOptions.at(-1),"capex_definitions"),false);
      for (const [loc,concept] of Object.entries(capexFixture.choices.mappings)) await page.selectOption(`#fv2-preview select[data-location="${loc}"]`,concept);
      await page.click("#fv2-validate"); await page.locator('[data-capex-location="csv:7"]').waitFor();
    }
    await startCapex(); assert.equal(await page.locator("#fv2-capex-tolerance-field").isVisible(),true);
    assert.equal(await page.locator("#fv2-capex-review img").count(),0); assert.equal(await page.evaluate(()=>window.capexExecuted),undefined);
    const firstCapex=Object.keys(capexFixture.choices.capex_definitions)[0];
    await page.fill(`[data-capex-location="${firstCapex}"]`,capexFixture.choices.capex_definitions[firstCapex]);
    await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(await page.inputValue(`[data-capex-location="${firstCapex}"]`),capexFixture.choices.capex_definitions[firstCapex]);
    async function confirmCapex() {
      for (const [loc,quote] of Object.entries(capexFixture.choices.capex_definitions)) await page.fill(`[data-capex-location="${loc}"]`,quote);
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    }
    for (const [loc,quote] of Object.entries(capexFixture.choices.capex_definitions)) await page.fill(`[data-capex-location="${loc}"]`,quote);
    await page.locator("#fv2-capex-review").screenshot({path:"tmp/product-browser/capex-definitions-desktop.png"});
    await page.setViewportSize({width:390,height:844}); await page.locator("#fv2-capex-review").screenshot({path:"tmp/product-browser/capex-definitions-mobile.png"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true); await page.setViewportSize({width:1280,height:900});
    await confirmCapex();
    for (const value of ["37,50 %","62,50 %"]) assert.ok((await page.locator("#fv2-metrics").textContent()).includes(value));
    await page.fill("#fv2-capex-tolerance","1"); await page.dispatchEvent("#fv2-capex-tolerance","change"); assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.fill("#fv2-capex-tolerance","0"); await page.dispatchEvent("#fv2-capex-tolerance","change");
    await page.selectOption("#fv2-locale","pt-PT"); assert.equal(await page.locator("#fv2-capex-review input").count(),0);
    await startCapex(); await confirmCapex();
    pending=false; await page.click("#fv2-generate"); await page.waitForFunction(()=>!document.getElementById("fv2-result").hidden);
    await page.click("#fv2-new"); assert.equal(await page.locator("#fv2-capex-review input").count(),0);
    capexMode=false; planningVersions=["1","2","3","4","5","6","7","8","9","10"]; await reloadSignedIn(); await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-capex-tolerance-field").isHidden(),true); assert.equal(await page.locator("#fv2-debt-tolerance-field").isVisible(),true);
    roicMode=true; planningVersions=["1","2","3","4","5","6","7","8","9","10","11","12"]; await reloadSignedIn(); await page.click("#mod-financial");
    async function startRoic() {
      await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(roicFixture.content,"base64")});
      await page.selectOption("#fv2-language","en"); await page.selectOption("#fv2-locale","en-GB");
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
      assert.equal(Object.hasOwn(roicOptions.at(-1),"roic_sources"),false);
      await page.selectOption('#fv2-preview select[data-location="csv:7"]',"nopat_with_explicit_tax_assumptions");
      await page.click("#fv2-validate"); await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
      assert.equal(await page.locator('[data-roic-role="nopat"]').count(),0);
      await page.selectOption('#fv2-preview select[data-location="csv:8"]',"average_invested_capital");
      await page.click("#fv2-validate"); await page.locator('[data-roic-role="nopat"]').waitFor();
    }
    await startRoic();
    assert.equal(await page.locator("#fv2-roic-review img").count(),0);assert.equal(await page.evaluate(()=>window.roicExecuted),undefined);
    async function fillRoic() {
      for (const [role,quoteKey] of [["nopat","nopat_definition"],["tax_assumptions","tax_assumptions"],["average_capital","average_capital_definition"]]) {
        await page.selectOption(`[data-roic-role="${role}"]`,roicFixture.choices.roic_sources[role+"_location"]);
        await page.fill(`[data-roic-quote="${quoteKey}"]`,roicFixture.choices.roic_sources[quoteKey]);
      }
    }
    await page.selectOption('[data-roic-role="nopat"]',"csv:7");await page.fill('[data-roic-quote="nopat_definition"]',roicFixture.choices.roic_sources.nopat_definition);
    await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(await page.inputValue('[data-roic-quote="nopat_definition"]'),roicFixture.choices.roic_sources.nopat_definition);
    await fillRoic();
    await page.locator("#fv2-roic-review").screenshot({path:"tmp/product-browser/roic-definitions-desktop.png",style:"header, #chatBtn { visibility: hidden !important; }"});
    await page.setViewportSize({width:390,height:844});await page.locator("#fv2-roic-review").screenshot({path:"tmp/product-browser/roic-definitions-mobile.png",style:"header, #chatBtn { visibility: hidden !important; }"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await page.setViewportSize({width:1280,height:900});
    await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    assert.ok((await page.locator("#fv2-metrics").textContent()).includes("20,00 %"));
    await page.selectOption('#fv2-preview select[data-location="csv:7"]',"");
    assert.equal(await page.locator("#fv2-generate").isDisabled(),true);assert.equal(await page.locator("#fv2-roic-review textarea").count(),0);
    await startRoic();await fillRoic();await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    pending=false;await page.click("#fv2-generate");await page.waitForFunction(()=>!document.getElementById("fv2-result").hidden);
    await page.click("#fv2-new");assert.equal(await page.locator("#fv2-roic-review textarea").count(),0);
    roicMode=false;planningVersions=["1","2","3","4","5","6","7","8","9","10","11"];await reloadSignedIn();await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-roic-review").isHidden(),true);
    wcMode=true;planningVersions=["1","2","3","4","5","6","7","8","9","10","11","12","13"];await reloadSignedIn();await page.click("#mod-financial");
    async function startWc() {
      await page.locator("#fv2-file").setInputFiles({name:"statements.csv",mimeType:"text/csv",buffer:Buffer.from(wcFixture.content,"base64")});
      await page.selectOption("#fv2-language","en");await page.selectOption("#fv2-locale","en-GB");
      await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
      assert.equal(Object.hasOwn(wcOptions.at(-1),"working_capital_definitions"),false);
      await page.selectOption(`#fv2-preview select[data-location="${wcFixture.locations.closing_nwc}"]`,"reported_operating_nwc");
      await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
      assert.equal(await page.locator("[data-wc-location]").count(),0);
      for(const [loc,concept] of Object.entries(wcFixture.choices.mappings))await page.selectOption(`#fv2-preview select[data-location="${loc}"]`,concept);
      await page.click("#fv2-validate");await page.locator("[data-wc-location]").first().waitFor();
    }
    await startWc();assert.equal(await page.locator("#fv2-wc-tolerance-field").isVisible(),true);
    assert.equal(await page.locator("#fv2-wc-review img").count(),0);assert.equal(await page.evaluate(()=>window.wcExecuted),undefined);
    const firstWc=Object.keys(wcFixture.choices.working_capital_definitions)[0];
    await page.fill(`[data-wc-location="${firstWc}"]`,wcFixture.choices.working_capital_definitions[firstWc]);
    await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-validate").disabled);
    assert.equal(await page.inputValue(`[data-wc-location="${firstWc}"]`),wcFixture.choices.working_capital_definitions[firstWc]);
    async function fillWc(){for(const [loc,quote] of Object.entries(wcFixture.choices.working_capital_definitions))await page.fill(`[data-wc-location="${loc}"]`,quote);}
    await fillWc();
    await page.locator("#fv2-wc-review").screenshot({path:"tmp/product-browser/working-capital-desktop.png",style:"header,#chatBtn{display:none!important;transition:none!important;}"});
    await page.setViewportSize({width:390,height:844});await page.locator("#fv2-wc-review").screenshot({path:"tmp/product-browser/working-capital-mobile.png",style:"header,#chatBtn{display:none!important;transition:none!important;}"});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);await page.setViewportSize({width:1280,height:900});
    await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    assert.ok((await page.locator("#fv2-metrics").textContent()).includes("20 EUR"));
    await page.fill("#fv2-wc-tolerance","1");await page.dispatchEvent("#fv2-wc-tolerance","change");assert.equal(await page.locator("#fv2-generate").isDisabled(),true);
    await page.fill("#fv2-wc-tolerance","0");await page.dispatchEvent("#fv2-wc-tolerance","change");
    await page.selectOption("#fv2-locale","pt-PT");assert.equal(await page.locator("[data-wc-location]").count(),0);
    await startWc();await fillWc();await page.click("#fv2-validate");await page.waitForFunction(()=>!document.getElementById("fv2-generate").disabled);
    pending=false;await page.click("#fv2-generate");await page.waitForFunction(()=>!document.getElementById("fv2-result").hidden);
    await page.click("#fv2-new");assert.equal(await page.locator("[data-wc-location]").count(),0);
    wcMode=false;planningVersions=["1","2","3","4","5","6","7","8","9","10","11","12"];await reloadSignedIn();await page.click("#mod-financial");
    assert.equal(await page.locator("#fv2-wc-tolerance-field").isHidden(),true);
    assert.deepEqual(errors,[]);
    console.log("Financial product browser flow passed: preview/mapping, safe rendering, durable resume, quota display, report/PDF, history, mobile layout, logout and disabled feature.");
    await context.close();
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
