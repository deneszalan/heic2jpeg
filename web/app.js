/* HEIC to JPEG Converter - web version. Everything runs locally in the browser. */
/* global Zip */
(function () {
  "use strict";

  const HEIC_NAME = /\.(heic|heif|hif)$/i;
  const SETTINGS_KEY = "heic2jpeg.settings";
  const ICON = {
    check: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M1.5 6.6 4.6 9.4 10.6 2.4"/></svg>',
    cross: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg>',
    arrow: '<svg class="arrow" viewBox="0 0 14 10" aria-hidden="true"><path d="M1 5h11M8.5 1.5 12 5 8.5 8.5"/></svg>',
    download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>',
  };
  const STATUS = {
    ready: { text: "Ready", icon: "" },
    waiting: { text: "Waiting…", icon: "" },
    converting: { text: "Converting…", icon: "" },
    done: { text: "Done", icon: ICON.check },
    failed: { text: "Failed", icon: ICON.cross },
  };

  const $ = function (id) { return document.getElementById(id); };
  const els = {
    dropZone: $("drop-zone"),
    listCard: $("list-card"),
    items: $("items"),
    summary: $("summary"),
    status: $("status"),
    progress: $("progress"),
    convert: $("convert"),
    download: $("download"),
    saveFolder: $("save-folder"),
    clear: $("clear-list"),
    addFiles: $("add-files"),
    addFolder: $("add-folder"),
    fileInput: $("file-input"),
    folderInput: $("folder-input"),
    quality: $("quality"),
    qualityValue: $("quality-value"),
    size: $("size"),
    keepMetadata: $("keep-metadata"),
    removeLocation: $("remove-location"),
    overlay: $("drop-overlay"),
  };

  const items = [];
  const keys = new Set();
  let nextId = 1;
  let statusIsCustom = false;
  const run = { active: false, stopping: false, queue: [], batch: [], options: null, workers: [], started: 0, done: 0 };

  // ---------------------------------------------------------------- helpers
  function plural(count, word) {
    word = word || "photo";
    return count + " " + word + (count === 1 ? "" : "s");
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " bytes";
    let size = bytes;
    let unit = "bytes";
    for (const u of ["KB", "MB", "GB"]) {
      size /= 1024;
      unit = u;
      if (size < 1024) break;
    }
    return (size < 100 ? size.toFixed(1) : size.toFixed(0)) + " " + unit;
  }

  function formatDuration(ms) {
    const seconds = ms / 1000;
    if (seconds < 10) return seconds.toFixed(1) + " seconds";
    if (seconds < 90) return Math.round(seconds) + " seconds";
    return Math.round(seconds / 60) + " minutes";
  }

  function qualityDescription(value) {
    if (value >= 96) return "Maximum";
    if (value >= 88) return "High";
    if (value >= 75) return "Good";
    if (value >= 60) return "Medium";
    return "Small file";
  }

  function jpegName(name) {
    return name.replace(/\.[^.]*$/, "") + ".jpg";
  }

  function setStatus(text, kind, custom) {
    els.status.textContent = text;
    els.status.className = "status" + (kind && kind !== "muted" ? " " + kind : "");
    statusIsCustom = custom !== false;
  }

  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
  }

  // ---------------------------------------------------------------- settings
  function loadSettings() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {}; } catch (error) { saved = {}; }
    if (saved.quality >= 50 && saved.quality <= 100) els.quality.value = saved.quality;
    if (typeof saved.size === "string" && els.size.querySelector('option[value="' + saved.size + '"]')) els.size.value = saved.size;
    if (typeof saved.keepMetadata === "boolean") els.keepMetadata.checked = saved.keepMetadata;
    if (typeof saved.removeLocation === "boolean") els.removeLocation.checked = saved.removeLocation;
  }

  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        quality: Number(els.quality.value),
        size: els.size.value,
        keepMetadata: els.keepMetadata.checked,
        removeLocation: els.removeLocation.checked,
      }));
    } catch (error) { /* private browsing: settings just aren't remembered */ }
  }

  function readOptions() {
    return {
      quality: Number(els.quality.value),
      maxSize: Number(els.size.value) || null,
      keepMetadata: els.keepMetadata.checked,
      removeLocation: els.keepMetadata.checked && els.removeLocation.checked,
    };
  }

  // ---------------------------------------------------------------- adding photos
  /** sources: [{file, folder, relDir}] where folder is shown to the user and relDir is kept in the ZIP. */
  function addSources(sources, ignored) {
    const byName = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
    sources = sources.slice().sort(function (a, b) {
      return byName.compare(a.folder, b.folder) || byName.compare(a.file.name, b.file.name);
    });
    let added = 0;
    let duplicates = 0;
    for (const source of sources) {
      const file = source.file;
      if (!HEIC_NAME.test(file.name) || file.name.startsWith("._")) continue;
      const key = [source.folder, file.name, file.size, file.lastModified].join("|");
      if (keys.has(key)) { duplicates++; continue; }
      keys.add(key);
      const item = {
        id: nextId++,
        key: key,
        file: file,
        folder: source.folder,
        relDir: source.relDir,
        status: "ready",
        error: "",
        blob: null,
        crc: 0,
        previewUrl: null,
      };
      items.push(item);
      els.items.appendChild(createRow(item));
      added++;
    }

    const parts = [];
    if (added) parts.push("Added " + plural(added) + ".");
    else if (duplicates) parts.push("Those photos are already in the list.");
    else parts.push("No HEIC photos were found in what you added.");
    if (ignored) parts.push("Ignored " + plural(ignored, "file") + " that " + (ignored === 1 ? "is" : "are") + " not HEIC.");
    if (added && !run.active) parts.push("Press Convert when you are ready.");
    setStatus(parts.join(" "), added ? "muted" : "warning");
    updateState();
  }

  function fromFileList(files) {
    const sources = [];
    let ignored = 0;
    for (const file of files) {
      const parts = (file.webkitRelativePath || "").split("/");
      if (parts.length > 1) {
        const dirs = parts.slice(0, -1);
        if (dirs.some(function (d) { return d.startsWith("."); })) continue;
        sources.push({ file: file, folder: dirs.join("/"), relDir: dirs.slice(1).join("/") });
      } else {
        if (!HEIC_NAME.test(file.name)) ignored++;
        sources.push({ file: file, folder: "", relDir: "" });
      }
    }
    return { sources: sources, ignored: ignored };
  }

  function readAllEntries(reader) {
    return new Promise(function (resolve, reject) {
      const all = [];
      (function next() {
        reader.readEntries(function (batch) {
          if (!batch.length) resolve(all);
          else { all.push.apply(all, batch); next(); }
        }, reject);
      })();
    });
  }

  async function walk(entry, rootName, relParts, out) {
    if (entry.isFile) {
      const file = await new Promise(function (resolve, reject) { entry.file(resolve, reject); });
      out.push({ file: file, folder: [rootName].concat(relParts).join("/"), relDir: relParts.join("/") });
    } else if (entry.isDirectory && !entry.name.startsWith(".")) {
      for (const child of await readAllEntries(entry.createReader())) {
        await walk(child, rootName, child.isDirectory ? relParts.concat(child.name) : relParts, out);
      }
    }
  }

  async function fromDataTransfer(dataTransfer) {
    // Entries must be taken synchronously, before the first await.
    const entries = [];
    const plainFiles = [];
    for (const item of Array.from(dataTransfer.items || [])) {
      if (item.kind !== "file") continue;
      const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
      if (entry) entries.push(entry);
      else if (item.getAsFile()) plainFiles.push(item.getAsFile());
    }
    if (!entries.length && !plainFiles.length) return fromFileList(Array.from(dataTransfer.files || []));

    const sources = [];
    let ignored = 0;
    for (const file of plainFiles) {
      if (!HEIC_NAME.test(file.name)) ignored++;
      sources.push({ file: file, folder: "", relDir: "" });
    }
    for (const entry of entries) {
      if (entry.isDirectory) {
        await walk(entry, entry.name, [], sources);
      } else {
        const file = await new Promise(function (resolve, reject) { entry.file(resolve, reject); });
        if (!HEIC_NAME.test(file.name)) ignored++;
        sources.push({ file: file, folder: "", relDir: "" });
      }
    }
    return { sources: sources, ignored: ignored };
  }

  // ---------------------------------------------------------------- list rows
  function createRow(item) {
    const row = document.createElement("li");
    row.className = "item";
    row.innerHTML =
      '<div class="photo"><div class="thumb">HEIC</div><div class="names"><div class="name"></div><div class="folder"></div></div></div>' +
      '<div class="size"></div>' +
      '<div class="state"><span class="badge"></span><span class="note"></span></div>' +
      '<div><button type="button" class="download-one" title="Download this JPEG" hidden>' + ICON.download + "</button></div>";
    row.querySelector(".name").textContent = item.file.name;
    const folder = row.querySelector(".folder");
    folder.textContent = item.folder || new Date(item.file.lastModified).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    folder.title = item.folder;
    row.querySelector(".download-one").addEventListener("click", function () {
      if (item.blob) saveBlob(item.blob, jpegName(item.file.name));
    });
    item.row = row;
    updateRow(item);
    return row;
  }

  function updateRow(item) {
    const row = item.row;
    const status = STATUS[item.status];
    const badge = row.querySelector(".badge");
    badge.className = "badge " + item.status;
    badge.innerHTML = status.icon;
    badge.appendChild(document.createTextNode(status.text));

    const note = row.querySelector(".note");
    note.textContent = item.status === "failed" ? item.error : "";
    note.title = note.textContent;

    const size = row.querySelector(".size");
    size.textContent = formatSize(item.file.size);
    if (item.status === "done" && item.blob) {
      size.insertAdjacentHTML("beforeend", ICON.arrow);
      const after = document.createElement("span");
      after.className = "after";
      after.textContent = formatSize(item.blob.size);
      size.appendChild(after);
    }

    const thumb = row.querySelector(".thumb");
    if (item.previewUrl && thumb.tagName !== "IMG") {
      const img = document.createElement("img");
      img.className = "thumb";
      img.alt = "";
      img.src = item.previewUrl;
      thumb.replaceWith(img);
    }
    row.querySelector(".download-one").hidden = item.status !== "done";
  }

  function setItemStatus(item, status, changes) {
    item.status = status;
    Object.assign(item, changes || {});
    updateRow(item);
  }

  function clearList() {
    if (run.active) return;
    for (const item of items) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    items.length = 0;
    keys.clear();
    els.items.textContent = "";
    setStatus("", "muted", false);
    updateState();
  }

  // ---------------------------------------------------------------- converting
  function createWorkers() {
    if (run.workers.length) return run.workers;
    const count = Math.max(1, Math.min(3, (navigator.hardwareConcurrency || 2) - 1));
    for (let i = 0; i < count; i++) run.workers.push(makeWorker());
    return run.workers;
  }

  function makeWorker() {
    const slot = { worker: new Worker("worker.js"), item: null };
    slot.worker.onmessage = function (event) { onResult(slot, event.data); };
    slot.worker.onerror = function (event) {
      event.preventDefault();
      // The worker crashed (e.g. it ran out of memory). Replace it and carry on.
      const item = slot.item;
      slot.worker.terminate();
      const index = run.workers.indexOf(slot);
      const fresh = makeWorker();
      if (index >= 0) run.workers[index] = fresh;
      if (item) onResult(fresh, { id: item.id, ok: false, error: "This photo could not be converted (your browser ran out of memory or the file is damaged)." });
    };
    return slot;
  }

  function startConversion() {
    let todo = items.filter(function (i) { return i.status === "ready" || i.status === "failed"; });
    if (!todo.length && items.length) {
      if (!window.confirm("All photos in the list have already been converted.\n\nConvert them again with the current settings?")) return;
      todo = items.slice();
    }
    if (!todo.length) return;
    let workers;
    try {
      workers = createWorkers();
    } catch (error) {
      setStatus("Your browser could not start the converter. Please use an up-to-date browser, or the Windows app.", "error");
      return;
    }
    saveSettings();
    run.active = true;
    run.stopping = false;
    run.options = readOptions();
    run.batch = todo;
    run.queue = todo.slice();
    run.done = 0;
    run.started = performance.now();
    for (const item of todo) setItemStatus(item, "waiting", { error: "", blob: null });
    els.progress.max = todo.length;
    els.progress.value = 0;
    els.progress.hidden = false;
    updateProgressText();
    updateState();
    for (const slot of workers) dispatch(slot);
  }

  function dispatch(slot) {
    slot.item = null;
    if (!run.stopping && run.queue.length) {
      const item = run.queue.shift();
      slot.item = item;
      setItemStatus(item, "converting");
      slot.worker.postMessage({ id: item.id, file: item.file, options: run.options });
    } else if (run.workers.every(function (s) { return !s.item; })) {
      finishConversion();
    }
  }

  function onResult(slot, message) {
    const item = items.find(function (i) { return i.id === message.id; });
    if (item) {
      if (message.ok) {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
        setItemStatus(item, "done", {
          blob: new Blob([message.jpeg], { type: "image/jpeg" }),
          crc: message.crc,
          previewUrl: URL.createObjectURL(message.preview),
        });
      } else {
        setItemStatus(item, "failed", { error: message.error });
      }
    }
    run.done++;
    els.progress.value = run.done;
    if (!run.stopping) updateProgressText();
    dispatch(slot);
  }

  function updateProgressText() {
    const total = run.batch.length;
    setStatus("Converting " + Math.min(run.done + 1, total) + " of " + plural(total) + "…", "muted");
  }

  function stopConversion() {
    run.stopping = true;
    els.convert.disabled = true;
    setStatus("Stopping after the photos in progress…", "muted");
  }

  function finishConversion() {
    if (!run.active) return;
    run.active = false;
    for (const item of run.batch) if (item.status === "waiting" || item.status === "converting") setItemStatus(item, "ready");
    const converted = run.batch.filter(function (i) { return i.status === "done"; }).length;
    const failed = run.batch.filter(function (i) { return i.status === "failed"; }).length;
    const elapsed = formatDuration(performance.now() - run.started);

    let text;
    let kind;
    if (run.stopping) {
      text = "Stopped. " + plural(converted) + " converted, the rest are still in the list.";
      kind = "warning";
    } else if (converted) {
      text = "Done! " + plural(converted) + " converted in " + elapsed + ". Click Download to save " + (converted === 1 ? "it." : "them.");
      kind = "success";
    } else {
      text = "";
      kind = "error";
    }
    if (failed) {
      text += " " + plural(failed) + " could not be converted — see the reason next to " + (failed === 1 ? "it" : "them") + ".";
      if (converted) kind = "warning";
    }
    setStatus(text.trim(), kind);
    els.progress.hidden = true;
    els.convert.disabled = false;
    const firstFailure = run.batch.find(function (i) { return i.status === "failed"; });
    if (firstFailure) firstFailure.row.scrollIntoView({ block: "nearest" });
    updateState();
  }

  // ---------------------------------------------------------------- saving
  function outputEntries() {
    const used = new Set();
    return items.filter(function (i) { return i.status === "done" && i.blob; }).map(function (item) {
      const dir = item.relDir ? item.relDir + "/" : "";
      const base = jpegName(item.file.name).replace(/\.jpg$/, "");
      let name = dir + base + ".jpg";
      for (let n = 1; used.has(name.toLowerCase()); n++) name = dir + base + " (" + n + ").jpg";
      used.add(name.toLowerCase());
      return { item: item, name: name };
    });
  }

  function downloadAll() {
    const entries = outputEntries();
    if (!entries.length) return;
    if (entries.length === 1) {
      saveBlob(entries[0].item.blob, entries[0].name.split("/").pop());
      return;
    }
    try {
      const zip = Zip.createZip(entries.map(function (e) {
        return { name: e.name, blob: e.item.blob, crc: e.item.crc, date: new Date(e.item.file.lastModified) };
      }));
      saveBlob(zip, "Photos as JPEG.zip");
      setStatus("Downloading " + plural(entries.length) + " as a ZIP file.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  async function saveToFolder() {
    const entries = outputEntries();
    if (!entries.length) return;
    let root;
    try {
      root = await window.showDirectoryPicker({ mode: "readwrite", startIn: "pictures" });
    } catch (error) {
      if (error.name !== "AbortError") setStatus("Could not open that folder: " + error.message, "error");
      return;
    }
    setStatus("Saving…", "muted");
    let saved = 0;
    try {
      for (const entry of entries) {
        const parts = entry.name.split("/");
        let dir = root;
        for (const part of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(part, { create: true });
        const fileName = await freeName(dir, parts[parts.length - 1]);
        const handle = await dir.getFileHandle(fileName, { create: true });
        const writable = await handle.createWritable();
        await writable.write(entry.item.blob);
        await writable.close();
        saved++;
      }
      setStatus("Saved " + plural(saved) + " to the folder “" + root.name + "”.", "success");
    } catch (error) {
      setStatus("Saved " + plural(saved) + ", then an error occurred: " + error.message, "error");
    }
  }

  async function freeName(dir, name) {
    const base = name.replace(/\.jpg$/, "");
    for (let n = 0; n < 10000; n++) {
      const candidate = n ? base + " (" + n + ").jpg" : name;
      try {
        await dir.getFileHandle(candidate);
      } catch (error) {
        if (error.name === "NotFoundError") return candidate; // keep both: never overwrite
        throw error;
      }
    }
    throw new Error("No free file name for " + name);
  }

  // ---------------------------------------------------------------- state
  function updateState() {
    const count = items.length;
    const ready = items.filter(function (i) { return i.status === "ready"; }).length;
    const failed = items.filter(function (i) { return i.status === "failed"; }).length;
    const done = items.filter(function (i) { return i.status === "done"; }).length;
    const todo = ready + failed;

    els.dropZone.hidden = count > 0;
    els.listCard.hidden = count === 0;
    els.summary.textContent = count ? plural(count) + "  ·  " + formatSize(items.reduce(function (s, i) { return s + i.file.size; }, 0)) : "";

    const convert = els.convert;
    convert.classList.toggle("stop", run.active);
    if (run.active) {
      convert.textContent = "Stop";
      convert.classList.remove("primary");
    } else {
      // The main button is whatever comes next: convert, or download the results.
      const downloadNext = !ready && done > 0;
      convert.classList.toggle("primary", !downloadNext);
      if (ready) convert.textContent = "Convert " + plural(todo);
      else if (failed) convert.textContent = "Retry " + plural(failed);
      else if (count) convert.textContent = "Convert again";
      else convert.textContent = "Convert";
      convert.disabled = count === 0;
    }

    els.download.hidden = run.active || done === 0;
    els.download.textContent = done === 1 ? "Download JPEG" : "Download all (ZIP)";
    els.download.classList.toggle("primary", !run.active && !ready && done > 0);
    els.saveFolder.hidden = run.active || done === 0 || typeof window.showDirectoryPicker !== "function";
    els.clear.disabled = run.active || count === 0;
    for (const control of [els.quality, els.size, els.keepMetadata]) control.disabled = run.active;
    els.removeLocation.disabled = run.active || !els.keepMetadata.checked;

    if (!statusIsCustom && !run.active) {
      if (!count) setStatus("Add some photos to get started.", "muted", false);
      else setStatus(todo ? "Ready to convert " + plural(todo) + "." : "", "muted", false);
    }
  }

  function updateQualityLabel() {
    els.qualityValue.textContent = els.quality.value + "  ·  " + qualityDescription(Number(els.quality.value));
  }

  // ---------------------------------------------------------------- wiring
  function openFilePicker() { els.fileInput.click(); }
  function openFolderPicker() { els.folderInput.click(); }

  els.dropZone.addEventListener("click", function (event) {
    if (!event.target.closest("button")) openFilePicker();
  });
  $("choose-files").addEventListener("click", openFilePicker);
  $("choose-folder").addEventListener("click", openFolderPicker);
  els.addFiles.addEventListener("click", openFilePicker);
  els.addFolder.addEventListener("click", openFolderPicker);
  els.clear.addEventListener("click", clearList);

  for (const input of [els.fileInput, els.folderInput]) {
    input.addEventListener("change", function () {
      const result = fromFileList(Array.from(input.files || []));
      input.value = "";
      addSources(result.sources, result.ignored);
    });
  }

  els.convert.addEventListener("click", function () {
    if (run.active) stopConversion();
    else startConversion();
  });
  els.download.addEventListener("click", downloadAll);
  els.saveFolder.addEventListener("click", saveToFolder);

  els.quality.addEventListener("input", updateQualityLabel);
  for (const control of [els.quality, els.size, els.removeLocation]) control.addEventListener("change", saveSettings);
  els.keepMetadata.addEventListener("change", function () { saveSettings(); updateState(); });

  // Drag and drop anywhere on the page.
  let dragDepth = 0;
  function hasFiles(event) {
    return Array.from((event.dataTransfer && event.dataTransfer.types) || []).indexOf("Files") >= 0;
  }
  function setDragging(on) {
    document.body.classList.toggle("dragging", on);
    els.overlay.hidden = !on || items.length === 0;
  }
  document.addEventListener("dragenter", function (event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth++;
    setDragging(true);
  });
  document.addEventListener("dragover", function (event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  document.addEventListener("dragleave", function (event) {
    if (!hasFiles(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) setDragging(false);
  });
  document.addEventListener("drop", async function (event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    setDragging(false);
    setStatus("Looking for photos…", "muted");
    try {
      const result = await fromDataTransfer(event.dataTransfer);
      addSources(result.sources, result.ignored);
    } catch (error) {
      setStatus("Could not read what you dropped: " + error.message, "error");
    }
  });

  window.addEventListener("beforeunload", function (event) {
    if (run.active) { event.preventDefault(); event.returnValue = ""; }
  });

  loadSettings();
  updateQualityLabel();
  updateState();

  // For automated tests.
  window.heic2jpeg = { items: items, run: run, addSources: addSources };
})();
