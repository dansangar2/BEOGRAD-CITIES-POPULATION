  function activateConfigTab(editor, name) {
    if (!editor || !name) {
      return;
    }
    var selectedButton = editor.querySelector('[data-config-tab="' + name + '"]');
    if (!selectedButton || selectedButton.hidden || selectedButton.dataset.configDisabledTab === "true") {
      return;
    }
    editor.querySelectorAll("[data-config-tab]").forEach(function (tab) {
      var active = tab === selectedButton;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    editor.querySelectorAll("[data-config-panel]").forEach(function (panel) {
      var active = panel.dataset.configPanel === name;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
      if (active && name === "scraping") {
        var preview = panel.querySelector("[data-generated-config-form]");
        var output = panel.querySelector("[data-generated-config]");
        if (preview && output && String(output.value || "").trim()) {
          preview.hidden = false;
        }
      }
    });
  }

  function initConfigTabs(root) {
    (root || document).querySelectorAll("[data-config-editor]").forEach(function (editor) {
      if (editor.dataset.configTabsReady === "true") {
        return;
      }
      editor.dataset.configTabsReady = "true";
      editor.querySelectorAll("[data-config-tab]").forEach(function (button) {
        button.addEventListener("click", function () {
          activateConfigTab(editor, button.dataset.configTab);
        });
      });
    });
  }


  function manualPageCheckboxValue(row, name, fallback) {
    var hidden = row ? row.querySelector('[name="' + name + '"][data-checkbox-fallback]') : null;
    if (hidden) {
      var label = hidden.closest(".manual-mini-check");
      var toggle = label ? label.querySelector("[data-checkbox-toggle]") : null;
      if (toggle) {
        hidden.value = toggle.checked ? "true" : "false";
      }
      return String(hidden.value || (fallback ? "true" : "false")).toLowerCase() === "true";
    }
    return Boolean(fallback);
  }

  function manualPageRepeatValue(row, name) {
    var field = row ? row.querySelector('[name="' + name + '"]') : null;
    if (!field) {
      return null;
    }
    var value = String(field.value || "").trim();
    if (value === "") {
      return null;
    }
    var parsed = parseInt(value, 10);
    if (!Number.isFinite(parsed) || parsed <= 1) {
      return null;
    }
    return parsed;
  }

  function syncManualPagesJson(form) {
    if (!form || !form.matches || !form.matches(".config-manual-form")) {
      return;
    }
    var target = form.querySelector("[data-manual-pages-json]");
    if (!target) {
      target = document.createElement("input");
      target.type = "hidden";
      target.name = "pages_json";
      target.setAttribute("data-manual-pages-json", "");
      form.appendChild(target);
    }
    var pages = [];
    var tbody = form.querySelector("[data-manual-pages]");
    if (!tbody) {
      target.value = "[]";
      return;
    }
    Array.prototype.slice.call(tbody.querySelectorAll("tr")).forEach(function (row) {
      bindCheckboxFallbacks(row);
      syncPagePathHidden(row, { silent: true });
      var sourceField = row.querySelector('[name="page_source"]');
      var source = sourceField ? String(sourceField.value || "cities").toLowerCase() : "cities";
      if (["cities", "admin", "citiesadmin"].indexOf(source) === -1) {
        source = "cities";
      }
      var paths = pagePathValuesFromRow(row);
      if (!paths.length) {
        return;
      }
      var levelField = row.querySelector('[name="page_force_highest_level"], [name="page_lowest_level"], [name="page_level"]');
      var levelText = levelField ? String(levelField.value || "").trim() : "";
      var parentLevelField = row.querySelector('[name="page_parent_level"]');
      var parentLevelText = parentLevelField ? String(parentLevelField.value || "").trim() : "";
      var page = {
        enabled: manualPageEnabledValue(row),
        source: source,
        path: paths,
        sum_to_root: manualPageCheckboxValue(row, "page_sum_to_root", false),
        include: {
          infosection: manualPageCheckboxValue(row, "page_include_infosection", true)
        }
      };
      if (levelText !== "") {
        page.force_highest_level = levelText;
      }
      if (parentLevelText !== "") {
        page.parent_level = parentLevelText;
      }
      var repeat = {};
      var repeatInfosection = manualPageRepeatValue(row, "page_repeat_infosection");
      if (repeatInfosection !== null) {
        repeat.infosection = repeatInfosection;
      }
      page.include.major_subdivision = manualPageCheckboxValue(row, "page_include_major_subdivision", true);
      if (source === "admin" || source === "citiesadmin") {
        page.include.minor_subdivision = manualPageCheckboxValue(row, "page_include_minor_subdivision", true);
      }
      if (source === "cities" || source === "citiesadmin") {
        page.include.cities = manualPageCheckboxValue(row, "page_include_cities", true);
      }
      var repeatMajor = manualPageRepeatValue(row, "page_repeat_major_subdivision");
      var repeatMinor = manualPageRepeatValue(row, "page_repeat_minor_subdivision");
      var repeatCities = manualPageRepeatValue(row, "page_repeat_cities");
      if (repeatMajor !== null) { repeat.major_subdivision = repeatMajor; }
      if (repeatMinor !== null) { repeat.minor_subdivision = repeatMinor; }
      if (repeatCities !== null) { repeat.cities = repeatCities; }
      if (Object.keys(repeat).length) {
        page.repeat = repeat;
      }
      pages.push(page);
    });
    target.value = JSON.stringify(pages);
  }

  function activeConfigTabName(editor) {
    var active = editor ? editor.querySelector("[data-config-tab].is-active") : null;
    return active ? active.dataset.configTab || "" : "";
  }

  function manualPageEnabledValue(row) {
    var field = row ? row.querySelector("[data-page-enabled]") : null;
    return !field || String(field.value || "true").toLowerCase() !== "false";
  }

  function updateManualPageEnabledState(row) {
    if (!row) {
      return;
    }
    var enabled = manualPageEnabledValue(row);
    row.classList.toggle("is-page-disabled", !enabled);
    var button = row.querySelector("[data-toggle-page-enabled]");
    if (button) {
      var label = enabled
        ? (button.dataset.enabledLabel || "Desactivar bloque")
        : (button.dataset.disabledLabel || "Reactivar bloque");
      var icon = enabled
        ? (button.dataset.enabledIcon || "⏸")
        : (button.dataset.disabledIcon || "↻");
      button.textContent = icon;
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
      button.classList.toggle("is-reactivate", !enabled);
    }
  }

  function notifyConfigFormChanged(element) {
    var form = element && element.closest ? element.closest("[data-config-editor-form]") : null;
    if (form) {
      form.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function configEditorSaveButton(form, submitter) {
    if (submitter && submitter.matches && submitter.matches("button, input[type='submit']")) {
      return submitter;
    }
    return form ? form.querySelector("button[type='submit']:not([data-config-action]), input[type='submit']:not([data-config-action])") : null;
  }

  function setConfigAutosaveStatus(editor, message, state) {
    if (!editor) {
      return;
    }
    editor.querySelectorAll("[data-config-autosave-status]").forEach(function (status) {
      status.textContent = message || "";
      status.dataset.state = state || "";
    });
  }

  function submitConfigEditorSave(form, editor, submitter, options) {
    options = options || {};
    var submittedRevision = form._configAutosaveRevision || 0;
    var button = configEditorSaveButton(form, submitter);
    var previousButtonText = button ? button.textContent : "";
    var title = editor.dataset.saveLabel || (button ? button.textContent : "Guardar configuración");
    var silent = options.silent === true;
    var toast = silent ? null : createConfigToast(title, editor.dataset.savingLabel || "Guardando configuración...", editor);
    if (silent) {
      setConfigAutosaveStatus(editor, editor.dataset.savingLabel || "Guardando configuración...", "saving");
    }
    if (button) {
      button.disabled = true;
      if (editor.dataset.savingLabel) {
        button.textContent = editor.dataset.savingLabel;
      }
    }
    syncManualPagesJson(form);
    fetch(form.action || window.location.href, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfFromForm(form)
      }
    }).then(parseJsonResponseText).then(function (data) {
      if ((form._configAutosaveRevision || 0) === submittedRevision) {
        form._configAutosavePending = false;
        if (data.city_builder) {
          updateManualConfigCities(form, [], data.city_builder);
          // A different legal level can hide existing unifications. Preserve
          // their SQL blocks until the user explicitly edits the city builder.
          var citiesLoaded = form.querySelector("[data-config-cities-loaded]");
          if (citiesLoaded) {
            citiesLoaded.value = "0";
          }
        }
      }
      var message = data.message || editor.dataset.saveSuccessLabel || "Configuración guardada correctamente.";
      if (data.notice) {
        message += " " + data.notice;
      }
      if (toast) {
        setConfigToastStatus(toast, "succeeded", message);
        toast.element.classList.add("is-success");
        scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
      } else {
        setConfigAutosaveStatus(editor, message, "saved");
      }
      if (data.slug && editor.dataset.slug !== data.slug) {
        editor.dataset.slug = data.slug;
      }
      if (data.task_restarted && data.status_url) {
        var taskToast = createConfigToast(data.label || editor.dataset.populatingLabel || "Populando", data.notice || data.label || "", editor);
        if (taskToast.detailLink && data.detail_url) {
          taskToast.detailLink.href = data.detail_url;
          taskToast.detailLink.hidden = false;
        }
        pollConfigTask(data.status_url, taskToast, null, data.summary_url || "", editor, "scrape", data.label || "");
      }
    }).catch(function (error) {
      var errorMessage = error.message || editor.dataset.saveErrorLabel || "No se pudo guardar la configuración.";
      if (toast) {
        setConfigToastStatus(toast, "failed", errorMessage);
        toast.element.classList.add("is-error");
        scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
      } else {
        setConfigAutosaveStatus(editor, errorMessage, "failed");
      }
    }).finally(function () {
      if (button) {
        button.disabled = false;
        if (previousButtonText) {
          button.textContent = previousButtonText;
        }
      }
    });
  }

  function scheduleConfigEditorAutosave(form, editor, options) {
    if (!form || !editor || editor.dataset.mode !== "edit" || editor.dataset.configHydrating === "1") {
      return;
    }
    options = options || {};
    window.clearTimeout(form._configAutosaveTimer);
    form._configAutosaveRevision = (form._configAutosaveRevision || 0) + 1;
    form._configAutosavePending = true;
    setConfigAutosaveStatus(editor, "Cambios pendientes...", "pending");
    form._configAutosaveTimer = window.setTimeout(function () {
      if (editor.dataset.configHydrating === "1") {
        return;
      }
      submitConfigEditorSave(form, editor, null, { silent: true });
    }, options.delay !== undefined ? options.delay : 300);
  }

  function flushConfigEditorAutosave(form, editor) {
    if (!form || !editor || editor.dataset.mode !== "edit" || !form._configAutosavePending) {
      return;
    }
    window.clearTimeout(form._configAutosaveTimer);
    syncManualPagesJson(form);
    var editorModeInput = form.querySelector('input[name="editor_mode"]');
    if (editorModeInput) {
      editorModeInput.value = form.dataset.configEditorForm || editorModeInput.value || "manual";
    }
    var action = form.action || window.location.href;
    var body = new FormData(form);
    if (window.navigator && typeof window.navigator.sendBeacon === "function") {
      try {
        if (window.navigator.sendBeacon(action, body)) {
          form._configAutosavePending = false;
          return;
        }
      } catch (error) {}
    }
    try {
      fetch(action, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        keepalive: true,
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfFromForm(form)
        }
      });
      form._configAutosavePending = false;
    } catch (error) {}
  }

  function initConfigEditorSubmitGuards(editor) {
    if (!editor || editor.dataset.configSubmitGuardsReady === "true") {
      return;
    }
    editor.dataset.configSubmitGuardsReady = "true";
    editor.querySelectorAll("[data-config-editor-form]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        syncManualPagesJson(form);
        var formMode = form.dataset.configEditorForm || "";
        var activeMode = activeConfigTabName(editor);
        if (activeMode !== formMode) {
          event.preventDefault();
          return;
        }
        var editorModeInput = form.querySelector('input[name="editor_mode"]');
        if (editorModeInput) {
          editorModeInput.value = formMode;
        }
      });
      form.addEventListener("submit", function (event) {
        if (event.defaultPrevented || editor.dataset.mode !== "edit") {
          return;
        }
        var submitter = event.submitter || document.activeElement;
        if (submitter && submitter.matches && submitter.matches("[data-config-action]")) {
          return;
        }
        event.preventDefault();
        submitConfigEditorSave(form, editor, submitter);
      });
      form.addEventListener("input", function (event) {
        if (event.target && event.target.closest && event.target.closest("[data-config-action]")) {
          return;
        }
        scheduleConfigEditorAutosave(form, editor);
      });
      form.addEventListener("change", function (event) {
        if (event.target && event.target.closest && event.target.closest("[data-config-action]")) {
          return;
        }
        scheduleConfigEditorAutosave(form, editor, { delay: 0 });
      });
      window.addEventListener("beforeunload", function () {
        flushConfigEditorAutosave(form, editor);
      });
    });
  }

  function setConfigEditorLoading(editor, loading, message, isError) {
    var overlay = editor ? editor.querySelector("[data-config-editor-loading]") : null;
    if (!editor || !overlay) {
      return;
    }
    editor.classList.toggle("is-loading", Boolean(loading));
    editor.classList.toggle("is-loading-error", Boolean(isError));
    overlay.hidden = !loading && !isError;
    overlay.classList.toggle("is-error", Boolean(isError));
    overlay.classList.toggle("is-active", Boolean(loading || isError));
    var text = overlay.querySelector("span:last-child");
    if (text && message) {
      text.textContent = message;
    }
  }

  function loadConfigEditorData(editor, options) {
    options = options || {};
    if (!editor || (!options.force && editor.dataset.editorDataLoaded === "1") || !editor.dataset.editorDataUrl) {
      setConfigEditorLoading(editor, false);
      return Promise.resolve();
    }
    editor.dataset.editorDataLoaded = "1";
    if (!options.silent) {
      setConfigEditorLoading(editor, true, editor.dataset.loadingLabel || "Cargando datos...");
    }
    return fetch(editor.dataset.editorDataUrl, {
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    }).then(parseJsonResponse).then(function (data) {
      if (!data || data.ok === false) {
        throw new Error(editor.dataset.errorLabel || "No se pudieron cargar los datos.");
      }
      editor.dataset.configHydrating = "1";
      try {
        updateConfigEditorFromGeneratedContent(editor, data.content || "", data.manual || null, { updateGeneratedPreview: false });
      } finally {
        delete editor.dataset.configHydrating;
      }
      setConfigEditorLoading(editor, false);
    }).catch(function (error) {
      // The server-rendered form remains usable if dynamic hydration fails.
      setConfigEditorLoading(editor, true, (error && error.message) || editor.dataset.errorLabel || "No se pudieron cargar los datos.", true);
      window.setTimeout(function () {
        setConfigEditorLoading(editor, false);
      }, 3500);
    });
  }

  function refreshConfigEditorData(editor) {
    if (!editor) {
      return Promise.resolve();
    }
    delete editor.dataset.editorDataLoaded;
    return loadConfigEditorData(editor, { force: true, silent: true });
  }

  function initConfigEditor(root) {
    try {
      initConfigTabs(root);
    } catch (error) {
      window.console && window.console.error && window.console.error("Error inicializando pesta\u00f1as de configuraci\u00f3n", error);
    }
    (root || document).querySelectorAll("[data-config-editor]").forEach(function (editor) {
      if (editor.dataset.configEditorReady === "true") {
        return;
      }
      editor.dataset.configEditorReady = "true";
      var sourceScript = editor.querySelector("[data-source-entities-json]");
      var sourceEntities = [];
      try {
        sourceEntities = JSON.parse(sourceScript ? sourceScript.textContent : "[]") || [];
      } catch (error) {
        sourceEntities = [];
      }
      try {
        editor.dataset.configHydrating = "1";
        initConfigEditorSubmitGuards(editor);
        initManualPages(editor);
        initManualAssetOverrides(editor);
        initCityTransfer(editor, sourceEntities);
        initConfigGenerator(editor);
        initAiLoginLinks(editor);
      } catch (error) {
        window.console && window.console.error && window.console.error("Error inicializando configuraci\u00f3n", error);
      } finally {
        delete editor.dataset.configHydrating;
        loadConfigEditorData(editor);
      }
      var params = new URLSearchParams(window.location.search || "");
      var tab = params.get("tab") || editor.dataset.restoreTab || "";
      if (tab) {
        activateConfigTab(editor, tab);
      }
    });
  }

  function pagePathValuesFromRow(row) {
    var values = [];
    if (!row) {
      return values;
    }
    row.querySelectorAll("[data-page-path-input]").forEach(function (input) {
      var value = (input.value || "").trim();
      if (value && values.indexOf(value) === -1) {
        values.push(value);
      }
    });
    return values;
  }

  function syncPagePathHidden(row, options) {
    options = options || {};
    if (!row) {
      return;
    }
    var hidden = row.querySelector("[data-page-path-hidden]");
    if (!hidden) {
      return;
    }
    var nextValue = pagePathValuesFromRow(row).join("\n");
    var changed = hidden.value !== nextValue;
    hidden.value = nextValue;
    if (changed && !options.silent) {
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function createPagePathBox(row, value) {
    var list = row ? row.querySelector("[data-page-path-list]") : null;
    if (!list) {
      return null;
    }
    var wrapper = document.createElement("div");
    wrapper.className = "page-path-row";
    wrapper.setAttribute("data-page-path-row", "");

    var input = document.createElement("input");
    input.type = "text";
    input.setAttribute("data-page-path-input", "");
    input.placeholder = "admin";
    input.value = value || "";
    wrapper.appendChild(input);

    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "quiet danger-text";
    remove.setAttribute("data-remove-page-path", "");
    remove.setAttribute("aria-label", "Quitar ruta");
    remove.textContent = "×";
    wrapper.appendChild(remove);

    list.appendChild(wrapper);
    input.addEventListener("input", function () { syncPagePathHidden(row); });
    input.addEventListener("change", function () { syncPagePathHidden(row); });
    remove.addEventListener("click", function () {
      if (list.querySelectorAll("[data-page-path-row]").length > 1) {
        wrapper.remove();
      } else {
        input.value = "";
      }
      syncPagePathHidden(row);
    });
    return wrapper;
  }

  function setPagePathBoxes(row, paths) {
    var list = row ? row.querySelector("[data-page-path-list]") : null;
    if (!list) {
      return;
    }
    list.innerHTML = "";
    var values = Array.isArray(paths) ? paths : [];
    if (!values.length) {
      values = [""];
    }
    values.forEach(function (path) {
      createPagePathBox(row, path || "");
    });
    syncPagePathHidden(row);
  }

  function splitPagePathsText(text) {
    var raw = String(text || "");
    if (!raw.trim()) {
      return [];
    }
    return raw.split(/\r?\n/).map(function (value) {
      return value.trim();
    }).filter(Boolean);
  }

  function bindPagePathControls(row) {
    if (!row || row.dataset.pagePathReady === "true") {
      return;
    }
    row.dataset.pagePathReady = "true";
    row.querySelectorAll("[data-page-path-input]").forEach(function (input) {
      input.addEventListener("input", function () { syncPagePathHidden(row); });
      input.addEventListener("change", function () { syncPagePathHidden(row); });
    });
    row.querySelectorAll("[data-remove-page-path]").forEach(function (remove) {
      remove.addEventListener("click", function () {
        var pathRows = row.querySelectorAll("[data-page-path-row]");
        var wrapper = remove.closest("[data-page-path-row]");
        var input = wrapper ? wrapper.querySelector("[data-page-path-input]") : null;
        if (pathRows.length > 1 && wrapper) {
          wrapper.remove();
        } else if (input) {
          input.value = "";
        }
        syncPagePathHidden(row);
      });
    });
    var addPath = row.querySelector("[data-add-page-path]");
    if (addPath) {
      addPath.addEventListener("click", function () {
        var created = createPagePathBox(row, "");
        syncPagePathHidden(row);
        var input = created ? created.querySelector("[data-page-path-input]") : null;
        if (input) {
          input.focus();
        }
      });
    }
    var hidden = row.querySelector("[data-page-path-hidden]");
    if (hidden && !pagePathValuesFromRow(row).length && hidden.value) {
      setPagePathBoxes(row, splitPagePathsText(hidden.value));
    } else {
      syncPagePathHidden(row);
    }
  }

  function syncManualPageRowOrder(tbody) {
    if (!tbody) {
      return;
    }
    Array.prototype.slice.call(tbody.querySelectorAll("tr")).forEach(function (row, index) {
      row.dataset.pageOrder = String(index + 1);
      row.querySelectorAll("[data-page-path-hidden]").forEach(function (hidden) {
        var pageRow = hidden.closest("tr");
        if (pageRow) {
          syncPagePathHidden(pageRow);
        }
      });
    });
  }

  var manualPageDraggingRow = null;

  function clearManualPageDropTargets(tbody) {
    if (!tbody) {
      return;
    }
    tbody.querySelectorAll("tr.is-page-row-drop-target").forEach(function (row) {
      row.classList.remove("is-page-row-drop-target");
    });
  }

  function clearManualPageDragState(tbody) {
    if (!tbody) {
      return;
    }
    tbody.querySelectorAll("tr.is-page-row-dragging, tr.is-page-row-drop-target").forEach(function (row) {
      row.classList.remove("is-page-row-dragging", "is-page-row-drop-target");
    });
    manualPageDraggingRow = null;
  }

  function bindManualPageDragContainer(tbody) {
    if (!tbody || tbody.dataset.pageDragContainerReady === "true") {
      return;
    }
    tbody.dataset.pageDragContainerReady = "true";
    tbody.addEventListener("dragover", function (event) {
      var dragging = manualPageDraggingRow;
      if (!dragging || dragging.parentNode !== tbody) {
        return;
      }
      var target = event.target && event.target.closest ? event.target.closest("tr") : null;
      if (!target || target.parentNode !== tbody || target === dragging) {
        return;
      }
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "move";
      }
      clearManualPageDropTargets(tbody);
      target.classList.add("is-page-row-drop-target");
    });
    tbody.addEventListener("drop", function (event) {
      var dragging = manualPageDraggingRow;
      if (!dragging || dragging.parentNode !== tbody) {
        return;
      }
      var target = event.target && event.target.closest ? event.target.closest("tr") : null;
      if (!target || target.parentNode !== tbody || target === dragging) {
        clearManualPageDragState(tbody);
        return;
      }
      event.preventDefault();
      var rect = target.getBoundingClientRect();
      var insertAfter = event.clientY > rect.top + rect.height / 2;
      if (insertAfter) {
        tbody.insertBefore(dragging, target.nextSibling);
      } else {
        tbody.insertBefore(dragging, target);
      }
      clearManualPageDragState(tbody);
      syncManualPageRowOrder(tbody);
    });
    tbody.addEventListener("dragend", function () {
      clearManualPageDragState(tbody);
    });
  }

  function moveManualPageRowByKeyboard(row, tbody, direction) {
    if (!row || !tbody) {
      return;
    }
    if (direction < 0 && row.previousElementSibling) {
      tbody.insertBefore(row, row.previousElementSibling);
    } else if (direction > 0 && row.nextElementSibling) {
      tbody.insertBefore(row.nextElementSibling, row);
    } else {
      return;
    }
    syncManualPageRowOrder(tbody);
    var handle = row.querySelector("[data-page-drag-handle]");
    if (handle) {
      handle.focus();
    }
  }

  function bindManualPageDrag(row, tbody) {
    if (!row || !tbody || row.dataset.pageDragReady === "true") {
      return;
    }
    row.dataset.pageDragReady = "true";
    var handle = row.querySelector("[data-page-drag-handle]");
    if (!handle) {
      return;
    }
    handle.addEventListener("dragstart", function (event) {
      clearManualPageDragState(tbody);
      manualPageDraggingRow = row;
      row.classList.add("is-page-row-dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", row.dataset.pageOrder || "");
      }
    });
    handle.addEventListener("dragend", function () {
      clearManualPageDragState(tbody);
      syncManualPageRowOrder(tbody);
    });
    handle.addEventListener("keydown", function (event) {
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveManualPageRowByKeyboard(row, tbody, -1);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        moveManualPageRowByKeyboard(row, tbody, 1);
      }
    });
  }

  function syncCheckboxFallback(toggle, options) {
    options = options || {};
    if (!toggle) {
      return;
    }
    var label = toggle.closest(".manual-mini-check");
    var hidden = label ? label.querySelector("[data-checkbox-fallback]") : null;
    if (hidden) {
      var nextValue = toggle.checked ? "true" : "false";
      var changed = hidden.value !== nextValue;
      hidden.value = nextValue;
      if (changed && !options.silent) {
        hidden.dispatchEvent(new Event("input", { bubbles: true }));
        hidden.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }

  function bindCheckboxFallbacks(row) {
    if (!row) {
      return;
    }
    row.querySelectorAll("[data-checkbox-toggle]").forEach(function (toggle) {
      if (toggle.dataset.checkboxBound !== "1") {
        toggle.dataset.checkboxBound = "1";
        toggle.addEventListener("change", function () {
          syncCheckboxFallback(toggle);
          notifyConfigFormChanged(toggle);
        });
      }
      syncCheckboxFallback(toggle, { silent: true });
    });
  }

  function updatePageConfigVisibility(row) {
    if (!row) {
      return;
    }
    var sourceSelect = row.querySelector('[name="page_source"]');
    var source = sourceSelect ? String(sourceSelect.value || "cities").toLowerCase() : "cities";
    row.querySelectorAll("[data-source-only]").forEach(function (element) {
      var allowed = String(element.dataset.sourceOnly || "").toLowerCase().split(/[\s,]+/).filter(Boolean);
      var visible = !allowed.length || allowed.indexOf(source) !== -1;
      element.hidden = !visible;
      element.classList.toggle("is-hidden", !visible);
    });
  }

  function setCheckboxFallbackField(row, name, value) {
    var hidden = row ? row.querySelector('[name="' + name + '"][data-checkbox-fallback]') : null;
    if (!hidden) {
      return false;
    }
    var boolValue = String(value === undefined || value === null || value === "" ? "false" : value).toLowerCase() === "true";
    hidden.value = boolValue ? "true" : "false";
    var label = hidden.closest(".manual-mini-check");
    var toggle = label ? label.querySelector("[data-checkbox-toggle]") : null;
    if (toggle) {
      toggle.checked = boolValue;
    }
    hidden.dispatchEvent(new Event("input", { bubbles: true }));
    hidden.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function bindManualPageRow(row, tbody) {
    if (!row) {
      return;
    }
    var remove = row.querySelector("[data-remove-page-row]");
    if (remove && remove.dataset.bound !== "true") {
      remove.dataset.bound = "true";
      remove.addEventListener("click", function () {
        if (tbody.querySelectorAll("tr").length > 1) {
          row.remove();
          syncManualPageRowOrder(tbody);
          notifyConfigFormChanged(tbody);
        }
      });
    }
    var toggleEnabled = row.querySelector("[data-toggle-page-enabled]");
    if (toggleEnabled && toggleEnabled.dataset.bound !== "true") {
      toggleEnabled.dataset.bound = "true";
      toggleEnabled.addEventListener("click", function () {
        var field = row.querySelector("[data-page-enabled]");
        if (field) {
          field.value = manualPageEnabledValue(row) ? "false" : "true";
        }
        updateManualPageEnabledState(row);
        notifyConfigFormChanged(row);
      });
    }
    bindPagePathControls(row);
    bindCheckboxFallbacks(row);
    var sourceSelect = row.querySelector('[name="page_source"]');
    if (sourceSelect && sourceSelect.dataset.sourceVisibilityBound !== "1") {
      sourceSelect.dataset.sourceVisibilityBound = "1";
      sourceSelect.addEventListener("change", function () {
        updatePageConfigVisibility(row);
      });
    }
    updatePageConfigVisibility(row);
    updateManualPageEnabledState(row);
    bindManualPageDrag(row, tbody);
  }

  function resetManualPageRow(row) {
    if (!row) {
      return;
    }
    row.querySelectorAll("input, textarea").forEach(function (field) {
      if (field.matches("[data-page-path-input]")) {
        return;
      }
      if (field.name === "page_force_highest_level" || field.name === "page_lowest_level" || field.name === "page_level" || field.name === "page_parent_level" || field.name.indexOf("page_repeat_") === 0) {
        field.value = "";
      } else if (field.matches("[data-checkbox-fallback]")) {
        field.value = field.name === "page_sum_to_root" ? "false" : "true";
      } else if (field.name === "page_enabled") {
        field.value = "true";
      } else {
        field.value = "";
      }
    });
    row.querySelectorAll("select").forEach(function (select) {
      if (select.name === "page_source") {
        select.value = "admin";
      } else {
        select.value = "";
      }
    });
    row.querySelectorAll("[data-checkbox-toggle]").forEach(function (toggle) {
      var label = toggle.closest(".manual-mini-check");
      var hidden = label ? label.querySelector("[data-checkbox-fallback]") : null;
      toggle.checked = !(hidden && hidden.name === "page_sum_to_root");
      syncCheckboxFallback(toggle);
    });
    row.dataset.pagePathReady = "false";
    setPagePathBoxes(row, [""]);
    bindPagePathControls(row);
    bindCheckboxFallbacks(row);
    updatePageConfigVisibility(row);
    updateManualPageEnabledState(row);
  }

  function initManualPages(editor) {
    var tbody = editor.querySelector("[data-manual-pages]");
    var add = editor.querySelector("[data-add-page-row]");
    if (!tbody || !add) {
      return;
    }
    bindManualPageDragContainer(tbody);
    Array.prototype.slice.call(tbody.querySelectorAll("tr")).forEach(function (row) {
      bindManualPageRow(row, tbody);
    });
    syncManualPageRowOrder(tbody);
    add.addEventListener("click", function () {
      var first = tbody.querySelector("tr");
      if (!first) {
        return;
      }
      var clone = first.cloneNode(true);
      clone.querySelectorAll("[data-remove-page-row]").forEach(function (button) {
        delete button.dataset.bound;
      });
      clone.querySelectorAll("[data-toggle-page-enabled]").forEach(function (button) {
        delete button.dataset.bound;
      });
      delete clone.dataset.pagePathReady;
      delete clone.dataset.pageDragReady;
      delete clone.dataset.pageOrder;
      resetManualPageRow(clone);
      bindManualPageRow(clone, tbody);
      tbody.appendChild(clone);
      syncManualPageRowOrder(tbody);
      notifyConfigFormChanged(tbody);
    });
  }

  function assetAssignmentDataRows(tbody) {
    if (!tbody) {
      return [];
    }
    return Array.prototype.slice.call(tbody.querySelectorAll("tr")).filter(function (row) {
      return !row.matches("[data-asset-empty-row]");
    });
  }

  function refreshAssetAssignmentEmptyRow(tbody) {
    if (!tbody) {
      return;
    }
    var emptyRow = tbody.querySelector("[data-asset-empty-row]");
    if (!emptyRow) {
      return;
    }
    emptyRow.hidden = assetAssignmentDataRows(tbody).length > 0;
  }

  function configAssetOptionsUrl(editor, params) {
    if (!editor) {
      return "";
    }
    var base = editor.dataset.assetOptionsUrl || editor.dataset.sourceEntitiesUrl || "";
    if (!base) {
      return "";
    }
    var url;
    try {
      url = new URL(base, window.location.origin);
    } catch (error) {
      return "";
    }
    url.searchParams.set("asset_options", "1");
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value !== undefined && value !== null && String(value) !== "") {
        url.searchParams.set(key, value);
      }
    });
    return url.toString();
  }

  function loadConfigAssetOptions(editor, params) {
    var url = configAssetOptionsUrl(editor, params || {});
    if (!url) {
      return Promise.resolve({ enabled: false, levels: [], entities: [], flags: [], coats: [] });
    }
    editor._assetOptionsCache = editor._assetOptionsCache || {};
    var key = JSON.stringify(params || {});
    if (!editor._assetOptionsCache[key]) {
      editor._assetOptionsCache[key] = fetch(url, { headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" } })
        .then(parseJsonResponse)
        .catch(function () {
          delete editor._assetOptionsCache[key];
          throw new Error(editor.dataset.errorLabel || "No se pudieron cargar las entidades.");
        });
    }
    return editor._assetOptionsCache[key];
  }

  function optionClassForAssetStatus(status) {
    if (status === "missing_flag") {
      return "asset-status-missing-flag";
    }
    if (status === "missing_coat") {
      return "asset-status-missing-coat";
    }
    if (status === "missing_both") {
      return "asset-status-missing-both";
    }
    return "";
  }

  function replaceSelectOptions(select, items, config) {
    if (!select) {
      return;
    }
    config = config || {};
    var selectedValue = config.selectedValue !== undefined ? String(config.selectedValue || "") : String(select.value || "");
    var placeholder = config.placeholder;
    if (placeholder === undefined) {
      placeholder = select.options.length ? select.options[0].textContent : "";
    }
    select.innerHTML = "";
    var empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder || "";
    select.appendChild(empty);
    var found = !selectedValue;
    (Array.isArray(items) ? items : []).forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.value !== undefined ? String(item.value) : String(item.id || "");
      option.textContent = item.label || item.name || option.value;
      if (item.level !== undefined && item.level !== null) {
        option.dataset.level = String(item.level);
      }
      if (item.status) {
        option.dataset.status = item.status;
        option.title = item.status_label || "";
        var statusClass = optionClassForAssetStatus(item.status);
        if (statusClass) {
          option.className = statusClass;
        }
      }
      if (item.assigned === false) {
        option.classList.add("asset-resource-unassigned");
      }
      if (option.value === selectedValue) {
        option.selected = true;
        found = true;
      }
      select.appendChild(option);
    });
    if (selectedValue && !found) {
      var preserved = document.createElement("option");
      preserved.value = selectedValue;
      preserved.textContent = selectedValue;
      preserved.selected = true;
      select.appendChild(preserved);
    }
  }

  function setAssetSelectBusy(select, busy, label) {
    if (!select) {
      return;
    }
    select.disabled = !!busy;
    if (busy) {
      replaceSelectOptions(select, [], { selectedValue: "", placeholder: label || "Cargando datos..." });
    }
  }

  function clearAssetResourceSelects(row) {
    row.querySelectorAll("[data-asset-assignment-resource]").forEach(function (select) {
      replaceSelectOptions(select, [], { selectedValue: "", placeholder: select.options.length ? select.options[0].textContent : "Sin cambio" });
    });
  }

  function loadAssetEntitiesForRow(row, options) {
    options = options || {};
    if (!row) {
      return Promise.resolve();
    }
    var editor = row.closest("[data-config-editor]");
    var level = row.querySelector("[data-asset-assignment-level]");
    var entity = row.querySelector("[data-asset-assignment-entity]");
    if (!editor || !level || !entity) {
      return Promise.resolve();
    }
    var selectedLevel = level.value || "";
    var selectedEntity = options.selectedEntity !== undefined ? String(options.selectedEntity || "") : String(entity.value || "");
    if (!selectedLevel) {
      replaceSelectOptions(entity, [], { selectedValue: "", placeholder: entity.options.length ? entity.options[0].textContent : "Entidad" });
      clearAssetResourceSelects(row);
      return Promise.resolve();
    }
    setAssetSelectBusy(entity, true, editor.dataset.loadingLabel || "Cargando datos...");
    clearAssetResourceSelects(row);
    return loadConfigAssetOptions(editor, { level: selectedLevel }).then(function (payload) {
      replaceSelectOptions(entity, payload.entities || [], { selectedValue: selectedEntity, placeholder: "Entidad" });
      entity.disabled = false;
      if (entity.value) {
        return loadAssetResourcesForRow(row, { selectedEntity: entity.value });
      }
      return null;
    }).catch(function () {
      replaceSelectOptions(entity, [], { selectedValue: selectedEntity, placeholder: editor.dataset.errorLabel || "No se pudieron cargar las entidades." });
      entity.disabled = false;
    });
  }

  function loadAssetResourcesForRow(row, options) {
    options = options || {};
    if (!row) {
      return Promise.resolve();
    }
    var editor = row.closest("[data-config-editor]");
    var level = row.querySelector("[data-asset-assignment-level]");
    var entity = row.querySelector("[data-asset-assignment-entity]");
    if (!editor || !entity || !entity.value) {
      clearAssetResourceSelects(row);
      return Promise.resolve();
    }
    var selectedEntity = options.selectedEntity !== undefined ? String(options.selectedEntity || "") : String(entity.value || "");
    var flag = row.querySelector('[data-asset-assignment-resource="flag"]');
    var coat = row.querySelector('[data-asset-assignment-resource="coat"]');
    var selectedFlag = flag ? flag.value : "";
    var selectedCoat = coat ? coat.value : "";
    setAssetSelectBusy(flag, true, editor.dataset.loadingLabel || "Cargando datos...");
    setAssetSelectBusy(coat, true, editor.dataset.loadingLabel || "Cargando datos...");
    return loadConfigAssetOptions(editor, { level: level ? level.value : "", entity_id: selectedEntity }).then(function (payload) {
      replaceSelectOptions(flag, payload.flags || [], { selectedValue: selectedFlag, placeholder: "Sin cambio" });
      replaceSelectOptions(coat, payload.coats || [], { selectedValue: selectedCoat, placeholder: "Sin cambio" });
      if (flag) { flag.disabled = false; }
      if (coat) { coat.disabled = false; }
    }).catch(function () {
      replaceSelectOptions(flag, [], { selectedValue: selectedFlag, placeholder: editor.dataset.errorLabel || "Error" });
      replaceSelectOptions(coat, [], { selectedValue: selectedCoat, placeholder: editor.dataset.errorLabel || "Error" });
      if (flag) { flag.disabled = false; }
      if (coat) { coat.disabled = false; }
    });
  }

  function bindManualAssetOverrideRow(row, tbody) {
    if (!row || row.matches("[data-asset-empty-row]")) {
      return;
    }
    var level = row.querySelector("[data-asset-assignment-level]");
    if (level && level.dataset.bound !== "true") {
      level.dataset.bound = "true";
      level.addEventListener("change", function () {
        var entity = row.querySelector("[data-asset-assignment-entity]");
        if (entity) {
          entity.value = "";
        }
        loadAssetEntitiesForRow(row, { selectedEntity: "" });
      });
    }
    var entity = row.querySelector("[data-asset-assignment-entity]");
    if (entity && entity.dataset.bound !== "true") {
      entity.dataset.bound = "true";
      entity.addEventListener("change", function () {
        loadAssetResourcesForRow(row);
      });
    }
    if (level && level.value && entity && entity.options.length <= 2) {
      loadAssetEntitiesForRow(row, { selectedEntity: entity.value });
    } else if (entity && entity.value) {
      loadAssetResourcesForRow(row, { selectedEntity: entity.value });
    }
    var remove = row.querySelector("[data-remove-asset-override-row]");
    if (remove && remove.dataset.bound !== "true") {
      remove.dataset.bound = "true";
      remove.addEventListener("click", function () {
        row.remove();
        refreshAssetAssignmentEmptyRow(tbody);
      });
    }
  }

  function updateAssetAssignmentEntityFilter(row) {
    return loadAssetEntitiesForRow(row, { selectedEntity: row ? (row.querySelector("[data-asset-assignment-entity]") || {}).value : "" });
  }

  function assetAssignmentTemplateRow(form) {
    var template = form ? form.querySelector("[data-asset-assignment-row-template]") : null;
    if (template && template.content) {
      var row = template.content.firstElementChild.cloneNode(true);
      resetManualAssetOverrideRow(row);
      return row;
    }
    var first = form ? form.querySelector("[data-manual-asset-overrides] tr:not([data-asset-empty-row])") : null;
    if (!first) {
      return null;
    }
    var clone = first.cloneNode(true);
    resetManualAssetOverrideRow(clone);
    return clone;
  }

  function resetManualAssetOverrideRow(row) {
    if (!row) {
      return;
    }
    row.querySelectorAll("select").forEach(function (select) {
      select.value = "";
      select.disabled = false;
    });
    row.querySelectorAll("[data-remove-asset-override-row]").forEach(function (button) {
      button.disabled = false;
      delete button.dataset.bound;
    });
    row.querySelectorAll("[data-asset-assignment-level], [data-asset-assignment-entity]").forEach(function (select) {
      delete select.dataset.bound;
    });
    var entity = row.querySelector("[data-asset-assignment-entity]");
    if (entity) {
      replaceSelectOptions(entity, [], { selectedValue: "", placeholder: entity.options.length ? entity.options[0].textContent : "Entidad" });
    }
    clearAssetResourceSelects(row);
  }

  function initManualAssetOverrides(editor) {
    var tbody = editor.querySelector("[data-manual-asset-overrides]");
    var add = editor.querySelector("[data-add-asset-override-row]");
    if (!tbody || !add) {
      return;
    }
    assetAssignmentDataRows(tbody).forEach(function (row) {
      bindManualAssetOverrideRow(row, tbody);
    });
    refreshAssetAssignmentEmptyRow(tbody);
    add.addEventListener("click", function () {
      if (add.disabled) {
        return;
      }
      var form = add.closest("form") || editor.querySelector(".config-manual-form");
      var row = assetAssignmentTemplateRow(form);
      if (!row) {
        return;
      }
      bindManualAssetOverrideRow(row, tbody);
      var emptyRow = tbody.querySelector("[data-asset-empty-row]");
      if (emptyRow) {
        tbody.insertBefore(row, emptyRow);
      } else {
        tbody.appendChild(row);
      }
      refreshAssetAssignmentEmptyRow(tbody);
      var firstInput = row.querySelector("select");
      if (firstInput) {
        firstInput.focus();
      }
    });
  }

  function updateManualVisualAssetsForm(form, visualAssets) {
    visualAssets = visualAssets || {};
    setManualFormField(form, "visual_asset_bulk_country_wikidata", visualAssets.bulk_country_wikidata || "true");
    setManualFormField(form, "visual_asset_strict_required", visualAssets.strict_required || "true");
    setManualFormField(form, "visual_asset_required_kinds", visualAssets.required_kinds || "flag, coat");
    setManualFormField(form, "visual_asset_required_levels", visualAssets.required_levels || "");
  }

  function setManualFormField(form, name, value) {
    var field = form ? form.querySelector('[name="' + name + '"]') : null;
    if (!field) {
      return;
    }
    field.value = value === undefined || value === null ? "" : String(value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function updateManualAssetOverrideRow(row, override) {
    setManualPageField(row, "asset_assignment_level", manualPageValue(override, "level"));
    setManualPageField(row, "asset_assignment_entity_id", manualPageValue(override, "entity_id"));
    setManualPageField(row, "asset_assignment_flag_qid", manualPageValue(override, "flag_qid"));
    setManualPageField(row, "asset_assignment_coat_qid", manualPageValue(override, "coat_qid"));
    updateAssetAssignmentEntityFilter(row);
  }

  function updateManualAssetOverridesForm(form, overrides) {
    var tbody = form ? form.querySelector("[data-manual-asset-overrides]") : null;
    if (!tbody) {
      return;
    }
    overrides = Array.isArray(overrides) ? overrides : [];
    assetAssignmentDataRows(tbody).forEach(function (row) {
      row.remove();
    });
    overrides.forEach(function (override) {
      var row = assetAssignmentTemplateRow(form);
      if (!row) {
        return;
      }
      updateManualAssetOverrideRow(row, override || {});
      bindManualAssetOverrideRow(row, tbody);
      var emptyRow = tbody.querySelector("[data-asset-empty-row]");
      if (emptyRow) {
        tbody.insertBefore(row, emptyRow);
      } else {
        tbody.appendChild(row);
      }
    });
    refreshAssetAssignmentEmptyRow(tbody);
  }


  function setManualPageField(row, name, value) {
    if (setCheckboxFallbackField(row, name, value)) {
      return;
    }
    var field = row ? row.querySelector('[name="' + name + '"]') : null;
    if (!field) {
      return;
    }
    field.value = value === undefined || value === null ? "" : String(value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function manualPageValue(page, key, fallback) {
    if (!page || page[key] === undefined || page[key] === null || page[key] === "") {
      return fallback === undefined ? "" : fallback;
    }
    return page[key];
  }

  function setManualPageInputField(row, name, value) {
    var field = row ? row.querySelector('[name="' + name + '"]') : null;
    if (field) {
      field.value = value === undefined || value === null ? "" : String(value);
    }
  }

  function normalizeConfigCityChild(child) {
    child = child || {};
    return {
      id: String(child.id || ""),
      code: String(child.code || ""),
      name: String(child.name || ""),
      label: String(child.label || child.name || child.code || child.id || ""),
      population_text: String(child.population_text || ""),
      level: child.level === undefined || child.level === null ? "" : String(child.level),
      type_text: String(child.type_text || child.entity_type || "")
    };
  }

  function normalizeConfigCityBuilderData(builder) {
    builder = builder || {};
    var parents = Array.isArray(builder.parent_options) ? builder.parent_options : [];
    var childrenByParent = builder.children_by_parent || {};
    var normalized = {
      enabled: Boolean(builder.enabled),
      legal_level: builder.legal_level === undefined || builder.legal_level === null ? "" : String(builder.legal_level),
      parent_level: builder.parent_level === undefined || builder.parent_level === null ? "" : String(builder.parent_level),
      country_code: String(builder.country_code || ""),
      children_url: String(builder.children_url || ""),
      parent_options: parents.map(function (parent) {
        parent = parent || {};
        return {
          id: String(parent.id || ""),
          code: String(parent.code || ""),
          name: String(parent.name || ""),
          label: String(parent.label || parent.name || parent.code || parent.id || ""),
          level: parent.level === undefined || parent.level === null ? "" : String(parent.level),
          type_text: String(parent.type_text || "")
        };
      }).filter(function (parent) { return parent.id; }),
      children_by_parent: {},
      sections: []
    };
    Object.keys(childrenByParent || {}).forEach(function (parentId) {
      normalized.children_by_parent[String(parentId)] = (childrenByParent[parentId] || []).map(function (child) {
        return normalizeConfigCityChild(child);
      }).filter(function (child) { return child.id; });
    });
    normalized.sections = (Array.isArray(builder.sections) ? builder.sections : [])
      .map(function (section) { return normalizeConfigCitySection(section, normalized); })
      .filter(Boolean);
    return normalized;
  }

  function configCityParentById(builder, parentId) {
    parentId = String(parentId || "");
    return (builder.parent_options || []).find(function (parent) {
      return parent.id === parentId;
    }) || null;
  }

  function configCityChildById(builder, parentId, childId) {
    childId = String(childId || "");
    return (builder.children_by_parent[String(parentId || "")] || []).find(function (child) {
      return child.id === childId;
    }) || null;
  }

  function normalizeConfigCitySection(section, builder) {
    section = section || {};
    var parentId = String(section.parent_id || "");
    var parent = configCityParentById(builder, parentId);
    if (!parent) {
      return null;
    }
    var selectedIds = uniqueValues((section.selected_ids || []).map(function (id) {
      return String(id || "");
    }).filter(function (id) {
      return id && configCityChildById(builder, parentId, id);
    }));
    return {
      parent_id: parentId,
      parent_name: String(section.parent_name || parent.name || ""),
      parent_label: String(section.parent_label || parent.label || parent.name || ""),
      parent_code: String(section.parent_code || parent.code || ""),
      parent_level: section.parent_level === undefined || section.parent_level === null || section.parent_level === ""
        ? builder.parent_level
        : String(section.parent_level),
      parent_type: String(section.parent_type || parent.type_text || ""),
      city: String(section.city || parent.name || ""),
      code: String(section.code || parent.code || parent.id || ""),
      level: section.level === undefined || section.level === null || section.level === ""
        ? builder.legal_level
        : String(section.level),
      type: String(section.type || "City"),
      selected_ids: selectedIds,
      keep_communes: Boolean(section.keep_communes)
    };
  }

  function configCityDestroySelect(select) {
    destroySelect2(select);
  }

  function configCityRefreshSelect(select) {
    syncNativeSearchSelect(select);
  }

  function ensureConfigCityChildren(form, parentId) {
    var state = form ? form._configCityBuilderState : null;
    parentId = String(parentId || "");
    if (!state || !parentId) {
      return Promise.resolve([]);
    }
    if ((state.data.children_by_parent[parentId] || []).length) {
      return Promise.resolve(state.data.children_by_parent[parentId]);
    }
    state.childrenLoading = state.childrenLoading || {};
    if (state.childrenLoading[parentId]) {
      return state.childrenLoading[parentId];
    }
    if (!state.data.children_url) {
      state.data.children_by_parent[parentId] = [];
      return Promise.resolve([]);
    }
    var url = new URL(state.data.children_url, window.location.href);
    url.searchParams.set("country_code", state.data.country_code || "");
    url.searchParams.set("parent_id", parentId);
    state.childrenLoading[parentId] = fetchJson(relativeUrlFrom(url.toString())).then(function (payload) {
      var legalLevel = String(state.data.legal_level || "");
      var children = (payload.children || []).map(function (child) {
        return normalizeConfigCityChild(child);
      }).filter(function (child) {
        return child.id && (!legalLevel || String(child.level || "") === legalLevel);
      });
      state.data.children_by_parent[parentId] = children;
      return children;
    }).catch(function (error) {
      state.data.children_by_parent[parentId] = [];
      throw error;
    }).finally(function () {
      delete state.childrenLoading[parentId];
    });
    return state.childrenLoading[parentId];
  }
