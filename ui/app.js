const state = {
  settings: null,
  layouts: [],
  vocab: null,
  projects: [],
  dataDir: "",
  tree: null,
  view: "home",
  pageId: null,
  panelId: null,
  charId: null,
  locId: null,
  refId: null,
  planChapters: "3",
  planPages: "4",
  readChapterId: null,
  sheets: null,
  sheetStamp: 0,
  printFor: null,
  sheetUrl: "",
  mangaBoards: [],
  mangaId: null,
  illustrations: [],
  illustId: null,
  formats: null,
  setupCreate: false,
  setupProjectId: null,
  setupTitle: "",
  setupKind: "manga",
  setupPreset: "b6",
  setupQuality: "print",
  setupCustomW: "",
  setupCustomH: "",
  setupUnit: "mm",
  setupDpi: "300",
  menuOpen: false,
  spreadAt: 0,
  windowTitle: "",
  storyFocus: "title",
  storyFocusId: "",
  storyVolumeId: null,
  storyNoteId: null,
  navVolumeId: null,
  navChapterId: null,
  pageTool: "lasso",
  lineStrength: 55,
  lassoMode: "select",
  divideOn: false,
  divide: { x: 0.5, y: 0.5, angle: 0 },
  botOpen: false,
  botId: null,
  groupId: null,
  selectMulti: false,
  selectedBit: null,
  elementId: null,
  botBox: null,
  sketchPreview: "",
  drawSketch: "",
  drawPreview: "",
  lassoPath: [],
  botMessages: [],
  botDraft: "",
  busy: false,
  status: "",
  helpOpen: false,
  meshSource: "prompt",
  meshPrompt: "",
  poseName: "",
  meshId: null,
};

const pending = new Map();
let seq = 1;
let previewToken = 0;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function rpc(method, params) {
  const id = String(seq++);
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    window.webkit.messageHandlers.koma.postMessage(JSON.stringify({ id, method, params: params || {} }));
  });
}

window.koma = {
  _recv(message) {
    if (message.type === "event") {
      if (message.event === "status") setStatus(message.text);
      return;
    }
    const item = pending.get(String(message.id));
    if (!item) return;
    pending.delete(String(message.id));
    if (message.ok) item.resolve(message.result);
    else item.reject(new Error(message.error || "Request failed"));
  },
};

function absorb(result) {
  if (!result) return result;
  if (result.tree) state.tree = result.tree;
  if (result.projects) state.projects = result.projects;
  if (result.settings) state.settings = result.settings;
  if (result.mangaBoards) state.mangaBoards = result.mangaBoards;
  if (result.illustrations) state.illustrations = result.illustrations;
  if (result.illustId) state.illustId = result.illustId;
  return result;
}

function toast(text, kind) {
  const el = document.getElementById("toast");
  el.hidden = false;
  el.className = kind || "";
  el.textContent = text;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 7000);
}

function setStatus(text) {
  state.status = text || "";
  const el = document.getElementById("status");
  if (el) el.textContent = state.status;
}

async function run(label, fn) {
  state.busy = true;
  setStatus(label);
  render();
  try {
    return await fn();
  } catch (error) {
    toast(error.message);
    return null;
  } finally {
    state.busy = false;
    setStatus("");
    render();
  }
}

function chapters() {
  return state.tree ? state.tree.chapters : [];
}

function allPages() {
  const rows = [];
  for (const chapter of chapters()) {
    for (const page of chapter.pages) rows.push({ page, chapter });
  }
  return rows;
}

function currentPage() {
  return allPages().find((row) => row.page.id === state.pageId) || null;
}

function currentPanel() {
  const row = currentPage();
  if (!row) return null;
  return row.page.panels.find((panel) => panel.id === state.panelId) || null;
}

function ensurePageSelection() {
  const rows = allPages();
  if (!rows.length) {
    state.pageId = null;
    state.panelId = null;
    return;
  }
  if (!rows.some((row) => row.page.id === state.pageId)) state.pageId = rows[0].page.id;
  const row = currentPage();
  if (state.panelId && !row.page.panels.some((panel) => panel.id === state.panelId)) {
    state.panelId = null;
  }
}

function placed(layoutId, reading) {
  const layout = state.layouts.find((item) => item.id === layoutId);
  const slots = layout ? layout.slots : [{ x: 0, y: 0, w: 1, h: 1 }];
  if (reading !== "rtl") return slots;
  return slots.map((slot) => ({ ...slot, x: 1 - slot.x - slot.w }));
}

function pageThumb(page, reading) {
  const slots = page.slots && page.slots.length ? page.slots : placed(page.layout || "classic", reading);
  const images = {};
  for (const panel of page.panels || []) images[panel.slot] = panel.imageUrl;
  const faces = slots.map((slot, index) => {
    const gap = 0.04;
    const style = `left:${(slot.x + gap) * 100}%;top:${(slot.y + gap) * 100}%;width:${Math.max(8, (slot.w - gap * 2) * 100)}%;height:${Math.max(8, (slot.h - gap * 2) * 100)}%;`;
    const image = images[index] ? `background-image:url('${esc(images[index])}')` : "";
    return `<span class="mini-slot" style="${style}${image}"></span>`;
  }).join("");
  return `<span class="page-thumb" aria-hidden="true">${faces}</span>`;
}

function panelHasWork(panel) {
  return !!(panel.imageUrl || String(panel.dialogue || "").trim() || String(panel.sfx || "").trim() || String(panel.action || "").trim() || String(panel.prompt || "").trim() || (panel.refs || []).length || (panel.versions || []).length);
}

function render() {
  const active = document.activeElement;
  let keep = null;
  if (active && active.dataset && active.dataset.save && active.dataset.field) {
    keep = {
      save: active.dataset.save,
      field: active.dataset.field,
      entity: active.dataset.entity || "",
      start: active.selectionStart,
      value: active.value,
    };
  }
  const scrolls = [...document.querySelectorAll("[data-scroll]")].map((el) => [el.dataset.scroll, el.scrollTop]);
  document.querySelectorAll("canvas[data-mesh-id]").forEach((canvas) => {
    const gl = canvas._gl;
    if (!gl) return;
    if (gl.isContextLost && gl.isContextLost()) {
      canvas._gl = null;
      return;
    }
    const lose = gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
    canvas._gl = null;
  });
  const topHtml = renderTop();
  const mainHtml = renderMain();
  document.body.dataset.view = state.view;
  document.body.dataset.studio = inStudio() ? "1" : "0";
  document.getElementById("top").innerHTML = topHtml;
  document.getElementById("main").innerHTML = mainHtml;
  paintHelp();
  syncWindowTitle();
  document.querySelectorAll("[data-save]").forEach((el) => {
    if ("value" in el) el.dataset.orig = el.value;
  });
  if (keep) {
    const el = document.querySelector(
      `[data-save="${keep.save}"][data-field="${keep.field}"][data-entity="${keep.entity}"]`,
    );
    if (el) {
      el.value = keep.value;
      el.focus();
      if (typeof keep.start === "number" && el.setSelectionRange) {
        el.setSelectionRange(keep.start, keep.start);
      }
    }
  }
  for (const [key, top] of scrolls) {
    const el = document.querySelector(`[data-scroll="${key}"]`);
    if (el) el.scrollTop = top;
  }
  mountBot();
  requestAnimationFrame(() => {
    paintDivide();
    restoreSketch();
    restoreDraw();
    if (window.mountMeshViewers) mountMeshViewers();
  });
  if (state.view === "pages" && state.panelId && state.pageTool === "lasso") refreshPreview();
}

function paintHelp() {
  let layer = document.getElementById("help");
  if (!state.helpOpen) {
    if (layer) layer.remove();
    return;
  }
  if (!layer) {
    layer = document.createElement("div");
    layer.id = "help";
    document.getElementById("shell").appendChild(layer);
  }
  layer.innerHTML = `
    <button type="button" class="help-back" data-act="close-help" aria-label="Close shortcuts"></button>
    <div class="help-card" data-act="help-card">
      <h2>Shortcuts</h2>
      <ul>
        <li><kbd>?</kbd> Show or hide this list</li>
        <li><kbd>R</kbd> Open the reference board for this book</li>
        <li><kbd>1</kbd>–<kbd>8</kbd> Lasso, Select, Text, Sketch, Effects, Imagine, Lines, Draw</li>
        <li><kbd>Ctrl</kbd>+<kbd>Z</kbd> Undo a page edit</li>
        <li><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> Redo</li>
        <li><kbd>[</kbd> <kbd>]</kbd> Move the selected panel earlier or later</li>
        <li><kbd>Delete</kbd> Delete the selected panel</li>
        <li><kbd>←</kbd> <kbd>→</kbd> Turn the page in Read</li>
        <li>3D model: drag orbits, Shift-drag pans, Alt-drag rolls, scroll zooms. Pose rotates a rigged model. <kbd>M</kbd> copies the left arm and leg onto the right. Grid, background, and Save this angle sit under the model</li>
      </ul>
      <p class="meta">Drag a page in the list, or a panel on the sheet, to reorder it. The Earlier and Later buttons do the same. The last 20 edits on a page stay on disk.</p>
      <button type="button" class="ghost" data-act="close-help">Close</button>
    </div>
  `;
}

function inStudio() {
  if (!state.tree) return false;
  return ["story", "cast", "places", "pages", "read", "illust", "settings", "about"].includes(state.view);
}

function syncWindowTitle() {
  const name = inStudio() && state.tree && state.tree.project ? String(state.tree.project.title || "").trim() : "";
  const next = name ? `Koma (${name})` : "Koma";
  document.title = next;
  if (state.windowTitle === next) return;
  state.windowTitle = next;
  rpc("shell.set_title", { title: next }).catch(() => {});
}

function renderMenu() {
  const items = [
    ["menu-manga", "Manga", ["story", "cast", "places", "pages", "read"].includes(state.view)],
    ["menu-illust", "Illustrations", state.view === "illust"],
    ["menu-bot", "Grok bot", !!state.botOpen],
    ["menu-settings", "Settings", state.view === "settings"],
    ["menu-help", "Shortcuts", !!state.helpOpen],
    ["menu-about", "About", state.view === "about"],
  ];
  const list = state.menuOpen ? `<div class="app-menu" role="menu">${items.map(([act, label, on]) => `
    <button type="button" role="menuitem" class="${on ? "on" : ""}" data-act="${act}">${label}</button>
  `).join("")}</div>` : "";
  return `
    <div class="menu-wrap">
      <button type="button" class="menu-btn" data-act="toggle-menu" aria-label="Menu" aria-expanded="${state.menuOpen ? "true" : "false"}">
        <span></span><span></span><span></span>
      </button>
      ${list}
    </div>
  `;
}

function themeButton() {
  const light = document.documentElement.dataset.theme === "light";
  return `<button type="button" class="quiet" data-act="theme">${light ? "Dark" : "Light"}</button>`;
}

function renderTop() {
  const status = `<span id="status">${esc(state.status)}</span>`;
  const theme = themeButton();
  const menu = renderMenu();
  if (state.view === "home") {
    const settings = `<button type="button" class="quiet" data-act="nav" data-view="settings">Settings</button>`;
    return `<span class="grow"></span>${status}${theme}${settings}`;
  }
  if (state.view === "setup") {
    const back = `<button type="button" class="quiet" data-act="go-home">Canvases</button>`;
    const settings = `<button type="button" class="quiet" data-act="nav" data-view="settings">Settings</button>`;
    return `${back}<span class="grow"></span>${status}${theme}${settings}`;
  }
  if (inStudio()) {
    const tools = [
      ["story", "Story"],
      ["cast", "Characters"],
      ["places", "Places"],
      ["pages", "Page"],
      ["read", "Read"],
    ].map(([view, label]) => `
      <button type="button" class="${state.view === view ? "on" : ""}" data-act="nav" data-view="${view}">${label}</button>
    `).join("");
    const back = `<button type="button" class="quiet" data-act="go-home">Canvases</button>`;
    return `${back}<nav class="tools">${tools}</nav><span class="grow"></span>${status}${theme}${menu}`;
  }
  const back = `<button type="button" class="quiet" data-act="go-home">Canvases</button>`;
  if (state.view === "illust") {
    const piece = currentIllust();
    const title = piece
      ? `<input class="title-input" data-save="illust" data-field="title" data-entity="${esc(piece.id)}" value="${esc(piece.title)}">`
      : `<span class="word">Illustration</span>`;
    const tools = piece
      ? `<button type="button" class="quiet" data-act="export-illust" data-id="${esc(piece.id)}" ${state.busy || !piece.imageUrl ? "disabled" : ""}>Export</button>
         <button type="button" class="quiet danger-quiet" data-act="delete-illust" data-id="${esc(piece.id)}" ${state.busy ? "disabled" : ""}>Delete</button>`
      : "";
    return `${back}${title}<span class="grow"></span>${status}${tools}${theme}${menu}`;
  }
  if (state.view === "about") {
    return `${back}<span class="word">About</span><span class="grow"></span>${status}${theme}${menu}`;
  }
  return `${back}<span class="word">Settings</span><span class="grow"></span>${status}${theme}${menu}`;
}

function renderMain() {
  if (state.view === "home") return renderHome();
  if (state.view === "setup") return renderSetup();
  if (state.view === "about") return renderAbout();
  if (state.view === "settings") return renderSettings();
  if (state.view === "illust" && !state.tree) return renderIllust();
  if (!state.tree) return renderHome();
  if (state.view === "illust") return desk(illustRail(), illustStage());
  if (state.view === "story") return renderStory();
  if (state.view === "cast") return renderCast();
  if (state.view === "places") return renderPlaces();
  if (state.view === "pages") return renderPages();
  if (state.view === "read") return renderRead();
  return renderHome();
}

function desk(rail, stage, side, sideClass) {
  const extra = side
    ? `<aside class="rail ${sideClass || "rail-side"}" data-scroll="side-${esc(state.view)}">${side}</aside>`
    : "";
  return `<div class="desk"><aside class="rail" data-scroll="rail-${esc(state.view)}">${rail}</aside><div class="desk-stage">${stage}</div>${extra}</div>`;
}

function currentBoard() {
  const boards = state.mangaBoards || [];
  return boards.find((board) => board.projectId === state.mangaId) || boards[0] || {
    projectId: null,
    title: "Untitled",
    layout: "classic",
    reading: "rtl",
    panels: [],
  };
}

function currentIllust() {
  const list = state.illustrations || [];
  return list.find((item) => item.id === state.illustId) || list[0] || null;
}

function boardStyle(board) {
  if (board && Number(board.trimW) > 0 && Number(board.trimH) > 0 && Number(board.pageDpi) > 0) {
    return `--aw:${board.trimW};--ah:${board.trimH}`;
  }
  return ratioStyle("2:3");
}

function ratioStyle(aspect) {
  const parts = String(aspect || "2:3").split(":");
  const aw = Number(parts[0]) || 2;
  const ah = Number(parts[1]) || 3;
  return `--aw:${aw};--ah:${ah}`;
}

function mangaFace(board) {
  const slots = placed(board.layout || "classic", board.reading || "rtl");
  const images = {};
  for (const panel of board.panels || []) images[panel.slot] = panel.imageUrl;
  return slots.map((slot, index) => {
    const gap = 0.014;
    const style = `left:${(slot.x + gap) * 100}%;top:${(slot.y + gap) * 100}%;width:${Math.max(0.04, slot.w - gap * 2) * 100}%;height:${Math.max(0.04, slot.h - gap * 2) * 100}%;`;
    const image = images[index] ? `background-image:url('${esc(images[index])}')` : "";
    return `<span class="mini-slot" style="${style}${image}"></span>`;
  }).join("");
}

function renderHome() {
  const board = currentBoard();
  const illust = (state.illustrations || [])[0] || null;
  const series = (state.mangaBoards || []).length > 1
    ? `<select class="series-pick" data-act="pick-series">${state.mangaBoards.map((item) => `
        <option value="${esc(item.projectId)}" ${item.projectId === board.projectId ? "selected" : ""}>${esc(item.title)}</option>
      `).join("")}</select>`
    : `<span class="board-sub">${esc(board.title || "Untitled")}</span>`;
  const picture = illust && illust.imageUrl ? `<img src="${esc(illust.imageUrl)}" alt="">` : "";
  const hits = (state.searchHits || []).map((hit) => `
    <button type="button" class="hit" data-act="open-hit" data-project="${esc(hit.projectId)}" data-chapter="${esc(hit.chapterId || "")}" data-page="${esc(hit.pageId || "")}" data-panel="${esc(hit.panelId || "")}" data-note="${esc(hit.noteId || "")}" data-character="${esc(hit.characterId || "")}" data-mood="${esc(hit.refId || "")}">
      <span class="hit-label">${esc(hit.label)}</span>
      <span class="hit-detail">${esc(hit.detail || "")}</span>
    </button>
  `).join("");
  const results = state.searchHits
    ? `<div class="search-hits">${hits || `<p class="meta">No matches.</p>`}</div>`
    : "";
  return `
    <div class="home-wrap">
      <div class="library-search">
        <input id="library-search" type="search" placeholder="Search titles, chapters, dialogue, notes, cast, references" value="${esc(state.searchQuery || "")}" ${state.busy ? "disabled" : ""}>
        <button type="button" data-act="search-library" ${state.busy ? "disabled" : ""}>Search</button>
      </div>
      ${results}
      <p class="home-hint">A manga is a book. An illustration is one picture. Inside a book, References on Story keeps pictures from this computer. Press ? for shortcuts.</p>
      <div class="studio">
      <div class="board-col">
        <button type="button" class="board-face" style="${boardStyle(board)}" data-act="open-manga" title="Open this manga" ${state.busy ? "disabled" : ""}>
          ${mangaFace(board)}
        </button>
        <div class="board-caption">
          <div class="board-titles">
            <span class="board-name">Manga</span>
            ${series}
          </div>
          ${board.projectId ? `<button type="button" class="quiet danger-quiet" data-act="delete-manga" data-id="${esc(board.projectId)}" ${state.busy ? "disabled" : ""}>Delete</button>` : ""}
          <button type="button" class="plus" data-act="new-manga" title="New manga canvas" ${state.busy ? "disabled" : ""}>+</button>
        </div>
      </div>
      <div class="board-col">
        <button type="button" class="board-face" style="${ratioStyle(illust ? illust.aspect : "2:3")}" data-act="open-illust" title="Open this illustration" ${state.busy ? "disabled" : ""}>
          ${picture}
        </button>
        <div class="board-caption">
          <div class="board-titles">
            <span class="board-name">Illustration</span>
            <span class="board-sub">${esc(illust ? illust.title : "Untitled")}</span>
          </div>
          <button type="button" class="plus" data-act="new-illust" title="New illustration canvas" ${state.busy ? "disabled" : ""}>+</button>
        </div>
      </div>
      </div>
    </div>
  `;
}

function roundEven(value) {
  const base = Math.floor(value);
  const frac = value - base;
  if (frac > 0.5) return base + 1;
  if (frac < 0.5) return base;
  return base % 2 === 0 ? base : base + 1;
}

function pagePixels(wMm, hMm, dpi, maxEdge) {
  if (!(wMm > 0) || !(hMm > 0) || !(dpi > 0)) return null;
  let width = Math.max(1, roundEven((wMm / 25.4) * dpi));
  let height = Math.max(1, roundEven((hMm / 25.4) * dpi));
  let capped = false;
  const longEdge = Math.max(width, height);
  if (longEdge > maxEdge) {
    const scale = maxEdge / longEdge;
    width = Math.max(1, roundEven(width * scale));
    height = Math.max(1, roundEven(height * scale));
    if (width > maxEdge) width = maxEdge;
    if (height > maxEdge) height = maxEdge;
    capped = true;
  }
  return { width, height, capped };
}

function trimNum(value) {
  if (!Number.isFinite(value)) return "";
  return String(Math.round(value * 1000) / 1000);
}

function setupChoice() {
  const formats = state.formats;
  const kind = state.setupKind === "western" ? "western" : "manga";
  const presets = (formats.presets || []).filter((item) => item.kind === kind);
  const preset = presets.find((item) => item.id === state.setupPreset) || null;
  const customSize = state.setupPreset === "custom";
  const unit = state.setupUnit === "in" ? "in" : "mm";
  let wMm = 0;
  let hMm = 0;
  if (customSize) {
    const width = Number(state.setupCustomW);
    const height = Number(state.setupCustomH);
    if (width > 0 && height > 0) {
      const factor = unit === "in" ? 25.4 : 1;
      wMm = Math.round(width * factor * 1000) / 1000;
      hMm = Math.round(height * factor * 1000) / 1000;
    }
  } else if (preset) {
    wMm = preset.wMm;
    hMm = preset.hMm;
  }
  const qualities = formats.qualities || [];
  const quality = qualities.find((item) => item.id === state.setupQuality) || qualities.find((item) => item.id === "print");
  let dpi = quality && quality.dpi ? quality.dpi : Number(state.setupDpi);
  if (state.setupQuality === "custom") dpi = Number(state.setupDpi);
  const limits = formats.limits || { dpiMin: 72, dpiMax: 1200, wMin: 40, wMax: 500, hMin: 40, hMax: 700 };
  let problem = "";
  if (customSize) {
    if (!(wMm > 0) || !(hMm > 0)) problem = "Enter a width and a height.";
    else if (wMm < limits.wMin || wMm > limits.wMax) problem = "Use a width between 40 and 500 mm.";
    else if (hMm < limits.hMin || hMm > limits.hMax) problem = "Use a height between 40 and 700 mm.";
  }
  if (!problem && state.setupQuality === "custom") {
    if (!Number.isFinite(dpi) || dpi < limits.dpiMin || dpi > limits.dpiMax || Math.round(dpi) !== dpi) {
      problem = "Use a resolution between 72 and 1200 dpi.";
    }
  }
  let pixels = problem ? null : pagePixels(wMm, hMm, dpi, formats.maxEdge || 5000);
  if (!problem && !pixels) problem = "Choose a page size.";
  if (problem) pixels = null;
  let measure = "";
  if (customSize && wMm > 0 && hMm > 0) {
    const shownW = unit === "in" ? wMm / 25.4 : wMm;
    const shownH = unit === "in" ? hMm / 25.4 : hMm;
    measure = `${trimNum(shownW)} × ${trimNum(shownH)} ${unit}`;
  } else if (preset) {
    measure = preset.label;
  }
  const qname = quality ? quality.name : "Print";
  const line = problem ? problem : `${measure} · ${qname} · ${dpi} dpi · ${pixels.width} × ${pixels.height} px`;
  const fallback = presets[0] || { wMm: 128, hMm: 182 };
  return {
    kind,
    presets,
    customSize,
    unit,
    wMm,
    hMm,
    dpi,
    pixels,
    problem,
    line,
    sheetW: wMm || fallback.wMm,
    sheetH: hMm || fallback.hMm,
    formats,
  };
}

function openBook(board) {
  const formats = state.formats || {};
  const mangaDefault = (formats.defaults && formats.defaults.manga) || { preset: "b6", quality: "print" };
  const saved = !!(board && board.pageDpi > 0 && board.pagePreset);
  state.setupCreate = !board;
  state.setupProjectId = board ? board.projectId : null;
  state.setupTitle = board ? (board.title || "") : "";
  state.setupKind = saved && board.pageKind === "western" ? "western" : "manga";
  state.setupPreset = saved ? board.pagePreset : mangaDefault.preset;
  state.setupQuality = saved && board.pageQuality ? board.pageQuality : (mangaDefault.quality || "print");
  state.setupUnit = saved && board.pageUnit === "in" ? "in" : "mm";
  state.setupDpi = String((saved && board.pageDpi) || 300);
  state.setupCustomW = "";
  state.setupCustomH = "";
  if (saved && board.pagePreset === "custom" && board.trimW > 0 && board.trimH > 0) {
    const factor = state.setupUnit === "in" ? 1 / 25.4 : 1;
    state.setupCustomW = trimNum(board.trimW * factor);
    state.setupCustomH = trimNum(board.trimH * factor);
  }
  state.view = "setup";
  state.printFor = null;
  render();
}

