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
    uploadPending: false,
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
    const locked = state.reloadRequired || state.autosavePending || state.uploadPending;
    $("submitButton").disabled = locked;
    $("saveButton").disabled = state.reloadRequired || state.autosavePending;
    $("fileInput").disabled = locked;
    $("imageInput").disabled = locked;
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
    setChoice("visualMode", details.visual_mode || "");
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
      : "Загрузи PNG или JPEG. Изображение сохранится во вложениях и дойдет до модераторов.";
  }

  function markChanged() {
    refreshCounters();
    setSaveState("Есть изменения");
    if (state.current) { autosave(); }
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

  function newDraft() {
    clearTimeout(state.autosaveTimer);
    state.current = null;
    state.attachments = [];
    state.timeline = [];
    setReloadRequired(false);
    fillForm({ revision: {} });
    renderAttachments();
    setEditorError("");
    setSaveState("Новый черновик");
    show("editor");
    form.title.focus();
  }

  async function createDraft() {
    const payload = await api("/api/writers/submissions", {
      method: "POST",
      headers: {
        "Idempotency-Key": makeIdempotencyKey("create"),
      },
      json: currentFields(),
    });
    state.current = payload.submission;
    setSaveState("Сохранено");
    return state.current;
  }

  async function autosave({ immediate = false } = {}) {
    if (state.reloadRequired) {
      return false;
    }

    clearTimeout(state.autosaveTimer);
    if (!immediate) {
      state.autosaveTimer = window.setTimeout(
        () => { void autosave({ immediate: true }); },
        700
      );
      return true;
    }

    // Selecting a photo can race with an autosave triggered by text input.
    // Wait for the existing mutation, rather than dropping the upload or
    // issuing a second concurrent draft create/PATCH.
    if (state.savePromise) {
      return state.savePromise;
    }
    state.autosavePending = true;
    updateActionAvailability();
    setSaveState("Сохраняю…");
    setEditorError("");
    const saving = (async () => {
      try {
        if (!state.current) {
          await createDraft();
        } else {
          const payload = await api(
            `/api/writers/submissions/${encodeURIComponent(state.current.id)}`,
            {
              method: "PATCH",
              json: {
                ...currentFields(),
                expected_version: state.current.version,
              },
            }
          );
          state.current = payload.submission;
        }
        setSaveState("Сохранено");
        return true;
      } catch (error) {
        if (error.code === "conflict") {
          setSaveState("Нужна перезагрузка");
        } else {
          setSaveState("Не сохранено");
          setEditorError(error.message || "Не удалось сохранить черновик.");
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
      if (state.savePromise === saving) {
        state.savePromise = null;
      }
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
  const SUPPORTED_PHOTO_EXT = /\\.(png|jpe?g|heic|heif|webp)$/i;

  function isPhotoFile(file) {
    return /^image\\//i.test(file.type || "") ||
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
        !/image\\/(heic|heif|webp)/i.test(file.type || "")) {
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
      const jpegName = filename.replace(/\\.[^.]+$/, "") + ".jpg";
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

  async function uploadFiles(fileList) {
    if (state.reloadRequired || state.uploadPending) {
      return null;
    }
    const originals = Array.from(fileList || []);
    if (!originals.length) { return null; }
    const slots = Math.max(0, 3 - state.attachments.length);
    if (originals.length > slots) {
      setEditorError(slots
        ? `Можно добавить еще только ${slots} файл(а). Максимум 3 вложения.`
        : "Достигнут лимит: максимум 3 вложения. Удали старый файл перед загрузкой.");
      return null;
    }

    setEditorError("");
    let files;
    try {
      files = await Promise.all(originals.map(prepareUploadFile));
    } catch (error) {
      setEditorError(error.message || "Не удалось подготовить фотографию.");
      return null;
    }

    // Always flush pending text changes before mutating the file count/version.
    const saved = await autosave({ immediate: true });
    if (!saved || !state.current || state.reloadRequired) { return null; }

    state.uploadPending = true;
    updateActionAvailability();
    setSaveState("Загружаю файлы…");
    let firstUploaded = null;
    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file, file.name);
        const uploaded = await api(
          `/api/writers/submissions/${encodeURIComponent(state.current.id)}/files`,
          { method: "POST", body: formData }
        );
        if (!firstUploaded) { firstUploaded = file; }
        state.attachments.push(uploaded);
        const refreshed = await api(
          `/api/writers/submissions/${encodeURIComponent(state.current.id)}`
        );
        state.current = refreshed.submission;
      }
      setSaveState("Файлы сохранены");
      return firstUploaded;
    } catch (error) {
      setEditorError(error.message || "Не удалось загрузить файл. Попробуй еще раз.");
      setSaveState("Ошибка загрузки файла");
      return null;
    } finally {
      renderAttachments();
      updateImageStatus();
      state.uploadPending = false;
      $("fileInput").value = "";
      updateActionAvailability();
    }
  }

  async function deleteAttachment(fileId) {
    if (!state.current || state.reloadRequired) {
      return;
    }
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
    $("newWorkButton").addEventListener("click", newDraft);
    $("editorBackButton").addEventListener("click", async () => {
      clearTimeout(state.autosaveTimer);
      if (state.current && !state.reloadRequired) {
        await autosave({ immediate: true });
      }
      await loadWorkspace();
      show("workspace");
    });
    $("detailBackButton").addEventListener("click", async () => {
      await loadWorkspace();
      show("workspace");
    });
    $("saveButton").addEventListener("click", () => autosave({ immediate: true }));
    $("submitButton").addEventListener("click", submitCurrent);
    $("fileInput").addEventListener("change", (event) => uploadFiles(event.target.files));
    $("imageInput").addEventListener("change", async (event) => {
      const file = (event.target.files || [])[0];
      if (!file) { return; }
      const uploaded = await uploadFiles([file]);
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
