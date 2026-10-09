(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const state = {
    items: [],
    filter: "all",
    current: null,
    attachments: [],
    timeline: [],
    autosaveTimer: null,
    autosavePending: false,
    savePromise: null,
    editSequence: 0,
    savedSequence: 0,
    createKey: null,
    uploadPending: false,
    uploadPreparing: false,
    reloadRequired: false,
    bootstrapped: false,
    paletteColors: ["#5B67F1", "#EF86AC", "#78CFBC", "#FFC777"],
    imagePreviewUrl: null,
  };

  const statusLabels = {
    DRAFT: "Черновик",
    SUBMITTED: "Отправлено",
    IN_REVIEW: "На проверке",
    APPROVED: "Одобрено",
    CHANGES_REQUESTED: "Нужны правки",
    REJECTED: "Отклонено",
    WITHDRAWN: "Отозвано",
  };

  const $ = (id) => document.getElementById(id);

  const views = {
    workspace: $("workspaceView"),
    editor: $("editorView"),
    detail: $("detailView"),
  };

  const form = {
    title: $("titleInput"),
    direction: $("directionInput"),
    rating: $("ratingInput"),
    fandom: $("fandomInput"),
    description: $("descriptionInput"),
    body: $("bodyInput"),
    url: $("urlInput"),
    extraLinks: $("extraLinksInput"),
    characters: $("charactersInput"),
    notes: $("notesInput"),
    sizeWords: $("sizeWordsInput"),
    pages: $("pagesInput"),
    parts: $("partsInput"),
  };

  function makeIdempotencyKey(prefix) {
    const random = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${random}`;
  }

  function show(viewName) {
    Object.entries(views).forEach(([name, element]) => {
      element.classList.toggle("hidden", name !== viewName);
    });
  }

  function setBanner(message, kind = "") {
    const banner = $("connectionBanner");
    banner.textContent = message || "";
    banner.className = "banner";
    if (kind) {
      banner.classList.add(`banner-${kind}`);
    }
    banner.classList.toggle("hidden", !message);
  }

  function setEditorError(message) {
    const banner = $("editorError");
    banner.textContent = message || "";
    banner.classList.toggle("hidden", !message);
  }

  function setBusy(active) {
    $("busyOverlay").classList.toggle("hidden", !active);
    $("busyOverlay").setAttribute("aria-hidden", active ? "false" : "true");
  }

  function setSaveState(text) {
    $("saveState").textContent = text;
  }

  function setReloadRequired(active) {
    state.reloadRequired = Boolean(active);
    $("reloadRequired").classList.toggle("hidden", !state.reloadRequired);
    updateActionAvailability();
  }

  function updateActionAvailability() {
    const locked = state.reloadRequired || state.autosavePending ||
      state.uploadPending || state.uploadPreparing;
    $("submitButton").disabled = locked;
    $("saveButton").disabled = locked;
    $("fileInput").disabled = locked;
    $("imageInput").disabled = locked;
    if (tg) {
      const unsaved = state.autosavePending ||
        state.editSequence > state.savedSequence;
      if (unsaved && typeof tg.enableClosingConfirmation === "function") {
        tg.enableClosingConfirmation();
      } else if (!unsaved && typeof tg.disableClosingConfirmation === "function") {
        tg.disableClosingConfirmation();
      }
    }
  }

  async function api(path, options = {}) {
    const request = {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: new Headers(options.headers || {}),
    };
    if (options.json !== undefined) {
      request.headers.set("Content-Type", "application/json");
      request.body = JSON.stringify(options.json);
    } else if (options.body !== undefined) {
      request.body = options.body;
    }

    const response = await fetch(path, request);
    if (response.status === 204) {
      return null;
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }

    if (response.status === 409) {
      setReloadRequired(true);
      const conflict = new Error("conflict");
      conflict.code = "conflict";
      conflict.status = 409;
      throw conflict;
    }

    if (!response.ok) {
      const error = new Error(
        payload && payload.message
          ? payload.message
          : payload && payload.error
            ? payload.error
            : `HTTP ${response.status}`
      );
      error.code = payload && payload.error ? payload.error : "request_failed";
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function bootstrapSession() {
    if (!tg) {
      throw new Error("Mini App нужно открыть внутри Telegram.");
    }
    tg.ready();
    tg.expand();

    const initData = window.Telegram.WebApp.initData;
    if (!initData) {
      throw new Error("Telegram не передал данные авторизации.");
    }

    await api("/api/writers/session", {
      method: "POST",
      json: { init_data: initData },
    });
    state.bootstrapped = true;
  }

  function safeTextElement(tag, text, className = "") {
    const node = document.createElement(tag);
    node.textContent = text == null ? "" : String(text);
    if (className) {
      node.className = className;
    }
    return node;
  }

  function formatDate(timestamp) {
    const numeric = Number(timestamp);
    if (!Number.isFinite(numeric) || numeric <= 0) {
      return "";
    }
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(numeric * 1000));
  }

  function selectedChoice(name) {
    const checked = document.querySelector(`[data-choice-group="${name}"] input:checked`);
    return checked ? checked.value : "";
  }

  function setChoice(name, value) {
    document.querySelectorAll(`[data-choice-group="${name}"] input`).forEach((input) => {
      input.checked = input.value === value;
    });
  }

  function positiveNumberOrNull(input) {
    const value = String(input.value || "").trim();
    return value && /^\d+$/.test(value) && Number(value) > 0 ? Number(value) : null;
  }

  function currentFields() {
    return {
      title: form.title.value.trim(),
      work_type: selectedChoice("workType"),
      genre: form.direction.value.trim(),
      description: form.description.value,
      body_text: form.body.value,
      external_url: form.url.value.trim() || null,
      has_ready_file: state.attachments.length > 0,
      details: {
        form_version: 2,
        fandom: selectedChoice("workType") === "ФФ" ? form.fandom.value.trim() : "",
        size_category: selectedChoice("sizeCategory"),
        rating: form.rating.value.trim(),
        completion: selectedChoice("completion"),
        size_words: selectedChoice("completion") === "завершен" ? positiveNumberOrNull(form.sizeWords) : null,
        pages: selectedChoice("completion") === "завершен" ? positiveNumberOrNull(form.pages) : null,
        parts: selectedChoice("completion") === "завершен" ? positiveNumberOrNull(form.parts) : null,
        extra_links: form.extraLinks.value.split(/\r?\n/).map((v) => v.trim()).filter(Boolean),
        characters: form.characters.value,
        notes: form.notes.value,
        visual_mode: selectedChoice("visualMode"),
        palette_colors: selectedChoice("visualMode") === "palette" ? state.paletteColors.slice() : [],
      },
    };
  }

  function updateDynamicSections() {
    const ff = selectedChoice("workType") === "ФФ";
    const finished = selectedChoice("completion") === "завершен";
    const visual = selectedChoice("visualMode");
    $("fandomField").classList.toggle("hidden", !ff);
    $("finishedFields").classList.toggle("hidden", !finished);
    $("paletteFields").classList.toggle("hidden", visual !== "palette");
    $("imageFields").classList.toggle("hidden", visual !== "image");
    if (visual === "palette") { renderIkfCover(); }
    form.fandom.required = ff;
    [form.sizeWords, form.pages, form.parts].forEach((field) => { field.required = finished; });
  }

  function clearImagePreview() {
    if (state.imagePreviewUrl) {
      URL.revokeObjectURL(state.imagePreviewUrl);
      state.imagePreviewUrl = null;
    }
    $("imagePreview").removeAttribute("src");
    $("imagePreview").classList.add("hidden");
  }

  function fillForm(submission) {
    const revision = submission && submission.revision ? submission.revision : {};
    const details = revision.details || {};
    form.title.value = revision.title || "";
    setChoice("workType", ["ФФ", "Оридж"].includes(revision.work_type) ? revision.work_type : "");
    setChoice("sizeCategory", details.size_category || "");
    setChoice("completion", details.completion || "");
    setChoice("visualMode", details.visual_mode || "palette");
    form.direction.value = revision.genre || "";
    form.fandom.value = details.fandom || "";
    form.rating.value = details.rating || "";
    form.sizeWords.value = details.size_words || "";
    form.pages.value = details.pages || "";
    form.parts.value = details.parts || "";
    form.extraLinks.value = Array.isArray(details.extra_links) ? details.extra_links.join("\n") : "";
    form.characters.value = details.characters || "";
    form.notes.value = details.notes || "";
    state.paletteColors = Array.isArray(details.palette_colors) && details.palette_colors.length === 4
      ? details.palette_colors.slice()
      : ["#5B67F1", "#EF86AC", "#78CFBC", "#FFC777"];
    form.description.value = revision.description || "";
    form.body.value = revision.body_text || "";
    form.url.value = revision.external_url || "";
    clearImagePreview();
    updateDynamicSections();
    renderPalette();
    refreshCounters();
    updateImageStatus();
  }

  function updateImageStatus() {
    const images = state.attachments.filter((file) => ["png", "jpeg"].includes(file.file_class));
    $("imageUploadStatus").textContent = images.length
      ? `Картинка загружена: ${images.map((file) => file.filename).join(", ")}`
      : "Загрузи свою обложку. Это платная опция: использование подтвердит владелец после оплаты.";
  }

  function markChanged() {
    refreshCounters();
    renderIkfCover();
    state.editSequence += 1;
    setSaveState("Есть несохраненные изменения");
    updateActionAvailability();
    // Create an actual server-side draft on the FIRST keystroke,
    // not after a later click on "Save" or after the user leaves.
    void autosave({ immediate: !state.current });
  }

  function renderPalette() {
    const preview = $("palettePreview");
    const editors = $("paletteEditors");
    preview.replaceChildren();
    editors.replaceChildren();
    state.paletteColors.forEach((hex, index) => {
      const swatch = document.createElement("div");
      swatch.className = "palette-swatch";
      swatch.style.backgroundColor = hex;
      swatch.setAttribute("aria-label", `Цвет ${index + 1}: ${hex}`);
      swatch.title = hex;
      preview.append(swatch);

      const editor = document.createElement("div");
      editor.className = "palette-editor";
      const caption = document.createElement("span");
      caption.textContent = `Цвет ${index + 1}`;
      const picker = document.createElement("input");
      picker.type = "color";
      picker.value = hex;
      picker.setAttribute("aria-label", `Выбрать цвет ${index + 1}`);
      const channels = [];
      for (let channel = 0; channel < 3; channel += 1) {
        const label = document.createElement("label");
        label.textContent = ["R", "G", "B"][channel];
        const input = document.createElement("input");
        input.type = "number"; input.min = "0"; input.max = "255"; input.step = "1";
        input.inputMode = "numeric";
        input.value = parseInt(hex.slice(channel * 2 + 1, channel * 2 + 3), 16);
        input.setAttribute("aria-label", `Цвет ${index + 1}, канал ${label.textContent}`);
        input.addEventListener("change", () => {
          const values = channels.map((node) => Number(node.value));
          if (values.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) {
            setEditorError("Каналы RGB должны быть целыми числами от 0 до 255.");
            return;
          }
          setEditorError("");
          state.paletteColors[index] = `#${values.map((n) => n.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
          picker.value = state.paletteColors[index];
          swatch.style.backgroundColor = picker.value;
          markChanged();
        });
        channels.push(input);
        label.append(input);
        editor.append(label);
      }
      picker.addEventListener("input", () => {
        state.paletteColors[index] = picker.value.toUpperCase();
        swatch.style.backgroundColor = picker.value;
        channels.forEach((node, c) => {
          node.value = parseInt(picker.value.slice(c * 2 + 1, c * 2 + 3), 16);
        });
        markChanged();
      });
      editor.prepend(caption, picker);
      editors.append(editor);
    });
    renderIkfCover();
  }

  function renderIkfCover() {
    const canvas = $("ikfCoverPreview");
    if (!canvas || !canvas.getContext) { return; }
    const ctx = canvas.getContext("2d");
    if (!ctx) { return; }
    const [first, second, third, fourth] = state.paletteColors;
    const { width, height } = canvas;
    // One deliberately rough, fixed layout: a color-combination aid for
    // the artist, NOT a choice of finished cover designs.
    const [offset, ribbon, slope] = [390, 0, 0];
    const polygon = (points, color) => {
      ctx.beginPath();
      ctx.moveTo(...points[0]);
      points.slice(1).forEach((point) => ctx.lineTo(...point));
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();
    };
    ctx.fillStyle = first;
    ctx.fillRect(0, 0, width, height);
    polygon([[0, 40], [offset + 30, 335 + slope], [0, height]], second);
    polygon([[0, height], [0, 320 + slope], [offset + 20, 294 + slope],
      [width, 130 + ribbon], [width, height]], third);
    polygon([[0, height], [0, height - 35 - Math.trunc(ribbon / 4)],
      [offset + 10, 335 + slope], [offset + 75, 347 + slope],
      [width, 237 + Math.trunc(ribbon / 2)], [width, height]], fourth);
    const foreground = (hex) => {
      const color = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255);
      const light = color.map((part) => part <= 0.04045
        ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4);
      const luma = light[0] * 0.2126 + light[1] * 0.7152 + light[2] * 0.0722;
      return luma > 0.24 ? "#161923" : "#F5F3ED";
    };
    ctx.strokeStyle = foreground(first);
    ctx.lineWidth = 3;
    for (let index = 0; index < 3; index += 1) {
      const delta = index * 7;
      ctx.beginPath();
      ctx.moveTo(offset + 5 + delta, 333 + slope + delta);
      ctx.lineTo(width, 88 + ribbon + delta);
      ctx.stroke();
    }
    ctx.fillStyle = foreground(fourth);
    ctx.textAlign = "right";
    ctx.textBaseline = "alphabetic";
    const rawTitle = form.title.value.trim().replace(/\s+/g, " ") || "Название произведения";
    const title = "«" + rawTitle.slice(0, 200) + "»";
    let lines = [];
    let size = 64;
    for (; size >= 26; size -= 2) {
      ctx.font = `italic ${size}px Georgia, "Times New Roman", serif`;
      lines = [];
      let line = "";
      title.split(" ").forEach((word) => {
        const candidate = (line + " " + word).trim();
        if (ctx.measureText(candidate).width <= 770) {
          line = candidate;
        } else {
          if (line) { lines.push(line); }
          line = word;
        }
      });
      if (line) { lines.push(line); }
      if (lines.length <= 2 && lines.every((value) => ctx.measureText(value).width <= 770)) {
        break;
      }
    }
    if (size < 26) {
      size = 24;
      ctx.font = `italic ${size}px Georgia, "Times New Roman", serif`;
      let cut = title;
      while (cut.length > 1 && ctx.measureText(cut).width > 770) {
        cut = cut.slice(0, -2) + "…";
      }
      lines = [cut];
    }
    let y = height - 20 - ((lines.length - 1) * (size + 9));
    lines.forEach((line) => {
      ctx.fillText(line, width - 36, y);
      y += size + 9;
    });
  }

  function validateReadyForm() {
    const values = currentFields();
    if (!values.title || !values.work_type || !values.details.size_category ||
        !values.genre || !values.details.rating || !values.details.completion ||
        !values.description.trim() || !values.external_url || !values.external_url.startsWith("https://") ||
        !values.details.visual_mode) {
      throw new Error("Заполни все обязательные строки анкеты и выбери варианты галочками.");
    }
    if (values.work_type === "ФФ" && !values.details.fandom) {
      throw new Error("Для фанфика укажи фандом.");
    }
    if (values.details.completion === "завершен" &&
        [values.details.size_words, values.details.pages, values.details.parts].some((n) => !n)) {
      throw new Error("Для завершенной работы укажи размер в словах, страницы и части.");
    }
    if (values.details.extra_links.length > 5) {
      throw new Error("Дополнительных ссылок может быть не больше пяти.");
    }
    if (values.details.visual_mode === "image" &&
        !state.attachments.some((file) => ["png", "jpeg"].includes(file.file_class))) {
      throw new Error("Добавь картинку PNG или JPEG.");
    }
    return values;
  }

  function refreshCounters() {
    $("titleCounter").textContent = String(form.title.value.length);
    $("descriptionCounter").textContent = String(form.description.value.length);
    $("bodyCounter").textContent = String(form.body.value.length);
  }

  function statusMatches(item) {
    const status = item.status;
    if (state.filter === "all") {
      return true;
    }
    if (state.filter === "finished") {
      return ["APPROVED", "REJECTED", "WITHDRAWN", "CHANGES_REQUESTED"].includes(status);
    }
    if (state.filter === "SUBMITTED") {
      return ["SUBMITTED", "IN_REVIEW"].includes(status);
    }
    return status === state.filter;
  }

  function renderWorkspace() {
    const list = $("workList");
    list.replaceChildren();

    const filtered = state.items.filter(statusMatches);
    $("emptyState").classList.toggle("hidden", filtered.length !== 0);

    filtered.forEach((item) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "work-card";
      card.addEventListener("click", () => openSubmission(item.id));

      const top = document.createElement("div");
      top.className = "work-card-top";

      const body = document.createElement("div");
      body.append(
        safeTextElement("p", statusLabels[item.status] || item.status, "status-pill"),
        safeTextElement("h2", item.title || "Без названия")
      );

      const updated = safeTextElement("span", formatDate(item.updated_at), "muted");
      top.append(body, updated);
      card.append(top);
      list.append(card);
    });
  }

  function renderAttachments() {
    const list = $("attachmentList");
    list.replaceChildren();

    if (!state.attachments.length) {
      list.append(safeTextElement("p", "Файлы пока не добавлены.", "muted"));
      return;
    }

    state.attachments.forEach((file) => {
      const row = document.createElement("div");
      row.className = "attachment";

      const label = safeTextElement(
        "span",
        `${file.filename || file.safe_filename || "Файл"} · ${Math.max(1, Math.round(Number(file.size || file.byte_size || 0) / 1024))} КБ`,
        "attachment-name"
      );
      row.append(label);

      if (state.current && state.current.status === "DRAFT" && file.id) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "button button-ghost";
        remove.textContent = "Удалить";
        remove.addEventListener("click", () => deleteAttachment(file.id));
        row.append(remove);
      }
      list.append(row);
    });
  }

  function renderTimeline() {
    const timeline = $("timeline");
    timeline.replaceChildren();

    if (!state.timeline.length) {
      timeline.append(safeTextElement("p", "История появится после первого действия.", "muted"));
      return;
    }

    state.timeline.forEach((event) => {
      const row = document.createElement("div");
      row.className = "timeline-item";
      row.append(safeTextElement("span", "", "timeline-dot"));

      const content = document.createElement("div");
      content.append(
        safeTextElement(
          "div",
          event.label || event.event_type || "Событие",
          "timeline-title"
        ),
        safeTextElement("div", formatDate(event.created_at), "timeline-time")
      );
      row.append(content);
      timeline.append(row);
    });
  }

  function renderDetail() {
    if (!state.current) {
      return;
    }
    const revision = state.current.revision || {};
    $("detailStatus").textContent = statusLabels[state.current.status] || state.current.status;
    $("detailTitle").textContent = revision.title || "Без названия";
    $("detailDescription").textContent = revision.description || "";

    const meta = $("detailMeta");
    meta.replaceChildren();
    [
      ["Формат", revision.work_type || "—"],
      ["Размер", (revision.details || {}).size_category || "—"],
      ["Направление", revision.genre || "—"],
      ["Рейтинг", (revision.details || {}).rating || "—"],
      ["Статус произведения", (revision.details || {}).completion || "—"],
      ["Фандом", (revision.details || {}).fandom || "—"],
      ["Размер (слов)", (revision.details || {}).size_words || "—"],
      ["Страниц", (revision.details || {}).pages || "—"],
      ["Частей", (revision.details || {}).parts || "—"],
      ["Фикбук", revision.external_url || "—"],
      ["Персонажи", (revision.details || {}).characters || "—"],
      ["Примечания", (revision.details || {}).notes || "—"],
      ["Другие ссылки", ((revision.details || {}).extra_links || []).join(" · ") || "—"],
      ["Визуал", (revision.details || {}).visual_mode === "palette"
        ? ((revision.details || {}).palette_colors || []).join(" · ")
        : ((revision.details || {}).visual_mode === "image" ? "Картинка во вложениях" : "—")],
      ["Редакция", `№${revision.revision_number || 1}`],
      ["Обновлено", formatDate(state.current.updated_at) || "—"],
    ].forEach(([name, value]) => {
      meta.append(
        safeTextElement("dt", name),
        safeTextElement("dd", value)
      );
    });

    const feedback = $("detailFeedback");
    const feedbackText = state.current.feedback || "";
    feedback.textContent = feedbackText;
    feedback.classList.toggle("hidden", !feedbackText);

    $("revisionButton").classList.toggle(
      "hidden",
      state.current.status !== "CHANGES_REQUESTED"
    );
    $("withdrawButton").classList.toggle(
      "hidden",
      ["APPROVED", "REJECTED", "WITHDRAWN"].includes(state.current.status)
    );
    renderTimeline();
  }

  async function loadWorkspace() {
    const payload = await api("/api/writers/submissions");
    state.items = Array.isArray(payload.items) ? payload.items : [];
    renderWorkspace();
  }

  async function loadHistory(submissionId) {
    try {
      const payload = await api(
        `/api/writers/submissions/${encodeURIComponent(submissionId)}/history`
      );
      state.timeline = Array.isArray(payload.items) ? payload.items : [];
    } catch (_error) {
      state.timeline = [];
    }
  }

  async function openSubmission(submissionId) {
    setBusy(true);
    try {
      const payload = await api(
        `/api/writers/submissions/${encodeURIComponent(submissionId)}`
      );
      state.current = payload.submission;
      state.attachments = Array.isArray(payload.files) ? payload.files : [];
      await loadHistory(submissionId);

      if (state.current.status === "DRAFT") {
        fillForm(state.current);
        state.editSequence = 0;
        state.savedSequence = 0;
        state.createKey = null;
        setReloadRequired(false);
        renderAttachments();
        show("editor");
      } else {
        renderDetail();
        show("detail");
      }
    } catch (error) {
      setBanner(error.message || "Не удалось открыть работу.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function newDraft() {
    // Header action remains visible when the editor is open. Never discard
    // the previous unsaved form by starting a new one while saving/failing.
    if (!views.editor.classList.contains("hidden")) {
      const saved = await autosave({ immediate: true });
      if (!saved) {
        setEditorError("Черновик не сохранился. Повтори сохранение, прежде чем создавать новую работу.");
        return;
      }
    }
    clearTimeout(state.autosaveTimer);
    state.current = null;
    state.attachments = [];
    state.timeline = [];
    state.editSequence = 0;
    state.savedSequence = 0;
    state.createKey = makeIdempotencyKey("create");
    setReloadRequired(false);
    fillForm({ revision: {} });
    renderAttachments();
    setEditorError("");
    setSaveState("Новый черновик — сохранится при вводе текста");
    show("editor");
    form.title.focus();
  }

  async function createDraft(fields) {
    // If POST succeeded but the network response was lost, reusing the same
    // key on retry prevents a duplicate draft in PostgreSQL.
    state.createKey ||= makeIdempotencyKey("create");
    const payload = await api("/api/writers/submissions", {
      method: "POST",
      headers: { "Idempotency-Key": state.createKey },
      json: fields,
    });
    state.current = payload.submission;
    state.createKey = null;
    return state.current;
  }

  async function autosave({ immediate = false } = {}) {
    if (state.reloadRequired) { return false; }
    clearTimeout(state.autosaveTimer);
    state.autosaveTimer = null;
    if (!immediate) {
      state.autosaveTimer = window.setTimeout(
        () => { void autosave({ immediate: true }); },
        700
      );
      return true;
    }
    if (state.uploadPending) {
      setSaveState("Изменения ждут завершения загрузки");
      return false;
    }
    if (state.savePromise) { return state.savePromise; }
    if (state.current && state.editSequence <= state.savedSequence) {
      return true;
    }

    state.autosavePending = true;
    updateActionAvailability();
    setSaveState("Сохраняю…");
    setEditorError("");
    const saving = (async () => {
      try {
        // Changes typed while an earlier request is in-flight must be sent
        // in the NEXT request, never silently counted as already saved.
        while (!state.current || state.editSequence > state.savedSequence) {
          const fields = currentFields();
          const capturedSequence = state.editSequence;
          if (!state.current) {
            await createDraft(fields);
          } else {
            const payload = await api(
              `/api/writers/submissions/${encodeURIComponent(state.current.id)}`,
              {
                method: "PATCH",
                json: { ...fields, expected_version: state.current.version },
              }
            );
            state.current = payload.submission;
          }
          state.savedSequence = capturedSequence;
          if (state.editSequence > capturedSequence) {
            setSaveState("Сохраняю новые изменения…");
          }
        }
        setSaveState("Сохранено на сервере");
        return true;
      } catch (error) {
        setSaveState("Не сохранено");
        if (error.code === "conflict") {
          setEditorError("Черновик обновился в другой вкладке. Скопируй важный текст перед перезагрузкой.");
        } else {
          setEditorError(
            "Не удалось сохранить черновик: " +
            (error.message || "Проверь подключение к интернету.") +
            ". Не закрывай форму и повтори сохранение."
          );
        }
        return false;
      } finally {
        state.autosavePending = false;
        updateActionAvailability();
      }
    })();
    state.savePromise = saving;
    try {
      return await saving;
    } finally {
      if (state.savePromise === saving) { state.savePromise = null; }
    }
  }

  async function submitCurrent() {
    if (state.reloadRequired || state.autosavePending || state.uploadPending) {
      return;
    }
    setEditorError("");
    try {
      validateReadyForm();
    } catch (error) {
      setEditorError(error.message);
      return;
    }
    const saved = await autosave({ immediate: true });
    if (!saved || !state.current || state.reloadRequired) {
      return;
    }

    setBusy(true);
    try {
      const payload = await api(
        `/api/writers/submissions/${encodeURIComponent(state.current.id)}/submit`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": makeIdempotencyKey("submit"),
          },
          json: {
            expected_version: state.current.version,
          },
        }
      );
      state.current = payload.submission;
      await loadHistory(state.current.id);
      await loadWorkspace();
      renderDetail();
      show("detail");
      if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.notificationOccurred("success");
      }
    } catch (error) {
      setEditorError(error.message || "Не удалось отправить работу.");
    } finally {
      setBusy(false);
    }
  }

  const MAX_PHOTO_BYTES = 20 * 1024 * 1024;
  const SUPPORTED_PHOTO_EXT = /\.(png|jpe?g|heic|heif|webp)$/i;

  function isPhotoFile(file) {
    return /^image\//i.test(file.type || "") ||
      SUPPORTED_PHOTO_EXT.test(file.name || "");
  }

  async function convertMobilePhoto(file) {
    const filename = String(file.name || "image");
    const extension = filename.split(".").pop().toLowerCase();
    if (extension === "png" || extension === "jpg" || extension === "jpeg") {
      const mime = extension === "png" ? "image/png" : "image/jpeg";
      // Some Telegram/iOS pickers return an empty or octet-stream MIME.
      // Preserve bytes but set the correct metadata. The backend still
      // verifies the actual signature, extension and content.
      return new File([file], filename, { type: mime });
    }
    if (!["heic", "heif", "webp"].includes(extension) &&
        !/image\/(heic|heif|webp)/i.test(file.type || "")) {
      throw new Error("Для изображения поддерживаются JPG, PNG, WebP и HEIC.");
    }
    if (file.size > MAX_PHOTO_BYTES) {
      throw new Error("Фото больше 20 МБ. Уменьши его перед отправкой.");
    }

    // HEIC/WebP are converted in the Telegram WebView to a publishable JPEG.
    // The server stores a genuine JPEG; no permissive binary MIME fallback.
    const objectUrl = URL.createObjectURL(file);
    let image;
    try {
      image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(
          "Не удалось открыть фото HEIC/WebP. На iPhone выбери «Наиболее совместимый» " +
          "формат камеры или сохрани изображение как JPG."
        ));
        img.src = objectUrl;
      });
      if (!image.naturalWidth || !image.naturalHeight) {
        throw new Error("Изображение не содержит допустимых размеров.");
      }
      const scale = Math.min(1, 2560 / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const context = canvas.getContext("2d");
      if (!context) { throw new Error("Устройство не поддерживает обработку изображений."); }
      context.fillStyle = "#FFFFFF";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
      if (!blob || blob.type !== "image/jpeg") {
        throw new Error("Не удалось преобразовать фотографию в JPEG.");
      }
      const jpegName = filename.replace(/\.[^.]+$/, "") + ".jpg";
      return new File([blob], jpegName, { type: "image/jpeg" });
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  }

  async function prepareUploadFile(file) {
    if (!file || !file.name) {
      throw new Error("Не удалось прочитать выбранный файл.");
    }
    const extension = file.name.split(".").pop().toLowerCase();
    if (isPhotoFile(file)) {
      const photo = await convertMobilePhoto(file);
      if (photo.size > MAX_PHOTO_BYTES) {
        throw new Error("Изображение слишком большое. Максимум 20 МБ.");
      }
      return photo;
    }
    if (!["pdf", "docx", "txt"].includes(extension)) {
      throw new Error("Доступны PDF, DOCX, TXT и фотографии JPG, PNG, WebP, HEIC.");
    }
    if (file.size > MAX_PHOTO_BYTES) {
      throw new Error("Файл больше 20 МБ.");
    }
    return file;
  }


  function updateUploadProgress(source, label, percent = null, phase = "active") {
    const prefix = source === "image" ? "image" : "file";
    const panel = $(`${prefix}UploadProgress`);
    const bar = $(`${prefix}UploadBar`);
    panel.classList.remove("hidden");
    panel.dataset.phase = phase;
    $(`${prefix}UploadLabel`).textContent = label;
    if (Number.isFinite(percent)) {
      const bounded = Math.max(0, Math.min(100, Math.round(percent)));
      bar.value = bounded;
      $(`${prefix}UploadPercent`).textContent = `${bounded}%`;
    } else {
      // Native HTML <progress> indeterminate while preparing, saving a
      // draft or waiting for Telegram storage/DB confirmation.
      bar.removeAttribute("value");
      $(`${prefix}UploadPercent`).textContent =
        phase === "error" ? "Ошибка" : "Подождите";
    }
  }

  function sendFileWithProgress(path, formData, onProgress, onTransferComplete) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", path, true);
      xhr.withCredentials = true; // Preserve the Telegram Mini App session.
      xhr.timeout = 300000;
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(event.loaded / event.total * 100);
        } else {
          onProgress(null);
        }
      });
      xhr.upload.addEventListener("load", () => {
        // 100% is only the network upload, not confirmation of persistence.
        onTransferComplete();
      });
      xhr.onload = () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (_error) {
          data = null;
        }
        if (xhr.status === 409) {
          setReloadRequired(true);
          const conflict = new Error("Черновик изменился в другой вкладке.");
          conflict.code = "conflict";
          reject(conflict);
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          const message = data && (data.message || data.error);
          const error = new Error(message || `Ошибка сервера: HTTP ${xhr.status}`);
          error.status = xhr.status;
          error.code = data && data.error ? data.error : "request_failed";
          reject(error);
          return;
        }
        if (!data || !data.id) {
          reject(new Error("Сервер не подтвердил сохранение файла."));
          return;
        }
        resolve(data);
      };
      xhr.onerror = () => reject(new Error(
        "Не удалось передать файл: соединение прервано. Проверь интернет."
      ));
      xhr.ontimeout = () => reject(new Error(
        "Сервер слишком долго обрабатывает файл. Проверь список вложений перед повтором."
      ));
      xhr.onabort = () => reject(new Error("Загрузка файла прервана."));
      try {
        xhr.send(formData);
      } catch (error) {
        reject(error);
      }
    });
  }

  async function uploadFiles(fileList, source = "file") {
    if (state.reloadRequired || state.uploadPending || state.uploadPreparing) {
      return null;
    }
    const originals = Array.from(fileList || []);
    if (!originals.length) { return null; }
    const slots = Math.max(0, 3 - state.attachments.length);
    if (originals.length > slots) {
      const reason = slots
        ? `Можно добавить еще только ${slots} файл(а). Максимум 3 вложения.`
        : "Достигнут лимит: максимум 3 вложения. Удали старый файл перед загрузкой.";
      setEditorError(reason);
      updateUploadProgress(source, reason, null, "error");
      return null;
    }
    state.uploadPreparing = true;
    updateActionAvailability();
    setEditorError("");
    let firstUploaded = null;
    let savedCount = 0;
    try {
      updateUploadProgress(source, "Подготавливаю выбранные файлы…");
      const files = await Promise.all(originals.map(prepareUploadFile));
      updateUploadProgress(source, "Сохраняю черновик перед загрузкой…");
      const saved = await autosave({ immediate: true });
      if (!saved || !state.current || state.reloadRequired) {
        throw new Error("Не удалось сохранить черновик. Файл не отправлен.");
      }

      state.uploadPending = true;
      updateActionAvailability();
      setSaveState("Загружаю файлы…");
      for (const [index, file] of files.entries()) {
        const position = `${index + 1} из ${files.length}`;
        updateUploadProgress(source, `Передаю файл ${position}: ${file.name}`, 0);
        const formData = new FormData();
        formData.append("file", file, file.name);
        const uploaded = await sendFileWithProgress(
          `/api/writers/submissions/${encodeURIComponent(state.current.id)}/files`,
          formData,
          (percent) => updateUploadProgress(
            source, `Передаю файл ${position}: ${file.name}`,
            percent,
          ),
          () => updateUploadProgress(
            source, `Файл ${position} передан. Сохраняем на сервере…`,
            100, "processing",
          ),
        );
        if (!firstUploaded) { firstUploaded = file; }
        state.attachments.push(uploaded);
        savedCount += 1;
        updateUploadProgress(
          source, `Проверяю сохранение файла ${position}…`, null, "processing",
        );
        try {
          const refreshed = await api(
            `/api/writers/submissions/${encodeURIComponent(state.current.id)}`
          );
          state.current = refreshed.submission;
        } catch (_error) {
          // POST 201 means persisted already. Never tell the author to retry
          // uploading blindly, as that would create duplicates.
          setReloadRequired(true);
          throw new Error(
            "Файл сохранен, но не удалось обновить анкету. Открой ее повторно, " +
            "чтобы убедиться, что вложение есть в списке."
          );
        }
        renderAttachments();
        updateImageStatus();
        updateUploadProgress(
          source, `✓ Файл добавлен (${position}): ${file.name}`, 100, "done",
        );
      }
      setSaveState("Файлы сохранены");
      return firstUploaded;
    } catch (error) {
      const reason = error.message || "Не удалось загрузить файл.";
      const message = savedCount
        ? `Сохранено файлов: ${savedCount} из ${originals.length}. ${reason}`
        : reason;
      setEditorError(message);
      setSaveState(savedCount ? "Не все файлы загружены" : "Ошибка загрузки файла");
      updateUploadProgress(source, message, null, "error");
      return null;
    } finally {
      renderAttachments();
      updateImageStatus();
      state.uploadPending = false;
      state.uploadPreparing = false;
      $("fileInput").value = "";
      updateActionAvailability();
      if (state.editSequence > state.savedSequence && !state.reloadRequired) {
        void autosave();
      }
    }
  }

  async function deleteAttachment(fileId) {
    if (!state.current || state.reloadRequired) {
      return;
    }
    if (!await autosave({ immediate: true })) { return; }
    state.uploadPending = true;
    updateActionAvailability();
    try {
      await api(
        `/api/writers/submissions/${encodeURIComponent(state.current.id)}/files/${encodeURIComponent(fileId)}`,
        { method: "DELETE" }
      );
      state.attachments = state.attachments.filter((file) => file.id !== fileId);
      const refreshed = await api(
        `/api/writers/submissions/${encodeURIComponent(state.current.id)}`
      );
      state.current = refreshed.submission;
      renderAttachments();
      updateImageStatus();
    } catch (error) {
      setEditorError(error.message || "Не удалось удалить файл.");
    } finally {
      state.uploadPending = false;
      updateActionAvailability();
      if (state.editSequence > state.savedSequence && !state.reloadRequired) {
        void autosave();
      }
    }
  }

  async function withdrawCurrent() {
    if (!state.current) {
      return;
    }
    setBusy(true);
    try {
      const payload = await api(
        `/api/writers/submissions/${encodeURIComponent(state.current.id)}/withdraw`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": makeIdempotencyKey("withdraw"),
          },
          json: {},
        }
      );
      state.current = payload.submission;
      await loadWorkspace();
      await loadHistory(state.current.id);
      renderDetail();
    } catch (error) {
      setBanner(error.message || "Не удалось отозвать работу.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function createRevision() {
    if (!state.current) {
      return;
    }
    setBusy(true);
    try {
      const payload = await api(
        `/api/writers/submissions/${encodeURIComponent(state.current.id)}/revisions`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": makeIdempotencyKey("revision"),
          },
          json: {},
        }
      );
      state.current = payload.submission;
      state.attachments = [];
      fillForm(state.current);
      state.editSequence = 0;
      state.savedSequence = 0;
      state.createKey = null;
      renderAttachments();
      setReloadRequired(false);
      show("editor");
    } catch (error) {
      setBanner(error.message || "Не удалось создать редакцию.", "error");
    } finally {
      setBusy(false);
    }
  }

  function bindEvents() {
    $("newWorkButton").addEventListener("click", () => { void newDraft(); });
    $("editorBackButton").addEventListener("click", async () => {
      const saved = await autosave({ immediate: true });
      if (!saved || state.reloadRequired) {
        setEditorError("Черновик не сохранен. Исправь ошибку и повтори сохранение, прежде чем выходить.");
        return;
      }
      try {
        await loadWorkspace();
        show("workspace");
      } catch (error) {
        setEditorError(error.message || "Не удалось обновить список черновиков.");
      }
    });
    $("detailBackButton").addEventListener("click", async () => {
      await loadWorkspace();
      show("workspace");
    });
    $("saveButton").addEventListener("click", () => autosave({ immediate: true }));
    $("submitButton").addEventListener("click", submitCurrent);
    $("fileInput").addEventListener("change", (event) => {
      void uploadFiles(event.target.files, "file");
    });
    $("imageInput").addEventListener("change", async (event) => {
      const file = (event.target.files || [])[0];
      if (!file) { return; }
      const uploaded = await uploadFiles([file], "image");
      $("imageInput").value = "";
      if (uploaded) {
        clearImagePreview();
        state.imagePreviewUrl = URL.createObjectURL(uploaded);
        $("imagePreview").src = state.imagePreviewUrl;
        $("imagePreview").classList.remove("hidden");
      }
    });
    $("withdrawButton").addEventListener("click", withdrawCurrent);
    $("revisionButton").addEventListener("click", createRevision);
    $("reloadButton").addEventListener("click", () => {
      if (state.current) {
        openSubmission(state.current.id);
      }
    });

    Object.values(form).forEach((field) => {
      field.addEventListener("input", markChanged);
    });
    document.querySelectorAll("[data-choice-group] input").forEach((input) => {
      input.addEventListener("change", () => {
        // Checkbox semantics: clicking an active choice removes its checkmark.
        // Choosing another value clears the previous choice in this group.
        if (input.checked) {
          input.closest("[data-choice-group]").querySelectorAll("input").forEach((other) => {
            if (other !== input) { other.checked = false; }
          });
        }
        updateDynamicSections();
        markChanged();
      });
    });

    document.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((item) => {
          item.classList.toggle("active", item === button);
        });
        state.filter = button.dataset.filter || "all";
        renderWorkspace();
      });
    });
  }

  async function boot() {
    bindEvents();
    renderAttachments();
    renderPalette();
    updateDynamicSections();
    setBusy(true);
    try {
      await bootstrapSession();
      await loadWorkspace();
      show("workspace");
      setBanner("");
    } catch (error) {
      setBanner(
        error.message || "Не удалось открыть Writers Submission.",
        "error"
      );
    } finally {
      setBusy(false);
      updateActionAvailability();
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
