const $ = (id) => document.getElementById(id);

let ENTRIES = [];
let SEL = -1;

let CONFIG = {
  master_style: "",
  negative_prompt: "",
  rgba_prefix: "",
  rgba_suffix: "",
};

let JOBS = [];
let STATUS = {
  model_ready: false,
};

let REFS = [];
let REF_SEL = new Set();

let hideDone = false;
let EXPORT_FOLDER = { id: "local", path: "" };
let modelDefaultsApplied = null;
let folderApiAvailable = false;
let lastFolderApiCheck = 0;

async function loadExportFolder() {
  lastFolderApiCheck = Date.now();
  try {
    displayExportFolder(await api("/api/export-folder"));
    folderApiAvailable = true;
    $("backendNotice").hidden = true;
    $("folderStatus").textContent = "";
    return true;
  } catch (error) {
    folderApiAvailable = false;
    $("exportFolderPath").value = "Serverneustart erforderlich";
    $("backendNotice").hidden = false;
    $("folderStatus").textContent =
      "Die Ordner-API ist nicht erreichbar. Starte den Python-Server neu und lade diese Seite erneut.";
    return false;
  }
}

const LARGE_FORMATS = [
  ["2048x2048", "2048 × 2048 · 2K, experimentell"],
  ["2400x1792", "2400 × 1792 · experimentell"],
  ["1792x2400", "1792 × 2400 · experimentell"],
  ["2752x1536", "2752 × 1536 · experimentell"],
];
function updateLargeFormats(allowed) {
  const select = $("fFmt");
  for (const option of [...select.options]) {
    if (option.dataset.large === "true") option.remove();
  }
  if (allowed)
    for (const [value, text] of LARGE_FORMATS) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      option.dataset.large = "true";
      select.add(option);
    }
  if (!select.value) select.value = "1024x1024";
}
function displayExportFolder(folder) {
  EXPORT_FOLDER = folder;
  $("exportFolderPath").value = folder.path;
  $("exportFolderPath").title = folder.path;
}

// Keep only the newest completed job per output path.
const latestDoneByKey = new Map();

// Suppress old completion notifications after a browser reload.
const seenDone = new Set();

let initialJobSync = true;

// Avoid reloading the same preview on every poll.
let lastPreviewJobId = null;
let lastPreviewKey = null;

function studioDialog({ title, message, input = false }) {
  return new Promise((resolve) => {
    const dialog = $("actionDialog");
    $("dialogTitle").textContent = title;
    $("dialogMessage").textContent = message;
    $("dialogField").hidden = !input;
    $("dialogInput").disabled = !input;
    $("dialogInput").value = "";
    $("dialogAccept").textContent = input
      ? "Bildauftrag anlegen"
      : "Bestätigen";
    const finish = (value) => {
      dialog.close();
      $("dialogForm").onsubmit = null;
      $("dialogCancel").onclick = null;
      dialog.oncancel = null;
      resolve(value);
    };
    $("dialogForm").onsubmit = (event) => {
      event.preventDefault();
      const value = $("dialogInput").value.trim();
      if (input && !value) return;
      finish(input ? value : true);
    };
    $("dialogCancel").onclick = () => finish(input ? null : false);
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish(input ? null : false);
    };
    dialog.showModal();
    (input ? $("dialogInput") : $("dialogCancel")).focus();
  });
}

async function confirmAction(message) {
  return studioDialog({ title: "Aktion bestätigen", message });
}

function toast(msg, isErr = false) {
  const t = $("toast");

  t.textContent = msg;
  t.className = isErr ? "err show" : "show";

  clearTimeout(t._h);

  t._h = setTimeout(() => (t.className = ""), 2600);
}

async function api(path, opts = undefined) {
  const options = {
    ...(opts || {}),
  };

  // Keep raw reference uploads free of a JSON content type.
  if (options.body != null && typeof options.body === "string") {
    options.headers = {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    };
  }

  const r = await fetch(path, options);

  if (!r.ok) {
    const text = await r.text();
    let message = text;
    try {
      message = JSON.parse(text).detail || text;
    } catch {}
    throw new Error(
      typeof message === "string" ? message : JSON.stringify(message),
    );
  }

  return r.json();
}

