// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  step: 1,
  files: [],
  pendingFiles: [],
  modules: [{ name: "Module 1", weightage: 100 }],
  difficulty: { Easy: 30, Medium: 40, Hard: 30 },
  marksScheme: [{ marks: 2, count: 6, attempt: 6 }, { marks: 5, count: 6, attempt: 6 }, { marks: 10, count: 4, attempt: 4 }],
  paper: null,
  dashboard: null,
  charts: {},
};

const DIFF_OPTIONS = ["Easy", "Medium", "Hard"];
const BLOOM_OPTIONS = ["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"];

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function toast(msg, isError) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 3500);
}

async function api(path, opts) {
  const res = await fetch(path, { cache: "no-store", ...opts });
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-json (file download) */ }
  if (!res.ok) {
    const msg = (data && data.error) ? data.error : `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------------------
// Stepper navigation
// ---------------------------------------------------------------------------
function showStep(n) {
  state.step = n;
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  document.getElementById(`panel-${n}`).classList.add("active");
  document.querySelectorAll(".step").forEach(s => {
    const sn = parseInt(s.dataset.step, 10);
    s.classList.toggle("active", sn === n);
    s.classList.toggle("done", sn < n);
  });
  if (n === 5 && state.dashboard) renderDashboard();
}

document.querySelectorAll(".step").forEach(el => {
  el.addEventListener("click", () => {
    const n = parseInt(el.dataset.step, 10);
    if (n <= 3 || state.paper) showStep(n);
    else toast("Generate the paper first.", true);
  });
});
document.querySelectorAll("[data-goto]").forEach(el => {
  el.addEventListener("click", () => showStep(parseInt(el.dataset.goto, 10)));
});

document.getElementById("resetBtn").addEventListener("click", async () => {
  if (!confirm("Start a new project? This clears all uploaded files and the current paper.")) return;
  await api("/api/reset", { method: "POST" });
  location.reload();
});

// ===========================================================================
// STEP 1: UPLOAD
// ===========================================================================
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
let uploadInProgress = false;

document.getElementById("browseBtn").addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dropzone.addEventListener("click", () => fileInput.click());
["dragenter", "dragover"].forEach(evt => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach(evt => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => { handleFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", (e) => handleFiles(e.target.files));

async function handleFiles(fileList) {
  const files = Array.from(fileList || []);
  if (uploadInProgress || !files.length) return;
  uploadInProgress = true;
  let uploadStatus = document.getElementById("uploadStatus");
  if (!uploadStatus) {
    uploadStatus = document.createElement("div");
    uploadStatus.id = "uploadStatus";
    uploadStatus.className = "upload-status";
    uploadStatus.setAttribute("role", "status");
    uploadStatus.setAttribute("aria-live", "polite");
    dropzone.insertAdjacentElement("afterend", uploadStatus);
  }
  uploadStatus.style.display = "block";
  const selectedLangs = Array.from(document.querySelectorAll("#ocrLangOptions input:checked")).map(i => i.value);
  const languages = (selectedLangs.length ? selectedLangs : ["english"]).join(",");
  const errors = [];
  const addedFiles = [];
  state.pendingFiles = files.map((file, index) => ({
    id: `pending-${Date.now()}-${index}`,
    filename: file.name,
    uploading: true,
    progress: 0,
    phase: "uploading",
  }));
  renderFileTable();
  try {
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      const pendingId = state.pendingFiles[index]?.id;
      const updateProgress = (phase, progress) => {
        const pending = state.pendingFiles.find(item => item.id === pendingId);
        if (!pending) return;
        pending.phase = phase;
        pending.progress = progress;
        uploadStatus.textContent = phase === "uploading"
          ? `Uploading ${file.name} (${index + 1} of ${files.length}): ${progress}%`
          : `Upload complete. Reading ${file.name} (${index + 1} of ${files.length}) with OCR...`;
        renderFileTable();
      };
      const form = new FormData();
      form.append("files", file);
      form.append("languages", languages);
      try {
        const result = await uploadFileWithProgress(form, updateProgress);
        errors.push(...(result.errors || []));
        if (result.added && result.added.length) {
          const addedIds = new Set(result.added.map(item => item.id));
          state.files = [...state.files.filter(item => !addedIds.has(item.id)), ...result.added];
          addedFiles.push(...result.added);
        }
      } catch (e) {
        errors.push(`${file.name}: ${e.message}`);
      }
      state.pendingFiles = state.pendingFiles.filter(item => item.id !== pendingId);
      renderFileTable();
    }
    const errBox = document.getElementById("uploadErrors");
    if (errors.length) {
      errBox.style.display = "block";
      errBox.textContent = errors.join("\n");
    } else {
      errBox.style.display = "none";
    }
    if (addedFiles.length) {
      const ocrCount = addedFiles.filter(file => file.ocr_used).length;
      toast(`Added ${addedFiles.length} file(s)${ocrCount ? ` (${ocrCount} read via OCR)` : ""}.`);
    }
    await refreshFiles();
  } catch (e) {
    toast(e.message, true);
  } finally {
    state.pendingFiles = [];
    uploadStatus.style.display = "none";
    renderFileTable();
    uploadInProgress = false;
    fileInput.value = "";
  }
}

function uploadFileWithProgress(form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/pdfs");
    xhr.upload.addEventListener("progress", event => {
      if (event.lengthComputable) {
        onProgress("uploading", Math.min(100, Math.round(event.loaded / event.total * 100)));
      }
    });
    xhr.upload.addEventListener("load", () => onProgress("processing", 100));
    xhr.addEventListener("load", () => {
      let result = {};
      try { result = JSON.parse(xhr.responseText); } catch (e) { /* handled as request failure below */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(result);
      else reject(new Error(result.error || `Upload failed (${xhr.status}).`));
    });
    xhr.addEventListener("error", () => reject(new Error("Network error while uploading file.")));
    xhr.addEventListener("abort", () => reject(new Error("File upload was cancelled.")));
    xhr.send(form);
  });
}

async function loadOcrLanguages() {
  try {
    const langs = await api("/api/ocr-languages");
    const names = Object.keys(langs || {});
    const box = document.getElementById("ocrLangBox");
    const opts = document.getElementById("ocrLangOptions");
    if (!names.length) { box.style.display = "none"; return; }
    opts.innerHTML = "";
    names.forEach(name => {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.value = name;
      cb.checked = name === "english";
      label.appendChild(cb);
      label.append(" " + name.charAt(0).toUpperCase() + name.slice(1));
      opts.appendChild(label);
    });
    box.style.display = "flex";
  } catch (e) { /* OCR languages endpoint unavailable - skip silently */ }
}

async function refreshFiles() {
  state.files = await api("/api/pdfs");
  const modules = await api("/api/modules");
  if (modules && modules.length) {
    state.modules = modules;
    renderModuleTable();
  }
  renderFileTable();
}

function renderFileTable() {
  const body = document.getElementById("fileTableBody");
  const emptyHint = document.getElementById("fileEmptyHint");
  body.innerHTML = "";
  const visibleFiles = [...state.files, ...state.pendingFiles];
  emptyHint.style.display = visibleFiles.length ? "none" : "block";

  visibleFiles.forEach(f => {
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    tdName.textContent = f.filename;
    if (f.ocr_used) {
      const badge = document.createElement("span");
      badge.className = "ocr-badge"; badge.textContent = "OCR";
      tdName.appendChild(badge);
    }
    tr.appendChild(tdName);

    const tdWords = document.createElement("td");
    if (f.uploading) {
      const progress = document.createElement("div");
      progress.className = `file-progress ${f.phase === "processing" ? "is-processing" : ""}`;
      progress.style.setProperty("--progress", `${f.progress || 0}%`);
      progress.setAttribute("role", "progressbar");
      progress.setAttribute("aria-valuemin", "0");
      progress.setAttribute("aria-valuemax", "100");
      progress.setAttribute("aria-valuenow", String(f.progress || 0));
      const indicator = document.createElement("span");
      indicator.textContent = f.phase === "processing" ? "" : `${f.progress || 0}%`;
      progress.appendChild(indicator);
      const phase = document.createElement("span");
      phase.className = "file-progress-label";
      phase.textContent = f.phase === "processing" ? "Reading / OCR..." : "Uploading";
      tdWords.append(progress, phase);
    } else {
      tdWords.textContent = f.word_count;
    }
    tr.appendChild(tdWords);

    const tdOcr = document.createElement("td");
    tdOcr.textContent = f.uploading ? (f.phase === "processing" ? "Reading" : "Waiting") : f.ocr_used ? "Yes" : "No";
    tr.appendChild(tdOcr);

    const tdRemove = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "icon-btn danger"; btn.textContent = "✕";
    btn.disabled = Boolean(f.uploading);
    if (!f.uploading) {
      btn.addEventListener("click", async () => {
        await api(`/api/pdfs/${f.id}`, { method: "DELETE" });
        await refreshFiles();
      });
    }
    tdRemove.appendChild(btn);
    tr.appendChild(tdRemove);

    body.appendChild(tr);
  });
}

document.getElementById("toStep2").addEventListener("click", () => {
  if (!state.files.length) { toast("Upload at least one file first.", true); return; }
  showStep(2);
});

// ===========================================================================
// STEP 2: CONFIGURE
// ===========================================================================
function renderModuleTable() {
  const body = document.getElementById("moduleTableBody");
  body.innerHTML = "";
  state.modules.forEach((m, i) => {
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    const nameInp = document.createElement("input");
    nameInp.type = "text"; nameInp.value = m.name;
    nameInp.addEventListener("input", () => { m.name = nameInp.value; renderFileTable(); });
    tdName.appendChild(nameInp);
    tr.appendChild(tdName);

    const tdWeight = document.createElement("td");
    const wInp = document.createElement("input");
    wInp.type = "number"; wInp.min = 0; wInp.max = 100; wInp.value = m.weightage;
    wInp.addEventListener("input", () => { m.weightage = parseFloat(wInp.value) || 0; updateModuleTotal(); });
    tdWeight.appendChild(wInp);
    tr.appendChild(tdWeight);

    const tdRemove = document.createElement("td");
    if (state.modules.length > 1) {
      const btn = document.createElement("button");
      btn.className = "icon-btn danger"; btn.textContent = "✕";
      btn.addEventListener("click", () => {
        state.modules.splice(i, 1);
        renderModuleTable();
        updateModuleTotal();
      });
      tdRemove.appendChild(btn);
    }
    tr.appendChild(tdRemove);

    body.appendChild(tr);
  });
  updateModuleTotal();
}

function updateModuleTotal() {
  const total = state.modules.reduce((s, m) => s + (parseFloat(m.weightage) || 0), 0);
  document.getElementById("moduleTotal").textContent = total.toFixed(1);
  document.getElementById("moduleTotalWarn").textContent = Math.abs(total - 100) > 0.5 ? "(must total 100%)" : "";
}

document.getElementById("addModuleBtn").addEventListener("click", () => {
  const used = new Set(state.modules.map(m => m.name));
  let number = 1;
  while (used.has(`Module ${number}`)) number += 1;
  state.modules.push({ name: `Module ${number}`, weightage: 0 });
  renderModuleTable();
});

document.getElementById("autoModuleBtn").addEventListener("click", async () => {
  try {
    const modules = await api("/api/modules/auto", { method: "POST" });
    state.modules = modules;
    renderModuleTable();
    toast("Module weights set according to extracted text volume.");
  } catch (e) {
    toast(e.message, true);
  }
});

// -------- Marks scheme (marks-value x count table) --------
function renderMarksSchemeTable() {
  const body = document.getElementById("marksSchemeBody");
  body.innerHTML = "";
  state.marksScheme.forEach((g, i) => {
    if (g.attempt == null) g.attempt = g.count;
    const tr = document.createElement("tr");

    const tdMarks = document.createElement("td");
    const marksInp = document.createElement("input");
    marksInp.type = "number"; marksInp.min = 1; marksInp.value = g.marks;
    marksInp.addEventListener("input", () => { g.marks = parseInt(marksInp.value, 10) || 0; updateSchemeTotals(); });
    tdMarks.appendChild(marksInp);
    tr.appendChild(tdMarks);

    const tdCount = document.createElement("td");
    const countInp = document.createElement("input");
    countInp.type = "number"; countInp.min = 1; countInp.value = g.count;
    countInp.addEventListener("input", () => {
      g.count = parseInt(countInp.value, 10) || 0;
      if (g.attempt > g.count) { g.attempt = g.count; attemptInp.value = g.attempt; }
      attemptInp.max = g.count;
      updateSchemeTotals();
    });
    tdCount.appendChild(countInp);
    tr.appendChild(tdCount);

    const tdAttempt = document.createElement("td");
    const attemptInp = document.createElement("input");
    attemptInp.type = "number"; attemptInp.min = 1; attemptInp.max = g.count; attemptInp.value = g.attempt;
    attemptInp.addEventListener("input", () => {
      g.attempt = parseInt(attemptInp.value, 10) || 0;
      if (g.attempt > g.count) { g.attempt = g.count; attemptInp.value = g.attempt; }
      updateSchemeTotals();
    });
    tdAttempt.appendChild(attemptInp);
    tr.appendChild(tdAttempt);

    const tdRemove = document.createElement("td");
    if (state.marksScheme.length > 1) {
      const btn = document.createElement("button");
      btn.className = "icon-btn danger"; btn.textContent = "✕";
      btn.addEventListener("click", () => {
        state.marksScheme.splice(i, 1);
        renderMarksSchemeTable();
      });
      tdRemove.appendChild(btn);
    }
    tr.appendChild(tdRemove);

    body.appendChild(tr);
  });
  updateSchemeTotals();
}

function updateSchemeTotals() {
  const totalMarks = state.marksScheme.reduce((s, g) => s + (g.marks || 0) * (g.attempt ?? g.count ?? 0), 0);
  const totalQ = state.marksScheme.reduce((s, g) => s + (g.attempt ?? g.count ?? 0), 0);
  document.getElementById("schemeTotalMarks").textContent = totalMarks;
  document.getElementById("schemeTotalQuestions").textContent = totalQ;
}

document.getElementById("addMarksRowBtn").addEventListener("click", () => {
  state.marksScheme.push({ marks: 5, count: 2, attempt: 2 });
  renderMarksSchemeTable();
});

function updateDiffTotal() {
  const e = parseFloat(document.getElementById("diffEasy").value) || 0;
  const m = parseFloat(document.getElementById("diffMedium").value) || 0;
  const h = parseFloat(document.getElementById("diffHard").value) || 0;
  const total = e + m + h;
  document.getElementById("diffTotal").textContent = total.toFixed(1);
  document.getElementById("diffTotalWarn").textContent = Math.abs(total - 100) > 0.5 ? "(must total 100%)" : "";
  state.difficulty = { Easy: e, Medium: m, Hard: h };
}
["diffEasy", "diffMedium", "diffHard"].forEach(id => {
  document.getElementById(id).addEventListener("input", updateDiffTotal);
});

document.getElementById("toStep3").addEventListener("click", async () => {
  updateModuleTotal();
  updateDiffTotal();
  const moduleTotal = state.modules.reduce((s, m) => s + (parseFloat(m.weightage) || 0), 0);
  const diffTotal = state.difficulty.Easy + state.difficulty.Medium + state.difficulty.Hard;
  if (Math.abs(moduleTotal - 100) > 0.5) { toast("Module weightage must total 100%.", true); return; }
  if (Math.abs(diffTotal - 100) > 0.5) { toast("Difficulty weightage must total 100%.", true); return; }
  if (!state.marksScheme.length || state.marksScheme.some(g => !g.marks || !g.count)) {
    toast("Fill in every marks-scheme row with a positive marks value and count.", true); return;
  }

  try {
    await api("/api/modules", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modules: state.modules }),
    });
    await api("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        difficulty_mix: state.difficulty,
        title: document.getElementById("cfgTitle").value,
        subject: document.getElementById("cfgSubject").value,
        duration: document.getElementById("cfgDuration").value,
        education_level: document.getElementById("cfgEducationLevel").value,
        use_all_material: document.getElementById("cfgUseAllMaterial").checked,
        marks_scheme: state.marksScheme,
      }),
    });
    showStep(3);
  } catch (e) {
    toast(e.message, true);
  }
});

// ===========================================================================
// STEP 3: GENERATE
// ===========================================================================
function estimateGenerationEta(samples, progress) {
  if (progress >= 100) return "ETA: complete";
  const now = performance.now();
  const recent = samples.filter(sample => now - sample.at <= 120000).slice(-12);
  if (recent.length < 2) return "ETA: estimating";

  const first = recent[0];
  const last = recent[recent.length - 1];
  const elapsedSeconds = (last.at - first.at) / 1000;
  const progressDelta = last.value - first.value;
  if (now - last.at > 90000) return "ETA: slow batch";
  if (elapsedSeconds < 8 || progressDelta < 0.2) return "ETA: estimating";

  const secondsRemaining = (100 - progress) / (progressDelta / elapsedSeconds);
  if (!Number.isFinite(secondsRemaining) || secondsRemaining < 0) return "ETA: estimating";
  if (secondsRemaining < 60) return `ETA: ~${Math.max(5, Math.ceil(secondsRemaining / 5) * 5)}s`;
  return `ETA: ~${Math.ceil(secondsRemaining / 60)}m`;
}

function getGenerationEtaElement() {
  let eta = document.getElementById("generateEta");
  if (eta) return eta;

  const progress = document.getElementById("generateProgress");
  if (!progress) return null;
  eta = document.createElement("span");
  eta.id = "generateEta";
  eta.className = "generate-eta";
  eta.setAttribute("aria-live", "polite");
  progress.append(eta);
  return eta;
}

function setGenerationProgress(progress, message, samples) {
  const bar = document.getElementById("generateProgressBar");
  const exactValue = Math.max(0, Math.min(100, Number(progress) || 0));
  const displayValue = Math.round(exactValue);
  bar.value = exactValue;
  document.getElementById("generateProgressValue").textContent = `${displayValue}%`;
  const eta = getGenerationEtaElement();
  if (eta) eta.textContent = estimateGenerationEta(samples, exactValue);
  document.getElementById("generateStatus").textContent = `${message} (${displayValue}%)`;
}

document.getElementById("generateBtn").addEventListener("click", async () => {
  const btn = document.getElementById("generateBtn");
  const status = document.getElementById("generateStatus");
  const progress = document.getElementById("generateProgress");
  const errBox = document.getElementById("generateError");
  errBox.style.display = "none";
  state.paper = null;
  state.dashboard = null;
  document.getElementById("toStep4").disabled = true;
  btn.disabled = true;
  progress.hidden = false;
  const progressSamples = [{ value: 1, at: performance.now() }];
  setGenerationProgress(1, "Starting question generation", progressSamples);

  try {
    const started = await api("/api/generate", { method: "POST" });
    let result;
    while (!result) {
      const job = await api(`/api/generate/${encodeURIComponent(started.job_id)}`);
      const currentProgress = Number(job.progress) || 0;
      const lastSample = progressSamples[progressSamples.length - 1];
      if (currentProgress > lastSample.value) {
        progressSamples.push({ value: currentProgress, at: performance.now() });
        if (progressSamples.length > 12) progressSamples.shift();
      }
      setGenerationProgress(currentProgress, job.message || "Generating question paper", progressSamples);
      if (job.status === "failed") throw new Error(job.error || "Question generation failed.");
      if (job.status === "completed") result = job.result;
      else await new Promise(resolve => setTimeout(resolve, 500));
    }

    const incompleteMessage = incompletePaperMessage(result.paper, result.dashboard);
    if (incompleteMessage) throw new Error(incompleteMessage);
    state.paper = result.paper;
    state.dashboard = result.dashboard;
    const target = result.paper.meta.total_marks_target;
    const actual = result.paper.meta.total_marks_actual;
    const partial = actual < target || (result.paper.shortfalls || []).length > 0;
    let msg = partial
      ? `Best-supported partial paper — ${result.paper.questions.length} questions, ${actual} of ${target} target marks.`
      : `Done — ${result.paper.questions.length} questions generated, ${actual} marks.`;
    const questionWriter = result.dashboard.quality_metrics?.question_writer;
    if (questionWriter) msg += ` Question writer: ${questionWriter}.`;
    const paperWarnings = result.paper.warnings || [];
    if (paperWarnings.length) {
      msg += ` Source limitation: ${paperWarnings.join(" ")}`;
    }
    const moduleWarnings = (result.dashboard.module_analysis || [])
      .filter(m => m.target_marks !== m.actual_marks)
      .map(m => {
        const supply = result.paper.meta.candidate_supply_by_module?.[m.module] ?? 0;
        return `${m.module}: target ${m.target_marks} marks, generated ${m.actual_marks} (${supply} source candidates)`;
      });
    if (moduleWarnings.length) {
      msg += ` Module balance: ${moduleWarnings.join("; ")}.`;
    }
    status.textContent = msg;
    document.getElementById("toStep4").disabled = false;
    renderEditStep();
    toast("Question paper generated.");
  } catch (e) {
    status.textContent = "";
    errBox.style.display = "block";
    errBox.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

function incompletePaperMessage(paper, dashboard) {
  if (!paper) return "Generation returned no paper. Check the source files and configuration, then try again.";
  if (!Array.isArray(paper.questions) || !paper.questions.length) {
    return "No source-supported questions were found. Add explanatory source material or reduce the paper requirements.";
  }
  return "";
}

document.getElementById("toStep4").addEventListener("click", () => showStep(4));

// ===========================================================================
// STEP 4: EDIT PAPER
// ===========================================================================
function renderEditStep() {
  if (!state.paper) return;
  const meta = state.paper.meta;
  document.getElementById("editTitle").value = meta.title;
  document.getElementById("editSubject").value = meta.subject;
  document.getElementById("editDuration").value = meta.duration;
  document.getElementById("editTotalMarks").textContent = meta.total_marks_actual;

  renderInstructions();
  renderQuestionList();
}

["editTitle", "editSubject", "editDuration"].forEach(id => {
  document.getElementById(id).addEventListener("input", debounce(savePaperMeta, 500));
});

async function savePaperMeta() {
  if (!state.paper) return;
  state.paper.meta.title = document.getElementById("editTitle").value;
  state.paper.meta.subject = document.getElementById("editSubject").value;
  state.paper.meta.duration = document.getElementById("editDuration").value;
  await api("/api/paper", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ meta: state.paper.meta }),
  });
}

function renderInstructions() {
  const list = document.getElementById("instructionsList");
  list.innerHTML = "";
  state.paper.meta.instructions.forEach((instr, i) => {
    const li = document.createElement("li");
    const inp = document.createElement("input");
    inp.type = "text"; inp.value = instr;
    inp.addEventListener("input", debounce(() => {
      state.paper.meta.instructions[i] = inp.value;
      savePaperMeta();
    }, 500));
    li.appendChild(inp);
    const btn = document.createElement("button");
    btn.className = "icon-btn danger"; btn.textContent = "✕";
    btn.addEventListener("click", () => {
      state.paper.meta.instructions.splice(i, 1);
      renderInstructions();
      savePaperMeta();
    });
    li.appendChild(btn);
    list.appendChild(li);
  });
}

document.getElementById("addInstructionBtn").addEventListener("click", () => {
  state.paper.meta.instructions.push("New instruction");
  renderInstructions();
  savePaperMeta();
});

function difficultyClass(d) {
  return { Easy: "easy", Medium: "medium", Hard: "hard" }[d] || "";
}

function computeTotalMarksClient(questions) {
  let total = 0;
  const counted = new Set();
  questions.forEach(q => {
    const gid = q.group_id;
    if (!gid) { total += q.marks || 0; return; }
    if (counted.has(gid)) return;
    counted.add(gid);
    const siblings = questions.filter(x => x.group_id === gid);
    const attempt = Math.min(q.group_attempt || siblings.length, siblings.length);
    total += attempt * (q.marks || 0);
  });
  return total;
}

function refreshTotalMarksDisplay() {
  document.getElementById("editTotalMarks").textContent = computeTotalMarksClient(state.paper.questions);
}

function renderQuestionList() {
  const container = document.getElementById("questionList");
  container.innerHTML = "";
  const rendered = new Set();
  state.paper.questions.forEach((q) => {
    if (q.group_id) {
      if (rendered.has(q.group_id)) return;
      rendered.add(q.group_id);
      const siblings = state.paper.questions.filter(x => x.group_id === q.group_id);
      container.appendChild(buildGroupBlock(q.group_id, siblings));
    } else {
      const idx = state.paper.questions.indexOf(q);
      container.appendChild(buildQuestionCard(q, idx));
    }
  });
}

function buildGroupBlock(groupId, siblings) {
  const wrap = document.createElement("div");
  wrap.className = "question-group";
  wrap.dataset.groupId = groupId;

  const head = document.createElement("div");
  head.className = "question-group-head";

  const num = document.createElement("span");
  num.className = "q-number";
  num.textContent = `Q${siblings[0].number}.`;
  head.appendChild(num);

  const label = document.createElement("span");
  label.className = "group-choice-label";
  const attemptInp = document.createElement("input");
  attemptInp.type = "number"; attemptInp.className = "q-marks"; attemptInp.min = 1;
  attemptInp.max = siblings.length; attemptInp.value = siblings[0].group_attempt || siblings.length;
  attemptInp.addEventListener("change", () => {
    let v = parseInt(attemptInp.value, 10) || 1;
    v = Math.max(1, Math.min(v, siblings.length));
    attemptInp.value = v;
    siblings.forEach(s => { s.group_attempt = v; });
    savePaperQuestions();
  });
  label.append("Attempt any ", attemptInp, ` of the following ${siblings.length} (${siblings[0].marks} marks each)`);
  head.appendChild(label);

  const spacer = document.createElement("div");
  spacer.className = "q-spacer";
  head.appendChild(spacer);

  const addAltBtn = document.createElement("button");
  addAltBtn.className = "icon-btn"; addAltBtn.title = "Add another alternative"; addAltBtn.textContent = "+";
  addAltBtn.addEventListener("click", () => addAlternativeToGroup(groupId, siblings[0]));
  head.appendChild(addAltBtn);

  wrap.appendChild(head);

  const itemsBox = document.createElement("div");
  itemsBox.className = "question-group-items";
  siblings.forEach((s) => {
    const idx = state.paper.questions.indexOf(s);
    const card = buildQuestionCard(s, idx, { inGroup: true, label: s.group_label });
    itemsBox.appendChild(card);
  });
  wrap.appendChild(itemsBox);

  return wrap;
}

async function addAlternativeToGroup(groupId, templateQ) {
  try {
    await api("/api/paper/questions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group_id: groupId, type: templateQ.type, module: templateQ.module,
        difficulty: templateQ.difficulty, marks: templateQ.marks,
        question: "New alternative — edit me.", answer: "Model answer — edit me.",
      }),
    });
    state.paper = await api("/api/paper");
    renderQuestionList();
    toast("Alternative added — edit its text below.");
  } catch (e) {
    toast(e.message, true);
  }
}

function buildQuestionCard(q, idx, opts) {
  opts = opts || {};
  const card = document.createElement("div");
  card.className = "question-card" + (opts.inGroup ? " in-group" : "");
  card.dataset.id = q.id;

  const head = document.createElement("div");
  head.className = "question-card-head";

  const num = document.createElement("span");
  num.className = "q-number";
  num.textContent = opts.inGroup ? (opts.label || "") : `Q${q.number}`;
  head.appendChild(num);

  const badge = document.createElement("span");
  badge.className = `q-badge ${difficultyClass(q.difficulty)}`;
  badge.textContent = q.difficulty;
  head.appendChild(badge);

  const modSel = document.createElement("select");
  modSel.className = "q-select";
  state.modules.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m.name; opt.textContent = m.name;
    if (m.name === q.module) opt.selected = true;
    modSel.appendChild(opt);
  });
  modSel.addEventListener("change", () => { q.module = modSel.value; savePaperQuestions(); });
  head.appendChild(modSel);

  const diffSel = document.createElement("select");
  diffSel.className = "q-select";
  DIFF_OPTIONS.forEach(d => {
    const opt = document.createElement("option");
    opt.value = d; opt.textContent = d;
    if (d === q.difficulty) opt.selected = true;
    diffSel.appendChild(opt);
  });
  diffSel.addEventListener("change", () => {
    q.difficulty = diffSel.value; badge.className = `q-badge ${difficultyClass(q.difficulty)}`; badge.textContent = q.difficulty;
    savePaperQuestions();
  });
  head.appendChild(diffSel);

  if (!opts.inGroup) {
    const marksInp = document.createElement("input");
    marksInp.type = "number"; marksInp.className = "q-marks"; marksInp.min = 1; marksInp.value = q.marks;
    marksInp.addEventListener("change", () => {
      q.marks = parseInt(marksInp.value, 10) || 1;
      refreshTotalMarksDisplay();
      savePaperQuestions();
    });
    head.appendChild(marksInp);
  }

  const spacer = document.createElement("div");
  spacer.className = "q-spacer";
  head.appendChild(spacer);

  const actions = document.createElement("div");
  actions.className = "q-actions";

  if (!opts.inGroup) {
    const upBtn = document.createElement("button");
    upBtn.className = "icon-btn"; upBtn.title = "Move up"; upBtn.textContent = "↑";
    upBtn.addEventListener("click", () => moveQuestion(idx, -1));
    actions.appendChild(upBtn);

    const downBtn = document.createElement("button");
    downBtn.className = "icon-btn"; downBtn.title = "Move down"; downBtn.textContent = "↓";
    downBtn.addEventListener("click", () => moveQuestion(idx, 1));
    actions.appendChild(downBtn);
  }

  const regenBtn = document.createElement("button");
  regenBtn.className = "icon-btn"; regenBtn.title = "Regenerate this question"; regenBtn.textContent = "⟳";
  regenBtn.addEventListener("click", () => regenerateQuestion(q.id));
  actions.appendChild(regenBtn);

  const delBtn = document.createElement("button");
  delBtn.className = "icon-btn danger"; delBtn.title = "Delete"; delBtn.textContent = "✕";
  delBtn.addEventListener("click", () => deleteQuestion(q.id));
  actions.appendChild(delBtn);

  head.appendChild(actions);
  card.appendChild(head);

  const qLabel = document.createElement("span");
  qLabel.className = "q-answer-label"; qLabel.textContent = "Question";
  card.appendChild(qLabel);
  const qText = document.createElement("textarea");
  qText.className = "q-textarea"; qText.value = q.question; qText.rows = 2;
  qText.addEventListener("input", debounce(() => { q.question = qText.value; savePaperQuestions(); }, 500));
  card.appendChild(qText);

  const aLabel = document.createElement("span");
  aLabel.className = "q-answer-label"; aLabel.textContent = "Model Answer";
  card.appendChild(aLabel);
  const aText = document.createElement("textarea");
  aText.className = "q-textarea"; aText.value = q.answer; aText.rows = 3;
  aText.addEventListener("input", debounce(() => { q.answer = aText.value; savePaperQuestions(); }, 500));
  card.appendChild(aText);

  const meta = document.createElement("div");
  meta.className = "q-meta";
  meta.innerHTML = `<span>Topic: ${escapeHtml(q.topic || "—")}</span><span>Bloom: ${q.bloom}</span>`;
  card.appendChild(meta);

  return card;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function moveQuestion(idx, dir) {
  const arr = state.paper.questions;
  const j = idx + dir;
  if (j < 0 || j >= arr.length) return;
  [arr[idx], arr[j]] = [arr[j], arr[idx]];
  renderQuestionList();
  savePaperQuestions();
}

async function deleteQuestion(id) {
  await api(`/api/paper/questions/${id}`, { method: "DELETE" });
  state.paper = await api("/api/paper");
  refreshTotalMarksDisplay();
  renderQuestionList();
}

async function regenerateQuestion(id) {
  try {
    await api(`/api/paper/questions/${id}/regenerate`, { method: "POST" });
    state.paper = await api("/api/paper");
    renderQuestionList();
    toast("Question regenerated.");
  } catch (e) {
    toast(e.message, true);
  }
}

document.getElementById("addQuestionBtn").addEventListener("click", async () => {
  await api("/api/paper/questions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "Explanation", question: "New question — edit me.", answer: "Model answer — edit me.",
      module: state.modules[0].name, difficulty: "Medium", marks: 5,
    }),
  });
  state.paper = await api("/api/paper");
  refreshTotalMarksDisplay();
  renderQuestionList();
});

const savePaperQuestions = debounce(async () => {
  if (!state.paper) return;
  await api("/api/paper", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ questions: state.paper.questions }),
  });
  refreshTotalMarksDisplay();
}, 500);

document.getElementById("toStep5").addEventListener("click", async () => {
  try {
    state.dashboard = await api("/api/dashboard");
  } catch (e) { /* ignore, use stale */ }
  showStep(5);
});

// ===========================================================================
// STEP 5: DASHBOARD
// ===========================================================================
function renderDashboard() {
  const d = state.dashboard;
  if (!d) return;

  const q = d.quality_metrics || {};
  const cardsHtml = [
    ["Candidates Generated", q.total_candidates],
    ["Accepted", q.accepted],
    ["Rejected (invalid)", q.rejected_invalid],
    ["Duplicates Removed", q.duplicates_removed],
    ["Avg Quality Score", q.avg_quality_score],
  ].map(([label, val]) => `
    <div class="metric-card"><div class="metric-value">${val ?? 0}</div><div class="metric-label">${label}</div></div>
  `).join("");
  document.getElementById("qualityCards").innerHTML = cardsHtml;

  const moduleAnalysis = d.module_analysis || [];
  drawChart("chartModule", "bar", {
    labels: moduleAnalysis.map(m => m.module),
    datasets: [
      { label: "Target Marks", data: moduleAnalysis.map(m => m.target_marks), backgroundColor: "#DCD2FB" },
      { label: "Actual Marks", data: moduleAnalysis.map(m => m.actual_marks), backgroundColor: "#6E3AF2" },
    ],
  });

  const diffAnalysis = d.difficulty_analysis || [];
  drawChart("chartDifficulty", "bar", {
    labels: diffAnalysis.map(x => x.difficulty),
    datasets: [
      { label: "Target %", data: diffAnalysis.map(x => x.target_pct), backgroundColor: "#DCD2FB" },
      { label: "Actual %", data: diffAnalysis.map(x => x.actual_pct), backgroundColor: "#00A98F" },
    ],
  });

  const pdfAnalysis = d.pdf_analysis || [];
  drawChart("chartPdf", "bar", {
    labels: pdfAnalysis.map(p => p.filename),
    datasets: [{ label: "Questions Contributed", data: pdfAnalysis.map(p => p.questions_contributed), backgroundColor: "#00B8A9" }],
  }, true);

  const marksDiff = (d.marks_analysis || {}).by_difficulty || {};
  drawChart("chartMarksDiff", "bar", {
    labels: Object.keys(marksDiff),
    datasets: [{ label: "Marks", data: Object.values(marksDiff), backgroundColor: "#FF6F91" }],
  });
}

function drawChart(canvasId, type, data, horizontal) {
  const canvas = document.getElementById(canvasId);
  const wrapper = canvas.parentElement;
  const hasData = data.datasets.some(ds => (ds.data || []).some(v => v !== undefined && v !== null && v !== 0))
    && (data.labels || []).length > 0;

  if (state.charts[canvasId]) { state.charts[canvasId].destroy(); state.charts[canvasId] = null; }

  let emptyMsg = wrapper.querySelector(".dash-empty");
  if (!hasData) {
    canvas.style.display = "none";
    if (!emptyMsg) {
      emptyMsg = document.createElement("div");
      emptyMsg.className = "dash-empty";
      emptyMsg.textContent = "No data for this chart yet.";
      wrapper.appendChild(emptyMsg);
    }
    return;
  }
  if (emptyMsg) emptyMsg.remove();
  canvas.style.display = "block";

  try {
    const ctx = canvas.getContext("2d");
    state.charts[canvasId] = new Chart(ctx, {
      type, data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: horizontal ? "y" : "x",
        plugins: { legend: { display: type !== "bar" || data.datasets.length > 1, position: "bottom" } },
      },
    });
  } catch (e) {
    console.error(`Chart "${canvasId}" failed to render:`, e);
  }
}

// ===========================================================================
// STEP 6: EXPORT
// ===========================================================================
async function downloadFile(path, fallbackName) {
  try {
    const res = await fetch(path, { method: "POST" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || "Export failed.");
    }
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : fallbackName;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    toast(e.message, true);
  }
}

const paperPreviewDialog = document.getElementById("paperPreviewDialog");
const paperPreviewFrame = document.getElementById("paperPreviewFrame");
let paperPreviewUrl = null;

async function previewPaperPdf() {
  const previewButton = document.getElementById("previewPaperPdf");
  previewButton.disabled = true;
  previewButton.textContent = "Preparing preview...";
  try {
    const res = await fetch("/api/export/paper/pdf", { method: "POST" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || "Could not create the question paper preview.");
    }
    const pdfBlob = await res.blob();
    if (paperPreviewUrl) URL.revokeObjectURL(paperPreviewUrl);
    paperPreviewUrl = URL.createObjectURL(pdfBlob);
    paperPreviewFrame.src = paperPreviewUrl;
    paperPreviewDialog.showModal();
  } catch (e) {
    toast(e.message, true);
  } finally {
    previewButton.disabled = false;
    previewButton.textContent = "Preview Question Paper";
  }
}

document.getElementById("previewPaperPdf").addEventListener("click", previewPaperPdf);
document.getElementById("closePaperPreview").addEventListener("click", () => paperPreviewDialog.close());
paperPreviewDialog.addEventListener("close", () => {
  paperPreviewFrame.removeAttribute("src");
  if (paperPreviewUrl) {
    URL.revokeObjectURL(paperPreviewUrl);
    paperPreviewUrl = null;
  }
});

document.getElementById("exportPaperPdf").addEventListener("click", () => downloadFile("/api/export/paper/pdf", "question_paper.pdf"));
document.getElementById("exportPaperDocx").addEventListener("click", () => downloadFile("/api/export/paper/docx", "question_paper.docx"));
document.getElementById("exportKeyPdf").addEventListener("click", () => downloadFile("/api/export/answerkey/pdf", "answer_key.pdf"));
document.getElementById("exportKeyDocx").addEventListener("click", () => downloadFile("/api/export/answerkey/docx", "answer_key.docx"));

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
renderMarksSchemeTable();
renderModuleTable();
loadOcrLanguages();

(async function init() {
  try {
    state.files = await api("/api/pdfs");
    renderFileTable();
    const modules = await api("/api/modules");
    if (modules && modules.length) state.modules = modules;
    renderModuleTable();
    renderMarksSchemeTable();
    const cfg = await api("/api/config");
    if (cfg) {
      state.difficulty = cfg.difficulty_mix;
      document.getElementById("diffEasy").value = state.difficulty.Easy;
      document.getElementById("diffMedium").value = state.difficulty.Medium;
      document.getElementById("diffHard").value = state.difficulty.Hard;
      updateDiffTotal();
      document.getElementById("cfgTitle").value = cfg.config.title;
      document.getElementById("cfgSubject").value = cfg.config.subject;
      document.getElementById("cfgDuration").value = cfg.config.duration;
      document.getElementById("cfgEducationLevel").value = cfg.config.education_level || "General / Mixed";
      if (cfg.config.marks_scheme && cfg.config.marks_scheme.length) {
        state.marksScheme = cfg.config.marks_scheme;
      }
      renderMarksSchemeTable();
    }
    try {
      const savedPaper = await api("/api/paper");
      const savedDashboard = await api("/api/dashboard");
      const incompleteMessage = incompletePaperMessage(savedPaper, savedDashboard);
      if (incompleteMessage) {
        state.paper = null;
        state.dashboard = null;
        const errBox = document.getElementById("generateError");
        errBox.textContent = incompleteMessage;
        errBox.style.display = "block";
        showStep(3);
      } else {
        state.paper = savedPaper;
        state.dashboard = savedDashboard;
        document.getElementById("toStep4").disabled = false;
        renderEditStep();
      }
    } catch (e) { /* no paper yet */ }
  } catch (e) {
    console.error(e);
  }
})();
