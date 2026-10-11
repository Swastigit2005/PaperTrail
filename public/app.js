// PaperTrail demo frontend. Plain ES module, no build step.
const TESSERACT_URL = "https://cdn.jsdelivr.net/npm/tesseract.js@7.0.0/dist/tesseract.min.js";
const PDFJS_URL = "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.4.299/build/pdf.min.mjs";
const PDFJS_WORKER = "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.4.299/build/pdf.worker.min.mjs";
const MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.1.0/dist/mermaid.esm.min.mjs";
const MAX_BYTES = 5 * 1024 * 1024;
const SESSION_LIVE_CAP = 8;
const UNSURE_BELOW = 0.75;

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------------------------
// Plain-language vocabulary
// ---------------------------------------------------------------------------------------------
const USECASES = {
  claims: {
    icon: "🏥", name: "Health insurance claim", sub: "Prescription + pharmacy bill",
    story: "An insurer receives a patient's <b>prescription</b> and <b>pharmacy bill</b>. Did the patient only buy what the doctor prescribed, at sensible quantities?",
    checks: ["Every billed medicine was prescribed (brand or generic name)", "Same patient on both documents", "Bill dated after the prescription", "Quantity fits the prescribed course", "Bill totals add up", "Medicine names are real (checked against RxNorm)"],
  },
  payables: {
    icon: "🧾", name: "Supplier invoice", sub: "Invoice + purchase order",
    story: "Before paying a supplier, the accounts team compares the <b>invoice</b> with the <b>purchase order</b> they issued. Is everything billed exactly as agreed?",
    checks: ["Invoice comes from the supplier on the order", "Every invoiced item was ordered", "No more units than ordered", "Prices match the agreed rates", "GST numbers are genuine", "Tax and totals are calculated correctly"],
  },
  expenses: {
    icon: "💳", name: "Employee expense", sub: "Receipt + expense claim",
    story: "An employee asks to be reimbursed. Does the <b>claim form</b> match the <b>receipt</b>, is it recent enough, and has this receipt been claimed before?",
    checks: ["Claimed amount is not more than the receipt", "Receipt is within the 30-day policy", "Same date and shop on both", "Receipt has not been claimed already"],
  },
};
const DOC = {
  prescription: { name: "Doctor's prescription", icon: "🩺" },
  bill: { name: "Pharmacy bill", icon: "💊" },
  invoice: { name: "Supplier invoice", icon: "🧾" },
  purchase_order: { name: "Purchase order", icon: "📋" },
  receipt: { name: "Shop receipt", icon: "🧾" },
  expense_claim: { name: "Expense claim form", icon: "📝" },
};
const ISSUE = {
  unprescribed_medicine: "a medicine nobody prescribed", patient_name_mismatch: "bill is for a different patient",
  bill_before_prescription: "bill dated before the prescription", implausible_quantity: "far too much medicine billed",
  arithmetic_error: "totals don't add up", gstin_mismatch: "invoice from a different supplier",
  invalid_gstin: "fake GST number", rate_above_po: "price higher than agreed", qty_over_po: "more units than ordered",
  line_not_on_po: "an item that was never ordered", wrong_tax_split: "wrong type of tax charged",
  total_mismatch: "wrong grand total", claim_amount_mismatch: "claims more than the receipt",
  duplicate_receipt: "receipt already claimed before", outside_policy_window: "receipt too old for the policy",
};
// code -> [what we checked, what went wrong]
const CHECK = {
  unprescribed_medicine: ["Every billed medicine was prescribed", "The bill charges for medicine the doctor didn't prescribe"],
  patient_name_mismatch: ["Same patient on both documents", "The patient names don't match"],
  bill_before_prescription: ["Bill dated on or after the prescription", "The bill date doesn't fit the prescription"],
  bill_too_late: ["Bill dated within 30 days of the prescription", "The bill is long after the prescription"],
  implausible_quantity: ["Quantities fit the prescribed course", "Far more medicine was billed than prescribed"],
  arithmetic_error: ["Amounts and totals add up", "The amounts or totals don't add up"],
  unknown_drug: ["Medicine names were recognised", "A medicine name may have been misread"],
  implausible_date: ["Dates look realistic", "A date looks wrong"],
  gstin_mismatch: ["Invoice is from the supplier on the purchase order", "The invoice is from a different supplier"],
  po_reference_mismatch: ["Invoice quotes the right purchase order", "The invoice quotes a different purchase order"],
  line_not_on_po: ["Every invoiced item was ordered", "The invoice includes something that was never ordered"],
  hsn_mismatch: ["Product codes match the order", "A product code differs from the order"],
  qty_over_po: ["No more units billed than ordered", "More units were billed than ordered"],
  rate_above_po: ["Prices match the agreed rates", "A price is higher than agreed"],
  invalid_gstin: ["GST numbers are genuine", "A GST number is not valid"],
  total_mismatch: ["Grand total = amount before tax + tax", "The grand total is wrong"],
  wrong_tax_split: ["Right type of GST for the states involved", "The wrong type of GST was charged"],
  invalid_gst_slab: ["GST rate is a legal rate", "The GST rate isn't a valid rate"],
  po_arithmetic_error: ["Purchase order amounts add up", "The purchase order amounts don't add up"],
  claim_amount_mismatch: ["Claimed amount is not more than the receipt", "The claim is for more than the receipt"],
  expense_date_mismatch: ["Same date on the claim and the receipt", "The dates on the claim and receipt differ"],
  outside_policy_window: ["Receipt is within the 30-day policy", "The receipt is outside the 30-day policy"],
  merchant_mismatch: ["Same shop on the claim and the receipt", "The shop names don't match"],
  duplicate_receipt: ["This receipt hasn't been claimed before", "This receipt was already claimed"],
  receipt_total_mismatch: ["Receipt items add up to the total", "The receipt items don't add up"],
  expense_after_claim: ["Expense happened before the claim was filed", "The expense date is after the claim date"],
  non_positive_claim: ["Claimed amount is positive", "The claimed amount isn't valid"],
  low_confidence: ["Every important detail was clearly readable", "Some important details were hard to read"],
  critical_field_missing: ["All key details were found", "Some key details are missing or unreadable"],
};
// per-document checks always shown (they only produce findings when something is wrong)
const VALIDATOR_ROWS = {
  claims: ["arithmetic_error", "unknown_drug"],
  payables: ["invalid_gstin", "arithmetic_error", "total_mismatch", "wrong_tax_split"],
  expenses: ["receipt_total_mismatch"],
};
const OPTIONAL_ROWS = ["implausible_date", "invalid_gst_slab", "expense_after_claim", "non_positive_claim", "po_arithmetic_error"];
const FIELD = {
  doctor_name: "Doctor", registration_number: "Doctor's registration no.", clinic_name: "Clinic", patient_name: "Patient",
  patient_age: "Age", date: "Date", diagnosis: "Diagnosis", medicines: "Medicines", name: "Medicine", strength: "Strength",
  dosage: "Dose", frequency: "How often", duration: "For how long", pharmacy_name: "Pharmacy", bill_number: "Bill no.",
  line_items: "Items", medicine: "Medicine", batch: "Batch", qty: "Quantity", rate: "Unit price", amount: "Amount",
  discount: "Discount", total: "Total", seller_name: "Supplier", seller_gstin: "Supplier GST no.", buyer_name: "Buyer",
  buyer_gstin: "Buyer GST no.", invoice_number: "Invoice no.", invoice_date: "Invoice date",
  po_reference: "Purchase order quoted", place_of_supply: "Place of supply", description: "Description", hsn: "Product code (HSN)",
  gst_rate: "GST %", taxable_value: "Amount before tax", cgst: "Central GST", sgst: "State GST", igst: "Integrated GST",
  grand_total: "Grand total", po_number: "Purchase order no.", po_date: "Order date", vendor_name: "Supplier",
  vendor_gstin: "Supplier GST no.", merchant: "Shop", receipt_number: "Receipt no.", items: "Items", tax: "Tax",
  payment_mode: "Paid by", employee_name: "Employee", employee_id: "Employee ID", claim_date: "Claim date",
  expense_date: "Expense date", category: "Category", amount_claimed: "Amount claimed", purpose: "Purpose",
};
const ITEM_WORD = { line_items: "item", medicines: "medicine", items: "item" };
const label = (k) => FIELD[k] || k.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());