function exportUrl(file, dir = "", bust = 0) {
  const parts = [];

  if (dir) {
    const dirs = dir.split(/[\\/]+/).filter(Boolean);

    for (const d of dirs) {
      parts.push(encodeURIComponent(d));
    }
  }

  parts.push(encodeURIComponent(file));

  let url =
    "/api/exports/" +
    encodeURIComponent(EXPORT_FOLDER.id) +
    "/" +
    parts.join("/");

  if (bust) {
    url += "?t=" + encodeURIComponent(bust);
  }

  return url;
}

// Identical filenames in different subfolders need distinct keys.
function assetKey(file, dir = "") {
  return (dir || "").replace(/\\/g, "/") + "::" + (file || "");
}

function addBust(url, bust) {
  if (!url) return url;

  const separator = url.includes("?") ? "&" : "?";

  return (
    url +
    separator +
    "t=" +
    encodeURIComponent(Math.floor(Number(bust || Date.now())))
  );
}

function finalPrompt() {
  const p = $("fPrompt").value.trim();

  let d = $("fMaster").checked ? CONFIG.master_style + " " + p : p;

  if ($("fRgba").checked) {
    d = CONFIG.rgba_prefix + " " + d + " " + CONFIG.rgba_suffix;
  }

  return d.trim();
}

function renderPromptPreview() {
  const cfg = parseFloat($("fCfg").value) || 1.0;

  let negativeText;

  if (cfg > 1.0) {
    negativeText = CONFIG.negative_prompt;
  } else {
    negativeText = "(CFG 1.0 — Negative Prompt wird nicht für CFG verwendet)";
  }

  $("promptPreview").textContent =
    finalPrompt() + "\n\nNEGATIVE: " + negativeText;
}

function currentEntry() {
  const exportSize = parseInt($("fExportSize").value, 10);

  const [w, h] = $("fFmt").value.split("x").map(Number);

  const steps = parseInt($("fSteps").value, 10) || 30;

  const cfg = parseFloat($("fCfg").value);

  return {
    ...(ENTRIES[SEL] || {}),

    file:
      ($("fFile").value.trim() || "mein-bild.png").replace(/\.png$/i, "") +
      ".png",

    dir: $("fDir").value.trim(),

    prompt: $("fPrompt").value.trim(),

    seed: Number.isFinite(Number($("fSeed").value))
      ? Number($("fSeed").value)
      : 42,

    width: w,
    height: h,

    steps: steps,

    cfg: Number.isFinite(cfg) ? cfg : 1.0,

    allow_large: $("allowLargeFormats").checked,
    export_id: EXPORT_FOLDER.id,

    refs: [...REF_SEL],

    export_size: exportSize === 0 ? null : exportSize,

    use_master: $("fMaster").checked,

    use_rgba: $("fRgba").checked,
  };
}

let libraryBusy = false;
let lastListSignature = "";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  const button = $("themeToggle");
  button.setAttribute("aria-pressed", String(dark));
  button.setAttribute(
    "aria-label",
    dark ? "Hellmodus einschalten" : "Darkmode einschalten",
  );
  button.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#i-${dark ? "sun" : "moon"}"/></svg><span>${dark ? "Hell" : "Dunkel"}</span>`;
  try {
    localStorage.setItem("qwen-studio-theme", theme);
  } catch {}
}
applyTheme(document.documentElement.dataset.theme || "light");
$("themeToggle").onclick = () =>
  applyTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  );

async function mutateLibrary(operation) {
  if (libraryBusy) return;
  libraryBusy = true;
  try {
    return await operation();
  } catch (error) {
    toast(
      error.message || "Auftragsliste konnte nicht gespeichert werden",
      true,
    );
    return false;
  } finally {
    libraryBusy = false;
    renderList();
  }
}

async function persistEntries(entries, selectedKey) {
  const res = await api("/api/manifest", {
    method: "POST",
    body: JSON.stringify({ entries }),
  });
  ENTRIES = res.entries;
  SEL = selectedKey
    ? ENTRIES.findIndex((e) => assetKey(e.file, e.dir || "") === selectedKey)
    : -1;
}

