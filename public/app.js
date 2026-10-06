// PaperTrail demo frontend. Plain ES module, no build step.
const TESSERACT_URL = "https://cdn.jsdelivr.net/npm/tesseract.js@7.0.0/dist/tesseract.min.js";
const PDFJS_URL = "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.4.299/build/pdf.min.mjs";
const PDFJS_WORKER = "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.4.299/build/pdf.worker.min.mjs";
const MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.1.0/dist/mermaid.esm.min.mjs";
const MAX_BYTES = 5 * 1024 * 1024;
const SESSION_RUN_CAP = 8;
const REVIEW_THRESHOLD = 0.75;

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// --- storage (all best-effort: private windows may block it) ---------------------------------
const store = {
  get(area, key, fallback) {
    try { const v = window[area].getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
  },
  set(area, key, value) {
    try { window[area].setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};

const state = { packs: [], samples: {}, pack: "claims", sample: null, current: null };

// --- boot ------------------------------------------------------------------------------------
async function getJSON(url, fallbackUrl) {
  try {
    const r = await fetch(url);
    if (r.ok) return await r.json();
  } catch { /* fall through */ }
  if (fallbackUrl) { const r = await fetch(fallbackUrl); if (r.ok) return r.json(); }
  throw new Error(`could not load ${url}`);
}

async function boot() {
  setupTabs();
  const [packs, samples, site] = await Promise.all([
    getJSON("/data/packs.json", "/api/packs"),
    getJSON("/samples/index.json").catch(() => ({})),
    getJSON("/data/site.json").catch(() => ({})),
  ]);
  state.packs = packs;
  state.samples = samples;
  if (site.repo_url) $("#repo-link").href = site.repo_url;
  else $("#repo-link").hidden = true;
  renderPackPicker();
  selectPack(state.pack);
  renderQueue();
  $("#upload-form").addEventListener("submit", onUpload);
  document.querySelectorAll(".file input").forEach((inp) => inp.addEventListener("change", () => {
    const label = inp.closest(".file");
    label.classList.toggle("has", inp.files.length > 0);
    label.querySelector("span").textContent = inp.files[0]?.name || label.querySelector("span").dataset.empty;
  }));
  document.querySelectorAll(".file span").forEach((s) => { s.dataset.empty = s.textContent; });
  $("#zoom .close").addEventListener("click", () => $("#zoom").close());
  $("#zoom").addEventListener("click", (e) => { if (e.target.id === "zoom") $("#zoom").close(); });
}

function setupTabs() {
  const tabs = document.querySelectorAll(".tabs button");
  const loaded = {};
  tabs.forEach((btn) => btn.addEventListener("click", () => {
    tabs.forEach((b) => b.setAttribute("aria-selected", String(b === btn)));
    document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.id !== `tab-${btn.dataset.tab}`; });
    if (btn.dataset.tab === "bench" && !loaded.bench) { loaded.bench = true; loadBenchmark(); }
    if (btn.dataset.tab === "how" && !loaded.how) { loaded.how = true; loadHow(); }
    if (btn.dataset.tab === "queue") renderQueue();
  }));
}

// --- pack + samples ----------------------------------------------------------------------------
function renderPackPicker() {
  $("#pack-picker").innerHTML = state.packs.map((p) => `
    <button class="pack-card" role="radio" aria-checked="false" data-pack="${esc(p.name)}">
      ${p.benchmarked ? "" : '<span class="tag">demo only</span>'}
      <strong>${esc(p.title)}</strong><span>${esc(p.description)}</span>
    </button>`).join("");
  document.querySelectorAll(".pack-card").forEach((b) => b.addEventListener("click", () => selectPack(b.dataset.pack)));
}

function packInfo(name) { return state.packs.find((p) => p.name === name); }

function selectPack(name) {
  state.pack = name;
  document.querySelectorAll(".pack-card").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.pack === name)));
  const samples = state.samples[name] || [];
  $("#samples").innerHTML = samples.length ? samples.map((s) => `
    <button class="sample" data-id="${esc(s.id)}">
      <span class="t">${esc(s.title)}</span>
      <span class="m"><span class="badge">${esc(s.tier)} scan</span>${s.seeded.length
        ? s.seeded.map((k) => `<span class="badge">${esc(k.replaceAll("_", " "))}</span>`).join("")
        : '<span class="badge">no seeded issue</span>'}</span>
    </button>`).join("") : '<p class="hint">No samples built yet.</p>';
  document.querySelectorAll(".sample").forEach((b) => b.addEventListener("click", () => loadSample(b.dataset.id)));
  const types = packInfo(name)?.doc_types || [];
  document.querySelectorAll(".file-row select").forEach((sel, i) => {
    sel.innerHTML = '<option value="auto">Auto-detect</option>' +
      types.map((t, j) => `<option value="${esc(t.name)}" ${i === j ? "" : ""}>${esc(t.title)}</option>`).join("");
  });
  if (samples.length) loadSample(samples[0].id);
}