function humanRef(ref) {
  const m = ref.match(/^([a-z_]+)\.([a-z_]+)(?:\[(\d+)\])?(?:\.([a-z_]+))?$/);
  if (!m) return ref;
  const [, doc, field, idx, sub] = m;
  const parts = [DOC[doc]?.name || label(doc)];
  if (idx !== undefined) parts.push(`${ITEM_WORD[field] || "row"} ${Number(idx) + 1}`);
  parts.push(label(sub || field).toLowerCase());
  return parts.join(" › ");
}
function humanize(text) {
  return String(text ?? "")
    .replace(/case sample-([a-z0-9_]+)/g, (_, id) => {
      const s = (state.samples[state.pack] || []).find((x) => x.id === id);
      return s ? `an earlier claim (demo example “${s.title}”)` : "an earlier claim";
    })
    .replace(/case (case-[a-z0-9]+)/g, "an earlier claim in this visit")
    .replace(/\b(prescription|bill|invoice|purchase_order|receipt|expense_claim)\.[a-z_]+(?:\[\d+\])?(?:\.[a-z_]+)?/g, (r) => `“${humanRef(r)}”`);
}
function fmt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) {
    const [y, m, d] = v.split("-");
    return new Date(Date.UTC(+y, +m - 1, +d)).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  }
  return String(v);
}
const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);

// ---------------------------------------------------------------------------------------------
// storage (best effort)
// ---------------------------------------------------------------------------------------------
const store = {
  get(area, key, fallback) { try { const v = window[area].getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
  set(area, key, value) { try { window[area].setItem(key, JSON.stringify(value)); } catch { /* unavailable */ } },
};

const state = { packs: [], samples: {}, pack: "claims", slots: {}, busy: false, resultCache: {} };

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`could not load ${url}`);
  return r.json();
}

// ---------------------------------------------------------------------------------------------
// routing
// ---------------------------------------------------------------------------------------------
const loaded = {};
function route() {
  const page = (location.hash || "#try").slice(1);
  const valid = ["try", "how", "accuracy"].includes(page) ? page : "try";
  $$(".page").forEach((p) => { p.hidden = p.id !== `page-${valid}`; });
  $$(".nav a[data-page]").forEach((a) => a.classList.toggle("active", a.dataset.page === valid));
  if (valid === "how" && !loaded.how) { loaded.how = true; renderHow(); }
  if (valid === "accuracy" && !loaded.acc) { loaded.acc = true; renderAccuracy(); }
  window.scrollTo({ top: 0 });
}