function resetEditor() {
  SEL = -1;
  $("fFile").value = "mein-bild.png";
  $("fDir").value = "";
  $("fPrompt").value = "";
  $("fSeed").value = "42";
  $("fSteps").value = String(STATUS.default_steps || 30);
  $("fCfg").value = "1";
  $("fFmt").value = "1024x1024";
  $("fExportSize").value = "0";
  $("fMaster").checked = false;
  $("fRgba").checked = false;
  $("allowLargeFormats").checked = false;
  updateLargeFormats(false);
  REF_SEL.clear();
  lastPreviewJobId = null;
  lastPreviewKey = null;
  $("resultMeta").textContent = "";
  showPreview(null);
  renderRefs();
  renderPromptPreview();
}

async function saveManifest() {
  return mutateLibrary(async () => {
    if (SEL < 0 || !ENTRIES[SEL])
      return toast("Lege zuerst einen Bildauftrag an.", true);
    const entries = ENTRIES.map((e, i) => (i === SEL ? currentEntry() : e));
    const selected = entries[SEL];
    await persistEntries(entries, assetKey(selected.file, selected.dir || ""));
    toast("Bildaufträge gespeichert");
    return true;
  });
}

function entryCompleted(entry) {
  return Boolean(entry.done);
}

function removableCompleted(entry) {
  return (
    !jobForEntry(entry) && Boolean(entry.done || latestDoneForEntry(entry))
  );
}

async function removeEntries(keys) {
  const targets = ENTRIES.filter((e) =>
    keys.has(assetKey(e.file, e.dir || "")),
  );
  if (!targets.length) return toast("Keine passenden Bildaufträge vorhanden.");
  if (
    !(await confirmAction(
      `${targets.length === 1 ? "Diesen Bildauftrag" : targets.length + " Bildaufträge"} aus der Liste entfernen?\nPNG-Dateien und Referenzbilder bleiben erhalten. Zugehörige wartende Jobs werden abgebrochen. Laufende Generierungen laufen weiter.`,
    ))
  )
    return;
  return mutateLibrary(async () => {
    const selected = ENTRIES[SEL];
    const selectedKey = selected && assetKey(selected.file, selected.dir || "");
    const remaining = ENTRIES.filter(
      (e) => !keys.has(assetKey(e.file, e.dir || "")),
    );
    const keepKey = selectedKey && !keys.has(selectedKey) ? selectedKey : null;
    for (const job of JOBS.filter(
      (j) => j.status === "queued" && keys.has(assetKey(j.file, j.dir || "")),
    )) {
      await api("/api/job/" + job.id, { method: "DELETE" });
    }
    await persistEntries(remaining, keepKey);
    if (!remaining.length) await api("/api/jobs/history", { method: "DELETE" });
    if (SEL >= 0) select(SEL);
    else if (ENTRIES.length) select(0);
    else resetEditor();
    JOBS = JOBS.filter(
      (j) =>
        j.status === "running" ||
        j.status === "queued" ||
        ENTRIES.some(
          (e) =>
            assetKey(e.file, e.dir || "") === assetKey(j.file, j.dir || ""),
        ),
    );
    refreshLatestDoneJobs();
    renderQueue();
    toast(
      `${targets.length} ${targets.length === 1 ? "Bildauftrag" : "Bildaufträge"} entfernt. Bilder bleiben erhalten.`,
    );
  });
}

function refreshLatestDoneJobs() {
  latestDoneByKey.clear();

  // The server returns newest first, so the first completion wins.
  for (const j of JOBS) {
    if (j.status !== "done") {
      continue;
    }

    const key = assetKey(j.file, j.dir || "");

    if (!latestDoneByKey.has(key)) {
      latestDoneByKey.set(key, j);
    }
  }
}

function latestDoneForEntry(entry) {
  if (!entry) {
    return null;
  }

  return latestDoneByKey.get(assetKey(entry.file, entry.dir || "")) || null;
}

function jobForEntry(entry) {
  if (!entry) {
    return null;
  }

  const key = assetKey(entry.file, entry.dir || "");

  return (
    JOBS.find(
      (j) =>
        assetKey(j.file, j.dir || "") === key &&
        (j.status === "running" || j.status === "queued"),
    ) || null
  );
}

