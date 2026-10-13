(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const state = {
    items: [],
    filter: "all",
    current: null,
    attachments: [],
    pendingFiles: [],
    coverFileId: null,
    timeline: [],
    autosaveTimer: null,
    autosavePending: false,
    savePromise: null,
    editSequence: 0,
    savedSequence: 0,
    createKey: null,
    submitKey: null,
    uploadPending: false,
    uploadPreparing: false,
    submitting: false,
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
    mainGenre1: $("mainGenre1Input"),
    mainGenre2: $("mainGenre2Input"),
    mainGenre3: $("mainGenre3Input"),
    warnings: $("warningsInput"),
    sketchProfile: $("sketchProfileUrlInput"),
    sketchArticle: $("sketchArticleUrlInput"),
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
      state.uploadPending || state.uploadPreparing || state.submitting;
    $("submitButton").disabled = locked;
    $("saveButton").disabled = locked;
    $("fileInput").disabled = locked;
    $("imageInput").disabled = locked;
    if (tg) {
      const unsaved = state.autosavePending ||
        state.editSequence > state.savedSequence ||
        state.pendingFiles.length > 0;
      if (unsaved && typeof tg.enableClosingConfirmation === "function") {
        tg.enableClosingConfirmation();
      } else if (!unsaved && typeof tg.disableClosingConfirmation === "function") {
        tg.disableClosingConfirmation();
      }
    }
  }

  // Keep protocol failures separate from validation failures. Do not expose
  // HTML error pages, proxy traces or internal exceptions to the author.
  function requestFailure(status, code, message = "", reference = "") {
    const details = String(message || "").trim();
    const lookup = {
      unauthorized: "Сессия Telegram истекла. Закрой и снова открой Mini App через бота.",
      not_found: "Черновик не найден или больше недоступен. Вернись к списку работ.",
      conflict: "Черновик изменился в другой вкладке. Проверь сохраненные данные перед повтором.",
      payload_too_large: "Файл или запрос слишком большой. Для вложения предел — 20 МБ.",
      rate_limited: "Слишком много запросов. Подожди минуту и повтори действие.",
      expected_version_required: "Версия черновика не передана. Переоткрой работу.",
      idempotency_key_required: "Не передан идентификатор операции. Переоткрой Mini App.",
    };
    let explanation = lookup[code];
    // Back-end validators use stable technical messages. Translate the most
    // frequent ones so authors know exactly which field to correct.
    if (status === 422 && details) {
      const validationHints = [
        [/work description needs at least three sentences/i, "Описание: нужно не менее трех законченных предложений с точкой, ! или ?."],
        [/work description: max 110 words/i, "Описание: допускается максимум 110 слов."],
        [/sketch description: max 35 words and 230 characters/i, "Описание зарисовки: максимум 35 слов и 230 символов."],
        [/maximum file count reached/i, "К этой работе уже прикреплено максимальное число файлов (3). Удали лишний файл."],
        [/file size exceeds maximum/i, "Размер вложения превышает лимит 20 МБ. Уменьши файл."],
        [/file is empty/i, "Выбранный файл пустой. Выбери другой."],
        [/unsupported file extension/i, "Формат файла не поддерживается. Допустимы PNG, JPG, PDF, DOCX и TXT."],
        [/file extension and MIME type do not match|file extension, MIME type and detected file class do not match/i,
          "Расширение файла не соответствует его содержимому. Сохрани файл в исходном формате и выбери заново."],
        [/PNG header is invalid|JPEG ending is invalid/i, "Изображение повреждено или не соответствует формату. Сохрани его заново как PNG или JPG."],
        [/TXT must be valid UTF-8 text/i, "Текстовый файл должен быть сохранен в кодировке UTF-8."],
        [/DOCX (structure|container) is invalid/i, "Документ DOCX поврежден. Пересохрани документ и прикрепи заново."],
        [/submission payload requires an HTTPS link, body text or a ready file/i,
          "Добавь ссылку HTTPS на работу, текст или хотя бы один сохраненный файл."],
        [/external_url must be an HTTPS URL/i, "Ссылка на произведение должна начинаться с https:// и не содержать логин или пароль."],
        [/main_genres must have 1–3 entries/i, "Выбери от одного до трех основных жанров."],
        [/warnings require yes selection|provide at least one content warning/i,
          "Проверь предупреждения: выбери «Да» и укажи хотя бы одно предупреждение."],
      ];
      const hint = validationHints.find(([pattern]) => pattern.test(details));
      if (hint) explanation = hint[1];
    }
    if (code === "telegram_file_staging_unavailable") {
      explanation = details || "Не удалось передать файл в закрытое Telegram-хранилище. Повтори загрузку позднее.";
    }
    if (code === "eligibility_unavailable") {
      explanation = details || "Telegram временно не может проверить подписку. Повтори отправку позднее.";
    }
    if (!explanation && status === 401) explanation = lookup.unauthorized;
    if (!explanation && status === 403) explanation = details || "Нет разрешения на эту операцию.";
    if (!explanation && status === 404) explanation = lookup.not_found;
    if (!explanation && status === 409) explanation = lookup.conflict;
    if (!explanation && status === 413) explanation = lookup.payload_too_large;
    if (!explanation && status === 422) explanation = details || "Данные анкеты не прошли проверку.";
    if (!explanation && status === 429) explanation = lookup.rate_limited;
    if (!explanation && status >= 500) {
      explanation = "Ошибка на стороне сервера. Текст черновика останется в форме; повтори позднее.";
      if (reference) explanation += " Код обращения: " + String(reference).slice(0, 32) + ".";
    }
    if (!explanation) explanation = "Запрос не выполнен (HTTP " + status + "). Повтори действие.";
    const error = new Error(explanation);
    error.code = code || "request_failed";
    error.status = status;
    return error;
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

    const timeoutMs = options.timeoutMs || 30000;
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timeout = controller
      ? window.setTimeout(() => controller.abort(), timeoutMs)
      : null;
    if (controller) request.signal = controller.signal;
    let response;
    try {
      response = await fetch(path, request);
    } catch (cause) {
      const timedOut = controller && controller.signal.aborted;
      const error = new Error(timedOut
        ? "Сервер не ответил за 30 секунд. Операция могла выполниться — проверь статус перед повтором."
        : "Нет соединения с сервером. Проверь интернет и повтори действие.");
      error.code = timedOut ? "request_timeout" : "network_error";
      throw error;
    } finally {
      if (timeout !== null) window.clearTimeout(timeout);
    }
    if (response.status === 204) return null;

    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (response.status === 409) {
      setReloadRequired(true);
    }
    if (!response.ok) {
      throw requestFailure(
        response.status,
        payload && payload.error,
        payload && payload.message,
        payload && payload.reference
      );
    }
    if (!payload || typeof payload !== "object") {
      const error = new Error(
        "Сервер ответил без подтверждения. Проверь состояние черновика перед повтором."
      );
      error.code = "invalid_response";
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
    const sketch = selectedChoice("workType") === "Зарисовка";
    const genres = [form.mainGenre1, form.mainGenre2, form.mainGenre3]
      .map((field) => field.value.trim()).filter(Boolean);
    const warningList = form.warnings.value.split(/[,;\n]+/).map((value) => value.trim()).filter(Boolean);
    return {
      title: form.title.value.trim(),
      work_type: selectedChoice("workType"),
      genre: form.direction.value.trim(),
      description: form.description.value,
      body_text: sketch ? "" : form.body.value,
      external_url: sketch ? (form.sketchArticle.value.trim() || null) : (form.url.value.trim() || null),
      has_ready_file: state.attachments.length > 0,
      details: {
        form_version: 3,
        fandom: (selectedChoice("workType") === "ФФ" || (sketch && selectedChoice("sketchOrigin") === "Фандом"))
          ? form.fandom.value.trim() : "",
        sketch_origin: sketch ? selectedChoice("sketchOrigin") : "",
        sketch_profile_url: sketch ? form.sketchProfile.value.trim() : "",
        sketch_article_url: sketch ? form.sketchArticle.value.trim() : "",
        main_genres: genres,
        warnings_present: selectedChoice("warningsPresent"),
        warnings: selectedChoice("warningsPresent") === "yes" ? warningList : [],
        size_category: sketch ? "" : selectedChoice("sizeCategory"),
        rating: form.rating.value.trim(),
        completion: sketch ? "" : selectedChoice("completion"),
        size_words: !sketch && selectedChoice("completion") === "завершен" ? positiveNumberOrNull(form.sizeWords) : null,
        pages: !sketch && selectedChoice("completion") === "завершен" ? positiveNumberOrNull(form.pages) : null,
        parts: !sketch && selectedChoice("completion") === "завершен" ? positiveNumberOrNull(form.parts) : null,
        extra_links: sketch ? [] : form.extraLinks.value.split(/\r?\n/).map((v) => v.trim()).filter(Boolean),
        characters: sketch ? "" : form.characters.value,
        notes: sketch ? "" : form.notes.value,
        visual_mode: sketch ? "editorial" : selectedChoice("visualMode"),
        cover_file_id: !sketch && selectedChoice("visualMode") === "image" ? state.coverFileId : null,
        palette_colors: !sketch && selectedChoice("visualMode") === "palette" ? state.paletteColors.slice() : [],
      },
    };
  }

  function updateDynamicSections() {
    const sketch = selectedChoice("workType") === "Зарисовка";
    const ff = selectedChoice("workType") === "ФФ" || (sketch && selectedChoice("sketchOrigin") === "Фандом");
    const finished = selectedChoice("completion") === "завершен";
    const visual = selectedChoice("visualMode");
    $("fandomField").classList.toggle("hidden", !ff);
    $("sketchOriginField").classList.toggle("hidden", !sketch);
    $("sketchLinksFields").classList.toggle("hidden", !sketch);
    $("sketchCoverNotice").classList.toggle("hidden", !sketch);
    ["sizeCategorySection", "completionSection", "regularWorkUrlField", "extraLinksField",
     "charactersField", "notesField", "visualModeSection", "bodySection"].forEach((id) => {
      $(id).classList.toggle("hidden", sketch);
    });
    $("attachmentsSection").classList.toggle("hidden", sketch && !state.attachments.length);
    $("warningsField").classList.toggle("hidden", selectedChoice("warningsPresent") !== "yes");
    $("descriptionHint").textContent = sketch
      ? "Для зарисовки: до 35 слов и 230 символов (примерно три короткие строки)."
      : "Для большой работы: минимум 3 предложения, до 110 слов.";
    form.description.maxLength = sketch ? 230 : 2000;
    form.sketchProfile.required = sketch;
    form.sketchArticle.required = sketch;
    form.url.required = !sketch;
    $("finishedFields").classList.toggle("hidden", sketch || !finished);
    $("paletteFields").classList.toggle("hidden", sketch || visual !== "palette");
    $("imageFields").classList.toggle("hidden", sketch || visual !== "image");
    if ((sketch || visual !== "image") && state.pendingFiles.some((entry) => entry.source === "image")) {
      state.pendingFiles = state.pendingFiles.filter((entry) => entry.source !== "image");
      clearImagePreview();
      renderAttachments();
      updateImageStatus();
    }
    if (!sketch && visual === "palette") { renderIkfCover(); }
    form.fandom.required = ff;
    [form.sizeWords, form.pages, form.parts].forEach((field) => { field.required = !sketch && finished; });
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
    setChoice("workType", ["ФФ", "Оридж", "Зарисовка"].includes(revision.work_type) ? revision.work_type : "");
    setChoice("sketchOrigin", details.sketch_origin || "");
    setChoice("warningsPresent", details.warnings_present || "");
    [form.mainGenre1, form.mainGenre2, form.mainGenre3].forEach((field, index) => {
      field.value = Array.isArray(details.main_genres) ? (details.main_genres[index] || "") : "";
    });
    form.warnings.value = Array.isArray(details.warnings) ? details.warnings.join(", ") : "";
    form.sketchProfile.value = details.sketch_profile_url || "";
    form.sketchArticle.value = details.sketch_article_url || (revision.work_type === "Зарисовка" ? revision.external_url || "" : "");
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
    state.coverFileId = details.cover_file_id || null;
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
    const pending = state.pendingFiles.filter((entry) => entry.source === "image");
    const uploaded = state.attachments.filter((file) =>
      ["png", "jpeg"].includes(file.file_class)
    );
    $("imageUploadStatus").textContent = pending.length
      ? `Выбрана фотография: ${pending.map((entry) => entry.file.name).join(", ")}. Загружаем в черновик; при ошибке повтори выбор файла.`
      : uploaded.length
        ? `Изображение уже прикреплено к этой заявке: ${uploaded.map((file) => file.filename).join(", ")}`
        : "Выбери фото. До отправки анкеты оно останется только на устройстве. Своя обложка — платная опция.";
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

  function countDescriptionSentences(text) {
    const value = String(text || "").replace(/\s+/gu, " ").trim();
    const endings = /[.!?]+[»”"’)\]]*(?=\s|$)/gu;
    let found = 0;
    let last = 0;
    let match;
    while ((match = endings.exec(value)) !== null) {
      const part = value.slice(last, match.index)
        .replace(/https?:\/\/\S+|(^|\s)#[^\s]+/gu, " ");
      if (/[\p{L}\p{N}]/u.test(part)) found += 1;
      last = endings.lastIndex;
    }
    return found;
  }

  function countWords(text) {
    return (String(text || "").match(/[\p{L}\p{N}]+(?:[-’'][\p{L}\p{N}]+)*/gu) || []).length;
  }

  function validDomain(value, host) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password &&
        [host, "www." + host].includes(url.hostname.toLowerCase());
    } catch (_) {
      return false;
    }
  }

  function validateReadyForm() {
    const values = currentFields();
    const sketch = values.work_type === "Зарисовка";
    const details = values.details;
    if (!values.title || !["ФФ", "Оридж", "Зарисовка"].includes(values.work_type) ||
        !values.genre || !details.rating || !values.description.trim()) {
      throw new Error("Заполни название, формат, направление, рейтинг и описание.");
    }
    if (!sketch && (!details.size_category || !details.completion || !details.visual_mode)) {
      throw new Error("Для большой работы выбери размер, статус и оформление.");
    }
    if (sketch) {
      if (!details.sketch_origin || (details.sketch_origin === "Фандом" && !details.fandom)) {
        throw new Error("Для зарисовки укажи фандом или ориджинал.");
      }
      if (!validDomain(details.sketch_profile_url, "ficbook.net") ||
          !validDomain(details.sketch_article_url, "telegra.ph")) {
        throw new Error("Нужны HTTPS-ссылки на профиль Фикбука и статью telegra.ph.");
      }
    } else {
      if (!values.external_url || !values.external_url.startsWith("https://")) {
        throw new Error("Укажи HTTPS-ссылку на произведение на Фикбуке.");
      }
      if (values.work_type === "ФФ" && !details.fandom) {
        throw new Error("Для фанфика укажи фандом.");
      }
      if (details.completion === "завершен" &&
          [details.size_words, details.pages, details.parts].some((n) => !n)) {
        throw new Error("Для завершенной работы укажи размер, страницы и части.");
      }
    }
    if (!details.main_genres.length || details.main_genres.length > 3 ||
        new Set(details.main_genres.map((value) => value.toLocaleLowerCase("ru"))).size !== details.main_genres.length) {
      throw new Error("Укажи от одного до трех разных основных жанров.");
    }
    if (!["yes", "no"].includes(details.warnings_present)) {
      throw new Error("Обязательно ответь на вопрос о предупреждениях: Да или Нет.");
    }
    if (details.warnings_present === "yes" && (!details.warnings.length || details.warnings.length > 10)) {
      throw new Error("При выборе «Да» перечисли от 1 до 10 предупреждений.");
    }
    const wordCount = countWords(values.description);
    if (sketch) {
      if (wordCount > 35 || values.description.length > 230) {
        throw new Error("Описание зарисовки: не больше 35 слов и 230 символов.");
      }
    } else {
      const sentenceCount = countDescriptionSentences(values.description);
      if (sentenceCount < 3) {
        throw new Error(
          "Нужно минимум 3 законченных предложения (с точкой, ! или ?). " +
          "Хештеги и незаконченная последняя фраза не считаются предложениями."
        );
      }
      if (wordCount > 110) {
        throw new Error("Описание большой работы: максимум 110 слов.");
      }
    }
    if (!sketch && details.extra_links.length > 5) {
      throw new Error("Дополнительных ссылок может быть не больше пяти.");
    }
    if (!sketch && details.visual_mode === "image" &&
        !state.pendingFiles.some((entry) => entry.source === "image") &&
        !state.attachments.some((file) => ["png", "jpeg"].includes(file.file_class))) {
      throw new Error("Выбери свою фотографию для платной обложки.");
    }
    return values;
  }

  function refreshCounters() {
    $("titleCounter").textContent = String(form.title.value.length);
    const wordCount = countWords(form.description.value);
    const sketch = selectedChoice("workType") === "Зарисовка";
    const sentences = countDescriptionSentences(form.description.value);
    $("descriptionCounter").textContent = sketch
      ? wordCount + "/35 слов · " + form.description.value.length + "/230 символов"
      : wordCount + "/110 слов · " + sentences + "/3 завершенных предложения" +
        (sentences < 3 ? " (нужно еще " + (3 - sentences) + ")" : " ✓");
    const slots = [form.mainGenre1, form.mainGenre2, form.mainGenre3];
    const amount = slots.filter((field) => field.value.trim()).length;
    const progress = $("genreProgress");
    progress.setAttribute("aria-label", "Заполнено жанров: " + amount + " из 3");
    Array.from(progress.children).forEach((segment, index) => {
      segment.classList.toggle("filled", index < amount);
    });
    $("genreProgressLabel").textContent = "Заполнено: " + amount + " из 3";
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

  function removePendingFile(localId) {
    const found = state.pendingFiles.find((entry) => entry.id === localId);
    if (!found || state.uploadPending || state.uploadPreparing) { return; }
    state.pendingFiles = state.pendingFiles.filter((entry) => entry.id !== localId);
    if (found.source === "image") {
      clearImagePreview();
      $("imageInput").value = "";
    }
    renderAttachments();
    updateImageStatus();
    updateActionAvailability();
  }

  function clearPendingFiles() {
    state.pendingFiles = [];
    state.coverFileId = null;
    clearImagePreview();
    $("fileInput").value = "";
    $("imageInput").value = "";
    renderAttachments();
    updateImageStatus();
    updateActionAvailability();
  }

  function renderAttachments() {
    const list = $("attachmentList");
    list.replaceChildren();
    const all = [
      ...state.attachments.map((file) => ({
        file, id: file.id, pending: false,
        name: file.filename || file.safe_filename || "Файл",
        size: file.size || file.byte_size || 0,
      })),
      ...state.pendingFiles.map((entry) => ({
        file: entry, id: entry.id, pending: true, source: entry.source,
        name: entry.file.name, size: entry.file.size,
      })),
    ];
    if (!all.length) {
      list.append(safeTextElement("p", "Файлы не выбраны. Можно приложить их позже, перед отправкой анкеты.", "muted"));
      return;
    }
    all.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "attachment";
      const classification = entry.pending && entry.source === "image"
        ? "Своя обложка (платно)"
        : "Вложение";
      const label = safeTextElement(
        "span",
        `${classification}: ${entry.name} · ${Math.max(1, Math.round(Number(entry.size) / 1024))} КБ · ${entry.pending ? "только на устройстве, не отправлен" : "на сервере"}`,
        "attachment-name",
      );
      row.append(label);
      if (entry.pending || (state.current && state.current.status === "DRAFT" && entry.id)) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "button button-ghost";
        remove.textContent = "Убрать";
        remove.addEventListener("click", () =>
          entry.pending ? removePendingFile(entry.id) : deleteAttachment(entry.id)
        );
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
      ["Основа зарисовки", (revision.details || {}).sketch_origin || "—"],
      ["Основные жанры", ((revision.details || {}).main_genres || []).join(" • ") || "—"],
      ["Предупреждения", (revision.details || {}).warnings_present === "no"
        ? "Нет" : ((revision.details || {}).warnings || []).join(" × ") || "—"],
      ["Профиль Фикбука", (revision.details || {}).sketch_profile_url || "—"],
      ["Статья Telegraph", (revision.details || {}).sketch_article_url || "—"],
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
      if (state.pendingFiles.length) {
        setBanner("Локальные файлы не сохраняются в черновике. После повторного открытия их нужно выбрать заново.", "warning");
      }
      const preserveSubmitKey = Boolean(
        state.current && payload.submission &&
        String(state.current.id) === String(payload.submission.id) &&
        state.current.revision && payload.submission.revision &&
        String(state.current.revision.id) === String(payload.submission.revision.id)
      );
      state.pendingFiles = [];
      clearImagePreview();
      state.current = payload.submission;
      if (!preserveSubmitKey) state.submitKey = null;
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
      if (state.pendingFiles.length && !window.confirm(
        "Выбранные файлы еще не отправлены и будут убраны из формы. Создать новую работу?"
      )) { return; }
    }
    clearTimeout(state.autosaveTimer);
    state.current = null;
    state.attachments = [];
    clearPendingFiles();
    state.timeline = [];
    state.editSequence = 0;
    state.savedSequence = 0;
    state.createKey = makeIdempotencyKey("create");
    state.submitKey = null;
    setReloadRequired(false);
    fillForm({ revision: {} });
    renderAttachments();
    setEditorError("");
    setSaveState("Новый черновик — сохранится при вводе текста");
    show("editor");
    form.title.focus();
  }

  function verifiedDraftResponse(payload, expectedId = null, previousVersion = null) {
    const item = payload && payload.submission;
    if (!item || typeof item.id !== "string" || !item.id ||
        item.status !== "DRAFT" || !Number.isSafeInteger(Number(item.version)) ||
        Number(item.version) < 1 ||
        (expectedId !== null && String(item.id) !== String(expectedId)) ||
        (previousVersion !== null && Number(item.version) <= Number(previousVersion))) {
      const error = new Error(
        "Сервер вернул неполное подтверждение черновика. Не создавай новую заявку: проверь список работ."
      );
      error.code = "invalid_response";
      throw error;
    }
    return item;
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
    // A 2xx without a valid draft must never consume the idempotency key.
    const created = verifiedDraftResponse(payload);
    state.current = created;
    state.createKey = null;
    state.submitKey = null;
    return state.current;
  }

  function draftMatchesSavedRequest(revision, fields) {
    // Detect a PATCH that committed before its HTTP response disappeared.
    // Comparing *all* submitted fields prevents silently adopting another
    // editor's revision and overwriting their changes.
    if (!revision || !fields || !fields.details || !revision.details) return false;
    const clean = (value) => String(value == null ? "" : value)
      .replace(/\s+/gu, " ").trim();
    for (const key of ["title", "work_type", "genre", "description"]) {
      if (clean(revision[key]) !== clean(fields[key])) return false;
    }
    if (String(revision.body_text || "").trim() !== String(fields.body_text || "").trim()) return false;
    if (clean(revision.external_url) !== clean(fields.external_url)) return false;
    for (const [key, value] of Object.entries(fields.details)) {
      const remote = revision.details[key];
      if (Array.isArray(value)) {
        if (!Array.isArray(remote)) return false;
        const normalizeList = (items) => items.map((item) =>
          key === "palette_colors" ? clean(item).toUpperCase() : clean(item)
        ).filter(Boolean);
        let expected = normalizeList(value);
        if (key === "extra_links") expected = [...new Set(expected)];
        if (JSON.stringify(expected) !== JSON.stringify(normalizeList(remote))) return false;
      } else if (typeof value === "number" || value === null) {
        if (value !== (remote === undefined ? null : remote)) return false;
      } else if (clean(value) !== clean(remote)) {
        return false;
      }
    }
    return true;
  }

  async function reconcileDraftSave(fields, expectedVersion, revisionId, error) {
    const uncertain = ["network_error", "request_timeout", "invalid_response", "conflict"].includes(error.code) ||
      Number(error.status) >= 500;
    if (!uncertain || !state.current) return false;
    try {
      const payload = await api(
        "/api/writers/submissions/" + encodeURIComponent(state.current.id)
      );
      const remote = payload && payload.submission;
      if (!remote || remote.status !== "DRAFT" ||
          Number(remote.version) <= expectedVersion ||
          String(remote.revision && remote.revision.id) !== String(revisionId) ||
          !draftMatchesSavedRequest(remote.revision, fields)) {
        return false;
      }
      state.current = remote;
      setReloadRequired(false);
      setSaveState("Сохранение подтверждено после восстановления связи");
      return true;
    } catch (_verificationFailure) {
      return false;
    }
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
            const expectedVersion = Number(state.current.version);
            const revisionId = state.current.revision && state.current.revision.id;
            try {
              const payload = await api(
                `/api/writers/submissions/${encodeURIComponent(state.current.id)}`,
                {
                  method: "PATCH",
                  json: { ...fields, expected_version: expectedVersion },
                }
              );
              state.current = verifiedDraftResponse(
                payload, state.current.id, expectedVersion
              );
            } catch (error) {
              if (!await reconcileDraftSave(fields, expectedVersion, revisionId, error)) {
                throw error;
              }
            }
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

  async function showAcceptedSubmission(submission) {
    // Once the submit endpoint or a recovery GET confirms acceptance, no
    // subsequent list-refresh error may be represented as a failed submit.
    state.current = submission;
    state.submitKey = null;
    setReloadRequired(false);
    clearPendingFiles();
    await loadHistory(submission.id);
    try {
      await loadWorkspace();
      setBanner("");
    } catch (_error) {
      setBanner("Заявка принята, но список работ пока не обновился. Открой его позже.", "warning");
    }
    renderDetail();
    show("detail");
    if (tg && tg.HapticFeedback) {
      tg.HapticFeedback.notificationOccurred("success");
    }
  }

  async function confirmSubmissionAfterFailure(error) {
    // The POST response may be lost *after* the database transaction commits.
    // Read the actual status instead of inviting a duplicate POST.
    const uncertain = ["network_error", "request_timeout", "invalid_response", "conflict"].includes(error.code) ||
      Number(error.status) >= 500;
    if (!uncertain || !state.current) return false;
    try {
      const payload = await api(
        "/api/writers/submissions/" + encodeURIComponent(state.current.id)
      );
      const received = payload && payload.submission;
      if (!received) throw new Error("Сервер не вернул состояние заявки.");
      if (["SUBMITTED", "IN_REVIEW", "APPROVED", "CHANGES_REQUESTED", "REJECTED"].includes(received.status)) {
        await showAcceptedSubmission(received);
        return true;
      }
      // Keep the original idempotency key on an unconfirmed submission;
      // never replace it with a new key for a retry.
      if (received.status === "DRAFT") {
        state.current = received;
        if (error.code !== "conflict") setReloadRequired(false);
      }
      return false;
    } catch (checkError) {
      setEditorError(
        "Не удалось подтвердить результат отправки: " + checkError.message +
        " Не создавай вторую заявку. Открой черновик и проверь статус в списке работ."
      );
      return true;
    }
  }

  async function saveDraftAndFiles() {
    if (state.reloadRequired || state.autosavePending ||
        state.uploadPending || state.uploadPreparing || state.submitting) return false;
    if (!await autosave({ immediate: true })) return false;
    if (!state.pendingFiles.length) return true;
    // Authors can recover a chosen image even if the initial automatic
    // upload failed because the draft was incomplete or offline.
    return uploadQueuedFiles();
  }

  function lockFormDuringSubmission(locked) {
    if (typeof document === "undefined") return;
    document.querySelectorAll("#editorView input, #editorView textarea, #editorView select")
      .forEach((input) => { input.disabled = Boolean(locked); });
  }

  async function submitCurrent() {
    if (state.reloadRequired || state.autosavePending ||
        state.uploadPending || state.uploadPreparing || state.submitting) return;
    setEditorError("");
    try {
      validateReadyForm();
    } catch (error) {
      setEditorError(error.message);
      return;
    }
    state.submitting = true;
    lockFormDuringSubmission(true);
    updateActionAvailability();
    try {
      const saved = await autosave({ immediate: true });
      if (!saved || !state.current || state.reloadRequired) return;
      if (!await uploadQueuedFiles()) return;
      if (!await autosave({ immediate: true }) || state.reloadRequired) return;
      try {
        validateReadyForm();
      } catch (error) {
        setEditorError(error.message);
        return;
      }
      state.submitKey ||= makeIdempotencyKey("submit");
      setBusy(true);
      try {
        const payload = await api(
          "/api/writers/submissions/" + encodeURIComponent(state.current.id) + "/submit",
          {
            method: "POST",
            headers: { "Idempotency-Key": state.submitKey },
            json: { expected_version: state.current.version },
          }
        );
        const accepted = payload && payload.submission;
        if (!accepted || String(accepted.id) !== String(state.current.id) ||
            !["SUBMITTED", "IN_REVIEW", "APPROVED", "CHANGES_REQUESTED", "REJECTED"].includes(accepted.status)) {
          const error = new Error("Сервер не подтвердил изменение статуса заявки.");
          error.code = "invalid_response";
          throw error;
        }
        await showAcceptedSubmission(accepted);
      } catch (error) {
        if (await confirmSubmissionAfterFailure(error)) return;
        setEditorError(
          "Отправка не подтверждена: " + error.message +
          " Черновик и уже загруженные вложения сохраняются. Не создавай вторую заявку."
        );
      } finally {
        setBusy(false);
      }
    } finally {
      state.submitting = false;
      lockFormDuringSubmission(false);
      updateActionAvailability();
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
        phase === "error" ? "Ошибка" : phase === "waiting" ? "Не отправлено" : "Подождите";
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
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(requestFailure(
            xhr.status,
            data && data.error,
            data && data.message,
            data && data.reference
          ));
          return;
        }
        if (!data || !data.id) {
          const error = new Error("Сервер не подтвердил сохранение файла.");
          error.code = "upload_invalid_response";
          reject(error);
          return;
        }
        resolve(data);
      };
      xhr.onerror = () => {
        const error = new Error("Не удалось передать файл: соединение прервано. Проверь интернет.");
        error.code = "upload_network_error";
        reject(error);
      };
      xhr.ontimeout = () => {
        const error = new Error("Сервер не подтвердил файл за пять минут.");
        error.code = "upload_timeout";
        reject(error);
      };
      xhr.onabort = () => reject(new Error("Загрузка файла прервана."));
      try {
        xhr.send(formData);
      } catch (error) {
        reject(error);
      }
    });
  }


  async function queueFilesLocally(fileList, source = "file") {
    if (state.reloadRequired || state.uploadPending || state.uploadPreparing) return false;
    const originals = Array.from(fileList || []);
    if (!originals.length) return false;
    const existing = state.pendingFiles.filter((entry) => source !== "image" || entry.source !== "image");
    if (originals.length + existing.length + state.attachments.length > 3) {
      setEditorError("Максимум три файла на одну работу. Убери лишнее вложение.");
      return false;
    }
    state.uploadPreparing = true;
    updateActionAvailability();
    try {
      const prepared = [];
      for (const original of originals) {
        const file = await prepareUploadFile(original);
        prepared.push({ id: makeIdempotencyKey("local-file"), file, source });
      }
      state.pendingFiles = existing.concat(prepared);
      if (source === "image") {
        clearImagePreview();
        state.imagePreviewUrl = URL.createObjectURL(prepared[0].file);
        $("imagePreview").src = state.imagePreviewUrl;
        $("imagePreview").classList.remove("hidden");
      }
      renderAttachments();
      updateImageStatus();
      setEditorError("");
      updateUploadProgress(source, "Файл выбран. Загружаем его в черновик…", null, "waiting");
      return true;
    } catch (error) {
      setEditorError(error.message || "Не удалось подготовить файл.");
      updateUploadProgress(source, error.message || "Ошибка выбора файла", null, "error");
      return false;
    } finally {
      state.uploadPreparing = false;
      updateActionAvailability();
    }
  }

  async function persistSelectedFiles(files, source) {
    // Telegram users expect a chosen photo to be attached immediately, not
    // silently left only in WebView RAM until the final submission.
    if (!await queueFilesLocally(files, source)) return;
    updateUploadProgress(source, "Сохраняю черновик перед загрузкой…", null, "processing");
    if (!await autosave({ immediate: true })) {
      updateUploadProgress(source, "Черновик не сохранился — файл пока на устройстве", null, "error");
      return;
    }
    await uploadQueuedFiles();
  }

  async function verifyAmbiguousFileUpload(entry, knownIds) {
    // Never match by filename alone: two distinct photos often have the same
    // name. A new server-side file must match the actual SHA-256 and size.
    const subtle = typeof crypto !== "undefined" && crypto.subtle;
    const digest = subtle && typeof entry.file.arrayBuffer === "function"
      ? await subtle.digest("SHA-256", await entry.file.arrayBuffer())
      : null;
    const hash = digest
      ? Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("")
      : null;
    const payload = await api(
      "/api/writers/submissions/" + encodeURIComponent(state.current.id)
    );
    if (payload && payload.submission) state.current = payload.submission;
    const files = payload && Array.isArray(payload.files) ? payload.files : [];
    if (!hash) return null;
    return files.find((item) =>
      !knownIds.has(String(item.id)) &&
      String(item.sha256 || "").toLowerCase() === hash &&
      Number(item.size) === entry.file.size
    ) || null;
  }

  async function uploadQueuedFiles() {
    if (state.reloadRequired || state.uploadPending || state.uploadPreparing) return false;
    if (!state.pendingFiles.length) return true;
    if (!state.current) {
      setEditorError("Сначала сохрани черновик.");
      return false;
    }
    state.uploadPending = true;
    updateActionAvailability();
    const count = state.pendingFiles.length;
    let done = 0;
    try {
      const ordered = [
        ...state.pendingFiles.filter((entry) => entry.source === "image"),
        ...state.pendingFiles.filter((entry) => entry.source !== "image"),
      ];
      for (const entry of ordered) {
        const file = entry.file, source = entry.source;
        const position = (done + 1) + " из " + count;
        updateUploadProgress(source, "Передаю " + position + ": " + file.name, 0);
        const formData = new FormData();
        formData.append("file", file, file.name);
        const knownIds = new Set(state.attachments.map((item) => String(item.id)));
        let uploaded;
        try {
          uploaded = await sendFileWithProgress(
            "/api/writers/submissions/" + encodeURIComponent(state.current.id) + "/files",
            formData,
            (percent) => updateUploadProgress(source, "Передаю " + position + ": " + file.name, percent),
            () => updateUploadProgress(source, "Передано " + position + ". Сохраняем на сервере…", 100, "processing"),
          );
        } catch (error) {
          const ambiguous = ["upload_network_error", "upload_timeout", "upload_invalid_response"].includes(error.code) ||
            (Number(error.status) >= 500 && error.code !== "telegram_file_staging_unavailable");
          if (!ambiguous) throw error;
          updateUploadProgress(source, "Проверяю, сохранился ли файл на сервере…", null, "processing");
          try {
            uploaded = await verifyAmbiguousFileUpload(entry, knownIds);
          } catch (_checkError) {
            uploaded = null;
          }
          if (!uploaded) {
            setReloadRequired(true);
            throw new Error(
              "Сервер не подтвердил результат загрузки. Не загружай файл повторно вслепую: " +
              "переоткрой черновик, проверь вложения и только затем повтори при необходимости."
            );
          }
        }
        // HTTP 201 confirms storage. Remove locally before GET to avoid
        // re-sending a successfully stored file after interrupted refresh.
        state.pendingFiles = state.pendingFiles.filter((item) => item.id !== entry.id);
        state.attachments.push(uploaded);
        if (source === "image") {
          state.coverFileId = uploaded.id;
          state.editSequence += 1;
        }
        done += 1;
        renderAttachments();
        updateImageStatus();
        updateUploadProgress(source, "Проверяю сохранение " + position + "…", null, "processing");
        try {
          const refreshed = await api(
            "/api/writers/submissions/" + encodeURIComponent(state.current.id)
          );
          state.current = refreshed.submission;
        } catch (_error) {
          setReloadRequired(true);
          throw new Error("Файл уже сохранен, но версия анкеты не обновилась. Открой работу заново и проверь список.");
        }
        updateUploadProgress(source, "✓ Файл сохранен " + position + ": " + file.name, 100, "done");
      }
      setSaveState("Все вложения сохранены");
      return true;
    } catch (error) {
      setEditorError(
        "Сохранено файлов: " + done + " из " + count + ". " +
        (error.message || "Ошибка передачи.") +
        " Заявка не отправлена. Проверь вложения и повтори попытку."
      );
      return false;
    } finally {
      state.uploadPending = false;
      updateActionAvailability();
      renderAttachments();
      updateImageStatus();
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
      if (state.coverFileId === fileId) {
        state.coverFileId = null;
        state.editSequence += 1;
      }
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
      clearPendingFiles();
      fillForm(state.current);
      state.editSequence = 0;
      state.savedSequence = 0;
      state.createKey = null;
    state.submitKey = null;
      renderAttachments();
      setReloadRequired(false);
      show("editor");
    } catch (error) {
      setBanner(error.message || "Не удалось создать редакцию.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function reloadDraftSafely() {
    if (!state.current) return false;
    if (state.autosavePending || state.savePromise ||
        state.uploadPending || state.uploadPreparing || state.submitting) {
      setEditorError("Дождись завершения сохранения или загрузки перед обновлением черновика.");
      return false;
    }
    const hasUnsaved = state.editSequence > state.savedSequence ||
      state.pendingFiles.length > 0;
    if (hasUnsaved && !window.confirm(
      "В черновике есть несохраненный текст или незагруженные файлы. " +
      "Обновление заменит текущие данные сохраненной версией. " +
      "Сначала скопируй важный текст. Все равно обновить?"
    )) return false;
    await openSubmission(state.current.id);
    return true;
  }

  function bindEvents() {
    $("newWorkButton").addEventListener("click", () => { void newDraft(); });
    $("editorBackButton").addEventListener("click", async () => {
      if (state.pendingFiles.length && !window.confirm(
        "Выбранные файлы еще не отправлены. Текст черновика сохранится, но файлы придется выбрать заново. Выйти?"
      )) { return; }
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
    $("saveButton").addEventListener("click", () => { void saveDraftAndFiles(); });
    $("submitButton").addEventListener("click", submitCurrent);
    $("fileInput").addEventListener("change", (event) => {
      const chosen = Array.from(event.target.files || []);
      event.target.value = "";
      if (chosen.length) void persistSelectedFiles(chosen, "file");
    });
    $("imageInput").addEventListener("change", (event) => {
      const chosen = Array.from(event.target.files || []).slice(0, 1);
      event.target.value = "";
      if (chosen.length) void persistSelectedFiles(chosen, "image");
    });
    $("withdrawButton").addEventListener("click", withdrawCurrent);
    $("revisionButton").addEventListener("click", createRevision);
    window.addEventListener("offline", () => {
      setBanner("Нет интернета. Не закрывай Mini App: несохраненный текст и выбранные файлы остаются в этой форме.", "warning");
    });
    window.addEventListener("online", () => {
      if (state.reloadRequired) return;
      if (!views.editor.classList.contains("hidden") &&
          (state.editSequence > state.savedSequence || state.pendingFiles.length)) {
        void saveDraftAndFiles().then((saved) => {
          if (saved) setBanner("");
        });
      } else {
        setBanner("");
      }
    });
    $("reloadButton").addEventListener("click", () => { void reloadDraftSafely(); });

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