// ---------------------------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------------------------
async function boot() {
  const [packs, samples, site] = await Promise.all([
    getJSON("/data/packs.json").catch(() => getJSON("/api/packs")),
    getJSON("/samples/index.json").catch(() => ({})),
    getJSON("/data/site.json").catch(() => ({})),
  ]);
  state.packs = packs;
  state.samples = samples;
  if (site.repo_url) { $("#repo-link").href = site.repo_url; $("#repo-link").hidden = false; }
  window.addEventListener("hashchange", route);
  route();
  renderUsecases();
  selectPack("claims");
  $("#run-btn").addEventListener("click", () => run());
  $("#clear-btn").addEventListener("click", () => { clearSlots(); showPlaceholder(); setMessage(""); });
  $("#zoom .close").addEventListener("click", () => $("#zoom").close());
  $("#zoom").addEventListener("click", (e) => { if (e.target.id === "zoom") $("#zoom").close(); });
  renderRecent();
}

const packInfo = (name) => state.packs.find((p) => p.name === name);
const docTypes = (pack = state.pack) => (packInfo(pack)?.doc_types || []).map((d) => d.name);

// ---------------------------------------------------------------------------------------------
// side panel: use cases + scenarios
// ---------------------------------------------------------------------------------------------
function renderUsecases() {
  const order = ["claims", "payables", "expenses"].filter((p) => packInfo(p));
  $("#usecases").innerHTML = order.map((p) => `
    <button class="usecase" role="tab" aria-selected="false" data-pack="${p}">
      <span class="ico" aria-hidden="true">${USECASES[p].icon}</span>
      <span><strong>${USECASES[p].name}</strong><span class="sub">${USECASES[p].sub}</span></span>
    </button>`).join("");
  $$(".usecase").forEach((b) => b.addEventListener("click", () => { selectPack(b.dataset.pack); showPlaceholder(); setMessage(""); }));
}

function selectPack(name) {
  state.pack = name;
  $$(".usecase").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.pack === name)));
  $("#story").innerHTML = USECASES[name].story;
  const [a, b] = docTypes(name);
  $("#checker-title").textContent = `Check a ${USECASES[name].name.toLowerCase()}`;
  $("#checker-sub").textContent = `Drop the ${DOC[a].name.toLowerCase()} and the ${DOC[b].name.toLowerCase()} below. We read both, compare them, and give you a verdict.`;
  $("#download-all").href = `/examples/${name}.zip`;
  renderScenarios();
  clearSlots();
}

function expectBadge(s) {
  if (!s.seeded.length) return '<span class="expect good">✓ Expected: looks good</span>';
  return `<span class="expect bad">✕ Hidden problem: ${esc(s.seeded.map((k) => ISSUE[k] || k.replaceAll("_", " ")).join(" + "))}</span>`;
}

function renderScenarios() {
  const list = state.samples[state.pack] || [];
  $("#scenarios").innerHTML = list.map((s, n) => `
    <article class="scenario" data-id="${esc(s.id)}">
      <div class="sc-top"><span class="sc-n">#${n + 1}</span><span class="sc-title">${esc(s.title)}</span></div>
      ${expectBadge(s)}
      <div class="sc-docs">${docTypes().filter((dt) => s.documents[dt]).map((dt) => {
        const url = `/samples/${state.pack}/${s.id}/${s.documents[dt]}`;
        return `<a class="mini" draggable="true" href="${esc(url)}" data-id="${esc(s.id)}" data-dt="${esc(dt)}"
                  title="Drag into the ${esc(DOC[dt].name)} box (or click to add it)">
          <img src="${esc(url)}" alt="" loading="lazy"><span>${esc(DOC[dt].name)}<span class="grip">⠿ drag me</span></span></a>`;
      }).join("")}</div>
      <button class="btn-try" data-try="${esc(s.id)}">Try this example →</button>
    </article>`).join("") || '<p class="muted">No examples available.</p>';
  $$(".mini").forEach((a) => {
    a.addEventListener("dragstart", (e) => {
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("application/x-papertrail", JSON.stringify({ pack: state.pack, id: a.dataset.id, dt: a.dataset.dt }));
      e.dataTransfer.setData("text/uri-list", new URL(a.getAttribute("href"), location.href).href);
    });
    a.addEventListener("click", (e) => {  // a click places it too (handy on touch screens)
      e.preventDefault();
      placeExample(state.pack, a.dataset.id, a.dataset.dt);
    });
  });
  $$("[data-try]").forEach((b) => b.addEventListener("click", async () => {
    $$(".scenario").forEach((el) => el.classList.toggle("active", el.dataset.id === b.dataset.try));
    await Promise.all(docTypes().map((dt) => placeExample(state.pack, b.dataset.try, dt)));
    $(".checker").scrollIntoView({ behavior: "smooth", block: "start" });
    run();
  }));
}

// ---------------------------------------------------------------------------------------------
// drop slots
// ---------------------------------------------------------------------------------------------
function clearSlots() {
  state.slots = {};
  renderSlots();
}