function renderSetup() {
  if (!state.formats) {
    return `<div class="book"><p class="book-spec">Page sizes are still loading.</p></div>`;
  }
  const choice = setupChoice();
  const board = (state.mangaBoards || []).find((item) => item.projectId === state.setupProjectId);
  const reading = choice.kind === "western" ? "ltr" : "rtl";
  const face = mangaFace({
    layout: board ? board.layout : "classic",
    reading,
    panels: board ? board.panels : [],
  });
  const kinds = choice.formats.kinds.map((item) => `
    <button type="button" class="type-card ${item.id === choice.kind ? "on" : ""}" data-act="set-kind" data-value="${esc(item.id)}" ${state.busy ? "disabled" : ""}>
      <span class="type-name">${esc(item.label)}</span>
      <span class="type-line">${esc(item.line)}</span>
    </button>
  `).join("");
  const sizes = choice.presets.map((item) => `
    <button type="button" class="size-card ${item.id === state.setupPreset ? "on" : ""}" data-act="set-preset" data-value="${esc(item.id)}" ${state.busy ? "disabled" : ""}>
      <span class="size-paper" style="--aw:${item.wMm};--ah:${item.hMm}"></span>
      <span class="size-name">${esc(item.name)}</span>
      <span class="size-dim">${esc(item.label)}</span>
    </button>
  `).join("");
  const paperW = choice.customSize && choice.wMm > 0 ? choice.wMm : 2;
  const paperH = choice.customSize && choice.hMm > 0 ? choice.hMm : 3;
  const customSize = `
    <button type="button" class="size-card ${state.setupPreset === "custom" ? "on" : ""}" data-act="set-preset" data-value="custom" ${state.busy ? "disabled" : ""}>
      <span class="size-paper custom-paper" style="--aw:${paperW};--ah:${paperH}"></span>
      <span class="size-name">Custom</span>
      <span class="size-dim">Your size</span>
    </button>
  `;
  const qualities = (choice.formats.qualities || []).map((item) => `
    <button type="button" class="quality-card ${item.id === state.setupQuality ? "on" : ""}" data-act="set-quality" data-value="${esc(item.id)}" ${state.busy ? "disabled" : ""}>
      <span class="q-name">${esc(item.name)}</span>
      <span class="q-dpi">${item.dpi ? `${esc(item.dpi)} dpi` : "Your dpi"}</span>
    </button>
  `).join("");
  const sizeFields = choice.customSize ? `
    <label class="book-field">Width
      <input id="book-w" type="number" min="0" step="any" inputmode="decimal" data-save="local" data-field="setupCustomW" data-entity="" value="${esc(state.setupCustomW)}">
    </label>
    <label class="book-field">Height
      <input id="book-h" type="number" min="0" step="any" inputmode="decimal" data-save="local" data-field="setupCustomH" data-entity="" value="${esc(state.setupCustomH)}">
    </label>
    <label class="book-field">Unit
      <select id="book-unit" data-save="local" data-field="setupUnit" data-entity="">
        <option value="mm" ${state.setupUnit !== "in" ? "selected" : ""}>mm</option>
        <option value="in" ${state.setupUnit === "in" ? "selected" : ""}>in</option>
      </select>
    </label>
  ` : "";
  const dpiField = state.setupQuality === "custom" ? `
    <label class="book-field">DPI
      <input id="book-dpi" type="number" min="72" max="1200" step="1" data-save="local" data-field="setupDpi" data-entity="" value="${esc(state.setupDpi)}">
    </label>
  ` : "";
  const custom = sizeFields || dpiField ? `<div class="book-custom">${sizeFields}${dpiField}</div>` : "";
  return `
    <div class="book">
      <div class="book-stage" style="--aw:${choice.sheetW};--ah:${choice.sheetH}">
        <div class="book-sheet">${face}</div>
        <input id="book-title" class="book-title" data-save="local" data-field="setupTitle" data-entity="" value="${esc(state.setupTitle)}" placeholder="Title" aria-label="Title of the manga" ${state.busy ? "disabled" : ""}>
        <p class="book-spec ${choice.problem ? "book-problem" : ""}" id="book-spec">${esc(choice.line)}</p>
        <p class="book-cap" id="book-cap" ${choice.pixels && choice.pixels.capped ? "" : "hidden"}>The long edge is held at ${esc(choice.formats.maxEdge)} px so the page stays editable.</p>
      </div>
      <div class="book-choices">
        <p class="book-kicker">Type</p>
        <div class="type-row">${kinds}</div>
        <p class="book-kicker">Page</p>
        <div class="size-row">${sizes}${customSize}</div>
        <p class="book-kicker">Resolution</p>
        <div class="quality-row">${qualities}</div>
        ${custom}
        <button type="button" class="book-open" data-act="open-book" ${state.busy ? "disabled" : ""}>Open the page</button>
      </div>
    </div>
  `;
}

function paintSetupSpec() {
  if (state.view !== "setup" || !state.formats) return;
  const choice = setupChoice();
  const spec = document.getElementById("book-spec");
  if (spec) {
    spec.textContent = choice.line;
    spec.classList.toggle("book-problem", !!choice.problem);
  }
  const cap = document.getElementById("book-cap");
  if (cap) cap.hidden = !(choice.pixels && choice.pixels.capped);
  const stage = document.querySelector(".book-stage");
  if (stage) {
    stage.style.setProperty("--aw", String(choice.sheetW));
    stage.style.setProperty("--ah", String(choice.sheetH));
  }
  const paper = document.querySelector(".custom-paper");
  if (paper && choice.customSize && choice.wMm > 0 && choice.hMm > 0) {
    paper.style.setProperty("--aw", String(choice.wMm));
    paper.style.setProperty("--ah", String(choice.hMm));
  }
}

function renderIllust() {
  const piece = currentIllust();
  if (!piece) {
    return `
      <div class="illust">
        <div class="illust-stage">
          <button type="button" class="board-face" style="${ratioStyle("2:3")}" data-act="new-illust"></button>
        </div>
      </div>
    `;
  }
  const aspects = (state.vocab && state.vocab.illustAspects) || ["2:3", "1:1", "16:9"];
  const chips = aspects.map((aspect) => `
    <button type="button" class="chip ${aspect === piece.aspect ? "on" : ""}" data-act="set-illust" data-value="${esc(aspect)}" ${state.busy ? "disabled" : ""}>${esc(aspect)}</button>
  `).join("");
  const thumbs = state.illustrations.map((item) => `
    <button type="button" class="illust-thumb ${item.id === piece.id ? "on" : ""}" data-act="select-illust" data-id="${esc(item.id)}">
      ${item.imageUrl ? `<img src="${esc(item.imageUrl)}" alt="">` : "<span></span>"}
    </button>
  `).join("");
  const versions = (piece.versions || []).map((version) => `
    <button type="button" class="version ${version.current ? "on" : ""}" data-act="use-illust-version" data-id="${esc(version.id)}" title="${esc(version.note)}">
      <img src="${esc(version.url)}" alt="">
    </button>
  `).join("");
  return `
    <div class="illust">
      <div class="illust-stage">
        <div class="illust-paper" style="${ratioStyle(piece.aspect)}">
          ${piece.imageUrl ? `<img src="${esc(piece.imageUrl)}" alt="">` : ""}
        </div>
      </div>
      <div class="illust-dock">
        <div class="illust-strip">
          ${thumbs}
          <button type="button" class="plus" data-act="new-illust" title="New illustration" ${state.busy ? "disabled" : ""}>+</button>
        </div>
        <textarea id="illust-prompt" data-save="illust" data-field="prompt" data-entity="${esc(piece.id)}" placeholder="What is in this picture?">${esc(piece.prompt)}</textarea>
        <input id="illust-style" type="text" data-save="illust" data-field="artStyle" data-entity="${esc(piece.id)}" value="${esc(piece.artStyle)}" placeholder="Style">
        <div class="chips">${chips}</div>
        <div class="row">
          <button type="button" class="primary" data-act="draw-illust" ${state.busy ? "disabled" : ""}>Draw</button>
          <input id="illust-note" type="text" placeholder="What should change in this drawing?">
          <button type="button" class="ghost" data-act="revise-illust" ${state.busy || !piece.imageUrl ? "disabled" : ""}>Revise</button>
        </div>
        <div class="versions">${versions}</div>
      </div>
    </div>
  `;
}

function illustRail() {
  const piece = currentIllust();
  if (!piece) {
    return `
      <p class="rail-kicker">Illustration</p>
      <p class="meta">A free canvas for experiments, beside the manga.</p>
      <button type="button" class="rail-go" data-act="new-illust" ${state.busy ? "disabled" : ""}>New illustration</button>
    `;
  }
  const aspects = (state.vocab && state.vocab.illustAspects) || ["2:3", "1:1", "16:9"];
  const chips = aspects.map((aspect) => `
    <button type="button" class="chip ${aspect === piece.aspect ? "on" : ""}" data-act="set-illust" data-value="${esc(aspect)}" ${state.busy ? "disabled" : ""}>${esc(aspect)}</button>
  `).join("");
  const versions = (piece.versions || []).map((version) => `
    <button type="button" class="version ${version.current ? "on" : ""}" data-act="use-illust-version" data-id="${esc(version.id)}" title="${esc(version.note)}">
      <img src="${esc(version.url)}" alt="">
    </button>
  `).join("");
  return `
    <p class="rail-kicker">Illustration</p>
    <label class="field">Title
      <input type="text" data-save="illust" data-field="title" data-entity="${esc(piece.id)}" value="${esc(piece.title)}">
    </label>
    <label class="field">Picture
      <textarea id="illust-prompt" data-save="illust" data-field="prompt" data-entity="${esc(piece.id)}" placeholder="What is in this picture?">${esc(piece.prompt)}</textarea>
    </label>
    <label class="field">Style
      <input id="illust-style" type="text" data-save="illust" data-field="artStyle" data-entity="${esc(piece.id)}" value="${esc(piece.artStyle)}" placeholder="Style">
    </label>
    <div class="chips">${chips}</div>
    <button type="button" class="rail-go" data-act="draw-illust" ${state.busy ? "disabled" : ""}>Draw</button>
    <label class="field">Revise this drawing
      <input id="illust-note" type="text" placeholder="What should change in this drawing?">
    </label>
    <button type="button" class="ghost" data-act="revise-illust" ${state.busy || !piece.imageUrl ? "disabled" : ""}>Revise</button>
    <div class="section-label">Versions</div>
    <div class="versions">${versions || `<p class="meta">No drawings yet.</p>`}</div>
    <div class="row">
      <button type="button" class="ghost" data-act="new-illust" ${state.busy ? "disabled" : ""}>New</button>
      <button type="button" class="ghost" data-act="export-illust" data-id="${esc(piece.id)}" ${state.busy || !piece.imageUrl ? "disabled" : ""}>Export</button>
      <button type="button" class="danger quiet" data-act="delete-illust" data-id="${esc(piece.id)}" ${state.busy ? "disabled" : ""}>Delete</button>
    </div>
  `;
}

function illustStage() {
  const piece = currentIllust();
  if (!piece) {
    return `<div class="stage"><div class="empty">Start an illustration when you want to experiment away from the page.</div></div>`;
  }
  const thumbs = state.illustrations.map((item) => `
    <button type="button" class="illust-thumb ${item.id === piece.id ? "on" : ""}" data-act="select-illust" data-id="${esc(item.id)}">
      ${item.imageUrl ? `<img src="${esc(item.imageUrl)}" alt="">` : "<span></span>"}
    </button>
  `).join("");
  return `
    <div class="stage">
      <div class="studio-art" style="${ratioStyle(piece.aspect)}">
        ${piece.imageUrl ? `<img src="${esc(piece.imageUrl)}" alt="">` : ""}
      </div>
      <div class="illust-strip hero-thumbs">
        ${thumbs}
        <button type="button" class="plus" data-act="new-illust" title="New illustration" ${state.busy ? "disabled" : ""}>+</button>
      </div>
    </div>
  `;
}