async function loadSample(id) {
  document.querySelectorAll(".sample").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
  const meta = (state.samples[state.pack] || []).find((s) => s.id === id);
  const base = `/samples/${state.pack}/${id}`;
  try {
    const result = await getJSON(`${base}/result.json`);
    const images = Object.fromEntries(Object.entries(meta.documents).map(([dt, f]) => [dt, `${base}/${f}`]));
    showCase({ ...result, images, source: "sample", title: meta.title, seeded: meta.seeded });
  } catch (e) {
    $("#results").innerHTML = `<div class="empty">Could not load this sample (${esc(e.message)}).</div>`;
  }
}

// --- rendering a case ---------------------------------------------------------------------------
const OUTCOME = {
  approve: { label: "Approve", icon: "✓" },
  needs_review: { label: "Needs review", icon: "!" },
  flag: { label: "Flag", icon: "✕" },
};
const SEV_ICON = { hard: "✕", soft: "!", info: "✓" };

function confClass(c) { return c >= 0.9 ? "c-high" : c >= REVIEW_THRESHOLD ? "c-mid" : "c-low"; }

function fmtValue(v) {
  if (typeof v === "number") return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  return esc(v);
}

function isField(o) { return o && typeof o === "object" && "value" in o && "confidence" in o; }

function fieldRows(dt, data) {
  const rows = [];
  const leaf = (path, f) => {
    const ref = `${dt}.${path}`;
    const value = f.value === null
      ? `<span class="null">null: ${esc(f.null_reason || "not found")}</span>`
      : fmtValue(f.value);
    const pct = Math.round((f.confidence ?? 0) * 100);
    const ev = f.evidence && f.value !== null && String(f.evidence) !== String(f.value)
      ? `<div class="hint mono" style="margin:2px 0 0">“${esc(f.evidence)}”</div>` : "";
    rows.push(`<tr data-ref="${esc(ref)}"><td class="path">${esc(path)}</td><td>${value}${ev}</td>
      <td><span class="conf ${confClass(f.confidence)}" title="confidence ${pct}%"><span class="bar"><i style="width:${pct}%"></i></span>${pct}%</span></td></tr>`);
  };
  for (const [key, val] of Object.entries(data)) {
    if (Array.isArray(val)) {
      rows.push(`<tr class="group"><td colspan="3">${esc(key)} (${val.length})</td></tr>`);
      val.forEach((item, i) => {
        for (const [k, f] of Object.entries(item)) if (isField(f)) leaf(`${key}[${i}].${k}`, f);
      });
    } else if (isField(val)) {
      leaf(key, val);
    }
  }
  return rows.join("");
}

function chips(fields) {
  if (!fields?.length) return "";
  return `<span class="chips">${fields.map((f) => `<button class="chip" data-ref="${esc(f)}">${esc(f)}</button>`).join("")}</span>`;
}