function renderSlots() {
  $("#slots").innerHTML = docTypes().map((dt) => {
    const s = state.slots[dt];
    if (s) {
      return `<div class="slot filled" data-dt="${dt}">
        <div class="filled-view">${s.url ? `<img src="${esc(s.url)}" alt="">` : '<div class="slot-ico">📄</div>'}
          <div><div class="ok">✓ ${esc(DOC[dt].name)} added</div><div class="fname">${esc(s.file.name)}</div>
          ${s.example ? '<span class="tag">demo document · instant result</span>' : '<span class="tag">your file · read live by AI</span>'}</div></div>
        <button class="remove" data-remove="${dt}" aria-label="Remove ${esc(DOC[dt].name)}">✕ Remove</button></div>`;
    }
    return `<label class="slot" data-dt="${dt}">
      <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" aria-label="${esc(DOC[dt].name)}">
      <div><div class="slot-ico" aria-hidden="true">${DOC[dt].icon}</div><div class="slot-name">${esc(DOC[dt].name)}</div>
        <div class="slot-hint">Drag it here, or click to choose a file</div></div></label>`;
  }).join("");
  $$(".slot").forEach((el) => {
    const dt = el.dataset.dt;
    el.querySelector("input")?.addEventListener("change", (e) => e.target.files[0] && placeFile(e.target.files[0], dt));
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("over"); });
    el.addEventListener("dragleave", () => el.classList.remove("over"));
    el.addEventListener("drop", async (e) => {
      e.preventDefault();
      el.classList.remove("over");
      const ref = e.dataTransfer.getData("application/x-papertrail");
      if (ref) { const r = JSON.parse(ref); return placeExample(r.pack, r.id, r.dt); }
      if (e.dataTransfer.files.length) return placeFile(e.dataTransfer.files[0], dt);
    });
  });
  $$("[data-remove]").forEach((b) => b.addEventListener("click", (e) => {
    e.preventDefault();
    delete state.slots[b.dataset.remove];
    renderSlots();
  }));
  $("#run-btn").disabled = state.busy || docTypes().some((dt) => !state.slots[dt]);
}

async function placeExample(pack, id, dt) {
  if (pack !== state.pack) selectPack(pack);
  const s = state.samples[pack].find((x) => x.id === id);
  const n = state.samples[pack].indexOf(s) + 1;
  const url = `/samples/${pack}/${id}/${s.documents[dt]}`;
  const blob = await (await fetch(url)).blob();
  const file = new File([blob], `${pack}-${n}-${id}-${s.documents[dt]}`, { type: blob.type || "image/jpeg" });
  state.slots[dt] = { file, url: URL.createObjectURL(file), example: { pack, sample: s, doc_type: dt } };
  renderSlots();
  setMessage("");
}

async function sha256(blob) {
  const buf = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function findExample(file) {
  const hash = await sha256(file);
  for (const [pack, list] of Object.entries(state.samples)) {
    for (const s of list) for (const [dt, h] of Object.entries(s.hashes || {})) if (h === hash) return { pack, sample: s, doc_type: dt };
  }
  return null;
}

async function placeFile(file, dt) {
  if (file.size > MAX_BYTES) return setMessage(`${file.name} is larger than 5 MB. Please use a smaller file.`, true);
  if (!["image/jpeg", "image/png", "image/webp", "application/pdf"].includes(file.type)) {
    return setMessage("Please use a JPG, PNG or WEBP image, or a PDF (we read its first page).", true);
  }
  const ex = await findExample(file);  // a downloaded demo document dropped from the desktop
  if (ex) return placeExample(ex.pack, ex.sample.id, ex.doc_type);
  const url = file.type === "application/pdf" ? "" : URL.createObjectURL(file);
  state.slots[dt] = { file, url, example: null };
  renderSlots();
  setMessage("");
}

// ---------------------------------------------------------------------------------------------
// running a check
// ---------------------------------------------------------------------------------------------
function setMessage(text, err = false) {
  const m = $("#message");
  m.textContent = text;
  m.classList.toggle("err", err);
}

function setProgress(steps, active) {
  const el = $("#progress");
  el.hidden = !steps;
  if (!steps) return;
  el.innerHTML = steps.map((s, i) => `<div class="pstep ${i < active ? "done" : i === active ? "doing" : ""}">
    <span class="dot">${i < active ? "✓" : ""}</span>${esc(s)}</div>`).join("");
}

async function sampleResult(pack, id) {
  const key = `${pack}/${id}`;
  if (!state.resultCache[key]) state.resultCache[key] = await getJSON(`/samples/${key}/result.json`);
  return state.resultCache[key];
}

async function api(path, options) {
  const r = await fetch(path, options);
  let body = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) {
    const err = new Error(body?.message || body?.detail || `The server returned an error (${r.status}).`);
    err.status = r.status;
    err.fallback = body?.fallback || null;
    throw err;
  }
  return body;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    document.head.appendChild(Object.assign(document.createElement("script"), { src, onload: resolve, onerror: () => reject(new Error("could not load the text-recognition engine")) }));
  });
}

let ocrWorker = null;
async function browserOCR(blob) {
  if (!ocrWorker) {
    await loadScript(TESSERACT_URL);
    ocrWorker = await window.Tesseract.createWorker("eng");
  }
  return (await ocrWorker.recognize(blob)).data.text;
}