function renderSettings() {
  const settings = state.settings || { hasKey: false, keyHint: "", quality: "low", resolution: "1k" };
  const hint = settings.hasKey ? `A key is saved (${esc(settings.keyHint)}).` : "No key saved yet.";
  return `
    <div class="settings"><div class="settings-inner stack">
      <h2>Settings</h2>
      <p class="lede">${hint} The key stays in a private file on this machine. Image generation uses your xAI account.</p>
      <label class="field">API key
        <input id="api-key" type="password" placeholder="Paste a key from console.x.ai" autocomplete="off">
      </label>
      <div class="row">
        <button type="button" class="primary" data-act="save-key">Save key</button>
        <button type="button" class="danger" data-act="clear-key">Remove key</button>
      </div>
      <p class="meta">1k is the cheaper size while you iterate. 2k is for finished pages. Low quality is cheaper; medium keeps more detail. Auto lets xAI choose.</p>
      <label class="field">Resolution
        <select data-save="settings" data-field="resolution" data-entity="">
          ${["1k", "2k"].map((item) => `<option value="${item}" ${item === settings.resolution ? "selected" : ""}>${item}</option>`).join("")}
        </select>
      </label>
      <label class="field">Image quality
        <select data-save="settings" data-field="quality" data-entity="">
          ${[["low", "Low"], ["medium", "Medium"], ["auto", "Auto"]].map(([id, label]) => `<option value="${id}" ${id === settings.quality ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <h2>3D models</h2>
      <p class="lede">The default builds a mesh on your machine with TripoSR. It needs a separate install and does not use a paid account. Meshy and Tripo are optional. Choosing one sends the prompt, and a reference image when you use one, to that service.</p>
      <label class="field">3D provider
        <select data-save="settings" data-field="meshProvider" data-entity="">
          ${[
            ["local", "Local (TripoSR)"],
            ["meshy", "Meshy"],
            ["tripo", "Tripo"],
          ].map(([id, label]) => `<option value="${id}" ${id === (settings.meshProvider || "local") ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <p class="meta">${settings.localReady ? "A local 3D command is ready." : "No local backend found yet. Install TripoSR or set a command. Weights are not included with Koma."}</p>
      <label class="field">Local 3D command
        <input type="text" data-save="settings" data-field="meshCommand" data-entity="" value="${esc(settings.meshCommand || "")}" placeholder="Leave empty to use TripoSR if it is installed">
      </label>
      <p class="meta">${settings.hasMeshyKey ? `A Meshy key is saved (${esc(settings.meshyHint)}).` : "No Meshy key saved."}</p>
      <label class="field">Meshy API key
        <input id="meshy-key" type="password" placeholder="Paste a Meshy key" autocomplete="off">
      </label>
      <div class="row">
        <button type="button" class="primary" data-act="save-meshy">Save Meshy key</button>
        <button type="button" class="danger" data-act="clear-meshy">Remove Meshy key</button>
      </div>
      <p class="meta">${settings.hasTripoKey ? `A Tripo key is saved (${esc(settings.tripoHint)}).` : "No Tripo key saved."}</p>
      <label class="field">Tripo API key
        <input id="tripo-key" type="password" placeholder="Paste a Tripo key" autocomplete="off">
      </label>
      <div class="row">
        <button type="button" class="primary" data-act="save-tripo">Save Tripo key</button>
        <button type="button" class="danger" data-act="clear-tripo">Remove Tripo key</button>
      </div>
      <p class="meta">Projects live in ${esc(state.dataDir)}. Exports go to Documents/Koma. 3D models live in the meshes folder beside the images.</p>
      <button type="button" class="ghost" data-act="go-home">Back to canvases</button>
    </div></div>
  `;
}

function renderAbout() {
  return `
    <div class="about">
      <div class="about-poster">
        <p class="about-kicker">About Koma</p>
        <h1>See an idea become pages.</h1>
        <p>Koma is a manga studio for anyone who wants to watch an idea come to life. You shape the story, keep a cast that stays recognizable, and draw one panel at a time.</p>
        <p>It is also for mangakas who want to find out what a story would feel like if they worked on it. Try a scene, gather inspiration, and keep references beside the page.</p>
        <p class="about-age">Age rating <strong>Everyone</strong></p>
        <footer class="about-foot">
          <p>This Koma manga creator studio is built by Liam using Grok.</p>
          <button type="button" class="x-link" data-act="open-x" aria-label="Liam on X">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.727-8.835L1.254 2.25H8.08l4.253 5.622L18.244 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
          </button>
        </footer>
      </div>
    </div>
  `;
}

function volumes() {
  return state.tree && state.tree.volumes ? state.tree.volumes : [];
}

function volumeSelect(act, currentId) {
  const list = volumes();
  if (list.length < 2) return "";
  const options = list.map((item) => {
    const name = (item.title || "").trim() || "Volume";
    return `<option value="${esc(item.id)}" ${item.id === currentId ? "selected" : ""}>${esc(name)}</option>`;
  }).join("");
  return `<label class="field">Volume<select data-act="${act}">${options}</select></label>`;
}

function focusVolume(id) {
  state.navVolumeId = id;
  state.storyVolumeId = id;
  const kids = chapters().filter((item) => item.volumeId === id);
  state.navChapterId = kids[0] ? kids[0].id : null;
  const page = kids[0] && kids[0].pages && kids[0].pages[0];
  if (page) {
    state.pageId = page.id;
    state.panelId = null;
    if (state.storyFocus === "page") state.storyFocusId = page.id;
    if (state.storyFocus === "chapter") state.storyFocusId = kids[0].id;
  } else {
    state.pageId = null;
    state.panelId = null;
  }
  if (state.view === "story") state.storyFocus = "volume";
}

function storyNotes() {
  return state.tree && state.tree.notes ? state.tree.notes : [];
}

function chapterNo(chapter) {
  const list = chapters().filter((item) => item.volumeId === chapter.volumeId);
  const index = list.findIndex((item) => item.id === chapter.id);
  return index < 0 ? chapter.idx + 1 : index + 1;
}

function pageSlots(page, reading) {
  if (page.slots && page.slots.length) return page.slots;
  return placed(page.layout, reading);
}

function layoutOptions(page) {
  const custom = page.layout === "custom" ? `<option value="custom" selected>Custom</option>` : "";
  return custom + state.layouts.map((layout) => `
    <option value="${esc(layout.id)}" ${layout.id === page.layout ? "selected" : ""}>${esc(layout.label)}</option>
  `).join("");
}

function focusClass(focus, id) {
  if (state.storyFocus !== focus) return "";
  if (id && state.storyFocus === "volume") return id === state.storyVolumeId ? "on" : "";
  if (id && (focus === "chapter" || focus === "page")) return id === state.storyFocusId ? "on" : "";
  if (id && focus === "notes") return id === state.storyNoteId ? "on" : "";
  return "on";
}

function renderCast() {
  const people = state.tree.characters;
  if (state.charId && !people.some((person) => person.id === state.charId)) state.charId = null;
  const selected = people.find((person) => person.id === state.charId) || people[0] || null;
  if (selected) state.charId = selected.id;
  const list = people.map((person) => {
    const thumb = (person.meshes || []).map((mesh) => mesh.thumb).find(Boolean) || "";
    return `
    <button type="button" class="person ${selected && person.id === selected.id ? "on" : ""}" data-act="select-character" data-id="${esc(person.id)}">
      ${thumb ? `<img class="person-mesh" src="${esc(thumb)}" alt="">` : ""}
      <span class="person-copy">
        <strong>${esc(person.name)}</strong>
        <div class="meta">${esc(person.role || "No role")}</div>
        ${person.primaryRefId ? "" : `<div class="badge">No portrait</div>`}
      </span>
    </button>
  `;
  }).join("");
  const form = selected ? characterFields(selected) : `<p class="meta">Add a character when you want a face the pages can follow. You can import a picture you already have.</p>`;
  const stage = selected ? characterStage(selected) : `<div class="stage"><div class="empty">Add a character when you want a face the pages can follow. You can import a picture you already have.</div></div>`;
  const rail = `
    <button type="button" class="ghost" data-act="add-character">Add character</button>
    <div class="rail-list">${list}</div>
    ${form}
  `;
  return desk(rail, stage);
}

function characterFields(person) {
  return `
    <div class="row">
      <p class="rail-kicker">This character</p>
      <button type="button" class="danger quiet" data-act="delete-character" data-id="${esc(person.id)}">Delete</button>
    </div>
    <label class="field">Name<input type="text" data-save="character" data-field="name" data-entity="${esc(person.id)}" value="${esc(person.name)}"></label>
    <label class="field">Role<input type="text" data-save="character" data-field="role" data-entity="${esc(person.id)}" value="${esc(person.role)}"></label>
    <label class="field">Appearance
      <textarea data-save="character" data-field="appearance" data-entity="${esc(person.id)}">${esc(person.appearance)}</textarea>
    </label>
    <label class="field">Wardrobe<textarea data-save="character" data-field="wardrobe" data-entity="${esc(person.id)}">${esc(person.wardrobe)}</textarea></label>
    <label class="field">Personality<textarea data-save="character" data-field="personality" data-entity="${esc(person.id)}">${esc(person.personality)}</textarea></label>
    <button type="button" class="rail-go" data-act="gen-character" data-id="${esc(person.id)}" data-kind="portrait" ${state.busy ? "disabled" : ""}>Draw portrait</button>
    <div class="row">
      <button type="button" class="ghost" data-act="gen-character" data-id="${esc(person.id)}" data-kind="turnaround" ${state.busy ? "disabled" : ""}>Turnaround</button>
      <button type="button" class="ghost" data-act="gen-character" data-id="${esc(person.id)}" data-kind="expressions" ${state.busy ? "disabled" : ""}>Expressions</button>
      <button type="button" class="ghost" data-act="gen-character" data-id="${esc(person.id)}" data-kind="poses" ${state.busy ? "disabled" : ""}>Poses</button>
    </div>
    <button type="button" class="ghost" data-act="import-ref" data-owner="character" data-id="${esc(person.id)}" data-kind="portrait" ${state.busy ? "disabled" : ""}>Import portrait</button>
    <label class="field">Pose note
      <input id="pose-note" type="text" placeholder="standing, walking, a leap, and sitting">
    </label>
    <div class="row">
      <button type="button" class="ghost" data-act="primary-ref" ${state.refId ? "" : "disabled"}>Use as identity</button>
      <button type="button" class="danger" data-act="delete-ref" ${state.refId ? "" : "disabled"}>Delete reference</button>
    </div>
    <textarea id="ref-note" placeholder="Revise the selected reference. Shorten the hair. Keep the face."></textarea>
    <button type="button" class="ghost" data-act="revise-ref" ${state.busy ? "disabled" : ""}>Revise reference</button>
    <p class="rail-kicker">3D model</p>
    <label class="field">Prompt
      <textarea id="mesh-prompt" data-save="local" data-field="meshPrompt" data-entity="" placeholder="A full-body figure in the same clothes">${esc(state.meshPrompt || "")}</textarea>
    </label>
    <div class="row">
      <button type="button" class="chip ${state.meshSource === "refs" ? "" : "on"}" data-act="mesh-source" data-value="prompt">From prompt</button>
      <button type="button" class="chip ${state.meshSource === "refs" ? "on" : ""}" data-act="mesh-source" data-value="refs">From references</button>
    </div>
    <button type="button" class="rail-go" data-act="gen-mesh" data-id="${esc(person.id)}" ${state.busy ? "disabled" : ""}>Generate 3D model</button>
    <button type="button" class="ghost" data-act="import-mesh" data-id="${esc(person.id)}" ${state.busy ? "disabled" : ""}>Import GLB, VRM, or OBJ</button>
    <p class="meta">Local TripoSR is the default. It lifts a picture on your machine. A prompt with no portrait draws a reference first. Meshy and Tripo need a key in Settings. Import reads a GLB, VRM, or OBJ already on your machine.</p>
  `;
}

function characterStage(person) {
  const chosen = person.refs.find((ref) => ref.id === state.refId) || person.refs.find((ref) => ref.isPrimary) || person.refs[0] || null;
  const thumbs = person.refs.map((ref) => `
    <button type="button" class="illust-thumb ${chosen && ref.id === chosen.id ? "on" : ""}" data-act="select-ref" data-id="${esc(ref.id)}" title="${esc(ref.kind)}">
      <img src="${esc(ref.url)}" alt="">
    </button>
  `).join("");
  const compact = (person.meshes || []).length ? " compact" : "";
  return `
    <div class="stage">
      ${characterMesh(person)}
      <div class="hero-art${compact}">
        ${chosen ? `<img src="${esc(chosen.url)}" alt="">` : `<span class="hero-name">${esc(person.name)}</span>`}
      </div>
      <div class="illust-strip hero-thumbs">${thumbs}</div>
    </div>
  `;
}

function characterMesh(person) {
  const meshes = person.meshes || [];
  if (state.meshId && !meshes.some((mesh) => mesh.id === state.meshId)) state.meshId = null;
  const current = meshes.find((mesh) => mesh.id === state.meshId) || meshes[0] || null;
  if (current) state.meshId = current.id;
  const names = { local: "Local", meshy: "Meshy", tripo: "Tripo", import: "Imported" };
  const from = { refs: "from references", file: "from a file", prompt: "from prompt" };
  const meta = current
    ? `${names[current.provider] || current.provider} · ${from[current.source] || "from prompt"}`
    : "No 3D model yet.";
  const flags = window.meshFlags ? meshFlags() : { auto: false, wire: false, light: true, large: false, grid: false, backdrop: "dark", shape: "", angle: "" };
  const shapes = (window.meshCamera && meshCamera.shapeNames) || [];
  const shapeRow = shapes.length ? `
        <div class="row mesh-tools mesh-shapes">
          <button type="button" class="chip ${flags.shape ? "" : "on"}" data-act="mesh-shape" data-value="">Rest</button>
          ${shapes.map((name) => `<button type="button" class="chip ${flags.shape === name ? "on" : ""}" data-act="mesh-shape" data-value="${esc(name)}">${esc(name)}</button>`).join("")}
        </div>` : "";
  const older = meshes.filter((mesh) => !current || mesh.id !== current.id).map((mesh) => `
    <button type="button" class="chip" data-act="select-mesh" data-id="${esc(mesh.id)}">${esc(names[mesh.provider] || mesh.provider)}</button>
  `).join("");
  return `
    <section class="mesh-panel ${current && flags.large ? "large" : ""}">
      <div class="row">
        <p class="rail-kicker">3D model</p>
        <span class="meta">${esc(meta)}</span>
      </div>
      <div class="mesh-view ${current ? "" : "empty"}">
        ${current ? `<canvas data-mesh-id="${esc(current.id)}" data-mesh-url="${esc(current.url)}" data-has-thumb="${current.thumb ? "1" : ""}" width="640" height="360" aria-label="3D model of ${esc(person.name)}"></canvas>` : `<p class="mesh-empty">Generate a model, or import a GLB, VRM, or OBJ file.</p>`}
        <p class="mesh-note"></p>
      </div>
      ${current ? `<div class="mesh-side"><p class="meta">Drag to orbit any way. Shift-drag pans. Alt-drag rolls. Scroll zooms.</p>
        ${shapeRow}
        ${animControls(current)}
        ${poseControls(person, current)}
        ${shapes.length ? `<p class="meta">Expressions move morph targets and stay on while a pose is set. Front, side, and three-quarter turn the camera.</p>` : ""}
        <div class="row mesh-tools">
          <button type="button" class="chip ${flags.angle === "front" ? "on" : ""}" data-act="mesh-angle" data-value="front">Front</button>
          <button type="button" class="chip ${flags.angle === "side" ? "on" : ""}" data-act="mesh-angle" data-value="side">Side</button>
          <button type="button" class="chip ${flags.angle === "three" ? "on" : ""}" data-act="mesh-angle" data-value="three">Three-quarter</button>
          <button type="button" class="chip" data-act="mesh-reset">Reset view</button>
          <button type="button" class="chip ${flags.auto ? "on" : ""}" data-act="mesh-auto">Auto-rotate</button>
          <button type="button" class="chip ${flags.wire ? "on" : ""}" data-act="mesh-wire">Wireframe</button>
          <button type="button" class="chip ${flags.light ? "on" : ""}" data-act="mesh-light">Lighting</button>
          <button type="button" class="chip ${flags.grid ? "on" : ""}" data-act="mesh-grid">Grid</button>
          <button type="button" class="chip ${flags.outline ? "on" : ""}" data-act="mesh-outline">Outline</button>
          <button type="button" class="chip ${flags.backdrop === "light" ? "on" : ""}" data-act="mesh-ground">${flags.backdrop === "light" ? "Dark ground" : "Light ground"}</button>
          <button type="button" class="chip ${flags.large ? "on" : ""}" data-act="mesh-large">${flags.large ? "Close" : "Large view"}</button>
        </div>
        <div class="row">
          <button type="button" class="ghost" data-act="mesh-shot" data-id="${esc(person.id)}" data-mesh="${esc(current.id)}" ${state.busy ? "disabled" : ""}>Save this angle</button>
          <button type="button" class="ghost" data-act="mesh-turntable" data-id="${esc(current.id)}" data-kind="gif" ${state.busy ? "disabled" : ""}>Turntable GIF</button>
          <button type="button" class="ghost" data-act="mesh-turntable" data-id="${esc(current.id)}" data-kind="png" ${state.busy ? "disabled" : ""}>Turntable PNGs</button>
          <button type="button" class="ghost" data-act="mesh-shot" data-id="${esc(person.id)}" data-mesh="${esc(current.id)}" data-panel="${esc(state.panelId || "")}" ${state.busy || !state.panelId ? "disabled" : ""}>Use on panel</button>
          <button type="button" class="ghost" data-act="export-mesh" data-id="${esc(current.id)}" data-format="glb" ${state.busy ? "disabled" : ""}>Export GLB</button>
          <button type="button" class="ghost" data-act="export-posed" data-id="${esc(current.id)}" ${state.busy ? "disabled" : ""}>Export posed GLB</button>
          <button type="button" class="ghost" data-act="export-mesh" data-id="${esc(current.id)}" data-format="obj" ${state.busy ? "disabled" : ""}>Export OBJ</button>
          <button type="button" class="danger" data-act="delete-mesh" data-id="${esc(current.id)}" ${state.busy ? "disabled" : ""}>Delete model</button>
        </div>
        ${older ? `<div class="row">${older}</div>` : ""}</div>` : ""}
    </section>
  `;
}

function animControls(current) {
  const clips = (window.meshCamera && meshCamera.clips) || [];
  const bones = (window.meshCamera && meshCamera.bones) || [];
  if (!current || (!clips.length && !bones.length)) return "";
  const index = meshCamera.clip;
  const active = index >= 0 ? clips[index] : null;
  const duration = active ? Number(active.duration) || 0 : 0;
  const time = Math.min(Number(meshCamera.time) || 0, duration || 0);
  const playing = Boolean(meshCamera.playing && active);
  const loop = meshCamera.loop !== false;
  const speed = Number(meshCamera.speed) || 1;
  const chips = [`<button type="button" class="chip ${index < 0 ? "on" : ""}" data-act="mesh-clip" data-value="-1">Rest</button>`]
    .concat(clips.map((clip, clipIndex) => `<button type="button" class="chip ${index === clipIndex ? "on" : ""}" data-act="mesh-clip" data-value="${clipIndex}">${esc(clip.name)}</button>`))
    .join("");
  const speeds = [0.5, 1, 2].map((value) => `<button type="button" class="chip ${speed === value ? "on" : ""}" data-act="mesh-speed" data-value="${value}">${value}×</button>`).join("");
  return `
    <div class="row mesh-tools mesh-clips">${chips}</div>
    <div class="row mesh-tools">
      <button type="button" class="chip ${playing ? "on" : ""}" data-act="mesh-play" ${active ? "" : "disabled"}>${playing ? "Pause" : "Play"}</button>
      <button type="button" class="chip ${loop ? "on" : ""}" data-act="mesh-loop">Loop</button>
      ${speeds}
      <button type="button" class="ghost" data-act="mesh-anim-import" data-mesh="${esc(current.id)}" ${state.busy || !bones.length ? "disabled" : ""}>Import animation</button>
    </div>
    <label class="mesh-time">Time
      <input type="range" min="0" max="${duration || 0}" step="0.01" value="${time}" data-anim-time aria-label="Animation time" ${active ? "" : "disabled"}>
      <span data-anim-readout>${active ? `${time.toFixed(2)}s / ${duration.toFixed(2)}s` : "No clip"}</span>
    </label>
    <p class="meta">Play a clip from the file, scrub to a frame, then save that angle. Import animation reads a GLB or VRM on your machine and matches it by bone name, or by the humanoid map. An imported clip is saved with this model and is here the next time you open it. Nothing is uploaded.</p>`;
}

function poseControls(person, current) {
  const bones = (window.meshCamera && meshCamera.bones) || [];
  if (!current || !bones.length) return "";
  const flags = window.meshFlags ? meshFlags() : { poseMode: false, showBones: true, selected: "" };
  const presets = [
    ["bind", "T / A pose"],
    ["relaxed", "Standing"],
    ["walk", "Walking"],
    ["run", "Running"],
    ["sit", "Sitting"],
    ["point", "Pointing"],
    ["cross", "Arms crossed"],
    ["fight", "Fighting"],
    ["wave", "Wave"],
    ["kneel", "Kneeling"],
    ["stride", "Mid-stride"],
    ["crouch", "Crouch"],
  ];
  if (!flags.poseMode) {
    return `
      <div class="row mesh-tools">
        <button type="button" class="chip" data-act="mesh-pose">Pose</button>
      </div>
      <p class="meta">Pose rotates this rig. A VRM uses its humanoid bones. Other models match bones by name.</p>`;
  }
  const groups = [];
  for (const bone of bones) {
    let group = groups.find((item) => item.name === bone.group);
    if (!group) {
      group = { name: bone.group, bones: [] };
      groups.push(group);
    }
    group.bones.push(bone);
  }
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const geom = canvas && canvas._geom;
  const euler = window.boneEuler && flags.selected ? boneEuler(geom, flags.selected) : { x: 0, y: 0, z: 0 };
  const body = groups.filter((group) => group.name !== "Hands").map((group) => `
    <div class="mesh-bone-group">
      <span class="meta">${esc(group.name)}</span>
      ${group.bones.map((bone) => `<button type="button" class="chip ${flags.selected === bone.name ? "on" : ""}" data-act="mesh-bone" data-bone="${esc(bone.name)}">${esc(bone.label)}</button>`).join("")}
    </div>`).join("");
  const hands = groups.find((group) => group.name === "Hands");
  const handRow = hands ? `
    <details class="mesh-hands">
      <summary>Hands</summary>
      <div class="mesh-bone-group">
        ${hands.bones.map((bone) => `<button type="button" class="chip ${flags.selected === bone.name ? "on" : ""}" data-act="mesh-bone" data-bone="${esc(bone.name)}">${esc(bone.label)}</button>`).join("")}
      </div>
    </details>` : "";
  const saved = (person.poses || []).map((pose) => `
    <button type="button" class="chip" data-act="pose-load" data-id="${esc(pose.id)}" data-char="${esc(person.id)}">${esc(pose.name)}</button>
    <button type="button" class="quiet" data-act="pose-delete" data-id="${esc(pose.id)}">Delete</button>
  `).join("");
  return `
    <div class="row mesh-tools">
      <button type="button" class="chip on" data-act="mesh-pose">Pose</button>
      <button type="button" class="chip ${flags.showBones !== false ? "on" : ""}" data-act="mesh-bones">Show bones</button>
      ${presets.map(([value, label]) => `<button type="button" class="chip" data-act="mesh-preset" data-value="${value}">${label}</button>`).join("")}
      <button type="button" class="chip" data-act="mesh-mirror" title="Copy the left arm and leg onto the right (M)">Mirror</button>
    </div>
    <div class="mesh-bones" data-scroll="pose-bones">
      ${body}
      ${handRow}
    </div>
    <div class="mesh-sliders">
      ${["x", "y", "z"].map((axis) => `<label>${axis.toUpperCase()} <input type="range" min="-180" max="180" step="1" data-pose-axis="${axis}" value="${euler[axis]}" aria-label="${axis.toUpperCase()} rotation"></label>`).join("")}
      <label>Fingers <input type="range" min="0" max="100" step="1" data-finger-curl value="${Math.round((Number(window.meshCamera && meshCamera.fingerCurl) || 0) * 100)}" aria-label="Finger curl"></label>
    </div>
    <p class="meta">Click a bone here or on the model. Drag a handle, or use the sliders. Mirror copies the left arm and leg onto the right. Wave, Kneeling, Mid-stride, and Crouch are built in. Show bones turns the markers and handles on or off, and it starts on. T / A pose restores the file's bind pose.</p>
    <div class="row mesh-tools">
      <input id="pose-name" class="pose-name" type="text" data-save="local" data-field="poseName" data-entity="" value="${esc(state.poseName || "")}" placeholder="Name this pose" maxlength="40">
      <button type="button" class="ghost" data-act="pose-save" data-id="${esc(person.id)}" ${state.busy ? "disabled" : ""}>Save pose</button>
    </div>
    ${saved ? `<div class="row mesh-tools">${saved}</div>` : ""}
  `;
}

function renderPlaces() {
  const places = state.tree.locations;
  if (state.locId && !places.some((place) => place.id === state.locId)) state.locId = null;
  const selected = places.find((place) => place.id === state.locId) || places[0] || null;
  if (selected) state.locId = selected.id;
  const list = places.map((place) => `
    <button type="button" class="person ${selected && place.id === selected.id ? "on" : ""}" data-act="select-location" data-id="${esc(place.id)}">
      <strong>${esc(place.name)}</strong>
    </button>
  `).join("");
  const picture = selected && selected.refs.length ? selected.refs[0] : null;
  const rail = `
    <button type="button" class="ghost" data-act="add-location">Add place</button>
    <div class="rail-list">${list}</div>
    ${selected ? `
      <div class="row">
        <p class="rail-kicker">This place</p>
        <button type="button" class="danger quiet" data-act="delete-location" data-id="${esc(selected.id)}">Delete</button>
      </div>
      <label class="field">Name<input type="text" data-save="location" data-field="name" data-entity="${esc(selected.id)}" value="${esc(selected.name)}"></label>
      <label class="field">Description<textarea data-save="location" data-field="description" data-entity="${esc(selected.id)}">${esc(selected.description)}</textarea></label>
      <button type="button" class="rail-go" data-act="gen-location" data-id="${esc(selected.id)}" ${state.busy ? "disabled" : ""}>Draw the place</button>
      <button type="button" class="ghost" data-act="import-ref" data-owner="location" data-id="${esc(selected.id)}" ${state.busy ? "disabled" : ""}>Import picture</button>
      <textarea id="place-note" placeholder="Revise the view. Later afternoon, wet pavement."></textarea>
      <button type="button" class="ghost" data-act="revise-location" data-id="${esc(selected.id)}" ${state.busy ? "disabled" : ""}>Revise place</button>
    ` : `<p class="meta">Add a place when a scene needs a location. A picture you already have can be the view.</p>`}
  `;
  const stage = selected ? `
    <div class="stage">
      <div class="hero-art wide">
        ${picture ? `<img src="${esc(picture.url)}" alt="">` : `<span class="hero-name">${esc(selected.name)}</span>`}
      </div>
    </div>
  ` : `<div class="stage"><div class="empty">Add a place when a scene needs a location. A picture you already have can be the view.</div></div>`;
  return desk(rail, stage);
}

function renderStory() {
  const project = state.tree.project;
  const vols = volumes();
  if (!vols.some((item) => item.id === state.storyVolumeId)) state.storyVolumeId = vols[0] ? vols[0].id : null;
  const volume = vols.find((item) => item.id === state.storyVolumeId) || null;
  const volChapters = chapters().filter((item) => !volume || item.volumeId === volume.id);
  if (state.storyFocus === "chapter" && !volChapters.some((item) => item.id === state.storyFocusId)) {
    state.storyFocusId = volChapters[0] ? volChapters[0].id : "";
  }
  const pageIds = volChapters.flatMap((chapter) => chapter.pages.map((page) => page.id));
  if (state.storyFocus === "page" && !pageIds.includes(state.storyFocusId)) state.storyFocusId = pageIds[0] || "";
  const noteList = storyNotes();
  if (!noteList.some((item) => item.id === state.storyNoteId)) state.storyNoteId = noteList[0] ? noteList[0].id : null;
  let chapter = null;
  if (state.storyFocus === "page") {
    const found = allPages().find((row) => row.page.id === state.storyFocusId);
    if (found) chapter = volChapters.find((item) => item.id === found.chapter.id) || null;
  }
  if (!chapter && state.storyFocus === "chapter") {
    chapter = volChapters.find((item) => item.id === state.storyFocusId) || null;
  }
  if (!chapter) chapter = volChapters.find((item) => item.id === state.navChapterId) || volChapters[0] || null;
  const pageRow = allPages().find((row) => row.page.id === state.storyFocusId) || null;
  const note = noteList.find((item) => item.id === state.storyNoteId) || null;

  const noteButtons = noteList.map((item) => `
    <button type="button" class="page-link ${item.id === state.storyNoteId && state.storyFocus === "notes" ? "on" : ""}" data-act="story-focus" data-focus="notes" data-id="${esc(item.id)}">${esc(item.title || "Note")}</button>
  `).join("");
  const rail = `
    <div class="rail-tree">
      ${storyOutline(project, volume, chapter)}
      <button type="button" class="page-link ${focusClass("idea")}" data-act="story-focus" data-focus="idea" title="The premise of the book">Idea</button>
      <button type="button" class="page-link ${focusClass("mood")}" data-act="story-focus" data-focus="mood" title="Pictures from this computer, kept with the book">References</button>
      <p class="rail-kicker">Notes</p>
      ${noteButtons}
      ${treePlus("add-note")}
    </div>
  `;
  return desk(rail, storyStage(project, volume, chapter, pageRow, note), storySide(project, volume, chapter, pageRow, note));
}

function storyStage(project, volume, chapter, pageRow, note) {
  const plan = project.pendingPlan;
  const planCard = plan ? `
    <div class="plan-card stack">
      <strong>Plan ready${plan.title ? ": " + esc(plan.title) : ""}</strong>
      <p>${esc(plan.logline || "")}</p>
      <p class="meta">Cast: ${esc((plan.cast || []).join(", ") || "none")}</p>
      <p class="meta">Places: ${esc((plan.locations || []).join(", ") || "none")}</p>
      <ul class="beat-list">${(plan.chapters || []).map((item) => `<li>${esc(item.title)} · ${item.pages} pages</li>`).join("")}</ul>
      <div class="row">
        <button type="button" class="primary" data-act="apply-plan" data-mode="replace">${chapters().length ? "Replace outline" : "Apply plan"}</button>
        ${chapters().length ? `<button type="button" class="ghost" data-act="apply-plan" data-mode="append">Append chapters</button>` : ""}
        <button type="button" class="danger" data-act="discard-plan">Discard</button>
      </div>
    </div>
  ` : "";
  let body = "";
  if (state.storyFocus === "idea") {
    body = `
      <h2>Idea</h2>
      <label class="field big-field">The scope of the story
        <textarea data-save="project" data-field="premise" data-entity="${esc(project.id)}">${esc(project.premise)}</textarea>
      </label>
      ${planCard}
    `;
  } else if (state.storyFocus === "volume" && volume) {
    body = `
      <h2>${esc(volume.title || "Volume")}</h2>
      <label class="field big-field">What this volume is about
        <textarea data-save="volume" data-field="synopsis" data-entity="${esc(volume.id)}">${esc(volume.synopsis || "")}</textarea>
      </label>
    `;
  } else if (state.storyFocus === "chapter" && chapter) {
    body = `
      <h2>Chapter ${chapterNo(chapter)}</h2>
      <label class="field big-field">Synopsis
        <textarea data-save="chapter" data-field="synopsis" data-entity="${esc(chapter.id)}">${esc(chapter.synopsis || "")}</textarea>
      </label>
      <label class="field big-field">Beats, one per line
        <textarea data-save="chapter" data-field="beats" data-entity="${esc(chapter.id)}">${esc((chapter.beats || []).join("\n"))}</textarea>
      </label>
    `;
  } else if (state.storyFocus === "page" && pageRow) {
    body = `
      <h2>${esc(pageRow.chapter.title)} · Page ${pageRow.page.idx + 1}</h2>
      <label class="field big-field">Page notes
        <textarea data-save="page" data-field="notes" data-entity="${esc(pageRow.page.id)}">${esc(pageRow.page.notes || "")}</textarea>
      </label>
      <button type="button" class="rail-go" data-act="open-page" data-id="${esc(pageRow.page.id)}">Open this page</button>
    `;
  } else if (state.storyFocus === "notes" && note) {
    body = `
      <h2>${esc(note.title || "Note")}</h2>
      <label class="field big-field">Note
        <textarea data-save="note" data-field="body" data-entity="${esc(note.id)}">${esc(note.body || "")}</textarea>
      </label>
    `;
  } else if (state.storyFocus === "mood") {
    const moods = state.tree.moods || [];
    const tiles = moods.map((ref) => `
      <figure class="mood-card">
        <img src="${esc(ref.url)}" alt="${esc(ref.label || "Reference")}">
        <figcaption>${esc(ref.label || "Reference")}</figcaption>
        <button type="button" class="danger quiet" data-act="delete-mood" data-id="${esc(ref.id)}" title="Remove this picture from the board">Remove</button>
      </figure>
    `).join("");
    body = `
      <h2>References</h2>
      <p class="meta">Pictures already on your machine. They stay with this book and are not uploaded. Use them while you write, or as a reminder of a pose, a place, or a color.</p>
      ${moods.length ? `<div class="mood-grid">${tiles}</div>` : `<div class="empty">No pictures yet. Add one from a folder on this computer. Press ? for shortcuts.</div>`}
    `;
  } else {
    body = `
      <h2>Title</h2>
      <label class="field big-field">Book title
        <input type="text" data-save="project" data-field="title" data-entity="${esc(project.id)}" value="${esc(project.title)}">
      </label>
    `;
  }
  return `<section class="stage-scroll" data-scroll="outline"><div class="story-sheet">${body}</div></section>`;
}

function storySide(project, volume, chapter, pageRow, note) {
  const thread = (state.tree.messages || []).map((message) => `
    <div class="bubble ${esc(message.role)}">${esc(message.content)}</div>
  `).join("") || `<p class="meta">Plan from the idea, or write the chapters yourself.</p>`;
  if (state.storyFocus === "idea") {
    return `
      <p class="rail-kicker">Idea</p>
      <div class="row">
        <label class="field">Chapters<input type="number" min="1" max="12" data-save="local" data-field="planChapters" data-entity="" value="${esc(state.planChapters)}"></label>
        <label class="field">Pages each<input type="number" min="1" max="16" data-save="local" data-field="planPages" data-entity="" value="${esc(state.planPages)}"></label>
      </div>
      <button type="button" class="rail-go" data-act="plan" ${state.busy ? "disabled" : ""}>Plan the story</button>
      <details>
        <summary>Editor notes</summary>
        <div class="thread" data-scroll="thread">${thread}</div>
        <textarea id="note" placeholder="Ask for a change. Make chapter two quieter."></textarea>
        <button type="button" class="ghost" data-act="send-note" ${state.busy ? "disabled" : ""}>Send note</button>
      </details>
    `;
  }
  if (state.storyFocus === "volume" && volume) {
    return `
      <p class="rail-kicker">Volume</p>
      <label class="field">Title<input type="text" data-save="volume" data-field="title" data-entity="${esc(volume.id)}" value="${esc(volume.title)}"></label>
      <button type="button" class="ghost" data-act="add-chapter" data-id="${esc(volume.id)}">Add chapter</button>
      <button type="button" class="danger quiet" data-act="delete-volume" data-id="${esc(volume.id)}" ${volumes().length < 2 ? "disabled" : ""}>Delete volume</button>
      <p class="meta">Deleting a volume keeps its chapters by moving them to another volume.</p>
    `;
  }
  if (state.storyFocus === "chapter" && chapter) {
    const options = volumes().map((item) => `
      <option value="${esc(item.id)}" ${item.id === chapter.volumeId ? "selected" : ""}>${esc(item.title)}</option>
    `).join("");
    return `
      <p class="rail-kicker">Chapter ${chapterNo(chapter)}</p>
      <label class="field">Title<input type="text" data-save="chapter" data-field="title" data-entity="${esc(chapter.id)}" value="${esc(chapter.title)}"></label>
      <label class="field">Volume
        <select data-save="chapter" data-field="volumeId" data-entity="${esc(chapter.id)}">${options}</select>
      </label>
      <button type="button" class="ghost" data-act="add-page" data-id="${esc(chapter.id)}">Add page</button>
      <button type="button" class="ghost" data-act="duplicate-chapter" data-id="${esc(chapter.id)}" ${state.busy ? "disabled" : ""}>Duplicate chapter</button>
      <div class="row">
        <button type="button" class="ghost" data-act="move-chapter" data-id="${esc(chapter.id)}" data-dir="-1">Up</button>
        <button type="button" class="ghost" data-act="move-chapter" data-id="${esc(chapter.id)}" data-dir="1">Down</button>
      </div>
      <button type="button" class="danger quiet" data-act="delete-chapter" data-id="${esc(chapter.id)}">Delete chapter</button>
    `;
  }
  if (state.storyFocus === "page" && pageRow) {
    return `
      <p class="rail-kicker">Page ${pageRow.page.idx + 1}</p>
      <p class="meta">${esc(pageRow.page.layout)} · ${pageRow.page.panels.length} panels</p>
      <button type="button" class="rail-go" data-act="open-page" data-id="${esc(pageRow.page.id)}">Draw this page</button>
      <button type="button" class="danger quiet" data-act="delete-page" data-id="${esc(pageRow.page.id)}">Delete page</button>
    `;
  }
  if (state.storyFocus === "mood") {
    const count = (state.tree.moods || []).length;
    return `
      <p class="rail-kicker">Reference board</p>
      <p class="meta">${count ? `${count} picture${count === 1 ? "" : "s"} on this book.` : "Nothing here yet."} Koma copies a file you already have. It does not open a website or send the picture anywhere.</p>
      <button type="button" class="rail-go" data-act="import-ref" data-owner="project" data-id="${esc(project.id)}" data-kind="mood" title="Copy a picture from this computer onto the board" ${state.busy ? "disabled" : ""}>Add picture</button>
    `;
  }
  if (state.storyFocus === "notes" && note) {
    return `
      <p class="rail-kicker">Note</p>
      <label class="field">Title<input type="text" data-save="note" data-field="title" data-entity="${esc(note.id)}" value="${esc(note.title)}"></label>
      <label class="field">Kept with
        <select data-save="note" data-field="scope" data-entity="${esc(note.id)}">
          ${["story", "volume", "chapter", "page"].map((scope) => `<option value="${scope}" ${note.scope === scope ? "selected" : ""}>${scope}</option>`).join("")}
        </select>
      </label>
      <button type="button" class="danger quiet" data-act="delete-note" data-id="${esc(note.id)}">Delete note</button>
    `;
  }
  return `
    <p class="rail-kicker">Title</p>
    <label class="field">Logline<input type="text" data-save="project" data-field="logline" data-entity="${esc(project.id)}" value="${esc(project.logline)}"></label>
    <label class="field">Genre<input type="text" data-save="project" data-field="genre" data-entity="${esc(project.id)}" value="${esc(project.genre)}"></label>
    <label class="field">Tone<input type="text" data-save="project" data-field="tone" data-entity="${esc(project.id)}" value="${esc(project.tone)}"></label>
    <label class="field">Art style<textarea data-save="project" data-field="artStyle" data-entity="${esc(project.id)}">${esc(project.artStyle)}</textarea></label>
    <label class="field">Style notes<textarea data-save="project" data-field="styleNotes" data-entity="${esc(project.id)}">${esc(project.styleNotes)}</textarea></label>
    <label class="field">Reading
      <select data-save="project" data-field="reading" data-entity="${esc(project.id)}">
        <option value="rtl" ${project.reading === "rtl" ? "selected" : ""}>Right to left</option>
        <option value="ltr" ${project.reading === "ltr" ? "selected" : ""}>Left to right</option>
      </select>
    </label>
    <button type="button" class="ghost" data-act="add-volume">Add volume</button>
    <button type="button" class="danger quiet" data-act="delete-manga" data-id="${esc(project.id)}">Delete manga</button>
  `;
}

function storyOutline(project, volume, chapter) {
  const pages = chapter ? (chapter.pages || []).map((page) => `
    <div class="tree-node">
      <button type="button" class="page-link ${state.storyFocus === "page" && page.id === state.storyFocusId ? "on" : ""}" data-act="story-focus" data-focus="page" data-id="${esc(page.id)}">${pageThumb(page, project.reading)}<span>Page ${page.idx + 1}</span></button>
      ${treePlus("add-page", chapter.id)}
    </div>
  `).join("") : "";
  const chapterNode = chapter ? `
    <div class="tree-node">
      <button type="button" class="page-link ${state.storyFocus === "chapter" ? "on" : ""}" data-act="story-focus" data-focus="chapter" data-id="${esc(chapter.id)}">Chapter ${chapterNo(chapter)}</button>
      ${treePlus("add-page", chapter.id)}
      <div class="tree-kids">${pages}</div>
    </div>
  ` : "";
  const volumeName = volume ? ((volume.title || "").trim() || "Volume 1") : "";
  const volumeNode = volume ? `
    <div class="tree-node">
      <button type="button" class="page-link ${state.storyFocus === "volume" ? "on" : ""}" data-act="story-focus" data-focus="volume" data-id="${esc(volume.id)}">${esc(volumeName)}</button>
      ${chapter ? treePlus("add-page", chapter.id) : ""}
      <div class="tree-kids">${chapterNode}</div>
    </div>
  ` : "";
  const titleOn = state.storyFocus === "title" ? " on" : "";
  return `
    ${volumeSelect("pick-story-volume", volume ? volume.id : "")}
    <div class="tree-node">
      <label class="field tree-name tree-root${titleOn}">Title
        <input type="text" data-save="project" data-field="title" data-entity="${esc(project.id)}" value="${esc(project.title)}">
      </label>
      <div class="tree-kids">${volumeNode}</div>
    </div>
  `;
}

function treePlus(act, id) {
  const data = id ? ` data-id="${esc(id)}"` : "";
  return `<button type="button" class="plus tree-plus" data-act="${act}"${data} data-seed="1" aria-label="+" ${state.busy ? "disabled" : ""}>+</button>`;
}

function pageOutline(project, vols) {
  const volume = vols.find((item) => item.id === state.navVolumeId) || vols[0] || null;
  const kids = volume ? chapters().filter((chapter) => chapter.volumeId === volume.id) : [];
  const chapter = kids.find((item) => item.id === state.navChapterId) || kids[0] || null;
  const pages = chapter ? (chapter.pages || []).map((page) => `
    <div class="tree-node">
      <button type="button" class="page-link ${page.id === state.pageId ? "on" : ""}" draggable="true" data-drag="page" data-chapter="${esc(chapter.id)}" data-act="select-page" data-id="${esc(page.id)}">${pageThumb(page, project.reading)}<span>Page ${page.idx + 1}</span></button>
      ${treePlus("add-page", chapter.id)}
    </div>
  `).join("") : "";
  const chapterNode = chapter ? `
    <div class="tree-node">
      <button type="button" class="page-link on" data-act="nav-chapter" data-id="${esc(chapter.id)}">Chapter ${chapterNo(chapter)}</button>
      ${treePlus("add-page", chapter.id)}
      <div class="tree-kids">${pages}</div>
    </div>
  ` : "";
  const volumeName = volume ? ((volume.title || "").trim() || "Volume 1") : "";
  const volumeNode = volume ? `
    <div class="tree-node">
      <button type="button" class="page-link on" data-act="nav-volume" data-id="${esc(volume.id)}">${esc(volumeName)}</button>
      ${chapter ? treePlus("add-page", chapter.id) : treePlus("add-chapter", volume.id)}
      <div class="tree-kids">${chapterNode}</div>
    </div>
  ` : "";
  return `
    ${volumeSelect("pick-nav-volume", volume ? volume.id : "")}
    <div class="tree-node">
      <label class="field tree-name tree-root">Title
        <input type="text" data-save="project" data-field="title" data-entity="${esc(project.id)}" value="${esc(project.title)}">
      </label>
      <div class="tree-kids">${volumeNode}</div>
    </div>
  `;
}

function renderPages() {
  ensurePageSelection();
  const project = state.tree.project;
  const vols = volumes();
  const row = currentPage();
  if (row && (!state.navVolumeId || !vols.some((item) => item.id === state.navVolumeId))) {
    state.navVolumeId = row.chapter.volumeId || (vols[0] && vols[0].id) || null;
  }
  if (!vols.some((item) => item.id === state.navVolumeId)) state.navVolumeId = vols[0] ? vols[0].id : null;
  const volume = vols.find((item) => item.id === state.navVolumeId) || null;
  const volChapters = chapters().filter((item) => !volume || item.volumeId === volume.id);
  if (row && volChapters.some((item) => item.id === row.chapter.id)) state.navChapterId = row.chapter.id;
  if (!volChapters.some((item) => item.id === state.navChapterId)) state.navChapterId = volChapters[0] ? volChapters[0].id : null;
  const pageFile = row ? `
    <details>
      <summary>This page</summary>
      <div class="stack">
        <div class="row">
          <button type="button" class="ghost" data-act="move-page" data-id="${esc(row.page.id)}" data-dir="-1">Earlier</button>
          <button type="button" class="ghost" data-act="move-page" data-id="${esc(row.page.id)}" data-dir="1">Later</button>
        </div>
        <button type="button" class="ghost" data-act="duplicate-page" data-id="${esc(row.page.id)}" ${state.busy ? "disabled" : ""}>Duplicate page</button>
        <button type="button" class="ghost" data-act="draw-empty" data-scope="page" data-id="${esc(row.page.id)}" ${state.busy ? "disabled" : ""}>Draw empty panels</button>
        <button type="button" class="ghost" data-act="toggle-print" data-id="${esc(row.page.id)}" ${state.busy ? "disabled" : ""}>${state.printFor === row.page.id ? "Edit panels" : "Print preview"}</button>
        <div class="row">
          <button type="button" class="ghost" data-act="export-page" data-id="${esc(row.page.id)}">Export page</button>
          <button type="button" class="ghost" data-act="export-chapter" data-id="${esc(row.chapter.id)}" data-format="pdf">Chapter PDF</button>
          <button type="button" class="ghost" data-act="export-chapter" data-id="${esc(row.chapter.id)}" data-format="cbz">Chapter CBZ</button>
        </div>
        <button type="button" class="danger quiet" data-act="delete-page" data-id="${esc(row.page.id)}">Delete page</button>
      </div>
    </details>
  ` : "";
  const rail = `
    <div class="rail-tree">
      ${pageOutline(project, vols)}
      <p class="rail-kicker">Page history</p>
      <p class="meta">Drag a page or a panel to reorder it. Earlier and Later still work. Undo is kept after you quit.</p>
      <div class="row">
        <button type="button" class="ghost" data-act="undo-page" title="Undo (Ctrl+Z)">Undo</button>
        <button type="button" class="ghost" data-act="redo-page" title="Redo (Ctrl+Shift+Z)">Redo</button>
      </div>
      <p class="rail-kicker">Panel maker</p>
      <div class="row">
        <button type="button" class="ghost" data-act="suggest-layout" ${state.busy || !row ? "disabled" : ""}>Suggest</button>
        <button type="button" class="ghost" data-act="arm-divide" ${!row ? "disabled" : ""}>${state.divideOn ? "Divide" : "Divide"}</button>
      </div>
      ${state.divideOn ? `<button type="button" class="ghost" data-act="cancel-divide">Cancel</button><p class="meta">Drag the line. Rotate it from the middle or from either end, then Divide.</p>` : `<p class="meta">Suggest asks Grok for a layout. Divide lets you cut a panel yourself.</p>`}
      ${row ? `<label class="field">Start from a layout<select data-save="page" data-field="layout" data-entity="${esc(row.page.id)}">${layoutOptions(row.page)}</select></label>` : ""}
      ${pageFile}
    </div>
  `;
  const stage = row
    ? pageStage(row, project)
    : `<div class="stage"><div class="empty">Add a chapter and a page when you are ready to draw. Press ? for the page shortcuts.</div></div>`;
  return desk(rail, stage, toolDock(row ? row.page : null, row ? currentPanel() : null), "rail-tools");
}

function pageRatio(project) {
  const w = Number(project && project.trimW);
  const h = Number(project && project.trimH);
  if (w > 0 && h > 0) return `--aw:${w};--ah:${h}`;
  return "--aw:2;--ah:3";
}

function pageStage(row, project) {
  const reading = project.reading;
  const page = row.page;
  const slots = pageSlots(page, reading);
  const panel = currentPanel();
  const panels = page.panels.map((item) => {
    const slot = slots[item.slot] || { x: 0, y: 0, w: 1, h: 1 };
    const gap = 0.012;
    const style = `left:${(slot.x + gap) * 100}%;top:${(slot.y + gap) * 100}%;width:${Math.max(0.04, slot.w - gap * 2) * 100}%;height:${Math.max(0.04, slot.h - gap * 2) * 100}%;`;
    const image = item.imageUrl ? `background-image:url('${esc(item.imageUrl)}')` : "";
    const placed = (item.elements || []).filter((el) => el.kind === "balloon" && el.text);
    const balloon = placed.length
      ? placed.map((el) => `<div class="balloon placed ${esc(el.balloon)}" style="left:${el.x * 100}%;top:${el.y * 100}%;width:${el.w * 100}%;height:${el.h * 100}%">${esc(el.text)}</div>`).join("")
      : (item.dialogue && item.balloon !== "none"
        ? `<div class="balloon ${esc(item.balloon)} ${esc(item.balloonAnchor)}">${esc(item.dialogue)}</div>`
        : "");
    const sfx = item.sfx
      ? `<div class="sfx ${item.balloonAnchor && item.balloonAnchor.startsWith("t") ? "low" : "high"}">${esc(item.sfx)}</div>`
      : "";
    return `
      <button type="button" class="slot ${item.id === state.panelId ? "selected" : ""}" draggable="true" data-drag="panel" style="${style}${image}" data-act="select-panel" data-id="${esc(item.id)}">
        ${item.imageUrl ? "" : `<span class="ph">${item.slot + 1}</span>`}
        ${balloon}${sfx}
      </button>
    `;
  }).join("");
  const overlays = panel && state.printFor !== page.id ? pageOverlays(panel, slots) : "";
  const divide = state.divideOn && state.printFor !== page.id ? divideSvg() : "";
  const sheet = state.printFor === page.id && state.sheetUrl
    ? `<img class="print-page" style="${pageRatio(project)}" src="${esc(state.sheetUrl)}?t=${state.sheetStamp}" alt="Printed page">`
    : `<div class="paper" style="${pageRatio(project)}">${panels}${overlays}${divide}</div>`;
  return `<div class="stage-wrap"><div class="stage">${sheet}</div></div>`;
}

function elementOverlay(panel, slots) {
  const slot = slots[panel.slot] || { x: 0, y: 0, w: 1, h: 1 };
  const style = `left:${slot.x * 100}%;top:${slot.y * 100}%;width:${slot.w * 100}%;height:${slot.h * 100}%;`;
  const boxes = (panel.elements || []).map((el) => {
    const on = state.elementId === el.id ? " on" : "";
    return `<div class="el-box${on}" data-pen="el-move" data-id="${esc(el.id)}" style="left:${el.x * 100}%;top:${el.y * 100}%;width:${el.w * 100}%;height:${el.h * 100}%;"><span>${esc(el.text || "")}</span><i data-pen="el-resize" data-id="${esc(el.id)}"></i></div>`;
  }).join("");
  return `<div class="element-layer" style="${style}">${boxes}</div>`;
}

function readElements() {
  const panel = currentPanel();
  if (!panel) return [];
  const prior = {};
  for (const item of panel.elements || []) prior[item.id] = item;
  return [...document.querySelectorAll(".el-box")].map((box) => {
    const id = box.dataset.id;
    const known = prior[id] || { id, kind: "thing", text: "", balloon: "round" };
    return {
      id,
      kind: known.kind,
      text: known.text,
      balloon: known.balloon || "round",
      x: parseFloat(box.style.left) / 100,
      y: parseFloat(box.style.top) / 100,
      w: parseFloat(box.style.width) / 100,
      h: parseFloat(box.style.height) / 100,
    };
  });
}

function pageOverlays(panel, slots) {
  const slot = slots[panel.slot] || { x: 0, y: 0, w: 1, h: 1 };
  const style = `left:${slot.x * 100}%;top:${slot.y * 100}%;width:${slot.w * 100}%;height:${slot.h * 100}%;`;
  if (state.pageTool === "select") return elementOverlay(panel, slots);
  if (state.pageTool === "sketch") {
    return `<canvas class="sketch-layer gesture" data-pen="sketch" style="${style}"></canvas>`;
  }
  if (state.pageTool === "lasso") {
    return `<svg class="lasso-layer gesture" data-pen="lasso" viewBox="0 0 1 1" preserveAspectRatio="none" style="${style}"></svg>`;
  }
  return "";
}

function divideSvg() {
  return `
    <svg class="divide-layer gesture" viewBox="0 0 1 1" preserveAspectRatio="none">
      <line class="hit" data-pen="divide-line" x1="0" y1="0.5" x2="1" y2="0.5"></line>
      <line class="ink" x1="0" y1="0.5" x2="1" y2="0.5"></line>
      <circle data-pen="divide-a" cx="0.08" cy="0.5" r="0.028"></circle>
      <circle data-pen="divide-mid" cx="0.5" cy="0.5" r="0.034"></circle>
      <circle data-pen="divide-b" cx="0.92" cy="0.5" r="0.028"></circle>
    </svg>
  `;
}

const PAGE_TOOLS = [
  ["lasso", "Lasso"],
  ["select", "Select"],
  ["text", "Text"],
  ["sketch", "Sketch"],
  ["vfx", "Effects"],
  ["imagine", "Imagine"],
  ["lines", "Lines"],
  ["draw", "Draw"],
];

const VIBES = ["Screentone", "Speed lines", "Rain", "Night", "Dust", "Spark", "Quiet", "Harsh light"];

function toolDock(page, panel) {
  const strip = PAGE_TOOLS.map(([id, label]) => {
    const open = id === "imagine" ? !!page : !!panel;
    return `<button type="button" class="${state.pageTool === id ? "on" : ""}" data-act="pick-tool" data-tool="${id}" ${open ? "" : "disabled"}>${label}</button>`;
  }).join("");
  let props = `<p class="meta">${page ? "Select a panel." : "Add a page to start."}</p>`;
  if (page && state.pageTool === "imagine") props = imagineProps(page, panel);
  else if (panel) props = toolProps(panel);
  const moves = panel ? `
    <div class="row">
      <button type="button" class="ghost" data-act="move-panel" data-id="${esc(panel.id)}" data-dir="-1" title="Earlier panel ([)">Earlier</button>
      <button type="button" class="ghost" data-act="move-panel" data-id="${esc(panel.id)}" data-dir="1" title="Later panel (])">Later</button>
      <button type="button" class="danger quiet" data-act="delete-panel" data-id="${esc(panel.id)}" title="Delete panel (Delete)">Delete</button>
    </div>
  ` : "";
  return `<div class="tool-props" data-scroll="tools">${moves}${props}</div><div class="tool-strip">${strip}</div>`;
}

function toolProps(panel) {
  if (state.pageTool === "select") return selectProps(panel);
  if (state.pageTool === "text") return textProps(panel);
  if (state.pageTool === "sketch") return sketchProps(panel);
  if (state.pageTool === "vfx") return vibeProps(panel);
  if (state.pageTool === "lines") return linesProps(panel);
  if (state.pageTool === "draw") return drawProps(panel);
  return lassoProps(panel);
}

function refThumbs(panel, kind) {
  const refs = (panel.refs || []).filter((ref) => ref.kind === kind);
  if (!refs.length) return `<p class="meta">None yet.</p>`;
  return `<div class="ref-row">${refs.map((ref) => `
    <button type="button" class="version" data-act="delete-panel-ref" data-id="${esc(ref.id)}" title="Remove">
      <img src="${esc(ref.url)}" alt="">
    </button>
  `).join("")}</div>`;
}

function lassoProps(panel) {
  const vocab = state.vocab;
  const versions = panel.versions.map((version) => `
    <button type="button" class="version ${version.current ? "on" : ""}" data-act="use-version" data-panel="${esc(panel.id)}" data-id="${esc(version.id)}" title="${esc(version.note)}">
      <img src="${esc(version.url)}" alt="">
    </button>
  `).join("");
  return `
    <p class="rail-kicker">Panel ${panel.slot + 1}</p>
    <div class="row">
      <button type="button" class="ghost ${state.lassoMode === "select" ? "primary" : ""}" data-act="lasso-mode" data-mode="select">Select</button>
      <button type="button" class="ghost ${state.lassoMode === "circle" ? "primary" : ""}" data-act="lasso-mode" data-mode="circle">Circle</button>
    </div>
    <p class="meta">Lift the pen to redraw the marked part. With a key, that part is drawn again. Without one, it is softened.</p>
    <label class="field">Action
      <textarea data-save="panel" data-field="action" data-entity="${esc(panel.id)}">${esc(panel.action)}</textarea>
    </label>
    <label class="field">Prompt
      <textarea data-save="panel" data-field="prompt" data-entity="${esc(panel.id)}">${esc(panel.prompt || "")}</textarea>
    </label>
    <div class="section-label">Reference image</div>
    ${refThumbs(panel, "reference")}
    <button type="button" class="ghost" data-act="import-ref" data-owner="panel" data-id="${esc(panel.id)}" data-kind="reference" ${state.busy ? "disabled" : ""}>Add reference</button>
    <div class="section-label">Reference background</div>
    ${refThumbs(panel, "background")}
    <button type="button" class="ghost" data-act="import-ref" data-owner="panel" data-id="${esc(panel.id)}" data-kind="background" ${state.busy ? "disabled" : ""}>Add background</button>
    <label class="check">
      <input type="checkbox" data-act="toggle-continue" data-id="${esc(panel.id)}" ${panel.continuePrev ? "checked" : ""} ${state.busy ? "disabled" : ""}>
      Match the previous panel
    </label>
    <p id="draw-warn" class="warn"></p>
    <button type="button" class="rail-go" data-act="draw-panel" data-id="${esc(panel.id)}" ${state.busy ? "disabled" : ""}>Draw panel</button>
    <p id="draw-meta" class="meta"></p>
    <details>
      <summary>Look</summary>
      <div class="stack">
        <div class="section-label">Shot</div>
        <div class="chips">${chips("shot", panel.shot, vocab.shots, panel.id)}</div>
        <div class="section-label">Camera</div>
        <div class="chips">${chips("angle", panel.angle, vocab.angles, panel.id)}</div>
        <div class="section-label">Expression</div>
        <div class="chips">${chips("expression", panel.expression, vocab.expressions, panel.id)}</div>
        <div class="section-label">Composition</div>
        <div class="chips">${chips("composition", panel.composition, vocab.compositions, panel.id)}</div>
        <label class="field">Extra direction
          <textarea data-save="panel" data-field="extra" data-entity="${esc(panel.id)}">${esc(panel.extra)}</textarea>
        </label>
        <details id="prompt-box"><summary>Prompt</summary><pre id="prompt-text" class="prompt"></pre></details>
        <label class="field">Revise this drawing
          <textarea id="revise-note" placeholder="Push the camera lower. Keep the face and the outfit."></textarea>
        </label>
        <button type="button" class="ghost" data-act="revise-panel" data-id="${esc(panel.id)}" ${state.busy || !panel.imageUrl ? "disabled" : ""}>Revise panel</button>
        <div class="section-label">Versions</div>
        <div class="versions">${versions || `<p class="meta">No drawings yet.</p>`}</div>
      </div>
    </details>
  `;
}

function selectedLine(panel) {
  const bit = state.selectedBit;
  if (!bit) return "Select a character, a thing, or the background.";
  if (bit.kind === "character") {
    const person = state.tree.characters.find((item) => item.id === bit.id);
    return person ? `Selected: ${person.name}` : "Selected character";
  }
  if (bit.kind === "thing") return `Selected: ${bit.name}`;
  if (bit.kind === "background") {
    const place = state.tree.locations.find((item) => item.id === panel.locationId);
    return place ? `Selected: ${place.name}` : "Selected background";
  }
  return "Select a character, a thing, or the background.";
}

function selectProps(panel) {
  const elements = panel.elements || [];
  const selected = elements.find((item) => item.id === state.elementId) || null;
  const elementNote = selected
    ? `${selected.kind === "balloon" ? "Balloon" : "Thing"}: ${selected.text || "Empty"}`
    : "Drag a balloon or a thing. The corner handle resizes it.";
  const elementField = selected ? `
    <label class="field">Text
      <textarea data-save="balloon" data-field="text" data-entity="${esc(selected.id)}">${esc(selected.text || "")}</textarea>
    </label>
  ` : "";
  const cast = state.tree.characters.map((person) => {
    const on = (panel.characterIds || []).includes(person.id) ? " on" : "";
    return `<button type="button" class="chip${on}" data-act="pick-cast" data-id="${esc(panel.id)}" data-char="${esc(person.id)}">${esc(person.name)}</button>`;
  }).join("") || `<p class="meta">No cast yet.</p>`;
  const things = (panel.objects || []).map((item) => `
    <button type="button" class="chip${item.on ? " on" : ""}" data-act="pick-thing" data-id="${esc(panel.id)}" data-name="${esc(item.name)}">${esc(item.name)}</button>
  `).join("") || `<p class="meta">No things yet.</p>`;
  const places = state.tree.locations.map((place) => `
    <button type="button" class="chip${place.id === panel.locationId ? " on" : ""}" data-act="pick-place" data-id="${esc(panel.id)}" data-place="${esc(place.id)}">${esc(place.name)}</button>
  `).join("") || `<p class="meta">No places yet.</p>`;
  return `
    <p class="rail-kicker">Select</p>
    <p class="meta">${esc(elementNote)}</p>
    ${elementField}
    <button type="button" class="ghost" data-act="arrange-elements" data-id="${esc(panel.id)}" ${state.busy ? "disabled" : ""}>Arrange on the panel</button>
    <p class="meta">${esc(selectedLine(panel))}</p>
    <label class="check">
      <input type="checkbox" data-act="toggle-multi" ${state.selectMulti ? "checked" : ""}>
      Select several
    </label>
    <div class="section-label">Characters</div>
    <div class="chips">${cast}</div>
    <div class="section-label">Things</div>
    <div class="chips">${things}</div>
    <div class="section-label">Background</div>
    <div class="chips">${places}</div>
    <button type="button" class="ghost" data-act="pick-place" data-id="${esc(panel.id)}" data-place="">Clear background</button>
    <button type="button" class="rail-go" data-act="separate-panel" data-id="${esc(panel.id)}" ${state.busy ? "disabled" : ""}>Ask Grok to separate</button>
  `;
}

function textProps(panel) {
  const vocab = state.vocab;
  return `
    <p class="rail-kicker">Text</p>
    <div class="chips">
      ${[["speech", "Speech"], ["shout", "Shout"], ["thought", "Thought"], ["caption", "Caption"]].map(([id, label]) => `
        <button type="button" class="chip" data-act="balloon-preset" data-id="${esc(panel.id)}" data-preset="${id}">${label}</button>
      `).join("")}
    </div>
    <label class="field">Dialogue
      <textarea data-save="panel" data-field="dialogue" data-entity="${esc(panel.id)}">${esc(panel.dialogue)}</textarea>
    </label>
    <label class="field">Balloon
      <select data-save="panel" data-field="balloon" data-entity="${esc(panel.id)}">
        ${vocab.balloons.map((item) => `<option value="${esc(item.id)}" ${item.id === panel.balloon ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
      </select>
    </label>
    <label class="field">Balloon spot
      <select data-save="panel" data-field="balloonAnchor" data-entity="${esc(panel.id)}">
        ${vocab.anchors.map((item) => `<option value="${esc(item.id)}" ${item.id === panel.balloonAnchor ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
      </select>
    </label>
    <label class="field">Sound effect
      <input type="text" data-save="panel" data-field="sfx" data-entity="${esc(panel.id)}" value="${esc(panel.sfx)}">
    </label>
    <button type="button" class="rail-go" data-act="make-balloon" data-id="${esc(panel.id)}" ${state.busy ? "disabled" : ""}>AI text bubbles</button>
    <p class="meta">Lettering stays on the page. It is not drawn into the art.</p>
  `;
}

function drawProps(panel) {
  return `
    <p class="rail-kicker">Draw</p>
    <p class="meta">Panel ${panel.slot + 1}. Sketch on the left. The preview on the right updates as you draw.</p>
    <div class="draw-pair">
      <canvas class="draw-sketch gesture" data-pen="draw" width="320" height="480" aria-label="Sketch"></canvas>
      <canvas class="draw-preview" width="320" height="480" aria-label="Live preview"></canvas>
    </div>
    <p class="meta">With a key, the preview is a drawing. Without one, it is cleaned-up line art on this machine.</p>
  `;
}

function sketchProps(panel) {
  return `
    <p class="rail-kicker">Sketch</p>
    <p class="meta">Draw on this panel with the pen. Lift the pen and Koma refines it.</p>
    <p class="meta">Huion and Wacom pens use pressure, the same way Krita and Clip Studio do.</p>
    <label class="field">Keep this in the drawing
      <textarea data-save="panel" data-field="prompt" data-entity="${esc(panel.id)}">${esc(panel.prompt || "")}</textarea>
    </label>
    <p class="meta">Panel ${panel.slot + 1}${panel.imageUrl ? " already has a drawing." : "."}</p>
  `;
}

function linesProps(panel) {
  const strength = Number.isFinite(state.lineStrength) ? state.lineStrength : 55;
  return `
    <p class="rail-kicker">Lines</p>
    <p class="meta">Black line art on a clear ground. It stays on this machine.</p>
    <label class="field">Strength
      <input type="range" min="1" max="100" step="1" data-line-strength value="${strength}" aria-label="Line strength">
    </label>
    <p class="meta" data-line-readout>${strength}. Lower keeps the darkest strokes. Higher keeps lighter edges too.</p>
    <button type="button" class="rail-go" data-act="extract-lines" data-id="${esc(panel.id)}" ${state.busy || !panel.imageUrl ? "disabled" : ""}>Extract from this panel</button>
    <button type="button" class="ghost" data-act="extract-lines" data-id="${esc(panel.id)}" data-from="file" ${state.busy ? "disabled" : ""}>Extract from an image</button>
  `;
}

function vibeProps(panel) {
  const chipsHtml = VIBES.map((vibe) => `
    <button type="button" class="chip${panel.vibe === vibe ? " on" : ""}" data-act="set-vibe" data-id="${esc(panel.id)}" data-value="${esc(vibe)}">${esc(vibe)}</button>
  `).join("");
  return `
    <p class="rail-kicker">Effects</p>
    <p class="meta">Screentones, weather, and the atmosphere of this panel.</p>
    <div class="chips">${chipsHtml}</div>
    ${panel.vibe ? `<p class="meta">Atmosphere: ${esc(panel.vibe)}</p>` : ""}
    <p class="meta">${panel.imageUrl ? "Choosing one revises this drawing." : "The choice is saved and joins the next draw."}</p>
  `;
}

function imagineProps(page, panel) {
  const owner = panel ? "panel" : "page";
  const entity = panel || page;
  const prompt = panel ? (panel.prompt || "") : (page.prompt || "");
  const characterIds = panel ? (panel.characterIds || []) : (page.characterIds || []);
  const locationId = panel ? (panel.locationId || "") : (page.locationId || "");
  const people = (state.tree && state.tree.characters) || [];
  const places = (state.tree && state.tree.locations) || [];
  const cast = people.map((person) => {
    const on = characterIds.includes(person.id) ? " on" : "";
    return `<button type="button" class="chip${on}" data-act="imagine-cast" data-char="${esc(person.id)}">${esc(person.name)}</button>`;
  }).join("");
  const backgrounds = places.map((place) => {
    const on = place.id === locationId ? " on" : "";
    return `<button type="button" class="chip${on}" data-act="imagine-place" data-place="${esc(place.id)}">${esc(place.name)}</button>`;
  }).join("");
  const scope = panel ? `Panel ${panel.slot + 1}` : `Page ${page.idx + 1}`;
  return `
    <p class="rail-kicker">Imagine</p>
    <p class="meta">${esc(scope)}</p>
    <label class="field">${panel ? "Prompt for this panel" : "Prompt for this page"}
      <textarea data-save="${owner}" data-field="prompt" data-entity="${esc(entity.id)}">${esc(prompt)}</textarea>
    </label>
    <div class="section-label">Reference image</div>
    ${refThumbs(entity, "reference")}
    <button type="button" class="ghost" data-act="import-ref" data-owner="${owner}" data-id="${esc(entity.id)}" data-kind="reference" ${state.busy ? "disabled" : ""}>Add reference</button>
    <div class="section-label">Reference character</div>
    <div class="chips">${cast || `<p class="meta">Add people in Characters.</p>`}</div>
    <p class="meta">Their portrait goes with the drawing.</p>
    <div class="section-label">Reference background</div>
    <div class="chips">${backgrounds || `<p class="meta">No places yet.</p>`}</div>
    ${refThumbs(entity, "background")}
    <button type="button" class="ghost" data-act="import-ref" data-owner="${owner}" data-id="${esc(entity.id)}" data-kind="background" ${state.busy ? "disabled" : ""}>Add background</button>
    ${panel ? `<p class="meta">Click this panel again for the page prompt.</p>` : ""}
    <button type="button" class="rail-go" data-act="imagine-draw" ${state.busy ? "disabled" : ""}>${panel ? "Imagine this panel" : "Imagine this page"}</button>
  `;
}


function renderRead() {
  const list = chapters();
  if (!list.length) {
    return desk(
      `<p class="rail-kicker">Read</p><p class="meta">Add a chapter, then read it here.</p>`,
      `<div class="stage"><div class="empty">Add a chapter, then the finished pages show up here. Arrow keys turn the page once there is one to turn.</div></div>`,
    );
  }
  const selected = list.find((chapter) => chapter.id === state.readChapterId) || list[0];
  state.readChapterId = selected.id;
  const pages = selected.pages || [];
  const maxStart = pages.length <= 1 ? 0 : (pages.length % 2 === 0 ? pages.length - 2 : pages.length - 1);
  if (state.spreadAt < 0 || state.spreadAt > maxStart) state.spreadAt = 0;
  if (state.spreadAt % 2) state.spreadAt -= 1;
  const options = list.map((chapter) => `
    <option value="${esc(chapter.id)}" ${chapter.id === selected.id ? "selected" : ""}>${esc(chapter.title)}</option>
  `).join("");
  const empty = pages.reduce(
    (sum, page) => sum + page.panels.filter((panel) => !panel.imageUrl).length,
    0,
  );
  const first = pages.length ? state.spreadAt + 1 : 0;
  const last = Math.min(pages.length, state.spreadAt + 2);
  const range = !pages.length ? "No pages yet" : (first === last ? `Page ${first}` : `Pages ${first}–${last}`);
  const reading = state.tree.project.reading === "rtl"
    ? "Right to left. The earlier page sits on the right."
    : "Left to right.";
  const rail = `
    <p class="rail-kicker">Read</p>
    <label class="field">Chapter
      <select data-act="pick-chapter">${options}</select>
    </label>
    <p class="meta">${esc(range)}</p>
    <p class="meta">${esc(reading)} Arrow keys turn the page.</p>
    <p class="meta">${empty ? `${empty} panels still empty` : "Every panel has a drawing"}</p>
    <div class="row">
      <button type="button" class="ghost" data-act="spread-step" data-dir="-1" ${state.spreadAt <= 0 ? "disabled" : ""}>Earlier</button>
      <button type="button" class="ghost" data-act="spread-step" data-dir="1" ${state.spreadAt >= maxStart ? "disabled" : ""}>Later</button>
    </div>
    <button type="button" class="ghost" data-act="draw-empty" data-scope="chapter" data-id="${esc(selected.id)}" ${state.busy ? "disabled" : ""}>Draw empty panels</button>
    <button type="button" class="ghost" data-act="reload-read" ${state.busy ? "disabled" : ""}>Refresh</button>
    <button type="button" class="ghost" data-act="export-chapter" data-id="${esc(selected.id)}" data-format="pdf">Chapter PDF</button>
    <button type="button" class="ghost" data-act="export-chapter" data-id="${esc(selected.id)}" data-format="cbz">Chapter CBZ</button>
  `;
  const stage = pages.length
    ? `<div class="stage"><div class="spread">${spreadLeaves(pages, state.spreadAt, state.tree.project.reading).map((page) => readLeaf(page)).join("")}</div></div>`
    : `<div class="stage"><div class="empty">This chapter has no pages yet.</div></div>`;
  return desk(rail, stage);
}

function spreadLeaves(pages, start, reading) {
  const earlier = pages[start] || null;
  const later = pages[start + 1] || null;
  if (reading === "rtl") return [later, earlier];
  return [earlier, later];
}

function readLeaf(page) {
  if (!page) return `<div class="leaf blank" aria-hidden="true"></div>`;
  const sheet = (state.sheets || []).find((item) => item.pageId === page.id);
  const ratio = pageRatio(state.tree.project);
  const label = `Page ${page.idx + 1}`;
  if (!sheet) {
    return `<button type="button" class="leaf" style="${ratio}" data-act="open-page" data-id="${esc(page.id)}"><span>${label}</span></button>`;
  }
  return `<button type="button" class="leaf" style="${ratio}" data-act="open-page" data-id="${esc(page.id)}">
    <img src="${esc(sheet.url)}?t=${state.sheetStamp}" alt="${esc(label)}">
  </button>`;
}

async function loadSheets() {
  const list = chapters();
  const chapter = list.find((item) => item.id === state.readChapterId) || list[0];
  if (!chapter) {
    state.sheets = [];
    render();
    return;
  }
  state.readChapterId = chapter.id;
  if (!chapter.pages.length) {
    state.sheets = [];
    render();
    toast("This chapter has no pages yet.");
    return;
  }
  await run("Assembling the chapter…", async () => {
    const result = await rpc("chapter.sheets", { chapterId: chapter.id });
    state.sheets = result.sheets;
    state.sheetStamp = Date.now();
  });
}

function chips(field, current, options, panelId) {
  return options.map((option) => `
    <button type="button" class="chip ${option === current ? "on" : ""}" data-act="set-panel" data-id="${esc(panelId)}" data-field="${esc(field)}" data-value="${esc(option)}">${esc(option)}</button>
  `).join("");
}

async function refreshPreview() {
  const token = ++previewToken;
  const id = state.panelId;
  if (!id) return;
  try {
    const preview = await rpc("panel.preview", { id });
    if (token !== previewToken || state.panelId !== id) return;
    const meta = document.getElementById("draw-meta");
    const pre = document.getElementById("prompt-text");
    const warn = document.getElementById("draw-warn");
    if (meta) meta.textContent = `${preview.aspect} · ${preview.refCount ? preview.refCount + " refs" : "no refs"}`;
    if (pre) pre.textContent = preview.prompt;
    if (warn) warn.textContent = (preview.warnings || []).join(" ");
  } catch (_error) {
    /* The panel can disappear between render and preview. */
  }
}

async function saveField(kind, id, field, value) {
  if (kind === "local") {
    state[field] = value;
    return;
  }
  if (kind === "balloon") {
    const panel = currentPanel();
    if (!panel) return;
    const elements = (panel.elements || []).map((item) => ({ ...item }));
    const found = elements.find((item) => item.id === id);
    if (!found) return;
    found.text = value;
    const fields = { id: panel.id, elements };
    if (found.kind === "balloon") {
      const first = elements.find((item) => item.kind === "balloon");
      if (first && first.id === found.id) {
        fields.dialogue = value;
        fields.balloon = found.balloon || "round";
      }
    }
    setStatus("Saving…");
    try {
      absorb(await rpc("panel.update", fields));
    } catch (error) {
      setStatus("");
      throw error;
    }
    flashStatus("Saved");
    return;
  }
  let payloadValue = value;
  if (field === "beats") payloadValue = value.split("\n").map((line) => line.trim()).filter(Boolean);
  if (field === "locationId") payloadValue = value || null;
  const method = {
    project: "project.update",
    chapter: "chapter.update",
    page: "page.update",
    panel: "panel.update",
    character: "character.update",
    location: "location.update",
    illust: "illust.update",
    settings: "settings.save",
    volume: "volume.update",
    note: "note.update",
    bot: "bot.update",
    group: "group.update",
  }[kind];
  const params = kind === "settings" ? { [field]: payloadValue } : { id, [field]: payloadValue };
  setStatus("Saving…");
  try {
    absorb(await rpc(method, params));
  } catch (error) {
    setStatus("");
    throw error;
  }
  if (kind === "project" && field === "title") syncWindowTitle();
  flashStatus("Saved");
}

function flashStatus(text) {
  setStatus(text);
  clearTimeout(flashStatus._t);
  flashStatus._t = setTimeout(() => {
    if (state.status === text) setStatus("");
  }, 1400);
}

function paintMeshChrome() {
  const canvas = document.querySelector("canvas[data-mesh-id]");
  const mark = (act, on) => {
    const button = document.querySelector(`[data-act="${act}"]`);
    if (button) button.classList.toggle("on", Boolean(on));
  };
  mark("mesh-auto", meshCamera.auto);
  mark("mesh-wire", meshCamera.wire);
  mark("mesh-light", meshCamera.light);
  mark("mesh-grid", meshCamera.grid);
  mark("mesh-outline", meshCamera.outline);
  document.querySelectorAll("[data-act='mesh-angle']").forEach((button) => {
    button.classList.toggle("on", button.dataset.value === meshCamera.angle);
  });
  const ground = document.querySelector("[data-act='mesh-ground']");
  if (ground) {
    ground.classList.toggle("on", meshCamera.backdrop === "light");
    ground.textContent = meshCamera.backdrop === "light" ? "Dark ground" : "Light ground";
  }
  if (canvas && window.redraw) redraw(canvas);
}

function turntableFrames(canvas) {
  const count = 24;
  const yaw = meshCamera.yaw;
  const auto = meshCamera.auto;
  const playing = meshCamera.playing;
  meshCamera.auto = false;
  meshCamera.playing = false;
  const shots = [];
  try {
    for (let step = 0; step < count; step += 1) {
      meshCamera.yaw = yaw + (Math.PI * 2 * step) / count;
      redraw(canvas);
      shots.push(turntablePng(canvas, 480));
    }
  } finally {
    meshCamera.yaw = yaw;
    meshCamera.auto = auto;
    meshCamera.playing = playing;
    redraw(canvas);
  }
  if (shots.length !== count || shots.some((shot) => !shot || shot.length < 64)) {
    throw new Error("Could not read the view.");
  }
  return shots;
}

function turntablePng(canvas, limit) {
  const scale = Math.min(1, limit / Math.max(canvas.width, canvas.height, 1));
  const width = Math.max(2, Math.round(canvas.width * scale));
  const height = Math.max(2, Math.round(canvas.height * scale));
  if (scale === 1) return canvas.toDataURL("image/png");
  const copy = document.createElement("canvas");
  copy.width = width;
  copy.height = height;
  const ctx = copy.getContext("2d");
  if (!ctx) throw new Error("Could not read the view.");
  ctx.drawImage(canvas, 0, 0, width, height);
  return copy.toDataURL("image/png");
}

const actions = {
  theme() {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("koma-theme", next); } catch (_error) { /* The choice still applies for this visit. */ }
    render();
  },
  "go-home"() {
    state.menuOpen = false;
    state.view = "home";
    state.printFor = null;
    render();
    rpc("canvas.gallery", {}).then((result) => {
      absorb(result);
      if (state.view === "home") render();
    }).catch((error) => toast(error.message));
  },
  "open-manga"() {
    const board = currentBoard();
    openBook(board.projectId ? board : null);
  },
  "new-manga"() {
    openBook(null);
  },
  "set-kind"(button) {
    const kind = button.dataset.value === "western" ? "western" : "manga";
    state.setupKind = kind;
    if (state.setupPreset !== "custom" && state.formats) {
      const presets = state.formats.presets.filter((item) => item.kind === kind);
      if (!presets.some((item) => item.id === state.setupPreset)) {
        state.setupPreset = state.formats.defaults[kind].preset;
      }
    }
    render();
  },
  "set-preset"(button) {
    const next = button.dataset.value;
    if (next === "custom" && state.setupPreset !== "custom") {
      const choice = setupChoice();
      if (choice.wMm > 0 && choice.hMm > 0 && !state.setupCustomW) {
        const unit = state.setupUnit === "in" ? "in" : "mm";
        const factor = unit === "in" ? 1 / 25.4 : 1;
        state.setupCustomW = trimNum(choice.wMm * factor);
        state.setupCustomH = trimNum(choice.hMm * factor);
      }
    }
    state.setupPreset = next;
    render();
  },
  "set-quality"(button) {
    const next = button.dataset.value;
    if (next === "custom" && state.formats) {
      const current = state.formats.qualities.find((item) => item.id === state.setupQuality);
      if (current && current.dpi) state.setupDpi = String(current.dpi);
    }
    state.setupQuality = next;
    render();
  },
  "open-book"() {
    const titleEl = document.getElementById("book-title");
    const widthEl = document.getElementById("book-w");
    const heightEl = document.getElementById("book-h");
    const dpiEl = document.getElementById("book-dpi");
    if (titleEl) state.setupTitle = titleEl.value;
    if (widthEl) state.setupCustomW = widthEl.value;
    if (heightEl) state.setupCustomH = heightEl.value;
    if (dpiEl) state.setupDpi = dpiEl.value;
    const title = state.setupTitle.trim();
    if (!title) {
      toast("Name the manga.");
      return;
    }
    const choice = setupChoice();
    if (choice.problem) {
      toast(choice.problem);
      return;
    }
    const payload = {
      create: !!state.setupCreate,
      projectId: state.setupProjectId || "",
      title,
      kind: state.setupKind,
      preset: state.setupPreset,
      quality: state.setupQuality,
      width: state.setupCustomW,
      height: state.setupCustomH,
      unit: state.setupUnit,
      dpi: state.setupQuality === "custom" ? state.setupDpi : choice.dpi,
    };
    return run("Opening the page…", async () => {
      const result = await rpc("canvas.prepare", payload);
      absorb(result);
      state.view = "pages";
      state.mangaId = result.tree.project.id;
      state.pageId = null;
      state.panelId = null;
      state.printFor = null;
      state.menuOpen = false;
      state.spreadAt = 0;
    });
  },
  "toggle-menu"() {
    state.menuOpen = !state.menuOpen;
    render();
  },
  "menu-manga"() {
    state.menuOpen = false;
    if (!state.tree) {
      state.view = "home";
      render();
      return;
    }
    state.view = "pages";
    state.printFor = null;
    render();
  },
  "menu-illust"() {
    state.menuOpen = false;
    const latest = (state.illustrations || [])[0];
    return run("Opening the canvas…", async () => {
      const result = await rpc("canvas.open_illust", latest ? { id: latest.id } : {});
      absorb(result);
      state.view = "illust";
      state.illustId = result.illustId;
    });
  },
  "menu-bot"() {
    state.menuOpen = false;
    state.botOpen = true;
    const groups = state.tree && state.tree.botGroups ? state.tree.botGroups : [];
    if (!groups.some((item) => item.id === state.groupId)) state.groupId = groups[0] ? groups[0].id : null;
    render();
    if (state.groupId) loadBotHistory();
  },
  "menu-settings"() {
    state.menuOpen = false;
    state.printFor = null;
    state.view = "settings";
    render();
  },
  "menu-about"() {
    state.menuOpen = false;
    state.printFor = null;
    state.view = "about";
    render();
  },
  "menu-help"() {
    state.menuOpen = false;
    state.helpOpen = true;
    render();
  },
  "toggle-help"() {
    state.helpOpen = !state.helpOpen;
    state.menuOpen = false;
    render();
  },
  "close-help"() {
    state.helpOpen = false;
    render();
  },
  "help-card"() {},
  "open-x"() {
    return rpc("shell.open_url", { url: "https://x.com/the_lord_liam" });
  },
  "spread-step"(button) {
    const chapter = chapters().find((item) => item.id === state.readChapterId) || chapters()[0];
    const count = chapter ? chapter.pages.length : 0;
    const maxStart = count <= 1 ? 0 : (count % 2 === 0 ? count - 2 : count - 1);
    const dir = Number(button.dataset.dir) || 1;
    state.spreadAt = Math.max(0, Math.min(maxStart, state.spreadAt + dir * 2));
    render();
  },
  "open-illust"() {
    const latest = (state.illustrations || [])[0];
    return run("Opening the canvas…", async () => {
      const result = await rpc("canvas.open_illust", latest ? { id: latest.id } : {});
      absorb(result);
      state.view = "illust";
      state.illustId = result.illustId;
    });
  },
  "new-illust"() {
    return run("Opening a canvas…", async () => {
      const result = await rpc("canvas.open_illust", { create: true });
      absorb(result);
      state.view = "illust";
      state.illustId = result.illustId;
    });
  },
  "select-illust"(button) {
    state.illustId = button.dataset.id;
    state.view = "illust";
    render();
  },
  "set-illust"(button) {
    const prompt = document.getElementById("illust-prompt");
    const style = document.getElementById("illust-style");
    const payload = {
      id: state.illustId,
      aspect: button.dataset.value,
      prompt: prompt ? prompt.value : "",
      artStyle: style ? style.value : "",
    };
    return run("Saving…", async () => {
      absorb(await rpc("illust.update", payload));
    });
  },
  "draw-illust"() {
    const prompt = document.getElementById("illust-prompt");
    const style = document.getElementById("illust-style");
    const payload = {
      id: state.illustId,
      prompt: prompt ? prompt.value : "",
      artStyle: style ? style.value : "",
    };
    return run("Drawing the illustration…", async () => {
      absorb(await rpc("illust.draw", payload));
    });
  },
  "revise-illust"() {
    const note = document.getElementById("illust-note");
    const instruction = note ? note.value.trim() : "";
    if (!instruction) {
      toast("Say what to change.");
      return;
    }
    return run("Revising the illustration…", async () => {
      absorb(await rpc("illust.revise", { id: state.illustId, instruction }));
    });
  },
  "use-illust-version"(button) {
    return run("Restoring…", async () => {
      absorb(await rpc("illust.use_version", { id: state.illustId, versionId: button.dataset.id }));
    });
  },
  "delete-manga"(button) {
    const id = (button && button.dataset.id) || (state.tree && state.tree.project && state.tree.project.id);
    if (!id || !confirm("Delete this manga and its pages?")) return;
    return run("Deleting…", async () => {
      absorb(await rpc("project.delete", { id }));
      if (state.tree && state.tree.project && state.tree.project.id === id) {
        state.tree = null;
        state.pageId = null;
        state.panelId = null;
      }
      if (state.mangaId === id) state.mangaId = null;
      state.view = "home";
      state.menuOpen = false;
    });
  },
  "delete-illust"(button) {
    if (!confirm("Delete this illustration?")) return;
    return run("Deleting…", async () => {
      absorb(await rpc("illust.delete", { id: button.dataset.id }));
      state.illustId = null;
      state.view = state.tree ? "pages" : "home";
    });
  },
  "export-illust"(button) {
    return run("Exporting…", async () => {
      const result = await rpc("illust.export", { id: button.dataset.id });
      toast("Saved " + result.path, "ok");
      state.lastExport = result;
    });
  },
  nav(button) {
    state.menuOpen = false;
    state.view = button.dataset.view;
    if (state.view === "story" && volumes().some((item) => item.id === state.navVolumeId)) {
      state.storyVolumeId = state.navVolumeId;
    }
    if (state.view !== "pages") state.printFor = null;
    render();
    if (state.view === "read") loadSheets();
  },
  async "create-project"() {
    const title = document.getElementById("new-title").value;
    const premise = document.getElementById("new-premise").value;
    await run("Creating the project…", async () => {
      absorb(await rpc("project.create", { title, premise }));
      state.view = "story";
      state.pageId = null;
      state.panelId = null;
    });
  },
  "open-project"(button) {
    return run("Opening…", async () => {
      absorb(await rpc("project.open", { id: button.dataset.id }));
      state.view = "story";
      state.pageId = null;
      state.panelId = null;
      state.charId = null;
      state.locId = null;
      state.sheets = null;
      state.printFor = null;
      state.readChapterId = null;
    });
  },
  "save-key"() {
    const apiKey = document.getElementById("api-key").value.trim();
    if (!apiKey) {
      toast("Paste a key first.");
      return;
    }
    return run("Saving the key…", async () => {
      absorb(await rpc("settings.save", { apiKey }));
      toast("Key saved on this machine.", "ok");
    });
  },
  "clear-key"() {
    return run("Removing the key…", async () => {
      absorb(await rpc("settings.save", { clearKey: true }));
    });
  },
  "save-meshy"() {
    const meshyKey = document.getElementById("meshy-key").value.trim();
    if (!meshyKey) {
      toast("Paste a Meshy key first.");
      return;
    }
    return run("Saving the Meshy key…", async () => {
      absorb(await rpc("settings.save", { meshyKey }));
      toast("Meshy key saved on this machine.", "ok");
    });
  },
  "clear-meshy"() {
    return run("Removing the Meshy key…", async () => {
      absorb(await rpc("settings.save", { clearMeshyKey: true }));
    });
  },
  "save-tripo"() {
    const tripoKey = document.getElementById("tripo-key").value.trim();
    if (!tripoKey) {
      toast("Paste a Tripo key first.");
      return;
    }
    return run("Saving the Tripo key…", async () => {
      absorb(await rpc("settings.save", { tripoKey }));
      toast("Tripo key saved on this machine.", "ok");
    });
  },
  "clear-tripo"() {
    return run("Removing the Tripo key…", async () => {
      absorb(await rpc("settings.save", { clearTripoKey: true }));
    });
  },
  plan() {
    const project = state.tree.project;
    return run("Planning the story…", async () => {
      absorb(await rpc("story.send", {
        projectId: project.id,
        mode: "plan",
        chapters: Number(state.planChapters),
        pagesPer: Number(state.planPages),
      }));
    });
  },
  "send-note"() {
    const message = document.getElementById("note").value.trim();
    if (!message) return;
    return run("Thinking…", async () => {
      absorb(await rpc("story.send", { projectId: state.tree.project.id, mode: "note", message }));
    });
  },
  "apply-plan"(button) {
    const mode = button.dataset.mode || "replace";
    if (mode === "replace" && chapters().length && !confirm("Replace the current chapters with this plan?")) return;
    return run("Applying the plan…", async () => {
      absorb(await rpc("story.apply", { projectId: state.tree.project.id, mode }));
      state.view = "pages";
    });
  },
  "discard-plan"() {
    return run("Discarding…", async () => {
      absorb(await rpc("story.discard", { projectId: state.tree.project.id }));
    });
  },
  "add-chapter"(button) {
    const volumeId = (button && button.dataset.id) || (state.view === "pages" ? state.navVolumeId : state.storyVolumeId);
    return run("Adding a chapter…", async () => {
      absorb(await rpc("chapter.create", { projectId: state.tree.project.id, volumeId: volumeId || "" }));
      state.storyFocus = "chapter";
      const kids = chapters().filter((item) => !volumeId || item.volumeId === volumeId);
      const created = kids[kids.length - 1];
      if (created) {
        state.storyFocusId = created.id;
        state.navChapterId = created.id;
        state.navVolumeId = created.volumeId || volumeId || state.navVolumeId;
      }
      if (button && button.dataset.seed === "1" && created && !(created.pages || []).length) {
        absorb(await rpc("page.create", { chapterId: created.id }));
        const chapter = chapters().find((item) => item.id === created.id);
        const page = chapter && chapter.pages[chapter.pages.length - 1];
        if (page) {
          state.pageId = page.id;
          state.panelId = null;
        }
      }
    });
  },
  "delete-chapter"(button) {
    if (!confirm("Delete this chapter and its pages?")) return;
    return run("Deleting…", async () => {
      absorb(await rpc("chapter.delete", { id: button.dataset.id }));
    });
  },
  "duplicate-chapter"(button) {
    return run("Copying the chapter…", async () => {
      const result = await rpc("chapter.duplicate", { id: button.dataset.id });
      absorb(result);
      if (result.chapterId) {
        state.storyFocus = "chapter";
        state.storyFocusId = result.chapterId;
        state.navChapterId = result.chapterId;
        const created = chapters().find((item) => item.id === result.chapterId);
        if (created) state.storyVolumeId = created.volumeId || state.storyVolumeId;
      }
      toast("Chapter copied, with its pages.", "ok");
    });
  },
  "search-library"() {
    const field = document.getElementById("library-search");
    const query = field ? field.value.trim() : (state.searchQuery || "");
    state.searchQuery = query;
    if (!query) {
      state.searchHits = null;
      render();
      return;
    }
    return run("Searching…", async () => {
      const result = await rpc("library.search", { query });
      state.searchHits = result.hits || [];
      if (!state.searchHits.length) toast("Nothing matched that search.");
    });
  },
  "open-hit"(button) {
    const projectId = button.dataset.project;
    const pageId = button.dataset.page || "";
    const panelId = button.dataset.panel || "";
    const chapterId = button.dataset.chapter || "";
    const noteId = button.dataset.note || "";
    const characterId = button.dataset.character || "";
    const moodId = button.dataset.mood || "";
    return run("Opening…", async () => {
      absorb(await rpc("project.open", { id: projectId }));
      state.mangaId = projectId;
      state.searchHits = null;
      if (pageId) {
        state.view = "pages";
        state.pageId = pageId;
        state.panelId = panelId || null;
        const row = allPages().find((item) => item.page.id === pageId);
        if (row) {
          state.navChapterId = row.chapter.id;
          state.navVolumeId = row.chapter.volumeId || null;
        }
      } else if (characterId) {
        state.view = "cast";
        state.charId = characterId;
        state.refId = null;
      } else if (noteId) {
        state.view = "story";
        state.pageId = null;
        state.panelId = null;
        state.storyFocus = "notes";
        state.storyFocusId = noteId;
        state.storyNoteId = noteId;
      } else if (moodId) {
        state.view = "story";
        state.pageId = null;
        state.panelId = null;
        state.storyFocus = "mood";
      } else {
        state.view = "story";
        state.pageId = null;
        state.panelId = null;
        if (chapterId) {
          state.storyFocus = "chapter";
          state.storyFocusId = chapterId;
          state.navChapterId = chapterId;
          const chapter = chapters().find((item) => item.id === chapterId);
          if (chapter) state.storyVolumeId = chapter.volumeId || null;
        } else {
          state.storyFocus = "title";
          state.storyFocusId = "";
        }
      }
    });
  },
  "move-chapter"(button) {
    return run("Moving…", async () => {
      absorb(await rpc("chapter.move", { id: button.dataset.id, direction: Number(button.dataset.dir) }));
    });
  },
  "add-page"(button) {
    const chapterId = button.dataset.id;
    const stay = state.view === "story";
    return run("Adding a page…", async () => {
      absorb(await rpc("page.create", { chapterId }));
      const chapter = chapters().find((item) => item.id === chapterId);
      const page = chapter && chapter.pages[chapter.pages.length - 1];
      if (chapter && page) {
        state.navChapterId = chapter.id;
        state.navVolumeId = chapter.volumeId || state.navVolumeId;
        state.storyVolumeId = state.navVolumeId;
        state.pageId = page.id;
        state.panelId = null;
        if (stay) {
          state.storyFocus = "page";
          state.storyFocusId = page.id;
        } else {
          state.view = "pages";
        }
      } else if (!stay) {
        state.view = "pages";
      }
    });
  },
  "open-page"(button) {
    state.pageId = button.dataset.id;
    state.panelId = null;
    state.view = "pages";
    render();
  },
  "select-page"(button) {
    state.pageId = button.dataset.id;
    state.panelId = null;
    const found = allPages().find((item) => item.page.id === state.pageId);
    if (found) {
      state.navChapterId = found.chapter.id;
      state.navVolumeId = found.chapter.volumeId || state.navVolumeId;
    }
    render();
  },
  "undo-page"() {
    const row = currentPage();
    if (!row) return;
    return run("Undoing…", async () => {
      absorb(await rpc("page.undo", { pageId: row.page.id }));
      return true;
    }).then((result) => {
      if (result) flashStatus("Undone");
    });
  },
  "redo-page"() {
    const row = currentPage();
    if (!row) return;
    return run("Redoing…", async () => {
      absorb(await rpc("page.redo", { pageId: row.page.id }));
      return true;
    }).then((result) => {
      if (result) flashStatus("Redone");
    });
  },
  "balloon-preset"(button) {
    const presets = {
      speech: { balloon: "round", balloonAnchor: "tr" },
      shout: { balloon: "shout", balloonAnchor: "tr" },
      thought: { balloon: "thought", balloonAnchor: "tl" },
      caption: { balloon: "caption", balloonAnchor: "tl" },
    };
    const preset = presets[button.dataset.preset];
    if (!preset) return;
    return run("Saving the balloon…", async () => {
      absorb(await rpc("panel.update", { id: button.dataset.id, ...preset }));
    });
  },
  "delete-panel"(button) {
    const id = button.dataset.id || state.panelId;
    if (!id) return;
    const row = currentPage();
    const panel = row && row.page.panels.find((item) => item.id === id);
    if (!panel) return;
    if (row.page.panels.length < 2) {
      toast("A page needs at least one panel.");
      return;
    }
    const note = panelHasWork(panel) ? "Delete this panel and its art or lettering?" : "Delete this panel?";
    if (!confirm(note)) return;
    return run("Deleting the panel…", async () => {
      absorb(await rpc("panel.delete", { id }));
      if (state.panelId === id) state.panelId = null;
    });
  },
  "move-panel"(button) {
    const id = button.dataset.id || state.panelId;
    if (!id) return;
    return run("Moving the panel…", async () => {
      absorb(await rpc("panel.move", { id, direction: Number(button.dataset.dir) }));
    });
  },
  "select-panel"(button) {
    if (state.pageTool === "imagine" && state.panelId === button.dataset.id) {
      state.panelId = null;
      render();
      return;
    }
    const changed = state.panelId !== button.dataset.id;
    state.panelId = button.dataset.id;
    if (changed) state.elementId = null;
    if (changed && state.pageTool !== "imagine" && state.pageTool !== "lines" && state.pageTool !== "draw" && state.pageTool !== "select") {
      state.pageTool = "lasso";
      state.lassoPath = [];
      state.selectedBit = null;
    }
    if (changed) {
      state.drawSketch = "";
      state.drawPreview = "";
    }
    render();
  },
  "delete-page"(button) {
    if (!confirm("Delete this page?")) return;
    return run("Deleting…", async () => {
      absorb(await rpc("page.delete", { id: button.dataset.id }));
      state.pageId = null;
    });
  },
  "move-page"(button) {
    return run("Moving…", async () => {
      absorb(await rpc("page.move", { id: button.dataset.id, direction: Number(button.dataset.dir) }));
    });
  },
  "duplicate-page"(button) {
    return run("Copying the page…", async () => {
      const result = await rpc("page.duplicate", { pageId: button.dataset.id });
      absorb(result);
      state.pageId = result.pageId;
      state.panelId = null;
      state.printFor = null;
      toast("Page copied. Its drawings are separate from the original.", "ok");
    });
  },
  "toggle-continue"(input) {
    const on = input.checked;
    return run("Saving…", async () => {
      absorb(await rpc("panel.update", { id: input.dataset.id, continuePrev: on }));
    });
  },
  async "set-panel"(button) {
    absorb(await rpc("panel.update", {
      id: button.dataset.id,
      [button.dataset.field]: button.dataset.value,
    }));
    render();
  },
  async "toggle-character"(button) {
    const panel = currentPanel();
    if (!panel) return;
    const ids = panel.characterIds.slice();
    const index = ids.indexOf(button.dataset.char);
    if (index >= 0) ids.splice(index, 1);
    else ids.push(button.dataset.char);
    absorb(await rpc("panel.update", { id: button.dataset.id, characterIds: ids }));
    render();
  },
  "draw-panel"(button) {
    const id = button.dataset.id;
    const promptField = document.querySelector(`[data-save="panel"][data-field="prompt"][data-entity="${id}"]`);
    const actionField = document.querySelector(`[data-save="panel"][data-field="action"][data-entity="${id}"]`);
    let prompt = promptField ? promptField.value : "";
    if (state.lassoPath && state.lassoPath.length > 1) {
      prompt = `Focus on the circled region of this panel. ${prompt}`.trim();
    }
    const fields = { id };
    if (promptField) fields.prompt = prompt;
    if (actionField) fields.action = actionField.value;
    return run("Drawing the panel…", async () => {
      absorb(await rpc("panel.update", fields));
      absorb(await rpc("panel.draw", { id }));
      state.lassoPath = [];
    });
  },
  "revise-panel"(button) {
    const instruction = document.getElementById("revise-note").value.trim();
    if (!instruction) {
      toast("Say what to change.");
      return;
    }
    return run("Revising the panel…", async () => {
      absorb(await rpc("panel.revise", { id: button.dataset.id, instruction }));
    });
  },
  "use-version"(button) {
    return run("Restoring that version…", async () => {
      absorb(await rpc("panel.use_version", { panelId: button.dataset.panel, versionId: button.dataset.id }));
    });
  },
  "add-character"() {
    return run("Adding…", async () => {
      absorb(await rpc("character.create", { projectId: state.tree.project.id }));
      const people = state.tree.characters;
      state.charId = people.length ? people[people.length - 1].id : null;
    });
  },
  "select-character"(button) {
    state.charId = button.dataset.id;
    state.refId = null;
    render();
  },
  "delete-character"(button) {
    if (!confirm("Delete this character?")) return;
    return run("Deleting…", async () => {
      absorb(await rpc("character.delete", { id: button.dataset.id }));
      state.charId = null;
    });
  },
  "mesh-source"(button) {
    state.meshSource = button.dataset.value === "refs" ? "refs" : "prompt";
    render();
  },
  "import-mesh"(button) {
    return (async () => {
      const picked = await rpc("shell.pick_mesh", {});
      if (!picked || !picked.path) return;
      await run("Importing the model…", async () => {
        absorb(await rpc("character.mesh_import", { id: button.dataset.id, path: picked.path }));
        const person = (state.tree.characters || []).find((item) => item.id === button.dataset.id);
        if (person && person.meshes && person.meshes[0]) state.meshId = person.meshes[0].id;
      });
    })();
  },
  "mesh-reset"() {
    if (window.resetMeshCamera) resetMeshCamera();
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && window.redraw) redraw(canvas);
  },
  "mesh-auto"() {
    meshCamera.auto = !meshCamera.auto;
    paintMeshChrome();
  },
  "mesh-wire"() {
    meshCamera.wire = !meshCamera.wire;
    paintMeshChrome();
  },
  "mesh-light"() {
    meshCamera.light = !meshCamera.light;
    paintMeshChrome();
  },
  "mesh-grid"() {
    meshCamera.grid = !meshCamera.grid;
    paintMeshChrome();
  },
  "mesh-outline"() {
    meshCamera.outline = !meshCamera.outline;
    paintMeshChrome();
  },
  "mesh-ground"() {
    meshCamera.backdrop = meshCamera.backdrop === "light" ? "dark" : "light";
    paintMeshChrome();
  },
  "mesh-shape"(button) {
    meshCamera.shape = button.dataset.value || "";
    const canvas = document.querySelector("canvas[data-mesh-id]");
    document.querySelectorAll("[data-act='mesh-shape']").forEach((chip) => {
      chip.classList.toggle("on", (chip.dataset.value || "") === (meshCamera.shape || ""));
    });
    if (canvas && canvas._geom && canvas._geom.rig && typeof applyClipFrame === "function" && typeof redraw === "function") {
      applyClipFrame(canvas._geom);
      redraw(canvas);
      return;
    }
    render();
  },
  "mesh-pose"() {
    meshCamera.poseMode = !meshCamera.poseMode;
    render();
  },
  "mesh-bones"() {
    meshCamera.showBones = meshCamera.showBones === false;
    render();
  },
  "mesh-bone"(button) {
    meshCamera.selected = button.dataset.bone || "";
    meshCamera.poseMode = true;
    render();
  },
  "mesh-mirror"() {
    meshCamera.playing = false;
    meshCamera.clip = -1;
    meshCamera.time = 0;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && canvas._geom) canvas._geom._animLocals = null;
    if (window.applyMirrorPose) applyMirrorPose();
    render();
  },
  "mesh-preset"(button) {
    meshCamera.playing = false;
    meshCamera.clip = -1;
    meshCamera.time = 0;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && canvas._geom) canvas._geom._animLocals = null;
    if (window.applyPosePreset) applyPosePreset(button.dataset.value || "bind");
    render();
  },
  "mesh-clip"(button) {
    const index = Number(button.dataset.value);
    meshCamera.clip = Number.isFinite(index) ? index : -1;
    meshCamera.time = 0;
    meshCamera.playing = false;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && canvas._geom && window.applyClipFrame) {
      applyClipFrame(canvas._geom);
      redraw(canvas);
    }
    render();
  },
  "mesh-play"() {
    const clips = meshCamera.clips || [];
    if (meshCamera.clip < 0 && clips.length) meshCamera.clip = 0;
    const clip = clips[meshCamera.clip];
    if (!clip) return;
    if (meshCamera.playing) meshCamera.playing = false;
    else {
      const duration = Number(clip.duration) || 0;
      if (duration && meshCamera.time >= duration - 0.0001) meshCamera.time = 0;
      meshCamera.playing = true;
    }
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && canvas._geom && window.applyClipFrame) applyClipFrame(canvas._geom);
    const play = document.querySelector("[data-act='mesh-play']");
    if (play) {
      play.textContent = meshCamera.playing ? "Pause" : "Play";
      play.classList.toggle("on", Boolean(meshCamera.playing));
    }
  },
  "mesh-loop"() {
    meshCamera.loop = meshCamera.loop === false;
    const button = document.querySelector("[data-act='mesh-loop']");
    if (button) button.classList.toggle("on", meshCamera.loop !== false);
  },
  "mesh-speed"(button) {
    const speed = Number(button.dataset.value);
    meshCamera.speed = speed > 0 ? speed : 1;
    document.querySelectorAll("[data-act='mesh-speed']").forEach((chip) => {
      chip.classList.toggle("on", Number(chip.dataset.value) === meshCamera.speed);
    });
  },
  "mesh-anim-import"(button) {
    return (async () => {
      const picked = await rpc("shell.pick_mesh", {});
      if (!picked || !picked.path) return;
      await run("Importing the animation…", async () => {
        const result = await rpc("character.mesh_anim", { id: button.dataset.mesh, path: picked.path });
        const count = window.installAnimationClips ? installAnimationClips(result.clips || []) : 0;
        if (!count) toast("That file has no animation for this rig.");
      });
    })();
  },
  "pose-save"(button) {
    const field = document.getElementById("pose-name");
    const name = (field && field.value) || state.poseName || "";
    state.poseName = name;
    const rotations = (window.meshCamera && meshCamera.pose) || {};
    const shape = (window.meshCamera && meshCamera.shape) || "";
    return run("Saving pose…", async () => {
      absorb(await rpc("character.pose_save", { id: button.dataset.id, name, rotations, shape }));
    });
  },
  "pose-load"(button) {
    const person = (state.tree.characters || []).find((item) => item.id === button.dataset.char);
    const pose = person && (person.poses || []).find((item) => item.id === button.dataset.id);
    if (!pose || !window.meshCamera) return;
    meshCamera.playing = false;
    meshCamera.clip = -1;
    meshCamera.time = 0;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && canvas._geom) canvas._geom._animLocals = null;
    meshCamera.pose = pose.rotations || {};
    if (pose.shape) meshCamera.shape = pose.shape;
    meshCamera.poseMode = true;
    if (window.refreshMeshPose) refreshMeshPose();
    render();
  },
  "pose-delete"(button) {
    if (!confirm("Delete this pose?")) return;
    return run("Deleting pose…", async () => {
      absorb(await rpc("character.pose_delete", { id: button.dataset.id }));
    });
  },
  "mesh-angle"(button) {
    const angle = button.dataset.value || "";
    meshCamera.angle = angle;
    meshCamera.panX = 0;
    meshCamera.panY = 0;
    meshCamera.roll = 0;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    const front = window.facingYaw ? facingYaw(canvas && canvas._geom) : 0;
    if (angle === "front") {
      meshCamera.yaw = front;
      meshCamera.pitch = 0.05;
    } else if (angle === "side") {
      meshCamera.yaw = front - Math.PI / 2;
      meshCamera.pitch = 0.05;
    } else if (angle === "three") {
      meshCamera.yaw = front - 0.62;
      meshCamera.pitch = 0.16;
    }
    paintMeshChrome();
  },
  "mesh-turntable"(button) {
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (!canvas || canvas.dataset.ready !== "1" || !window.redraw) {
      toast("The model is still loading.");
      return;
    }
    const kind = button.dataset.kind === "png" ? "png" : "gif";
    const meshId = button.dataset.id;
    let frames;
    try {
      frames = turntableFrames(canvas);
    } catch (_error) {
      toast("Could not read the view.");
      return;
    }
    const label = kind === "png" ? "Saving the turntable frames…" : "Saving the turntable…";
    return run(label, async () => {
      const result = await rpc("character.mesh_turntable", { id: meshId, kind, frames });
      toast("Saved " + result.path, "ok");
    });
  },
  "mesh-shot"(button) {
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (!canvas || canvas.dataset.ready !== "1") {
      toast("The model is still loading.");
      return;
    }
    let png = "";
    try {
      png = canvas.toDataURL("image/png");
    } catch (_error) {
      toast("Could not read the view.");
      return;
    }
    const panelId = button.dataset.panel || "";
    return run("Saving this angle…", async () => {
      absorb(await rpc("character.mesh_shot", { id: button.dataset.mesh, png, panelId }));
      const person = (state.tree.characters || []).find((item) => item.id === button.dataset.id);
      const pose = person && person.refs.find((ref) => ref.label === "3D angle");
      if (pose) state.refId = pose.id;
    });
  },
  "mesh-large"() {
    meshCamera.large = !meshCamera.large;
    render();
    const panel = document.querySelector(".mesh-panel");
    if (meshCamera.large && panel && panel.requestFullscreen) {
      panel.requestFullscreen().catch(() => {});
    } else if (!meshCamera.large && document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
  },
  "gen-mesh"(button) {
    const field = document.getElementById("mesh-prompt");
    const prompt = field ? field.value : (state.meshPrompt || "");
    state.meshPrompt = prompt;
    return run("Building the 3D model…", async () => {
      absorb(await rpc("character.mesh", {
        id: button.dataset.id,
        prompt,
        source: state.meshSource || "prompt",
      }));
      const person = (state.tree.characters || []).find((item) => item.id === button.dataset.id);
      if (person && person.meshes && person.meshes[0]) state.meshId = person.meshes[0].id;
    });
  },
  "select-mesh"(button) {
    state.meshId = button.dataset.id;
    render();
  },
  "export-mesh"(button) {
    const format = button.dataset.format === "obj" ? "obj" : "glb";
    return run("Exporting the model…", async () => {
      const result = await rpc("character.mesh_export", { id: button.dataset.id, format });
      toast("Saved " + result.path, "ok");
    });
  },
  "export-posed"(button) {
    const canvas = document.querySelector("canvas[data-mesh-id]");
    const geom = canvas && canvas._geom;
    if (!geom || canvas.dataset.ready !== "1" || !window.posedExportPayload) {
      toast("The model is still loading.");
      return;
    }
    const payload = posedExportPayload(geom);
    return run("Exporting the posed model…", async () => {
      const result = await rpc("character.mesh_export_posed", Object.assign({ id: button.dataset.id }, payload));
      toast("Saved " + result.path, "ok");
    });
  },
  "delete-mesh"(button) {
    if (!confirm("Delete this 3D model?")) return;
    return run("Deleting the model…", async () => {
      absorb(await rpc("character.mesh_delete", { id: button.dataset.id }));
      state.meshId = null;
    });
  },
  "gen-character"(button) {
    const label = button.dataset.kind === "poses" && document.getElementById("pose-note")
      ? document.getElementById("pose-note").value
      : "";
    return run("Drawing the reference…", async () => {
      absorb(await rpc("character.generate", { id: button.dataset.id, kind: button.dataset.kind, label }));
    });
  },
  "select-ref"(button) {
    state.refId = button.dataset.id;
    render();
  },
  "primary-ref"() {
    if (!state.refId) return;
    return run("Setting the identity…", async () => {
      absorb(await rpc("character.primary", { refId: state.refId }));
    });
  },
  "delete-mood"(button) {
    if (!button.dataset.id || !confirm("Remove this picture from the reference board?")) return;
    return run("Removing…", async () => {
      absorb(await rpc("mood.delete", { refId: button.dataset.id }));
    });
  },
  "open-mood"() {
    const go = () => {
      state.view = "story";
      state.storyFocus = "mood";
      state.pageId = null;
      state.panelId = null;
      state.helpOpen = false;
      state.menuOpen = false;
    };
    if (state.tree && state.tree.project && inStudio()) {
      go();
      render();
      return;
    }
    const board = currentBoard();
    if (!board.projectId) {
      toast("Open a manga first. The reference board lives with the book.");
      return;
    }
    return run("Opening the reference board…", async () => {
      absorb(await rpc("project.open", { id: board.projectId }));
      state.mangaId = board.projectId;
      go();
    });
  },
  "delete-ref"() {
    if (!state.refId || !confirm("Delete this reference image?")) return;
    const refId = state.refId;
    state.refId = null;
    return run("Deleting…", async () => {
      absorb(await rpc("character.delete_ref", { refId }));
    });
  },
  "revise-ref"() {
    const instruction = document.getElementById("ref-note").value.trim();
    if (!state.refId || !instruction) {
      toast("Select a reference and say what to change.");
      return;
    }
    return run("Revising the reference…", async () => {
      absorb(await rpc("character.revise", { refId: state.refId, instruction }));
    });
  },
  "add-location"() {
    return run("Adding…", async () => {
      absorb(await rpc("location.create", { projectId: state.tree.project.id }));
      const places = state.tree.locations;
      state.locId = places.length ? places[places.length - 1].id : null;
    });
  },
  "select-location"(button) {
    state.locId = button.dataset.id;
    render();
  },
  "delete-location"(button) {
    if (!confirm("Delete this place?")) return;
    return run("Deleting…", async () => {
      absorb(await rpc("location.delete", { id: button.dataset.id }));
      state.locId = null;
    });
  },
  "gen-location"(button) {
    return run("Drawing the place…", async () => {
      absorb(await rpc("location.generate", { id: button.dataset.id }));
    });
  },
  "revise-location"(button) {
    const instruction = document.getElementById("place-note").value.trim();
    if (!instruction) {
      toast("Say what to change.");
      return;
    }
    return run("Revising the place…", async () => {
      absorb(await rpc("location.generate", { id: button.dataset.id, instruction }));
    });
  },
  "draw-empty"(button) {
    const scope = button.dataset.scope;
    const id = button.dataset.id;
    let empty = 0;
    if (scope === "page") {
      const row = allPages().find((item) => item.page.id === id);
      empty = row ? row.page.panels.filter((panel) => !panel.imageUrl).length : 0;
    } else {
      const chapter = chapters().find((item) => item.id === id);
      empty = chapter
        ? chapter.pages.reduce((sum, page) => sum + page.panels.filter((panel) => !panel.imageUrl).length, 0)
        : 0;
    }
    if (!empty) {
      toast("Every panel already has a drawing.");
      return;
    }
    if (empty > 2 && !confirm(`Draw ${empty} empty panels? Each one uses your xAI account.`)) return;
    return run(`Drawing ${empty} panels…`, async () => {
      const params = scope === "page" ? { pageId: id } : { chapterId: id };
      const result = await rpc("panel.draw_empty", params);
      absorb(result);
      state.printFor = null;
      if (result.warning) toast(result.warning);
      else if (result.drawn) toast(`Drew ${result.drawn} panel${result.drawn === 1 ? "" : "s"}.`, "ok");
    }).then(() => {
      if (state.view === "read") return loadSheets();
    });
  },
  "toggle-print"(button) {
    if (state.printFor === button.dataset.id) {
      state.printFor = null;
      render();
      return;
    }
    return run("Composing the page…", async () => {
      const result = await rpc("page.sheet", { pageId: button.dataset.id });
      state.printFor = button.dataset.id;
      state.sheetUrl = result.url;
      state.sheetStamp = Date.now();
    });
  },
  async "extract-lines"(button) {
    const id = button.dataset.id;
    if (!id) return;
    const params = {
      id,
      strength: Math.min(1, Math.max(0, (Number(state.lineStrength) || 55) / 100)),
    };
    if (button.dataset.from === "file") {
      const picked = await rpc("shell.pick_image", {});
      if (!picked || !picked.path) return;
      params.path = picked.path;
    }
    return run("Extracting lines…", async () => {
      absorb(await rpc("panel.lines", params));
      toast("Line art is on the panel.", "ok");
    });
  },
  async "import-ref"(button) {
    const picked = await rpc("shell.pick_image", {});
    if (!picked || !picked.path) return;
    await run("Importing the picture…", async () => {
      absorb(await rpc("ref.import", {
        owner: button.dataset.owner,
        id: button.dataset.id,
        kind: button.dataset.kind || "portrait",
        path: picked.path,
      }));
    });
  },
  "reload-read"() {
    return loadSheets();
  },
  "export-page"(button) {
    return run("Exporting the page…", async () => {
      const result = await rpc("export.page", { pageId: button.dataset.id });
      toast("Saved " + result.path, "ok");
      state.lastExport = result;
    });
  },
  "export-chapter"(button) {
    return run("Exporting the chapter…", async () => {
      const result = await rpc("export.chapter", { chapterId: button.dataset.id, format: button.dataset.format });
      toast("Saved in " + result.folder, "ok");
      await rpc("shell.reveal", { path: result.folder });
    });
  },
  "story-focus"(button) {
    state.storyFocus = button.dataset.focus || "title";
    if (state.storyFocus === "volume") {
      state.storyVolumeId = button.dataset.id;
      state.navVolumeId = button.dataset.id;
      const chapter = chapters().find((item) => item.volumeId === state.storyVolumeId);
      if (chapter) state.navChapterId = chapter.id;
    }
    if (state.storyFocus === "chapter") {
      state.storyFocusId = button.dataset.id;
      state.navChapterId = button.dataset.id;
      const chapter = chapters().find((item) => item.id === state.storyFocusId);
      if (chapter) {
        state.storyVolumeId = chapter.volumeId || state.storyVolumeId;
        state.navVolumeId = state.storyVolumeId;
      }
    }
    if (state.storyFocus === "page") {
      state.storyFocusId = button.dataset.id;
      const found = allPages().find((item) => item.page.id === state.storyFocusId);
      if (found) {
        state.pageId = found.page.id;
        state.panelId = null;
        state.navChapterId = found.chapter.id;
        state.navVolumeId = found.chapter.volumeId || state.navVolumeId;
        state.storyVolumeId = state.navVolumeId;
      }
    }
    if (state.storyFocus === "notes") state.storyNoteId = button.dataset.id;
    render();
  },
  "add-volume"(button) {
    return run("Adding a volume…", async () => {
      const projectId = state.tree.project.id;
      absorb(await rpc("volume.create", { projectId }));
      const list = volumes();
      const volume = list.length ? list[list.length - 1] : null;
      state.storyVolumeId = volume ? volume.id : null;
      state.navVolumeId = state.storyVolumeId;
      state.storyFocus = "volume";
      if (button && button.dataset.seed === "1" && volume) {
        absorb(await rpc("chapter.create", { projectId, volumeId: volume.id }));
        const created = chapters().filter((item) => item.volumeId === volume.id).pop();
        if (created) {
          state.navChapterId = created.id;
          absorb(await rpc("page.create", { chapterId: created.id }));
          const chapter = chapters().find((item) => item.id === created.id);
          const page = chapter && chapter.pages[chapter.pages.length - 1];
          if (page) {
            state.pageId = page.id;
            state.panelId = null;
          }
        }
      }
    });
  },
  "delete-volume"(button) {
    if (!confirm("Move this volume's chapters into another volume?")) return;
    return run("Removing the volume…", async () => {
      absorb(await rpc("volume.delete", { id: button.dataset.id }));
      state.storyVolumeId = null;
      state.navVolumeId = null;
    });
  },
  "add-note"() {
    return run("Adding a note…", async () => {
      const result = await rpc("note.create", { projectId: state.tree.project.id, title: "Note" });
      absorb(result);
      state.storyNoteId = result.noteId;
      state.storyFocus = "notes";
    });
  },
  "delete-note"(button) {
    return run("Deleting the note…", async () => {
      absorb(await rpc("note.delete", { id: button.dataset.id }));
      state.storyNoteId = null;
    });
  },
  "nav-volume"(button) {
    state.navVolumeId = button.dataset.id;
    const chapter = chapters().find((item) => item.volumeId === state.navVolumeId);
    state.navChapterId = chapter ? chapter.id : null;
    if (chapter && chapter.pages[0]) {
      state.pageId = chapter.pages[0].id;
      state.panelId = null;
    }
    render();
  },
  "nav-chapter"(button) {
    state.navChapterId = button.dataset.id;
    const chapter = chapters().find((item) => item.id === state.navChapterId);
    if (chapter) {
      state.navVolumeId = chapter.volumeId || state.navVolumeId;
      if (chapter.pages[0]) {
        state.pageId = chapter.pages[0].id;
        state.panelId = null;
      }
    }
    render();
  },
  "suggest-layout"() {
    const row = currentPage();
    if (!row) return;
    return run("Choosing a layout…", async () => {
      const result = await rpc("page.suggest", { id: row.page.id });
      absorb(result);
      if (result.fromGrok) toast("Grok suggested " + result.label + ".", "ok");
      else if (result.needsKey) toast("Add a key in Settings for Grok to choose. Suggested a layout from this chapter.");
      else toast("Suggested a layout from this chapter.", "ok");
    });
  },
  "arm-divide"() {
    const row = currentPage();
    if (!row) return;
    if (!state.divideOn) {
      state.divideOn = true;
      state.divide = { x: 0.5, y: 0.5, angle: 0 };
      render();
      return;
    }
    return run("Dividing the panel…", async () => {
      const result = await rpc("page.divide", {
        id: row.page.id,
        x: state.divide.x,
        y: state.divide.y,
        angle: state.divide.angle,
      });
      absorb(result);
      state.divideOn = false;
      if (result.straightened) toast("The cut was straightened so the panels stay clean.", "ok");
      else toast("Divided the panel.", "ok");
    });
  },
  "cancel-divide"() {
    state.divideOn = false;
    render();
  },
  "pick-tool"(button) {
    state.pageTool = button.dataset.tool || "lasso";
    render();
  },
  "imagine-cast"(button) {
    const charId = button.dataset.char;
    const panel = currentPanel();
    const row = currentPage();
    if (!row || !charId) return;
    if (panel) {
      const ids = (panel.characterIds || []).slice();
      const index = ids.indexOf(charId);
      if (index >= 0) ids.splice(index, 1);
      else ids.push(charId);
      return run("Saving…", async () => {
        absorb(await rpc("panel.update", { id: panel.id, characterIds: ids }));
      });
    }
    const ids = (row.page.characterIds || []).slice();
    const index = ids.indexOf(charId);
    if (index >= 0) ids.splice(index, 1);
    else ids.push(charId);
    return run("Saving…", async () => {
      absorb(await rpc("page.update", { id: row.page.id, characterIds: ids }));
    });
  },
  "imagine-place"(button) {
    const placeId = button.dataset.place || "";
    const panel = currentPanel();
    const row = currentPage();
    if (!row) return;
    if (panel) {
      const next = panel.locationId === placeId ? null : (placeId || null);
      return run("Saving…", async () => {
        absorb(await rpc("panel.update", { id: panel.id, locationId: next }));
      });
    }
    const next = row.page.locationId === placeId ? null : (placeId || null);
    return run("Saving…", async () => {
      absorb(await rpc("page.update", { id: row.page.id, locationId: next }));
    });
  },
  "imagine-draw"() {
    const panel = currentPanel();
    const row = currentPage();
    if (!row) return;
    if (panel) {
      const promptField = document.querySelector(`[data-save="panel"][data-field="prompt"][data-entity="${panel.id}"]`);
      const fields = { id: panel.id };
      if (promptField) fields.prompt = promptField.value;
      return run("Imagining this panel…", async () => {
        absorb(await rpc("panel.update", fields));
        absorb(await rpc("panel.draw", { id: panel.id }));
      });
    }
    const empty = row.page.panels.filter((item) => !item.imageUrl).length;
    if (!empty) {
      toast("Every panel already has a drawing. Select a panel to imagine it.");
      return;
    }
    if (empty > 2 && !confirm(`Imagine ${empty} empty panels? Each one uses your xAI account.`)) return;
    const promptField = document.querySelector(`[data-save="page"][data-field="prompt"][data-entity="${row.page.id}"]`);
    const pageId = row.page.id;
    const prompt = promptField ? promptField.value : row.page.prompt || "";
    return run(`Imagining ${empty} panel${empty === 1 ? "" : "s"}…`, async () => {
      absorb(await rpc("page.update", { id: pageId, prompt }));
      const result = await rpc("panel.draw_empty", { pageId, imagine: true });
      absorb(result);
      state.printFor = null;
      if (result.warning) toast(result.warning);
      else if (result.drawn) toast(`Imagined ${result.drawn} panel${result.drawn === 1 ? "" : "s"}.`, "ok");
    });
  },
  "lasso-mode"(button) {
    state.lassoMode = button.dataset.mode === "circle" ? "circle" : "select";
    state.lassoPath = [];
    render();
  },
  "pick-cast"(button) {
    const panel = currentPanel();
    if (!panel) return;
    const id = button.dataset.char;
    let ids;
    if (state.selectMulti) {
      ids = (panel.characterIds || []).slice();
      const index = ids.indexOf(id);
      if (index >= 0) ids.splice(index, 1);
      else ids.push(id);
    } else {
      ids = [id];
    }
    state.selectedBit = { kind: "character", id };
    return run("Saving…", async () => {
      absorb(await rpc("panel.update", { id: panel.id, characterIds: ids }));
    });
  },
  "pick-thing"(button) {
    const panel = currentPanel();
    if (!panel) return;
    const name = button.dataset.name;
    const objects = (panel.objects || []).map((item) => ({ name: item.name, on: !!item.on }));
    if (state.selectMulti) {
      const found = objects.find((item) => item.name === name);
      if (found) found.on = !found.on;
    } else {
      for (const item of objects) item.on = item.name === name;
    }
    state.selectedBit = { kind: "thing", name };
    return run("Saving…", async () => {
      absorb(await rpc("panel.update", { id: panel.id, objects }));
    });
  },
  "pick-place"(button) {
    const panel = currentPanel();
    if (!panel) return;
    state.selectedBit = { kind: "background", id: button.dataset.place || "" };
    return run("Saving…", async () => {
      absorb(await rpc("panel.update", { id: panel.id, locationId: button.dataset.place || null }));
    });
  },
  "toggle-multi"(input) {
    state.selectMulti = !!input.checked;
    render();
  },
  "arrange-elements"() {
    if (!state.panelId) return;
    return run("Arranging the panel…", async () => {
      absorb(await rpc("panel.arrange", { id: state.panelId }));
    });
  },
  "separate-panel"() {
    if (!state.panelId) return;
    return run("Separating the panel…", async () => {
      absorb(await rpc("panel.separate", { id: state.panelId }));
    });
  },
  "make-balloon"() {
    if (!state.panelId) return;
    return run("Writing the balloon…", async () => {
      absorb(await rpc("panel.letter", { id: state.panelId }));
    });
  },
  "set-vibe"(button) {
    const panel = currentPanel();
    if (!panel) return;
    const vibe = button.dataset.value;
    return run("Setting the atmosphere…", async () => {
      absorb(await rpc("panel.update", { id: panel.id, vibe }));
      if (panel.imageUrl) absorb(await rpc("panel.revise", { id: panel.id, instruction: "Atmosphere: " + vibe }));
      else toast("The vibe is saved and will be in the next draw.", "ok");
    });
  },
  "delete-panel-ref"(button) {
    return run("Removing the reference…", async () => {
      absorb(await rpc("panel.delete_ref", { refId: button.dataset.id }));
    });
  },
  "bot-close"() {
    state.botOpen = false;
    render();
  },
  "pick-bot"(button) {
    state.botId = button.dataset.id;
    render();
  },
  "pick-group"(button) {
    state.groupId = button.dataset.id;
    render();
    loadBotHistory();
  },
  "create-bot"() {
    if (!state.tree) return;
    const name = (document.getElementById("bot-name").value || "").trim() || "Grok";
    const persona = (document.getElementById("bot-persona").value || "").trim();
    return run("Creating the bot…", async () => {
      const result = await rpc("bot.create", { projectId: state.tree.project.id, name, persona });
      absorb(result);
      state.botId = result.botId;
    });
  },
  "delete-bot"(button) {
    return run("Removing the bot…", async () => {
      absorb(await rpc("bot.delete", { id: button.dataset.id }));
      if (state.botId === button.dataset.id) state.botId = null;
    });
  },
  "create-group"() {
    if (!state.tree) return;
    const title = (document.getElementById("group-title").value || "").trim() || "Group";
    const botIds = [...document.querySelectorAll("[data-bot-check]:checked")].map((el) => el.value);
    return run("Opening the group…", async () => {
      const result = await rpc("group.create", { projectId: state.tree.project.id, title, botIds });
      absorb(result);
      state.groupId = result.groupId;
      state.botMessages = [];
    });
  },
  "delete-group"(button) {
    return run("Closing the group…", async () => {
      absorb(await rpc("group.delete", { id: button.dataset.id }));
      if (state.groupId === button.dataset.id) {
        state.groupId = null;
        state.botMessages = [];
      }
    });
  },
  "send-bot"() {
    const box = document.getElementById("bot-draft");
    const message = box ? box.value.trim() : "";
    if (!message) return;
    if (!state.groupId) {
      toast("Create a group and add a bot.");
      return;
    }
    state.botDraft = "";
    return run("Talking…", async () => {
      const result = await rpc("bot.send", {
        groupId: state.groupId,
        message,
        view: state.view,
        pageId: state.pageId || "",
        panelId: state.panelId || "",
      });
      absorb(result);
      state.botMessages = result.messages || [];
    });
  },
};