function renderList() {
  const signature = JSON.stringify([
    ENTRIES,
    SEL,
    hideDone,
    JOBS.map((j) => [j.id, j.status, j.image_url]),
  ]);
  if (lastListSignature === signature) return;
  lastListSignature = signature;

  const el = $("assetList");

  el.innerHTML = "";

  const visible = hideDone
    ? ENTRIES.filter((e) => !entryCompleted(e))
    : ENTRIES;

  const sorted = [...visible].sort(
    (a, b) => (a.done ? 1 : 0) - (b.done ? 1 : 0),
  );

  sorted.forEach((e) => {
    const i = ENTRIES.indexOf(e);

    const job = jobForEntry(e);

    const latest = latestDoneForEntry(e);

    const div = document.createElement("div");

    div.className =
      "asset" + (i === SEL ? " sel" : "") + (e.done ? " done" : "");

    div.tabIndex = 0;
    div.setAttribute("role", "group");
    div.setAttribute("aria-label", "Bildauftrag " + e.file);
    div.onclick = () => select(i);
    div.onkeydown = (event) => {
      if (
        event.target === div &&
        (event.key === "Enter" || event.key === " ")
      ) {
        event.preventDefault();
        select(i);
      }
    };

    // Use the actual versioned result filename from the completed job.
    const img = document.createElement("img");

    if (latest) {
      img.src = addBust(latest.image_url, latest.finished_at);
    } else {
      img.src = exportUrl(e.file, e.dir || "", Date.now());
    }

    img.onerror = () => {
      img.style.visibility = "hidden";
    };

    const lbl = document.createElement("span");

    lbl.textContent = e.file.replace(/\.png$/i, "");

    div.append(img, lbl);

    const ok = document.createElement("button");

    ok.className = "mini ok" + (e.done ? " on" : "");

    ok.innerHTML =
      '<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg>';

    ok.title = e.done ? "Als offen markieren" : "Als erledigt markieren";

    ok.setAttribute("aria-label", ok.title + ": " + e.file);
    ok.onclick = async (ev) => {
      ev.stopPropagation();
      await mutateLibrary(async () => {
        const selected = ENTRIES[SEL];
        const entries = ENTRIES.map((item) =>
          item === e ? { ...item, done: !item.done } : item,
        );
        await persistEntries(
          entries,
          selected && assetKey(selected.file, selected.dir || ""),
        );
      });
    };

    div.append(ok);

    const x = document.createElement("button");

    x.className = "mini x";

    x.innerHTML =
      '<svg class="icon" aria-hidden="true"><use href="#i-x"/></svg>';

    x.title = "Eintrag löschen (Dateien bleiben)";

    x.setAttribute("aria-label", "Bildauftrag entfernen: " + e.file);
    x.onclick = async (ev) => {
      ev.stopPropagation();
      await removeEntries(new Set([assetKey(e.file, e.dir || "")]));
    };

    div.append(x);

    if (job) {
      const dot = document.createElement("i");

      dot.className = "dot " + job.status;

      dot.title = job.status;

      div.append(dot);
    }

    el.append(div);
  });

  const emptyHint = $("libraryEmpty");
  if (emptyHint) emptyHint.hidden = ENTRIES.length > 0;

  $("assetCount").textContent = `(${visible.length}/${ENTRIES.length})`;
}

function select(i) {
  if (i < 0 || i >= ENTRIES.length) {
    return;
  }

  SEL = i;

  const e = ENTRIES[i];

  $("fFile").value = e.file || "mein-bild.png";

  $("fDir").value = e.dir || "";

  $("fPrompt").value = e.prompt || "";

  $("fSeed").value = e.seed ?? 42;

  $("fSteps").value = e.steps ?? 30;

  $("fCfg").value = Number.isFinite(Number(e.cfg)) ? Number(e.cfg) : 1.0;

  const exportSize = e.export_size == null ? 0 : e.export_size;

  $("fExportSize").value = String(exportSize);

  const fmt = `${e.width || 1024}x${e.height || 1024}`;
  $("allowLargeFormats").checked = Boolean(e.allow_large);
  updateLargeFormats(Boolean(e.allow_large));

  if ([...$("fFmt").options].some((o) => o.value === fmt)) {
    $("fFmt").value = fmt;
  }

  REF_SEL = new Set(e.refs || []);

  $("fMaster").checked = e.use_master !== false;

  $("fRgba").checked = e.use_rgba !== false;

  renderRefs();

  $("resultMeta").textContent = "";

  const latest = latestDoneForEntry(e);

  if (latest) {
    showPreview(addBust(latest.image_url, latest.finished_at));

    setResultMeta(latest);

    lastPreviewJobId = latest.id;

    lastPreviewKey = assetKey(e.file, e.dir || "");
  } else {
    showPreview(exportUrl(e.file, e.dir || "", Date.now()));

    lastPreviewJobId = null;

    lastPreviewKey = assetKey(e.file, e.dir || "");
  }

  renderList();

  renderPromptPreview();
}