async function toImageBlob(file) {
  let source;
  if (file.type === "application/pdf") {
    const pdfjs = await import(PDFJS_URL);
    pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
    const page = await (await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise).getPage(1);
    const viewport = page.getViewport({ scale: 2 });
    source = Object.assign(document.createElement("canvas"), { width: viewport.width, height: viewport.height });
    await page.render({ canvas: source, canvasContext: source.getContext("2d"), viewport }).promise;
  } else {
    source = await createImageBitmap(file, { imageOrientation: "from-image" });
  }
  const scale = Math.min(1, 1600 / Math.max(source.width, source.height));
  const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(source.width * scale), height: Math.round(source.height * scale) });
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
}

async function readLive(slot, dt, pipeline, onFallback) {
  const blob = await toImageBlob(slot.file);
  const cacheKey = `pt.x2.${state.pack}.${pipeline}.${dt}.${await sha256(blob)}`;
  const cached = store.get("localStorage", cacheKey, null);
  if (cached) return { res: cached, blob, live: false };
  const send = (ocrText) => {
    const form = new FormData();
    form.append("file", blob, "document.jpg");
    form.append("pack", state.pack);
    form.append("doc_type", dt);
    form.append("pipeline", pipeline);
    if (ocrText) form.append("ocr_text", ocrText);
    return api("/api/extract", { method: "POST", body: form });
  };
  let res;
  if (pipeline === "ocr+llm") {
    res = await send(await browserOCR(blob));
  } else {
    try {
      res = await send(null);
    } catch (err) {
      if (err.fallback !== "ocr") throw err;
      onFallback();
      res = await send(await browserOCR(blob));
    }
  }
  store.set("localStorage", cacheKey, res);
  return { res, blob, live: true };
}

async function run() {
  const types = docTypes();
  if (state.busy || types.some((dt) => !state.slots[dt])) return;
  state.busy = true;
  renderSlots();
  setMessage("");
  const pipeline = document.querySelector('input[name="pipeline"]:checked').value;
  const matching = document.querySelector('input[name="matching"]:checked').value;
  const steps = [...types.map((dt) => `Reading the ${DOC[dt].name.toLowerCase()}`), "Comparing the two", "Verdict"];
  try {
    const documents = {};
    const images = {};
    const notes = [];
    let live = 0;
    const examples = [];
    for (const [i, dt] of types.entries()) {
      setProgress(steps, i);
      const slot = state.slots[dt];
      if (slot.example) {
        await sleep(350);  // the result is saved; the pause only lets you see the steps
        documents[dt] = (await sampleResult(slot.example.pack, slot.example.sample.id)).documents[slot.example.doc_type];
        images[dt] = slot.url;
        examples.push(slot.example);
      } else {
        const used = store.get("sessionStorage", "pt.live", 0) + live;
        if (used >= SESSION_LIVE_CAP) throw new Error(`You've used the ${SESSION_LIVE_CAP} free live reads for this visit. The demo documents still work instantly.`);
        const { res, blob, live: wasLive } = await readLive(slot, dt, pipeline, () => {
          notes.push(`The vision AI was at its free daily limit, so the ${DOC[dt].name.toLowerCase()} was read with text recognition and a backup AI model instead.`);
          setMessage(`Vision AI is busy; reading the ${DOC[dt].name.toLowerCase()} with text recognition instead (a few seconds)…`);
        });
        documents[dt] = res;
        images[dt] = slot.url || URL.createObjectURL(blob);
        live += wasLive ? 1 : 0;
      }
    }
    setProgress(steps, types.length);
    let evaluation;
    const samePair = examples.length === types.length && examples.every((e) => e.sample.id === examples[0].sample.id);
    if (samePair) {
      await sleep(300);
      evaluation = (await sampleResult(state.pack, examples[0].sample.id)).evaluation;
    } else {
      evaluation = await api("/api/evaluate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pack: state.pack, case_id: `case-${Date.now().toString(36)}`, matching,
          documents: Object.fromEntries(types.map((dt) => [dt, documents[dt].data])),
          images: Object.fromEntries(types.map((dt) => [dt, { dhash: documents[dt].image?.dhash }]).filter(([, v]) => v.dhash)),
          history: store.get("sessionStorage", `pt.history.${state.pack}`, []),
        }),
      });
      store.set("sessionStorage", `pt.history.${state.pack}`, [...store.get("sessionStorage", `pt.history.${state.pack}`, []), ...(evaluation.history_add || [])]);
    }
    store.set("sessionStorage", "pt.live", store.get("sessionStorage", "pt.live", 0) + live);
    setProgress(steps, steps.length);
    for (const d of Object.values(documents)) for (const n of d.notes || []) if (!notes.includes(n) && !notes.length) notes.push(n);
    const c = {
      pack: state.pack, documents, evaluation, images, examples: examples.length, live,
      notes: [...notes, ...(evaluation.notes || [])], scenario: samePair ? examples[0].sample : null, at: Date.now(),
    };
    showResult(c);
    addRecent(c);
    setTimeout(() => setProgress(null), 900);
  } catch (err) {
    setProgress(null);
    setMessage(err.message + (err.status === 429 ? " Tip: the demo documents always work instantly." : ""), true);
  } finally {
    state.busy = false;
    renderSlots();
  }
}