function botLines(messages) {
  if (!messages || !messages.length) return `<p class="meta">Say something. Every bot in the group can answer.</p>`;
  return messages.map((message) => `
    <div class="bot-line"><strong>${esc(message.name || "Grok")}</strong>${esc(message.content)}</div>
  `).join("");
}

function botFrame() {
  if (!state.botBox) {
    const w = 560;
    const h = 440;
    state.botBox = {
      x: Math.max(16, (window.innerWidth - w) / 2),
      y: Math.max(56, (window.innerHeight - h) / 2),
      w,
      h,
    };
  }
  return state.botBox;
}

function botHtml() {
  const close = `<button type="button" class="ghost" data-act="bot-close">Close</button>`;
  if (!state.tree) {
    return `
      <header class="bot-drag"><strong>Grok</strong>${close}</header>
      <div class="bot-empty">Open a manga, then Koma can talk with you about the pages in front of you.</div>
    `;
  }
  const bots = state.tree.bots || [];
  const groups = state.tree.botGroups || [];
  if (!bots.some((item) => item.id === state.botId)) state.botId = bots[0] ? bots[0].id : null;
  if (!groups.some((item) => item.id === state.groupId)) state.groupId = groups[0] ? groups[0].id : null;
  const bot = bots.find((item) => item.id === state.botId) || null;
  const botList = bots.map((item) => `
    <button type="button" class="page-link ${item.id === state.botId ? "on" : ""}" data-act="pick-bot" data-id="${esc(item.id)}">${esc(item.name)}</button>
  `).join("");
  const groupList = groups.map((item) => `
    <button type="button" class="page-link ${item.id === state.groupId ? "on" : ""}" data-act="pick-group" data-id="${esc(item.id)}">${esc(item.title)}</button>
  `).join("");
  const checks = bots.map((item) => `
    <label class="check"><input type="checkbox" data-bot-check value="${esc(item.id)}"> ${esc(item.name)}</label>
  `).join("") || `<p class="meta">Create a bot first.</p>`;
  const persona = bot ? `
    <label class="field">Persona
      <textarea data-save="bot" data-field="persona" data-entity="${esc(bot.id)}">${esc(bot.persona || "")}</textarea>
    </label>
    <button type="button" class="danger quiet" data-act="delete-bot" data-id="${esc(bot.id)}">Remove bot</button>
  ` : "";
  return `
    <header class="bot-drag"><strong>Grok</strong>${close}</header>
    <div class="bot-body">
      <aside class="bot-side">
        <p class="rail-kicker">Bots</p>
        ${botList || `<p class="meta">No bots yet.</p>`}
        <label class="field">Name<input id="bot-name" type="text" placeholder="Editor"></label>
        <label class="field">Persona<textarea id="bot-persona" placeholder="A calm story editor."></textarea></label>
        <button type="button" class="ghost" data-act="create-bot">Create bot</button>
        ${persona}
        <p class="rail-kicker">Groups</p>
        ${groupList || `<p class="meta">No group yet.</p>`}
        <label class="field">Group<input id="group-title" type="text" placeholder="Writers' room"></label>
        ${checks}
        <button type="button" class="ghost" data-act="create-group">Create group</button>
        ${state.groupId ? `<button type="button" class="danger quiet" data-act="delete-group" data-id="${esc(state.groupId)}">Delete group</button>` : ""}
      </aside>
      <section class="bot-chat">
        <div class="bot-log" data-scroll="bot">${botLines(state.botMessages)}</div>
        <div class="bot-compose">
          <textarea id="bot-draft" placeholder="Ask the group about this page."></textarea>
          <button type="button" class="rail-go" data-act="send-bot" ${state.busy ? "disabled" : ""}>Send</button>
        </div>
      </section>
    </div>
  `;
}