function showCase(c) {
  state.current = c;
  const pack = packInfo(c.pack);
  const ev = c.evaluation;
  const d = ev.decision;
  const o = OUTCOME[d.outcome];
  const docTypes = pack.doc_types.map((t) => t.name).filter((t) => c.documents[t]);
  const tokens = Object.values(c.documents).reduce((s, x) => s + (x.usage?.total_tokens || 0), 0);
  const latency = Object.values(c.documents).reduce((s, x) => s + (x.latency_s || 0), 0);
  const models = [...new Set(Object.values(c.documents).map((x) => x.model))].join(", ");

  const docsHtml = docTypes.map((dt) => {
    const x = c.documents[dt];
    const valid = ev.validations.filter((f) => f.fields.some((r) => r.startsWith(`${dt}.`)));
    return `<article class="doc">
      <header><h3>${esc(x.title)}</h3><span>${x.detection?.method === "override" ? "type set manually" : `detected by ${esc(x.detection?.method)}`}</span></header>
      <div class="img" data-src="${esc(c.images?.[dt] || "")}">${c.images?.[dt] ? `<img src="${esc(c.images[dt])}" alt="${esc(x.title)} image" loading="lazy">` : ""}</div>
      ${valid.length ? `<div class="valid">${valid.map((f) => `<div><span class="ico ${f.severity}">${SEV_ICON[f.severity]}</span><span>${esc(f.message)}</span></div>`).join("")}</div>` : ""}
      <table class="fields"><thead><tr><th>Field</th><th>Value</th><th style="width:80px">Confidence</th></tr></thead>
      <tbody>${fieldRows(dt, x.data)}</tbody></table>
    </article>`;
  }).join("");

  const checksHtml = ev.crosschecks.map((ck) => {
    const msgs = ck.findings.filter((f) => f.severity !== "info" || ck.status === "pass")
      .map((f) => `<div>${esc(f.message)} ${chips(f.fields)}</div>`).join("");
    const label = { pass: "✓ pass", fail: "✕ mismatch", unverifiable: "! can't verify", error: "! error" }[ck.status];
    return `<tr class="${ck.status}"><td>${esc(ck.description)}<div class="hint mono" style="margin:2px 0 0">${esc(ck.code)}</div></td>
      <td><span class="pill ${ck.status}">${label}</span></td><td>${msgs || '<span class="hint">consistent</span>'}</td></tr>`;
  }).join("");

  $("#results").innerHTML = `
    <section class="decision">
      <div class="decision-head">
        <span class="outcome ${d.outcome}"><span aria-hidden="true">${o.icon}</span>${o.label}</span>
        <span class="who">Recommendation only. A human makes the final call.</span>
        <div class="actions"><button class="btn" id="export">Export JSON</button></div>
      </div>
      <ul class="reasons">${d.reasons.map((r) => `<li><span class="ico ${r.severity}" aria-label="${r.severity}">${SEV_ICON[r.severity]}</span>
        <span>${esc(r.message)} ${chips(r.fields)}</span></li>`).join("")}</ul>
      ${(ev.notes || []).map((n) => `<p class="note">${esc(n)}</p>`).join("")}
      <p class="meta">${c.source === "sample" ? `Sample: ${esc(c.title)}. Precomputed with the live engine. ` : "Live run. "}
        Extraction: ${c.pipeline === "vlm" ? "vision LLM" : "OCR + LLM"} (${esc(models)}), matching: ${esc(ev.matching || c.matching)},
        ${tokens.toLocaleString()} tokens, ${latency.toFixed(1)}s model time.
        ${c.seeded?.length ? `Seeded issues: ${c.seeded.map(esc).join(", ")}.` : ""}</p>
    </section>
    <p class="conf-legend"><span><span class="conf c-high"><span class="bar"><i style="width:100%"></i></span></span> ≥ 90% confident</span>
      <span><span class="conf c-mid"><span class="bar"><i style="width:80%"></i></span></span> 75–90%</span>
      <span><span class="conf c-low"><span class="bar"><i style="width:50%"></i></span></span> &lt; 75%: routed to review if critical</span></p>
    <div class="docs">${docsHtml}</div>
    <section class="panel"><h3>Cross-document checks</h3>
      <table class="checks"><thead><tr><th>Check</th><th>Status</th><th>Details (click a field to highlight it)</th></tr></thead>
      <tbody>${checksHtml}</tbody></table></section>`;

  $("#export").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify({ ...c, images: undefined }, null, 2)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `papertrail-${c.pack}-${Date.now()}.json` });
    a.click();
    URL.revokeObjectURL(a.href);
  });
  document.querySelectorAll("#results .chip").forEach((ch) => ch.addEventListener("click", () => highlight(ch.dataset.ref)));
  document.querySelectorAll("#results .doc .img").forEach((el) => el.addEventListener("click", () => {
    if (!el.dataset.src) return;
    $("#zoom img").src = el.dataset.src;
    $("#zoom").showModal();
  }));
}

function highlight(ref) {
  const rows = document.querySelectorAll("#results tr[data-ref]");
  let first = null;
  rows.forEach((tr) => {
    const r = tr.dataset.ref;
    const hit = r === ref || r.startsWith(`${ref}.`) || r.startsWith(`${ref}[`);
    tr.classList.toggle("hl", hit);
    if (hit && !first) first = tr;
  });
  first?.scrollIntoView({ behavior: "smooth", block: "center" });
}