// ---------------------------------------------------------------------------------------------
// verdict
// ---------------------------------------------------------------------------------------------
const VERDICT = {
  approve: { icon: "✓", label: "Looks good", title: "Everything matches. Safe to approve." },
  needs_review: { icon: "!", label: "Double-check", title: "Nothing is clearly wrong, but a person should take a look." },
  flag: { icon: "✕", label: "Problem found", title: "Don't approve this yet." },
};

function rowStatus(findings) {
  if (!findings.length) return "pass";
  return findings.some((f) => f.severity === "hard" && !f.unverifiable) ? "fail" : "unsure";
}

function checklist(c) {
  const ev = c.evaluation;
  const rows = [];
  const always = VALIDATOR_ROWS[c.pack] || [];
  for (const code of [...always, ...OPTIONAL_ROWS]) {
    const fs = ev.validations.filter((f) => f.code === code && f.severity !== "info");
    if (!fs.length && !always.includes(code)) continue;  // optional rows only appear when something is off
    rows.push({ code, status: rowStatus(fs), details: fs.map((f) => humanize(f.message)), fields: fs.flatMap((f) => f.fields) });
  }
  for (const o of ev.crosschecks) {
    const fs = o.findings.filter((f) => f.severity !== "info");
    rows.push({ code: o.code, status: o.status === "pass" ? "pass" : rowStatus(fs), details: fs.map((f) => humanize(f.message)), fields: fs.flatMap((f) => f.fields) });
  }
  const reading = ev.decision.reasons.filter((r) => ["low_confidence", "critical_field_missing"].includes(r.code));
  rows.push({ code: reading[0]?.code || "low_confidence", status: reading.length ? "unsure" : "pass",
    details: reading.map((r) => humanize(r.message)), fields: reading.flatMap((r) => r.fields) });
  const order = { fail: 0, unsure: 1, pass: 2 };
  return rows.sort((a, b) => order[a.status] - order[b.status]);
}

