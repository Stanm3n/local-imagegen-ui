const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id))
    nodes.set(id, {
      value: "",
      textContent: "",
      innerHTML: "",
      hidden: true,
      dataset: {},
      setAttribute() {},
    });
  return nodes.get(id);
};
const ctx = vm.createContext({
  document: {
    documentElement: { dataset: { theme: "light" } },
    getElementById: node,
  },
  localStorage: {
    setItem() {},
    getItem() {
      return null;
    },
  },
  fetch: async () => ({
    ok: false,
    text: async () => JSON.stringify({ detail: "Not Found" }),
  }),
  console,
  Date,
  setTimeout,
  clearTimeout,
});
const source = fs
  .readFileSync(
    require("node:path").join(__dirname, "../static/app.js"),
    "utf8",
  )
  .replace(/init\(\)\.catch\(showStartupError\);\s*$/, "");
vm.runInContext(source, ctx);
(async () => {
  assert.equal(await vm.runInContext("loadExportFolder()", ctx), false);
  assert.equal(node("backendNotice").hidden, false);
  assert.match(node("exportFolderPath").value, /Serverneustart/);
  assert.equal(typeof node("themeToggle").onclick, "function");
  ctx.fetch = async () => ({
    ok: true,
    json: async () => ({ id: "local", path: "C:/Images" }),
  });
  assert.equal(await vm.runInContext("loadExportFolder()", ctx), true);
  assert.equal(node("backendNotice").hidden, true);
  assert.equal(node("exportFolderPath").value, "C:/Images");
  const notifications = [];
  ctx.captureToast = (message) => notifications.push(message);
  vm.runInContext(
    `
    toast = captureToast;
    ENTRIES = [];
    SEL = -1;
    JOBS = [{id: "job-a", file: "first.png", status: "queued"}];
    initialJobSync = true;
    handleDones();
    initialJobSync = false;
    JOBS[0].status = "running";
    handleDones();
    JOBS[0].status = "done";
    JOBS[0].alpha_pct = 0;
    refreshLatestDoneJobs();
    handleDones();
    handleDones();
  `,
    ctx,
  );
  assert.equal(notifications.length, 1);
  assert.match(notifications[0], /first.png fertig/);
  vm.runInContext(
    `
    JOBS = [{id: "job-b", file: "second.png", status: "queued"}];
    handleDones();
    JOBS[0].status = "error";
    handleDones();
  `,
    ctx,
  );
  assert.equal(notifications.length, 2);
  assert.match(notifications[1], /second.png: Fehler/);
  vm.runInContext(
    'showStartupError(new Error("Saved library is not valid JSON"))',
    ctx,
  );
  assert.equal(node("backendNotice").hidden, false);
  assert.match(node("backendNotice").textContent, /not valid JSON/);
  assert.equal(node("btnGen").disabled, true);
  console.log(
    "PASS: backend recovery, terminal job notifications, visible startup errors",
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