// --- live upload --------------------------------------------------------------------------------
function setStatus(msg, err = false) {
  const s = $("#status");
  s.textContent = msg;
  s.classList.toggle("err", err);
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const s = Object.assign(document.createElement("script"), { src, onload: resolve, onerror: () => reject(new Error(`could not load ${src}`)) });
    document.head.appendChild(s);
  });
}

async function fileToImageBlob(file) {
  if (file.size > MAX_BYTES) throw new Error(`${file.name} is larger than 5 MB`);
  let source;
  if (file.type === "application/pdf") {
    const pdfjs = await import(PDFJS_URL);
    pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
    const pdf = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
    const page = await pdf.getPage(1);
    const viewport = page.getViewport({ scale: 2 });
    source = Object.assign(document.createElement("canvas"), { width: viewport.width, height: viewport.height });
    await page.render({ canvas: source, canvasContext: source.getContext("2d"), viewport }).promise;
  } else if (["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    source = await createImageBitmap(file, { imageOrientation: "from-image" });
  } else {
    throw new Error(`${file.name}: use JPG, PNG, WEBP or PDF`);
  }
  const scale = Math.min(1, 1600 / Math.max(source.width, source.height));
  const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(source.width * scale), height: Math.round(source.height * scale) });
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
}

async function sha256(blob) {
  const buf = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

let ocrWorker = null;
async function browserOCR(blob, label) {
  if (!ocrWorker) {
    setStatus("Loading OCR engine in your browser (first time only)…");
    await loadScript(TESSERACT_URL);
    ocrWorker = await window.Tesseract.createWorker("eng");
  }
  setStatus(`Running OCR on ${label}…`);
  const { data } = await ocrWorker.recognize(blob);
  return data.text;
}

async function api(path, options) {
  const r = await fetch(path, options);
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON error */ }
  if (r.status === 429) {
    const wait = body?.retry_after ? ` (about ${body.retry_after}s)` : "";
    throw new Error((body?.message || body?.detail || "The demo is busy. Please try again in a minute.") + wait);
  }
  if (!r.ok) throw new Error(body?.detail || body?.message || `request failed (${r.status})`);
  return body;
}