function showResult(c) {
  const d = c.evaluation.decision;
  const v = VERDICT[d.outcome];
  const rows = checklist(c);
  const problems = rows.filter((r) => r.status === "fail").length;
  const unsure = rows.filter((r) => r.status === "unsure").length;
  const passed = rows.filter((r) => r.status === "pass").length;
  const summary = d.outcome === "flag"
    ? `${problems} problem${problems === 1 ? "" : "s"} found; ${passed} of ${rows.length} checks passed.`
    : d.outcome === "needs_review"
      ? `${passed} of ${rows.length} checks passed; ${unsure} need${unsure === 1 ? "s" : ""} a person to confirm.`
      : `All ${rows.length} checks passed.`;
  const reasons = d.reasons.filter((r) => r.code !== "all_clear").slice(0, 4);
  const types = docTypes(c.pack);

  $("#result").innerHTML = `
    <section class="verdict ${d.outcome}">
      <div class="v-head"><div class="v-icon" aria-hidden="true">${v.icon}</div>
        <div><div class="v-label">${v.label}</div><div class="v-title">${esc(v.title)}</div><div class="v-sub">${esc(summary)}</div></div></div>
      ${reasons.length ? `<ul class="v-reasons">${reasons.map((r) => `<li data-fields="${esc(JSON.stringify(r.fields))}" title="Click to highlight it in the documents">
          <strong>${r.severity === "hard" ? "✕" : "!"} ${esc(CHECK[r.code]?.[1] || "Needs attention")}</strong>
          <span>${esc(humanize(r.message))}</span></li>`).join("")}</ul>` : ""}
      ${c.scenario ? `<p class="v-note">This was demo example “${esc(c.scenario.title)}”. ${c.scenario.seeded.length ? `The problem hidden in it: <b>${esc(c.scenario.seeded.map((k) => ISSUE[k] || k).join(" + "))}</b>.` : "No problem was hidden in it."}</p>` : ""}
      <p class="v-human">PaperTrail only recommends. A person always makes the final decision.</p>
    </section>

    <section class="card">
      <h3 class="section-title">What we checked</h3>
      <p class="section-sub">Click a row to highlight where it comes from in the documents below.</p>
      <ul class="checklist">${rows.map((r) => `
        <li class="check ${r.status} ${r.fields.length ? "clickable" : ""}" data-fields="${esc(JSON.stringify(r.fields))}">
          <span class="ci" aria-hidden="true">${r.status === "pass" ? "✓" : r.status === "fail" ? "✕" : "!"}</span>
          <div><div class="ct">${esc(CHECK[r.code]?.[0] || r.code)}</div>${r.status !== "pass" && r.details.length ? `<div class="cd">${r.details.map(esc).join("<br>")}</div>` : ""}</div>
          <span class="cs">${r.status === "pass" ? "OK" : r.status === "fail" ? "Problem" : "Check"}</span></li>`).join("")}</ul>
    </section>

    <section class="card">
      <h3 class="section-title">What the AI read</h3>
      <p class="section-sub">Every detail the AI wrote down from each document. Click a picture to enlarge it.</p>
      <div class="docs">${types.map((dt) => docCard(c, dt)).join("")}</div>
      <p class="legend"><span class="unsure-tag">unsure</span> means the AI wasn't confident about this value, so a person should double-check it. “–” means the value isn't on the document or couldn't be read (the AI never guesses).</p>
    </section>

    <section class="card"><details class="behind"><summary>Behind the scenes (for the technically curious)</summary><div class="tech">
      ${behind(c)}
    </div></details></section>`;

  $$("#result [data-fields]").forEach((el) => el.addEventListener("click", () => highlight(JSON.parse(el.dataset.fields))));
  $$("#result .doc-img").forEach((el) => el.addEventListener("click", () => { $("#zoom img").src = el.dataset.src; $("#zoom").showModal(); }));
  $("#export")?.addEventListener("click", () => download(c));
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function docCard(c, dt) {
  const x = c.documents[dt];
  const data = x.data || {};
  const scalar = Object.entries(data).filter(([, v]) => !Array.isArray(v));
  const lists = Object.entries(data).filter(([, v]) => Array.isArray(v));
  const cell = (f) => {
    if (!f || f.value === null || f.value === undefined) return `<span class="na" title="${esc(f?.null_reason || "")}">–</span>`;
    return `${esc(fmt(f.value))}${f.confidence < UNSURE_BELOW ? '<span class="unsure-tag">unsure</span>' : ""}`;
  };
  const img = c.images?.[dt];
  return `<article class="doc">
    <div class="doc-head"><h4>${DOC[dt].icon} ${esc(DOC[dt].name)}</h4></div>
    ${img ? `<div class="doc-img" data-src="${esc(img)}"><img src="${esc(img)}" alt="${esc(DOC[dt].name)}"></div>` : ""}
    <table class="facts"><tbody>${scalar.map(([k, f]) => `<tr data-ref="${dt}.${k}"><td>${esc(label(k))}</td><td>${cell(f)}</td></tr>`).join("")}</tbody></table>
    ${lists.map(([k, items]) => {
      const cols = items.length ? Object.keys(items[0]).filter((col) => items.some((it) => it[col]?.value !== null && it[col]?.value !== undefined)) : [];
      return `<div class="items-wrap"><h5>${esc(label(k))} (${items.length})</h5>${items.length ? `<table class="items">
        <thead><tr>${cols.map((col) => `<th>${esc(label(col))}</th>`).join("")}</tr></thead>
        <tbody>${items.map((it, i) => `<tr>${cols.map((col) => `<td data-ref="${dt}.${k}[${i}].${col}">${cell(it[col])}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>` : '<p class="muted">None found.</p>'}</div>`;
    }).join("")}
  </article>`;
}

function behind(c) {
  const docs = Object.values(c.documents);
  const models = [...new Set(docs.map((d) => d.model))].join(", ");
  const tokens = docs.reduce((s, d) => s + (d.usage?.total_tokens || 0), 0);
  const secs = docs.reduce((s, d) => s + (d.latency_s || 0), 0);
  const ev = c.evaluation;
  return [
    c.examples ? `<p>${c.examples === docs.length ? "Both documents are" : "One document is"} a bundled demo document. It was read once by the live AI and the result was saved, so it shows instantly and uses no AI quota.</p>` : "",
    ...c.notes.map((n) => `<p>${esc(n)}</p>`),
    `<p><b>AI model(s):</b> ${esc(models)} · <b>matching:</b> ${esc(ev.matching === "rules" ? "exact rules" : "rules, plus AI for differently-named items")} · <b>tokens:</b> ${tokens.toLocaleString()} · <b>AI reading time:</b> ${secs.toFixed(1)}s</p>`,
    `<p><b>Raw decision reasons:</b></p><pre>${esc(ev.decision.reasons.map((r) => `[${r.severity}] ${r.code}: ${r.message}`).join("\n"))}</pre>`,
    '<p><button class="btn-sm" id="export">Download the full result (JSON)</button></p>',
  ].join("");
}

function highlight(fields) {
  $$("#result .hl").forEach((el) => el.classList.remove("hl"));
  let first = null;
  for (const ref of fields || []) {
    $$("#result [data-ref]").forEach((el) => {
      const r = el.dataset.ref;
      if (r === ref || r.startsWith(`${ref}.`) || r.startsWith(`${ref}[`)) { el.classList.add("hl"); first = first || el; }
    });
  }
  first?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function download(c) {
  const blob = new Blob([JSON.stringify({ ...c, images: undefined }, null, 2)], { type: "application/json" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `papertrail-${c.pack}-${c.at}.json` });
  a.click();
  URL.revokeObjectURL(a.href);
}

function showPlaceholder() {
  $("#result").innerHTML = `<div class="placeholder"><div class="ph-icon" aria-hidden="true">🔍</div><h3>Your verdict will appear here</h3>
    <p>Not sure where to start? Click <strong>Try this example</strong> in the “Demo documents” panel.</p></div>`;
}

// ---------------------------------------------------------------------------------------------
// recent checks (this browser tab only)
// ---------------------------------------------------------------------------------------------
const recent = [];
function addRecent(c) {
  recent.unshift(c);
  recent.splice(8);
  renderRecent();
}
function renderRecent() {
  $("#recent").hidden = !recent.length;
  $("#recent-list").innerHTML = recent.map((c, i) => `<div class="recent-item" data-i="${i}">
    <span class="badge ${c.evaluation.decision.outcome}">${VERDICT[c.evaluation.decision.outcome].label}</span>
    <span>${USECASES[c.pack].icon} ${esc(c.scenario ? c.scenario.title : `${USECASES[c.pack].name}: your documents`)}</span>
    <span class="muted" style="margin-left:auto">${new Date(c.at).toLocaleTimeString()}</span></div>`).join("");
  $$(".recent-item").forEach((el) => el.addEventListener("click", () => {
    const c = recent[Number(el.dataset.i)];
    if (c.pack !== state.pack) selectPack(c.pack);
    showResult(c);
  }));
}

// ---------------------------------------------------------------------------------------------
// How it works + accuracy pages
// ---------------------------------------------------------------------------------------------
function renderMarkdown(md, base) {
  const div = document.createElement("div");
  div.innerHTML = window.DOMPurify.sanitize(window.marked.parse(md));
  div.querySelectorAll("img").forEach((img) => {
    const src = img.getAttribute("src");
    if (src && !/^(https?:|\/|data:)/.test(src)) img.src = `${base}/${src}`;
  });
  return div.innerHTML;
}

function renderHow() {
  $("#usecase-explain").innerHTML = Object.values(USECASES).map((u) => `<div class="ue">
    <div style="font-size:26px">${u.icon}</div><h3>${u.name}</h3><p>${u.story}</p>
    <ul>${u.checks.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>`).join("");
  $(".engineers", $("#page-how")).addEventListener("toggle", async (e) => {
    if (!e.target.open || loaded.arch) return;
    loaded.arch = true;
    try {
      const { default: mermaid } = await import(MERMAID_URL);
      mermaid.initialize({ startOnLoad: false, theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "neutral", securityLevel: "strict" });
      await mermaid.run({ nodes: [$("#arch")] });
    } catch { /* the diagram text stays readable */ }
    try { $("#pack-guide").innerHTML = renderMarkdown(await (await fetch("/docs/ADDING_A_PACK.md")).text(), "/docs"); } catch { /* optional */ }
  });
}

async function renderAccuracy() {
  let summary = {};
  try { summary = await getJSON("/benchmark/summary.json"); } catch { /* shown as unavailable */ }
  const names = { claims: "Health insurance claims", payables: "Supplier invoices" };
  $("#acc-cards").innerHTML = ["claims", "payables"].map((pack) => {
    const r = summary[`${pack}__vlm_llm`];
    if (!r) return `<section class="acc-pack"><h2>${names[pack]}</h2><p class="muted">Results not available yet.</p></section>`;
    const ocr = summary[`${pack}__ocr_llm`];
    const oracle = summary[`${pack}__oracle_llm`];
    const m = r.detection.micro;
    const partial = r.n_pairs < r.dataset.total_pairs ? ` (${r.n_pairs} of ${r.dataset.total_pairs} tested so far)` : "";
    return `<section class="acc-pack"><h2>${USECASES[pack].icon} ${names[pack]}</h2>
      <p class="sub">${r.n_pairs} document pairs read by the vision AI${partial}.</p>
      <div class="stats">
        <div class="stat"><div class="big t-good">${pct(m.recall)}</div><div class="lbl">of planted problems caught</div><div class="exp">How often a hidden mistake was flagged.</div></div>
        <div class="stat"><div class="big">${pct(m.false_alarm_rate)}</div><div class="lbl">false alarms</div><div class="exp">Clean pairs that were wrongly raised. Lower is better.</div></div>
        <div class="stat"><div class="big">${pct(r.extraction_f1)}</div><div class="lbl">reading accuracy</div><div class="exp">Field-level F1: how correctly details were read.</div></div>
        <div class="stat"><div class="big">${pct(r.decision.accuracy)}</div><div class="lbl">right verdict</div><div class="exp">Approve / double-check / stop, exactly as expected.</div></div>
      </div>
      ${oracle ? `<p class="acc-note"><b>Where the mistakes come from:</b> given perfectly read documents, the checking logic catches ${pct(oracle.detection.micro.recall)} of problems with ${pct(oracle.detection.micro.false_alarm_rate)} false alarms${
        oracle.detection.micro.false_alarm_rate <= m.false_alarm_rate && oracle.detection.micro.recall >= m.recall - 0.05
          ? ", so most remaining errors come from <i>reading</i> the documents, not from the checks" : ""}.</p>` : ""}
      ${ocr ? `<p class="acc-note"><b>Vision AI vs. text recognition:</b> reading the text first with OCR instead caught ${pct(ocr.detection.micro.recall)} of problems with ${pct(ocr.detection.micro.false_alarm_rate)} false alarms (reading accuracy ${pct(ocr.extraction_f1)}).</p>` : ""}
    </section>`;
  }).join("");
  $(".engineers", $("#page-accuracy")).addEventListener("toggle", async (e) => {
    if (!e.target.open || loaded.bench) return;
    loaded.bench = true;
    try { $("#bench-md").innerHTML = renderMarkdown(await (await fetch("/benchmark/BENCHMARK.md")).text(), "/benchmark"); } catch { $("#bench-md").textContent = "Report not available."; }
  });
}

boot().catch((e) => { $("#result").innerHTML = `<div class="placeholder"><h3>Could not start the demo</h3><p>${esc(e.message)}</p></div>`; });