function mountBot() {
  const shell = document.getElementById("shell");
  let dock = document.getElementById("bot-dock");
  if (!state.botOpen) {
    if (dock) dock.remove();
    return;
  }
  const box = botFrame();
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "bot-dock";
    shell.appendChild(dock);
    const watch = new ResizeObserver(() => {
      const rect = dock.getBoundingClientRect();
      if (!state.botBox) return;
      if (Math.abs(rect.width - state.botBox.w) < 2 && Math.abs(rect.height - state.botBox.h) < 2) return;
      state.botBox.w = rect.width;
      state.botBox.h = rect.height;
    });
    watch.observe(dock);
  }
  dock.style.left = box.x + "px";
  dock.style.top = box.y + "px";
  dock.style.width = box.w + "px";
  dock.style.height = box.h + "px";
  dock.innerHTML = botHtml();
  const draft = dock.querySelector("#bot-draft");
  if (draft) draft.value = state.botDraft;
  const log = dock.querySelector(".bot-log");
  if (log) log.scrollTop = log.scrollHeight;
}

async function loadBotHistory() {
  if (!state.groupId) return;
  try {
    const result = await rpc("bot.history", { id: state.groupId });
    state.botMessages = result.messages || [];
    const log = document.querySelector(".bot-log");
    if (log) {
      log.innerHTML = botLines(state.botMessages);
      log.scrollTop = log.scrollHeight;
    }
  } catch (error) {
    toast(error.message);
  }
}