async function onUpload(e) {
  e.preventDefault();
  const rows = [...document.querySelectorAll(".file-row")];
  const files = rows.map((r) => r.querySelector("input").files[0]);
  if (files.some((f) => !f)) return setStatus("Choose both documents first.", true);
  const runs = store.get("sessionStorage", "pt.runs", 0);
  if (runs >= SESSION_RUN_CAP) return setStatus(`Session limit of ${SESSION_RUN_CAP} live runs reached (free tier). Samples still work.`, true);
  const pipeline = new FormData(e.target).get("pipeline");
  const matching = new FormData(e.target).get("matching");
  const btn = $("#run-btn");
  btn.disabled = true;
  try {
    const documents = {};
    const images = {};
    for (const [i, file] of files.entries()) {
      setStatus(`Preparing document ${i + 1}…`);
      const blob = await fileToImageBlob(file);
      const hash = await sha256(blob);
      const chosen = rows[i].querySelector("select").value;
      const cacheKey = `pt.x.${state.pack}.${pipeline}.${chosen}.${hash}`;
      let res = store.get("localStorage", cacheKey, null);
      if (!res) {
        const form = new FormData();
        form.append("file", blob, `${file.name.replace(/\.[^.]+$/, "")}.jpg`);
        form.append("pack", state.pack);
        form.append("doc_type", chosen);
        form.append("pipeline", pipeline);
        if (pipeline === "ocr+llm") form.append("ocr_text", await browserOCR(blob, `document ${i + 1}`));
        setStatus(`Extracting document ${i + 1} with the ${pipeline === "vlm" ? "vision" : "text"} model…`);
        res = await api("/api/extract", { method: "POST", body: form });
        store.set("localStorage", cacheKey, res);
      }
      if (documents[res.doc_type]) throw new Error(`Both documents look like a ${res.title}. Set the types manually.`);
      documents[res.doc_type] = res;
      images[res.doc_type] = URL.createObjectURL(blob);
    }
    setStatus("Validating and cross-checking…");
    const caseId = `case-${Date.now().toString(36)}`;
    const evaluation = await api("/api/evaluate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pack: state.pack, case_id: caseId, matching,
        documents: Object.fromEntries(Object.entries(documents).map(([dt, x]) => [dt, x.data])),
        images: Object.fromEntries(Object.entries(documents).map(([dt, x]) => [dt, { dhash: x.image.dhash }])),
        history: store.get("sessionStorage", `pt.history.${state.pack}`, []),
      }),
    });
    store.set("sessionStorage", `pt.history.${state.pack}`,
      [...store.get("sessionStorage", `pt.history.${state.pack}`, []), ...evaluation.history_add]);
    store.set("sessionStorage", "pt.runs", runs + 1);
    const c = { pack: state.pack, pipeline, matching, documents, evaluation, images, source: "upload", title: "Your upload" };
    document.querySelectorAll(".sample").forEach((b) => b.classList.remove("active"));
    showCase(c);
    addToQueue(c, caseId);
    setStatus(`Done. ${SESSION_RUN_CAP - runs - 1} live runs left in this session.`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// --- review queue ---------------------------------------------------------------------------------
function addToQueue(c, caseId) {
  if (c.evaluation.decision.outcome === "approve") return;
  const q = store.get("sessionStorage", "pt.queue", []);
  q.unshift({ id: caseId, at: new Date().toLocaleTimeString(), pack: c.pack, outcome: c.evaluation.decision.outcome,
    reasons: c.evaluation.decision.reasons.map((r) => r.message), case: { ...c, images: undefined } });
  store.set("sessionStorage", "pt.queue", q.slice(0, 20));
  renderQueue();
}

function renderQueue() {
  const q = store.get("sessionStorage", "pt.queue", []);
  $("#queue-count").textContent = q.length;
  $("#queue").innerHTML = q.length ? q.map((item, i) => `
    <div class="qitem"><div class="row">
      <span class="outcome ${item.outcome}" style="font-size:14px"><span aria-hidden="true">${OUTCOME[item.outcome].icon}</span>${OUTCOME[item.outcome].label}</span>
      <strong>${esc(packInfo(item.pack)?.title || item.pack)}</strong><span class="hint" style="margin:0">${esc(item.at)}</span>
      <button class="btn" data-i="${i}" style="margin-left:auto">Open</button></div>
      <ul>${item.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>`).join("")
    : '<div class="empty">Nothing waiting. Upload a pair on the "Try a pack" tab: anything flagged or needing review lands here.</div>';
  document.querySelectorAll("#queue .btn").forEach((b) => b.addEventListener("click", () => {
    const item = store.get("sessionStorage", "pt.queue", [])[Number(b.dataset.i)];
    document.querySelector('.tabs button[data-tab="try"]').click();
    selectPackSilently(item.pack);
    showCase(item.case);
  }));
}

function selectPackSilently(name) {
  state.pack = name;
  document.querySelectorAll(".pack-card").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.pack === name)));
}

// --- benchmark + how it works ---------------------------------------------------------------------
function renderMarkdown(md, base) {
  const html = window.marked.parse(md);
  const clean = window.DOMPurify.sanitize(html);
  const div = document.createElement("div");
  div.innerHTML = clean;
  div.querySelectorAll("img").forEach((img) => {
    const src = img.getAttribute("src");
    if (src && !/^(https?:|\/|data:)/.test(src)) img.src = `${base}/${src}`;
  });
  return div.innerHTML;
}

async function loadBenchmark() {
  const el = $("#bench-md");
  try {
    let r = await fetch("/benchmark/BENCHMARK.md");
    if (!r.ok) r = await fetch("/api/benchmark");
    el.innerHTML = renderMarkdown(await r.text(), "/benchmark");
  } catch {
    el.innerHTML = '<p class="hint">Benchmark not available.</p>';
  }
}

async function loadHow() {
  try {
    const { default: mermaid } = await import(MERMAID_URL);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "neutral", securityLevel: "strict" });
    await mermaid.run({ nodes: [$("#arch")] });
  } catch { /* diagram source stays readable as text */ }
  $("#packs-info").innerHTML = state.packs.map((p) => `<div class="card"><strong>${esc(p.title)}</strong>
    <div class="hint" style="margin:2px 0 0">${p.doc_types.map((d) => esc(d.title)).join(" + ")}</div>
    <ul>${p.checks.map((c) => `<li>${esc(c.description)}</li>`).join("")}</ul></div>`).join("");
  try {
    const r = await fetch("/docs/ADDING_A_PACK.md");
    $("#pack-guide").innerHTML = renderMarkdown(await r.text(), "/docs");
  } catch {
    $("#pack-guide").innerHTML = '<p class="hint">Guide not available.</p>';
  }
}

boot().catch((e) => {
  $("#results").innerHTML = `<div class="empty">Could not start the demo: ${esc(e.message)}</div>`;
});
