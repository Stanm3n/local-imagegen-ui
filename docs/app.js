"use strict";
const data = window.QWEN_DEMO;
const $ = (s) => document.querySelector(s);
const dialog = $("#workDialog");
let selectedWork,
  selectedEdit = 0,
  rememberedFocus;
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $("#themeToggle").setAttribute(
    "aria-label",
    t === "dark" ? "Helles Design aktivieren" : "Dunkles Design aktivieren",
  );
  try {
    localStorage.setItem("qwen-demo-theme", t);
  } catch {}
}
let saved;
try {
  saved = localStorage.getItem("qwen-demo-theme");
} catch {}
setTheme(saved === "dark" ? "dark" : "light");
$("#themeToggle").addEventListener("click", () =>
  setTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  ),
);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}
data.works.forEach((w, i) => {
  const article = el("article", "work");
  article.dataset.category = w.category;
  const button = el("button", "work-visual");
  button.setAttribute(
    "aria-label",
    w.title + ": Bild, Prompt und Einstellungen öffnen",
  );
  const img = el("img");
  Object.assign(img, {
    src: w.preview,
    alt: w.alt,
    loading: "lazy",
    width: 1024,
    height: 1024,
  });
  button.append(img, el("span", "work-open", "Bild & Prompt ↗"));
  button.addEventListener("click", () => openWork(w));
  const meta = el("div", "work-meta"),
    text = el("div");
  text.append(
    el("span", "eyebrow", w.label),
    el("h3", "", w.title),
    el("p", "", w.description),
  );
  meta.append(text, el("span", "work-number", String(i + 1).padStart(2, "0")));
  article.append(button, meta);
  $("#gallery").append(article);
});
$("#resultCount").textContent =
  data.works.length + (data.works.length === 1 ? " Arbeit" : " Arbeiten");
document.querySelectorAll("[data-filter]").forEach((b) =>
  b.addEventListener("click", () => {
    document
      .querySelectorAll("[data-filter]")
      .forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    let count = 0;
    document.querySelectorAll(".work").forEach((w) => {
      w.hidden =
        b.dataset.filter !== "all" && w.dataset.category !== b.dataset.filter;
      if (!w.hidden) count++;
    });
    $("#resultCount").textContent =
      count + (count === 1 ? " Arbeit" : " Arbeiten");
  }),
);
function openWork(w) {
  selectedWork = w;
  rememberedFocus = document.activeElement;
  $("#dialogImage").src = w.original;
  $("#dialogImage").alt = w.alt;
  $("#dialogTitle").textContent = w.title;
  $("#dialogCategory").textContent = w.label;
  $("#dialogDescription").textContent = w.description;
  $("#dialogPrompt").value = w.prompt;
  $("#downloadImage").href = w.original;
  $("#downloadImage").download = w.original.split("/").pop();
  const settings = $("#dialogSettings");
  settings.replaceChildren();
  [
    ["Modell", w.model ?? "Nicht erfasst"],
    ["Format", w.size || "1024 × 1024"],
    [
      "Schritte / CFG",
      (w.steps ?? "Nicht erfasst") + " / " + (w.cfg ?? "Nicht erfasst"),
    ],
    ["Seed", String(w.seed)],
    [
      "Referenz",
      w.references?.length
        ? w.references.length + " Bilder"
        : w.reference
          ? "1 Bild"
          : "Keine",
    ],
    ["Laufzeit", w.time ? w.time + " s" : "Nicht erfasst"],
  ].forEach(([k, v]) => {
    const group = el("div");
    group.append(el("dt", "", k), el("dd", "", v));
    settings.append(group);
  });
  $("#copyStatus").textContent = "";
  $("#copyPrompt").textContent = "Prompt kopieren";
  dialog.showModal();
  document.body.classList.add("dialog-open");
  $("#closeDialog").focus();
}
$("#closeDialog").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => {
  const r = dialog.getBoundingClientRect();
  if (
    e.target === dialog &&
    (e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom)
  )
    dialog.close();
});
dialog.addEventListener("close", () => {
  document.body.classList.remove("dialog-open");
  rememberedFocus?.focus();
});
$("#copyPrompt").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(selectedWork.prompt);
    $("#copyStatus").textContent = "Prompt in die Zwischenablage kopiert.";
    $("#copyPrompt").textContent = "Kopiert";
  } catch {
    $("#dialogPrompt").focus();
    $("#dialogPrompt").select();
    $("#copyStatus").textContent =
      "Prompt markiert. Mit Strg+C oder ⌘C kopieren.";
  }
});
function showEdit(i) {
  selectedEdit = i;
  const w = data.edits[i];
  $("#editResult").src = w.original;
  $("#editResult").alt = w.alt;
  $("#editSource").src = w.before;
  $("#editSource").alt = w.beforeAlt;
  $("#comparison .before").textContent = w.beforeLabel || "Original";
  $("#comparison .after").textContent = w.afterLabel || "Bearbeitung";
  $("#comparison").style.aspectRatio = w.aspectRatio || "1 / 1";
  $("#editingLayout").dataset.orientation = w.orientation || "landscape";
  $("#editHeading").textContent = w.title;
  $("#editDescription").textContent = w.description;
  $("#editBrief").textContent = w.brief;
  $("#alphaNote").textContent = w.note;
  const refs = $("#editReferences");
  refs.replaceChildren();
  (w.references || []).forEach((ref) => {
    const link = el("a", "edit-ref");
    link.href = ref.href || ref.src;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const img = el("img");
    img.src = ref.src;
    img.alt = ref.label;
    img.loading = "lazy";
    link.append(img, el("span", "", ref.label));
    refs.append(link);
  });
  document
    .querySelectorAll("[data-edit]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(Number(b.dataset.edit) === i)),
    );
}
document
  .querySelectorAll("[data-edit]")
  .forEach((b) =>
    b.addEventListener("click", () => showEdit(Number(b.dataset.edit))),
  );
$("#editDetails").addEventListener("click", () =>
  openWork(data.edits[selectedEdit]),
);
$("#compareRange").addEventListener("input", (e) => {
  $("#comparison").style.setProperty("--split", e.target.value + "%");
  e.target.setAttribute(
    "aria-valuetext",
    e.target.value + " Prozent Original sichtbar",
  );
});
showEdit(0);

const appViews = {
  generate: {
    image: "assets/ui-generieren.webp?v=2",
    full: "assets/ui-generieren.png",
    alt: "Local ImageGen UI im Hellmodus: Bildaufträge, Prompt, Ausgabe und Poster-Vorschau",
    caption:
      "Bildgenerierung: Prompt, Dateiname, Bildformat und Vorschau an einem Ort.",
  },
  references: {
    image: "assets/ui-referenzen.webp?v=2",
    full: "assets/ui-referenzen.png",
    alt: "Local ImageGen UI im Dunkelmodus: zwei ausgewählte Referenzen und das Outfit-Ergebnis mit blauem Blazer",
    caption:
      "Referenzbearbeitung: Model und Kleidungsstück auswählen und die Änderung im Prompt beschreiben.",
  },
};
document.querySelectorAll("[data-app-view]").forEach((button) => {
  button.addEventListener("click", () => {
    const view = appViews[button.dataset.appView];
    if (!view) return;
    $("#appScreenshot").src = view.image;
    $("#appScreenshot").alt = view.alt;
    $("#appScreenshotLink").href = view.full;
    $("#appScreenshotFull").href = view.full;
    $("#appScreenshotCaption").textContent = view.caption;
    document.querySelectorAll("[data-app-view]").forEach((item) => {
      item.setAttribute("aria-pressed", String(item === button));
    });
  });
});