function paintDivide() {
  const svg = document.querySelector(".divide-layer");
  if (!svg || !state.divide) return;
  const rect = svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const rad = state.divide.angle * Math.PI / 180;
  const vx = Math.cos(rad) / rect.width;
  const vy = Math.sin(rad) / rect.height;
  const len = Math.hypot(vx, vy) || 1;
  const reach = 0.72;
  const ux = (vx / len) * reach;
  const uy = (vy / len) * reach;
  const x = state.divide.x;
  const y = state.divide.y;
  const attrs = {
    ".hit": { x1: x - ux, y1: y - uy, x2: x + ux, y2: y + uy },
    ".ink": { x1: x - ux, y1: y - uy, x2: x + ux, y2: y + uy },
    "[data-pen='divide-a']": { cx: x - ux, cy: y - uy },
    "[data-pen='divide-mid']": { cx: x, cy: y },
    "[data-pen='divide-b']": { cx: x + ux, cy: y + uy },
  };
  for (const [selector, values] of Object.entries(attrs)) {
    const el = svg.querySelector(selector);
    if (!el) continue;
    for (const [key, value] of Object.entries(values)) el.setAttribute(key, value);
  }
}

function paintDataUrl(canvas, url) {
  if (!canvas || !url) return;
  const img = new Image();
  img.onload = () => {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  };
  img.src = url;
}