async function loadRefs() {
  REFS = await api("/api/refs");

  renderRefs();
}

function renderRefs() {
  $("refSelectionInfo").textContent = REF_SEL.size
    ? `${REF_SEL.size} von 10 ausgewählt`
    : "Keine ausgewählt · optional bis zu 10 Bilder";
  const el = $("refList");

  el.innerHTML = "";

  if (!REFS.length) {
    el.innerHTML =
      '<span class="dim small empty">' +
      "Für Text zu Bild sind keine Referenzen nötig. " +
      "Optional Bilder hinzufügen und per Klick auswählen." +
      "</span>";

    return;
  }

  REFS.forEach((r) => {
    const div = document.createElement("div");

    div.className = "ref-item" + (REF_SEL.has(r.name) ? " sel" : "");

    div.title = r.name;

    const img = document.createElement("img");

    img.src = r.url;

    div.append(img);

    div.onclick = (ev) => {
      if (ev.target.classList.contains("x")) {
        return;
      }

      if (REF_SEL.has(r.name)) {
        REF_SEL.delete(r.name);
      } else if (REF_SEL.size < 10) {
        REF_SEL.add(r.name);
      } else {
        toast("Max. 10 Referenzen", true);
      }

      renderRefs();
    };

    const x = document.createElement("button");

    x.className = "x";

    x.innerHTML =
      '<svg class="icon" aria-hidden="true"><use href="#i-x"/></svg>';

    x.title = "Datei löschen";

    x.onclick = async (ev) => {
      ev.stopPropagation();

      if (!(await confirmAction(`Referenz "${r.name}" löschen?`))) {
        return;
      }

      await api("/api/refs/" + encodeURIComponent(r.name), {
        method: "DELETE",
      });

      REF_SEL.delete(r.name);

      await loadRefs();
    };

    div.append(x);

    el.append(div);
  });
}

function showPreview(url) {
  const img = $("preview");

  if (!url) {
    img.removeAttribute("src");

    img.style.display = "none";

    $("previewEmpty").style.display = "";

    return;
  }

  img.style.display = "none";

  $("previewEmpty").style.display = "";

  img.onload = () => {
    img.style.display = "";

    $("previewEmpty").style.display = "none";
  };

  img.onerror = () => {
    img.style.display = "none";

    $("previewEmpty").style.display = "";
  };

  img.src = url;
}

function setResultMeta(job) {
  if (!job) {
    $("resultMeta").textContent = "";
    return;
  }

  if (job.status === "error") {
    $("resultMeta").textContent = `${job.error || "Unbekannter Fehler"}`;

    return;
  }

  $("resultMeta").textContent =
    `${job.took_s}s · ` +
    `Seed ${job.seed} · ` +
    `${job.alpha_pct ?? "?"}% transparent · ` +
    `→ ${job.export_path} ` +
    `(${job.export_size || "original"}px)`;
}

function syncSelectedPreview() {
  if (SEL < 0 || !ENTRIES[SEL]) {
    return;
  }

  const entry = ENTRIES[SEL];

  const key = assetKey(entry.file, entry.dir || "");

  const latest = latestDoneByKey.get(key);

  if (!latest) {
    return;
  }

  // Do not reload an unchanged preview.
  if (lastPreviewJobId === latest.id && lastPreviewKey === key) {
    return;
  }

  lastPreviewJobId = latest.id;

  lastPreviewKey = key;

  showPreview(addBust(latest.image_url, latest.finished_at));

  setResultMeta(latest);
}