function restoreDraw() {
  paintDataUrl(document.querySelector(".draw-sketch"), state.drawSketch);
  paintDataUrl(document.querySelector(".draw-preview"), state.drawPreview);
}

function restoreSketch() {
  const canvas = document.querySelector(".sketch-layer");
  if (!canvas || !state.sketchPreview) return;
  const img = new Image();
  img.onload = () => {
    const scale = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round((rect.width || 1) * scale));
    canvas.height = Math.max(1, Math.round((rect.height || 1) * scale));
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  };
  img.src = state.sketchPreview;
}

function paintLasso() {
  const svg = document.querySelector(".lasso-layer");
  if (!svg) return;
  const path = state.lassoPath || [];
  if (state.lassoMode === "circle" && path.length >= 2) {
    const a = path[0];
    const b = path[1];
    const cx = (a.x + b.x) / 2;
    const cy = (a.y + b.y) / 2;
    const rx = Math.max(0.01, Math.abs(b.x - a.x) / 2);
    const ry = Math.max(0.01, Math.abs(b.y - a.y) / 2);
    svg.innerHTML = `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}"></ellipse>`;
    return;
  }
  if (!path.length) {
    svg.innerHTML = "";
    return;
  }
  svg.innerHTML = `<polyline points="${path.map((item) => `${item.x},${item.y}`).join(" ")}"></polyline>`;
}