async function generate() {
  if (!folderApiAvailable)
    return toast(
      "Bitte starte den Python-Server neu. Die neue Ordner-API fehlt noch.",
      true,
    );

  if (!STATUS.model_ready) {
    return toast("Modell lädt noch…", true);
  }

  const entry = currentEntry();

  if (!entry.prompt) {
    return toast("Prompt fehlt", true);
  }

  // Persist the current settings before queueing the generation.
  if (SEL < 0) {
    const saved = await mutateLibrary(async () => {
      await persistEntries(
        [...ENTRIES, entry],
        assetKey(entry.file, entry.dir || ""),
      );
      return true;
    });
    if (!saved) return;
  } else if (!(await saveManifest())) return;

  const cfg = parseFloat($("fCfg").value);

  const req = {
    ...entry,

    steps: parseInt($("fSteps").value, 10) || 30,

    cfg: Number.isFinite(cfg) ? cfg : 1.0,
  };

  const res = await api("/api/generate", {
    method: "POST",
    body: JSON.stringify(req),
  });

  toast(
    `${entry.file} in Queue ` +
      `(${res.queue_len} Position` +
      (REF_SEL.size ? ` · ${REF_SEL.size} Ref(s)` : "") +
      ")",
  );
}

function renderQueue() {
  const running = JOBS.find((j) => j.status === "running");

  const queuedJobs = JOBS.filter((j) => j.status === "queued");

  const doneLast = JOBS.find((j) => j.status === "done");

  if (running) {
    const elapsed = running.started_at
      ? Math.max(0, Date.now() / 1000 - running.started_at).toFixed(0)
      : 0;

    const cancelling = running.cancel_requested ? " · bricht ab…" : "";

    $("jobInfo").textContent =
      `${running.file}` +
      ` — Schritt ` +
      `${running.step}/${running.total_steps}` +
      ` · ${elapsed}s` +
      cancelling;

    $("barFill").style.width = `${
      (100 * running.step) / Math.max(1, running.total_steps)
    }%`;
  } else {
    $("jobInfo").textContent = doneLast
      ? `Zuletzt: ${doneLast.file} (${doneLast.took_s}s)`
      : "Bereit für deinen nächsten Auftrag";

    $("barFill").style.width = "0%";
  }

  $("queueInfo").textContent = queuedJobs.length
    ? `${queuedJobs.length} in Warteschlange`
    : "";

  $("btnStop").classList.toggle("hidden", !running);

  $("btnClearQueue").classList.toggle("hidden", queuedJobs.length === 0);
}

function handleDones() {
  for (const j of JOBS) {
    if (!["done", "error", "cancelled"].includes(j.status)) continue;
    if (seenDone.has(j.id)) {
      continue;
    }

    seenDone.add(j.id);

    if (initialJobSync) {
      continue;
    }

    if (j.status === "cancelled") {
      toast(`${j.file} abgebrochen`);

      continue;
    }

    if (j.status === "error") {
      const isCurrent =
        SEL >= 0 &&
        ENTRIES[SEL] &&
        assetKey(ENTRIES[SEL].file, ENTRIES[SEL].dir || "") ===
          assetKey(j.file, j.dir || "");

      if (isCurrent) {
        $("resultMeta").textContent = `${j.error}`;
      }

      toast(`${j.file}: Fehler`, true);

      continue;
    }

    if (j.status !== "done") {
      continue;
    }

    // Older completions must not replace a newer preview.
    const key = assetKey(j.file, j.dir || "");

    const latest = latestDoneByKey.get(key);

    const isLatest = latest && latest.id === j.id;

    if (!isLatest) {
      continue;
    }

    const isCurrent =
      SEL >= 0 &&
      ENTRIES[SEL] &&
      assetKey(ENTRIES[SEL].file, ENTRIES[SEL].dir || "") === key;

    if (isCurrent) {
      lastPreviewJobId = j.id;

      lastPreviewKey = key;

      showPreview(addBust(j.image_url, j.finished_at));

      setResultMeta(j);
    }

    toast(`${j.file} fertig ` + `(${j.alpha_pct ?? "?"}% Alpha)`);
  }
}