const pen = { kind: "", pointerId: null, host: null, sketch: null, last: null };
let sketchTimer = 0;
let sketchFlight = null;
let sketchWait = null;
let drawTimer = 0;
let drawToken = 0;

function penPoint(host, event) {
  const rect = host.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) / (rect.width || 1),
    y: (event.clientY - rect.top) / (rect.height || 1),
    rect,
  };
}

function pressureOf(event) {
  if ((event.pointerType === "pen" || event.pointerType === "touch") && event.pressure > 0) return event.pressure;
  return 0.5;
}

function onPenDown(event) {
  if (event.button !== 0 && event.pointerType === "mouse") return;
  const drag = event.target.closest && event.target.closest(".bot-drag");
  if (drag && !(event.target.closest && event.target.closest("[data-act]"))) {
    const dock = document.getElementById("bot-dock");
    if (!dock || !state.botBox) return;
    pen.kind = "bot";
    pen.pointerId = event.pointerId;
    pen.host = dock;
    pen.last = { px: event.clientX, py: event.clientY, ox: state.botBox.x, oy: state.botBox.y };
    drag.setPointerCapture(event.pointerId);
    return;
  }
  const host = event.target.closest && event.target.closest("[data-pen]");
  if (!host) return;
  event.preventDefault();
  if (host.dataset.pen === "el-move" || host.dataset.pen === "el-resize") {
    const layer = host.closest(".element-layer");
    const box = host.closest(".el-box");
    if (!layer || !box) return;
    if (box.setPointerCapture) box.setPointerCapture(event.pointerId);
    pen.kind = host.dataset.pen;
    pen.pointerId = event.pointerId;
    pen.host = layer;
    pen.boxEl = box;
    pen.moved = false;
    pen.box = {
      x: parseFloat(box.style.left) / 100,
      y: parseFloat(box.style.top) / 100,
      w: parseFloat(box.style.width) / 100,
      h: parseFloat(box.style.height) / 100,
    };
    pen.origin = penPoint(layer, event);
    state.elementId = box.dataset.id;
    document.querySelectorAll(".el-box.on").forEach((el) => el.classList.remove("on"));
    box.classList.add("on");
    return;
  }
  const cap = typeof host.setPointerCapture === "function" ? host : host.ownerSVGElement;
  if (cap && cap.setPointerCapture) cap.setPointerCapture(event.pointerId);
  pen.kind = host.dataset.pen;
  pen.pointerId = event.pointerId;
  pen.host = pen.kind.startsWith("divide") ? (host.closest(".divide-layer") || host) : host;
  pen.last = penPoint(pen.host, event);
  if (pen.kind === "sketch" || pen.kind === "draw") beginSketch(host, event);
  if (pen.kind === "lasso") state.lassoPath = [pen.last];
  paintLasso();
}

function onPenMove(event) {
  if (pen.pointerId !== event.pointerId || !pen.host) return;
  if (pen.kind === "bot" && state.botBox) {
    state.botBox.x = pen.last.ox + event.clientX - pen.last.px;
    state.botBox.y = pen.last.oy + event.clientY - pen.last.py;
    pen.host.style.left = state.botBox.x + "px";
    pen.host.style.top = state.botBox.y + "px";
    return;
  }
  const point = penPoint(pen.host, event);
  if ((pen.kind === "el-move" || pen.kind === "el-resize") && pen.boxEl && pen.box) {
    const dx = point.x - pen.origin.x;
    const dy = point.y - pen.origin.y;
    if (Math.abs(dx) > 0.004 || Math.abs(dy) > 0.004) pen.moved = true;
    let x = pen.box.x;
    let y = pen.box.y;
    let w = pen.box.w;
    let h = pen.box.h;
    if (pen.kind === "el-resize") {
      w = Math.min(1 - x, Math.max(0.08, pen.box.w + dx));
      h = Math.min(1 - y, Math.max(0.08, pen.box.h + dy));
    } else {
      x = Math.min(1 - w, Math.max(0, pen.box.x + dx));
      y = Math.min(1 - h, Math.max(0, pen.box.y + dy));
    }
    pen.boxEl.style.left = `${x * 100}%`;
    pen.boxEl.style.top = `${y * 100}%`;
    pen.boxEl.style.width = `${w * 100}%`;
    pen.boxEl.style.height = `${h * 100}%`;
    return;
  }
  if ((pen.kind === "sketch" || pen.kind === "draw") && pen.sketch) {
    const canvas = pen.host;
    const ctx = pen.sketch;
    ctx.lineWidth = (1.5 + pressureOf(event) * 5) * (window.devicePixelRatio || 1);
    ctx.lineTo(point.x * canvas.width, point.y * canvas.height);
    ctx.stroke();
    if (pen.kind === "draw") scheduleDraw();
    return;
  }
  if (pen.kind === "lasso") {
    if (state.lassoMode === "circle") state.lassoPath = [state.lassoPath[0] || point, point];
    else state.lassoPath.push(point);
    paintLasso();
    return;
  }
  if (pen.kind === "divide-line") {
    state.divide.x = Math.min(0.92, Math.max(0.08, point.x));
    state.divide.y = Math.min(0.92, Math.max(0.08, point.y));
    paintDivide();
    return;
  }
  if (pen.kind && pen.kind.startsWith("divide")) {
    const rect = pen.host.getBoundingClientRect();
    const cx = rect.left + state.divide.x * rect.width;
    const cy = rect.top + state.divide.y * rect.height;
    state.divide.angle = Math.atan2(event.clientY - cy, event.clientX - cx) * 180 / Math.PI;
    paintDivide();
  }
}

function onPenUp(event) {
  if (pen.pointerId !== event.pointerId) return;
  if (pen.kind === "sketch") {
    clearTimeout(sketchTimer);
    sketchTimer = setTimeout(sendSketch, 650);
  }
  if (pen.kind === "draw") {
    clearTimeout(drawTimer);
    sendDrawPreview();
  }
  if (pen.kind === "lasso") sendLasso();
  if (pen.kind === "el-move" || pen.kind === "el-resize") {
    const moved = !!pen.moved;
    const elements = readElements();
    pen.kind = "";
    pen.pointerId = null;
    pen.host = null;
    pen.boxEl = null;
    pen.box = null;
    if (moved && elements.length && state.panelId) {
      rpc("panel.update", { id: state.panelId, elements }).then((result) => {
        absorb(result);
        render();
      }).catch((error) => toast(error.message));
    } else {
      render();
    }
    return;
  }
  pen.kind = "";
  pen.pointerId = null;
  pen.host = null;
  pen.sketch = null;
}

function beginSketch(canvas, event) {
  clearTimeout(sketchTimer);
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  if (!canvas.width || !canvas.height) {
    canvas.width = Math.max(1, Math.round(rect.width * scale));
    canvas.height = Math.max(1, Math.round(rect.height * scale));
  }
  const ctx = canvas.getContext("2d");
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#161616";
  const point = penPoint(canvas, event);
  ctx.beginPath();
  ctx.moveTo(point.x * canvas.width, point.y * canvas.height);
  pen.sketch = ctx;
}

function scheduleDraw() {
  mirrorDraw();
  clearTimeout(drawTimer);
  drawTimer = setTimeout(sendDrawPreview, 320);
}

function mirrorDraw() {
  const sketch = document.querySelector(".draw-sketch");
  const preview = document.querySelector(".draw-preview");
  if (!sketch || !preview) return;
  const ctx = preview.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, preview.width, preview.height);
  ctx.drawImage(sketch, 0, 0, preview.width, preview.height);
}

function sendDrawPreview() {
  const sketch = document.querySelector(".draw-sketch");
  if (!sketch || !state.panelId || !sketch.width) return;
  state.drawSketch = sketch.toDataURL("image/png");
  const token = ++drawToken;
  const id = state.panelId;
  rpc("panel.draw_live", { id, png: state.drawSketch }).then((result) => {
    if (token !== drawToken || !result || !result.png) return;
    state.drawPreview = "data:image/png;base64," + result.png;
    paintDataUrl(document.querySelector(".draw-preview"), state.drawPreview);
  }).catch((error) => {
    if (token === drawToken) toast(error.message);
  });
}

function sendLasso() {
  const path = (state.lassoPath || []).map((item) => ({ x: item.x, y: item.y }));
  if (!state.panelId || path.length < 2) return;
  const id = state.panelId;
  const mode = state.lassoMode;
  return run("Redrawing that part…", async () => {
    const result = await rpc("panel.lasso", { id, path, mode });
    absorb(result);
    state.lassoPath = [];
    if (result && result.source === "ai") toast("Grok redrew that part.", "ok");
    else toast("Softened that part on this machine.", "ok");
  });
}

function sendSketch() {
  const canvas = document.querySelector(".sketch-layer");
  if (!canvas || !state.panelId || !canvas.width) return;
  state.sketchPreview = canvas.toDataURL("image/png");
  const out = document.createElement("canvas");
  out.width = canvas.width;
  out.height = canvas.height;
  const ctx = out.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(canvas, 0, 0);
  refineSketch(out.toDataURL("image/png"));
}

async function refineSketch(png) {
  if (sketchFlight) {
    sketchWait = png;
    return;
  }
  const id = state.panelId;
  setStatus("Refining the sketch…");
  sketchFlight = rpc("panel.sketch", { id, png });
  try {
    const result = await sketchFlight;
    absorb(result);
    if (!sketchWait) state.sketchPreview = "";
    if (pen.kind !== "sketch") render();
  } catch (error) {
    toast(error.message);
  } finally {
    sketchFlight = null;
    setStatus("");
    if (sketchWait) {
      const next = sketchWait;
      sketchWait = null;
      refineSketch(next);
    }
  }
}

document.addEventListener("pointerdown", onPenDown);
document.addEventListener("pointermove", onPenMove);
document.addEventListener("pointerup", onPenUp);
document.addEventListener("pointercancel", onPenUp);
document.addEventListener("wheel", (event) => {
  if (event.ctrlKey) event.preventDefault();
}, { passive: false });

const dragState = { kind: "", id: "" };

function reorderIds(ids, fromId, toId) {
  const next = ids.slice();
  const from = next.indexOf(fromId);
  const to = next.indexOf(toId);
  if (from < 0 || to < 0 || from === to) return null;
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

document.addEventListener("dragstart", (event) => {
  const el = event.target.closest("[data-drag]");
  if (!el || state.busy) {
    event.preventDefault();
    return;
  }
  dragState.kind = el.dataset.drag;
  dragState.id = el.dataset.id;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", el.dataset.id);
  el.classList.add("dragging");
});

document.addEventListener("dragover", (event) => {
  const el = event.target.closest("[data-drag]");
  if (!el || el.dataset.drag !== dragState.kind || !dragState.id || el.dataset.id === dragState.id) return;
  if (dragState.kind === "page" && el.dataset.chapter) {
    const source = document.querySelector(`[data-drag="page"][data-id="${dragState.id}"]`);
    if (source && source.dataset.chapter !== el.dataset.chapter) return;
  }
  event.preventDefault();
  document.querySelectorAll(".drag-over").forEach((node) => {
    if (node !== el) node.classList.remove("drag-over");
  });
  el.classList.add("drag-over");
});

document.addEventListener("drop", (event) => {
  const el = event.target.closest("[data-drag]");
  document.querySelectorAll(".drag-over, .dragging").forEach((node) => node.classList.remove("drag-over", "dragging"));
  if (!el || el.dataset.drag !== dragState.kind || !dragState.id) return;
  event.preventDefault();
  const fromId = dragState.id;
  const kind = dragState.kind;
  dragState.kind = "";
  dragState.id = "";
  if (kind === "page") {
    const chapterId = el.dataset.chapter;
    const ids = [...document.querySelectorAll(`[data-drag="page"][data-chapter="${chapterId}"]`)].map((node) => node.dataset.id);
    const next = reorderIds(ids, fromId, el.dataset.id);
    if (!next) return;
    Promise.resolve(run("Moving…", async () => {
      absorb(await rpc("page.reorder", { chapterId, ids: next }));
      state.pageId = fromId;
    })).catch((error) => toast(error.message || String(error)));
    return;
  }
  const row = currentPage();
  if (!row) return;
  const next = reorderIds(row.page.panels.map((panel) => panel.id), fromId, el.dataset.id);
  if (!next) return;
  Promise.resolve(run("Moving…", async () => {
    absorb(await rpc("panel.reorder", { pageId: row.page.id, ids: next }));
    state.panelId = fromId;
  })).catch((error) => toast(error.message || String(error)));
});

document.addEventListener("dragend", () => {
  dragState.kind = "";
  dragState.id = "";
  document.querySelectorAll(".drag-over, .dragging").forEach((node) => node.classList.remove("drag-over", "dragging"));
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-act]");
  const insideMenu = event.target.closest(".menu-wrap");
  if (state.menuOpen && !insideMenu) {
    state.menuOpen = false;
    if (!button || button.disabled) render();
  }
  if (!button || button.disabled) return;
  const act = actions[button.dataset.act];
  if (!act) return;
  Promise.resolve(act(button)).catch((error) => toast(error.message || String(error)));
});

document.addEventListener("focusin", (event) => {
  const el = event.target;
  if (!el.dataset || state.view !== "story") return;
  if (el.dataset.save !== "project" || el.dataset.field !== "title") return;
  if (state.storyFocus === "title") return;
  state.storyFocus = "title";
  render();
});

document.addEventListener("focusout", (event) => {
  const el = event.target;
  if (!el.dataset || !el.dataset.save || el.tagName === "SELECT") return;
  if (el.value === el.dataset.orig) return;
  el.dataset.orig = el.value;
  saveField(el.dataset.save, el.dataset.entity, el.dataset.field, el.value)
    .catch((error) => toast(error.message));
});

document.addEventListener("input", (event) => {
  const el = event.target;
  if (el && el.dataset && "lineStrength" in el.dataset) {
    state.lineStrength = Number(el.value) || 1;
    const readout = document.querySelector("[data-line-readout]");
    if (readout) readout.textContent = `${state.lineStrength}. Lower keeps the darkest strokes. Higher keeps lighter edges too.`;
    return;
  }
  if (el && el.id === "bot-draft") state.botDraft = el.value;
  if (el && el.dataset && el.dataset.poseAxis && window.setPoseAxis) {
    setPoseAxis(el.dataset.poseAxis, el.value);
    return;
  }
  if (el && el.dataset && el.dataset.fingerCurl != null && window.applyFingerCurl) {
    applyFingerCurl(Number(el.value) / 100);
    return;
  }
  if (el && el.dataset && el.dataset.animTime != null && window.meshCamera) {
    const next = Number(el.value) || 0;
    if (Math.abs(next - (Number(meshCamera.time) || 0)) < 0.0001) return;
    meshCamera.playing = false;
    meshCamera.time = next;
    const canvas = document.querySelector("canvas[data-mesh-id]");
    if (canvas && canvas._geom && window.applyClipFrame) {
      applyClipFrame(canvas._geom);
      redraw(canvas);
    }
    const readout = document.querySelector("[data-anim-readout]");
    const clips = meshCamera.clips || [];
    const clip = clips[meshCamera.clip];
    if (readout && clip) readout.textContent = `${meshCamera.time.toFixed(2)}s / ${(Number(clip.duration) || 0).toFixed(2)}s`;
    const play = document.querySelector("[data-act='mesh-play']");
    if (play) {
      play.textContent = "Play";
      play.classList.remove("on");
    }
    return;
  }
  if (!el.dataset || el.dataset.save !== "local") return;
  if (el.dataset.field === "meshPrompt") {
    state.meshPrompt = el.value;
    return;
  }
  if (el.dataset.field === "poseName") {
    state.poseName = el.value;
    return;
  }
  if (!String(el.dataset.field || "").startsWith("setup")) return;
  state[el.dataset.field] = el.value;
  paintSetupSpec();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && window.meshCamera && meshCamera.large && !state.helpOpen && !state.menuOpen) {
    meshCamera.large = false;
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
    render();
    return;
  }
  if (event.key === "Escape" && state.helpOpen) {
    state.helpOpen = false;
    render();
    return;
  }
  if (event.key === "Escape" && state.menuOpen) {
    state.menuOpen = false;
    render();
    return;
  }
  const typing = event.target && (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA" || event.target.tagName === "SELECT" || event.target.isContentEditable);
  if (!typing && !state.busy && !event.ctrlKey && !event.metaKey && !event.altKey && event.key.toLowerCase() === "m" && state.view === "cast" && window.meshCamera && meshCamera.poseMode && !(meshCamera.large)) {
    event.preventDefault();
    const act = actions["mesh-mirror"];
    if (act) Promise.resolve(act()).catch((error) => toast(error.message || String(error)));
    return;
  }
  if (!typing && !state.busy && !event.ctrlKey && !event.metaKey && !event.altKey && event.key.toLowerCase() === "r" && !(window.meshCamera && meshCamera.large)) {
    event.preventDefault();
    const act = actions["open-mood"];
    if (act) Promise.resolve(act()).catch((error) => toast(error.message || String(error)));
    return;
  }
  if (!typing && event.key === "?") {
    event.preventDefault();
    state.helpOpen = !state.helpOpen;
    state.menuOpen = false;
    render();
    return;
  }
  if (!typing && !state.busy && state.view === "read" && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
    event.preventDefault();
    const act = actions["spread-step"];
    if (act) act({ dataset: { dir: event.key === "ArrowRight" ? "1" : "-1" } });
    return;
  }
  if (!typing && !state.busy && state.view === "pages" && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    const act = actions[event.shiftKey ? "redo-page" : "undo-page"];
    if (act) Promise.resolve(act()).catch((error) => toast(error.message || String(error)));
    return;
  }
  if (!typing && !state.busy && state.view === "pages" && state.panelId) {
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      const act = actions["delete-panel"];
      if (act) Promise.resolve(act({ dataset: { id: state.panelId } })).catch((error) => toast(error.message || String(error)));
      return;
    }
    if (event.key === "[" || event.key === "]") {
      event.preventDefault();
      const act = actions["move-panel"];
      if (act) Promise.resolve(act({ dataset: { id: state.panelId, dir: event.key === "[" ? "-1" : "1" } })).catch((error) => toast(error.message || String(error)));
      return;
    }
    const toolKeys = ["lasso", "select", "text", "sketch", "vfx", "imagine", "lines", "draw"];
    const toolIndex = "12345678".indexOf(event.key);
    if (toolIndex >= 0) {
      event.preventDefault();
      const act = actions["pick-tool"];
      if (act) Promise.resolve(act({ dataset: { tool: toolKeys[toolIndex] } })).catch((error) => toast(error.message || String(error)));
      return;
    }
  }
  if (event.key === "Enter" && event.target && event.target.id === "library-search" && !state.busy) {
    event.preventDefault();
    const act = actions["search-library"];
    if (act) Promise.resolve(act()).catch((error) => toast(error.message || String(error)));
    return;
  }
  if (event.key !== "Enter" || state.view !== "setup" || state.busy) return;
  const target = event.target;
  if (!target || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return;
  if (target.dataset && target.dataset.act === "open-book") return;
  event.preventDefault();
  const act = actions["open-book"];
  if (act) Promise.resolve(act()).catch((error) => toast(error.message || String(error)));
});

document.addEventListener("change", (event) => {
  const el = event.target;
  if (el.dataset && el.dataset.act === "pick-series") {
    state.mangaId = el.value;
    render();
    return;
  }
  if (el.dataset && el.dataset.act === "pick-chapter") {
    state.readChapterId = el.value;
    state.spreadAt = 0;
    state.sheets = null;
    loadSheets();
    return;
  }
  if (el.dataset && (el.dataset.act === "pick-nav-volume" || el.dataset.act === "pick-story-volume")) {
    focusVolume(el.value);
    render();
    return;
  }
  if (!el.dataset || el.tagName !== "SELECT" || !el.dataset.save) return;
  const save = el.dataset.save === "page" && el.dataset.field === "layout"
    ? savePageLayout(el)
    : saveField(el.dataset.save, el.dataset.entity, el.dataset.field, el.value).then(() => render());
  Promise.resolve(save).catch((error) => {
    toast(error.message);
    render();
  });
});

async function savePageLayout(el) {
  const row = allPages().find((item) => item.page.id === el.dataset.entity);
  const next = el.value;
  if (!row || next === "custom") {
    render();
    return;
  }
  const layout = (state.layouts || []).find((item) => item.id === next);
  const count = layout ? layout.count : 1;
  const extras = row.page.panels.filter((panel) => panel.slot >= count);
  const worked = extras.filter(panelHasWork);
  let dropExtra = false;
  if (worked.length) {
    const noun = worked.length === 1 ? "panel" : "panels";
    if (!confirm(`Remove ${worked.length} ${noun} with art or lettering?`)) {
      render();
      return;
    }
    dropExtra = true;
  }
  try {
    absorb(await rpc("page.update", { id: row.page.id, layout: next, dropExtra }));
  } catch (error) {
    if (!dropExtra && /art or lettering/.test(error.message || "") && confirm(error.message)) {
      absorb(await rpc("page.update", { id: row.page.id, layout: next, dropExtra: true }));
    } else {
      throw error;
    }
  }
  render();
}

function loadTheme() {
  let saved = "";
  try { saved = localStorage.getItem("koma-theme") || ""; } catch (_error) { saved = ""; }
  document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
}

async function boot() {
  loadTheme();
  const main = document.getElementById("main");
  try {
    if (!window.webkit || !window.webkit.messageHandlers) {
      throw new Error("Open Koma from the app window, not a browser tab.");
    }
    const bootData = await rpc("bootstrap");
    state.settings = bootData.settings;
    state.layouts = bootData.layouts;
    state.vocab = bootData.vocab;
    state.projects = bootData.projects;
    state.mangaBoards = bootData.mangaBoards || [];
    state.illustrations = bootData.illustrations || [];
    state.formats = bootData.formats || null;
    state.dataDir = bootData.dataDir || "";
    render();
    main.dataset.ready = "1";
  } catch (error) {
    main.dataset.ready = "error";
    main.dataset.error = error.message || String(error);
    main.textContent = error.message || String(error);
  }
}

boot();