async function poll() {
  try {
    const [s, j] = await Promise.all([api("/api/status"), api("/api/jobs")]);

    STATUS = s;
    if (!folderApiAvailable && Date.now() - lastFolderApiCheck > 5000)
      await loadExportFolder();
    const modelKey = `${s.model}:${s.backend || "diffusers"}`;
    if (modelDefaultsApplied !== modelKey) {
      if (SEL < 0 || modelDefaultsApplied !== null) {
        $("fSteps").value = String(s.default_steps || 30);
        $("fCfg").value = String(s.default_cfg || 1);
      }
      modelDefaultsApplied = modelKey;
    }
    const turbo = s.model === "turbo";
    $("fSteps").disabled = turbo;
    $("fCfg").disabled = turbo;
    if (turbo) {
      $("fSteps").value = String(s.default_steps);
      $("fCfg").value = "1";
    }
    $("samplingHint").textContent = turbo
      ? `Turbo nutzt fest ${s.default_steps} Steps und CFG 1. Diese Werte werden vom Modell vorgegeben.`
      : "Standard: 30 Steps und CFG 1. Für den Einstieg musst du diese Werte nicht ändern.";

    const keys = new Set(ENTRIES.map((e) => assetKey(e.file, e.dir || "")));
    JOBS = (j.jobs || []).filter(
      (job) =>
        job.status === "running" ||
        job.status === "queued" ||
        keys.has(assetKey(job.file, job.dir || "")),
    );

    refreshLatestDoneJobs();

    const b = $("modelBadge");

    if (s.model_ready) {
      b.textContent =
        "" +
        (s.backend === "sdcpp"
          ? "TURBO · " + (s.quantization || "GGUF")
          : s.model === "turbo"
            ? "TURBO · BF16"
            : "Modell · BF16") +
        " bereit";

      b.className = "badge ok";
    } else if (s.model_error) {
      b.textContent = s.model_error.startsWith("UI-only mode")
        ? "Vorschau · Modell nicht geladen"
        : "Modell-Fehler (Server-Log)";

      b.className = s.model_error.startsWith("UI-only mode")
        ? "badge preview-mode"
        : "badge err";
    } else {
      b.textContent = "Modell lädt…";

      b.className = "badge loading";
    }

    $("btnGen").disabled = !s.model_ready || !folderApiAvailable;

    renderQueue();

    handleDones();

    syncSelectedPreview();

    renderList();

    initialJobSync = false;
  } catch {}
}

async function init() {
  $("btnChooseFolder").onclick = async () => {
    if (!(await loadExportFolder())) return;
    const button = $("btnChooseFolder");
    button.disabled = true;
    const label = button.querySelector("span");
    label.textContent = "Dialog geöffnet …";
    $("folderStatus").textContent = "Bitte wähle den Ordner im Windows-Dialog.";
    try {
      const folder = await api("/api/export-folder/pick", { method: "POST" });
      if (!folder.cancelled) displayExportFolder(folder);
      $("folderStatus").textContent = folder.cancelled
        ? "Ordnerauswahl abgebrochen."
        : "Ausgabeordner ausgewählt.";
    } catch (error) {
      $("folderStatus").textContent = error.message;
      toast(error.message || "Ordnerauswahl fehlgeschlagen", true);
    } finally {
      button.disabled = false;
      label.textContent = "Ordner wählen";
    }
  };
  $("allowLargeFormats").onchange = () =>
    updateLargeFormats($("allowLargeFormats").checked);
  await loadExportFolder();

  const res = await api("/api/manifest");

  ENTRIES = res.entries || [];

  CONFIG = res.config || CONFIG;

  if (ENTRIES.length) {
    select(0);
  } else {
    SEL = -1;

    showPreview(null);
  }

  renderPromptPreview();

  $("btnGen").onclick = async () => {
    try {
      await generate();
    } catch (error) {
      toast(error.message, true);
    }
  };

  $("btnSave").onclick = saveManifest;

  $("btnDice").onclick = () => {
    $("fSeed").value = Math.floor(Math.random() * 100000) + 1;
  };

  $("hideDone").onchange = () => {
    hideDone = $("hideDone").checked;

    renderList();
  };

  $("btnDel").onclick = async () => {
    const entry = ENTRIES[SEL];
    if (entry)
      await removeEntries(new Set([assetKey(entry.file, entry.dir || "")]));
  };
  $("btnRemoveDone").onclick = () =>
    removeEntries(
      new Set(
        ENTRIES.filter(removableCompleted).map((e) =>
          assetKey(e.file, e.dir || ""),
        ),
      ),
    );
  $("btnClearLibrary").onclick = () =>
    removeEntries(new Set(ENTRIES.map((e) => assetKey(e.file, e.dir || ""))));
  $("btnNew").onclick = async () => {
    const name = await studioDialog({
      title: "Neuer Bildauftrag",
      message:
        "Speichere deinen Prompt und die Einstellungen als eigenen Bildauftrag.",
      input: true,
    });
    if (!name) return;
    await mutateLibrary(async () => {
      const file = name.trim().replace(/\.png$/i, "") + ".png";
      if (ENTRIES.some((e) => e.file === file && !e.dir))
        return toast("Dieser Dateiname ist bereits in der Liste.", true);
      const entry = {
        file,
        prompt: "",
        seed: 42,
        width: 1024,
        height: 1024,
        steps: STATUS.default_steps || 30,
        cfg: 1,
        export_size: null,
        use_master: false,
        use_rgba: false,
        refs: [],
        done: false,
      };
      const existing = ENTRIES.map((e, i) => (i === SEL ? currentEntry() : e));
      await persistEntries([...existing, entry], assetKey(file, ""));
      select(SEL);
    });
  };

  $("btnBatchMissing").onclick = async () => {
    if (!STATUS.model_ready) {
      return toast("Modell lädt noch…", true);
    }

    const res = await api("/api/batch", {
      method: "POST",
      body: JSON.stringify({
        mode: "missing",
      }),
    });

    toast(
      res.queued
        ? `${res.queued} Bildaufträge eingereiht`
        : "Alle Bilder sind bereits vorhanden",
    );
  };

  $("btnBatchAll").onclick = async () => {
    if (
      !(await confirmAction(
        "Alle gespeicherten Bildaufträge neu generieren?\n" +
          "Gespeicherte Einstellungen werden verwendet; Turbo nutzt seine festen Sampling-Werte.",
      ))
    ) {
      return;
    }

    const res = await api("/api/batch", {
      method: "POST",
      body: JSON.stringify({
        mode: "all",
      }),
    });

    toast(`${res.queued} Bildaufträge eingereiht`);
  };

  $("btnRefAdd").onclick = () => $("refFile").click();

  $("refFile").onchange = async () => {
    for (const f of $("refFile").files) {
      const buf = await f.arrayBuffer();

      await api(`/api/refs/upload?name=${encodeURIComponent(f.name)}`, {
        method: "POST",
        body: buf,
      });
    }

    $("refFile").value = "";

    await loadRefs();

    toast("Referenzbilder hinzugefügt");
  };

  await loadRefs();

  ["fPrompt", "fMaster", "fRgba", "fCfg"].forEach((id) => {
    $(id).addEventListener("input", renderPromptPreview);

    $(id).addEventListener("change", renderPromptPreview);
  });

  ["fSteps", "fSeed", "fFmt", "fExportSize"].forEach((id) => {
    $(id).addEventListener("change", renderPromptPreview);
  });

  $("btnStop").onclick = async () => {
    const running = JOBS.find((j) => j.status === "running");

    if (!running) {
      return;
    }

    await api("/api/job/" + running.id, {
      method: "DELETE",
    });

    toast("Abbruch beim nächsten Schritt angefordert");

    poll();
  };

  $("btnClearQueue").onclick = async () => {
    const queued = JOBS.filter((j) => j.status === "queued");

    for (const j of queued) {
      await api("/api/job/" + j.id, {
        method: "DELETE",
      });
    }

    toast(`${queued.length} wartende Jobs abgebrochen`);

    poll();
  };

  setInterval(poll, 1000);

  await poll();
}

function showStartupError(error) {
  $("modelBadge").textContent = "Startfehler";
  $("modelBadge").className = "badge err";
  $("btnGen").disabled = true;
  $("backendNotice").hidden = false;
  $("backendNotice").textContent =
    error.message || "Die Oberfläche konnte nicht geladen werden.";
}

init().catch(showStartupError);
