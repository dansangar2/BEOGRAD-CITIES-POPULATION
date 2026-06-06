(function () {
  var THEME_STORAGE_KEY = "ciudades_del_mundo_theme";
  var THEME_EFFECTS_STORAGE_KEY = "ciudades_del_mundo_theme_effects";
  var STATS_COUNTRY_CACHE_KEY = "ciudades_del_mundo_stats_country_payload_sql_only_assets_v2";

  function applyTheme(theme) {
    var selected = theme || "light";
    if (selected === "light") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", selected);
    }
  }

  function applyThemeEffects(enabled) {
    if (enabled) {
      document.documentElement.setAttribute("data-theme-effects", "complex");
    } else {
      document.documentElement.removeAttribute("data-theme-effects");
    }
  }

  function initThemeSelector(root) {
    var scope = root || document;
    var select = scope.querySelector("[data-theme-select]");
    var effects = scope.querySelector("[data-theme-effects-toggle]");
    if (!select) {
      return;
    }
    var stored = "light";
    try {
      stored = window.localStorage.getItem(THEME_STORAGE_KEY) || "light";
    } catch (error) {}
    if (!select.querySelector('option[value="' + stored + '"]')) {
      stored = "light";
    }
    select.value = stored;
    applyTheme(stored);
    if (effects) {
      var storedEffects = false;
      try {
        storedEffects = window.localStorage.getItem(THEME_EFFECTS_STORAGE_KEY) === "1";
      } catch (error) {}
      effects.checked = storedEffects;
      applyThemeEffects(storedEffects);
      effects.addEventListener("change", function () {
        applyThemeEffects(effects.checked);
        try {
          window.localStorage.setItem(THEME_EFFECTS_STORAGE_KEY, effects.checked ? "1" : "0");
        } catch (error) {}
      });
    }
    select.addEventListener("change", function () {
      var value = select.value || "light";
      applyTheme(value);
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, value);
      } catch (error) {}
    });
  }


  function renderSortButtonLabel(button, label, active, direction) {
    var text = label || button.dataset.sort || "";
    button.textContent = "";
    var labelSpan = document.createElement("span");
    labelSpan.className = "table-sort-label";
    labelSpan.textContent = text;
    button.appendChild(labelSpan);
    if (active) {
      var arrowSpan = document.createElement("span");
      arrowSpan.className = "table-sort-arrow";
      arrowSpan.setAttribute("aria-hidden", "true");
      arrowSpan.textContent = direction > 0 ? "↑" : "↓";
      button.appendChild(arrowSpan);
    }
  }

  function initSelect2(root) {
    if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.select2) {
      return;
    }
    window.jQuery(root || document)
      .find("select[data-select2]")
      .each(function () {
        var select = window.jQuery(this);
        if (select.data("select2")) {
          return;
        }
        select.select2({
          width: "100%",
          placeholder: select.data("placeholder") || "",
          allowClear: true
        });
      });
  }

  function createStatusElement(className, message, loading) {
    var element = document.createElement("div");
    element.className = className;
    if (loading) {
      var spinner = document.createElement("span");
      spinner.className = "loading-spinner";
      spinner.setAttribute("aria-hidden", "true");
      element.appendChild(spinner);
    }
    element.appendChild(document.createTextNode(message));
    return element;
  }

  function setStatus(target, className, message, loading) {
    target.innerHTML = "";
    target.appendChild(createStatusElement(className, message, loading));
  }

  function setLoading(target, message) {
    setStatus(target, "table-loading", message, true);
  }

  function setError(target, message) {
    setStatus(target, "table-error", message, false);
  }

  function setInlineStatus(target, message, loading) {
    if (!target) {
      return;
    }
    target.hidden = false;
    target.innerHTML = "";
    target.classList.toggle("inline-loading", Boolean(loading));
    if (loading) {
      var spinner = document.createElement("span");
      spinner.className = "loading-spinner";
      spinner.setAttribute("aria-hidden", "true");
      target.appendChild(spinner);
    }
    target.appendChild(document.createTextNode(message || ""));
  }

  function formParams(form, page) {
    var params = form && form.dataset.clientSideFilter === "1"
      ? new URLSearchParams()
      : new URLSearchParams(new FormData(form));
    if (page && !(form && form.dataset.clientSideFilter === "1")) {
      params.set("page", page);
    } else {
      params.delete("page");
    }
    Array.from(params.keys()).forEach(function (key) {
      if (params.getAll(key).every(function (value) { return value === ""; })) {
        params.delete(key);
      }
    });
    return params;
  }

  function tableTarget(form) {
    var target = document.querySelector(form.dataset.target);
    return target;
  }

  function pageSizeForForm(form, fallback) {
    var select = form ? form.querySelector("select[name='page_size']") : null;
    var parsed = parseInt(select ? select.value : fallback, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function normalizedClientValue(value) {
    return String(value || "").trim().toLocaleLowerCase();
  }

  function clientRowMatchesForm(row, form) {
    if (!row || !form || form.dataset.clientSideFilter !== "1") {
      return true;
    }
    var queryInput = form.querySelector("input[type='search'][name='q']");
    if (queryInput) {
      var query = normalizedClientValue(queryInput.value);
      if (query) {
        var haystack = normalizedClientValue(row.dataset.search || [row.dataset.country, row.dataset.countryCode].join(" "));
        if (haystack.indexOf(query) === -1) {
          return false;
        }
      }
    }
    var statusSelect = form.querySelector("select[name='status']");
    if (statusSelect) {
      var selectedStatus = normalizedClientValue(statusSelect.value);
      if (selectedStatus) {
        var rowStatus = normalizedClientValue(row.dataset.status || row.dataset.task || "pending");
        if (!rowStatus) {
          rowStatus = "pending";
        }
        if (rowStatus !== selectedStatus) {
          return false;
        }
      }
    }
    return true;
  }

  function updateClientFilterState(target, form) {
    if (!target) {
      return false;
    }
    var rows = Array.prototype.slice.call(target.querySelectorAll("[data-client-row]"));
    rows.forEach(function (row) {
      row.dataset.clientFilterHidden = clientRowMatchesForm(row, form) ? "0" : "1";
    });
    return true;
  }

  function applyClientFilters(target, form, requestedPage) {
    if (!target || !updateClientFilterState(target, form)) {
      return false;
    }
    return renderClientPagination(target, form, requestedPage || 1);
  }

  function formForTableTarget(target) {
    if (!target || !target.id) {
      return null;
    }
    var escapedId = window.CSS && typeof window.CSS.escape === "function"
      ? window.CSS.escape(target.id)
      : String(target.id).replace(/"/g, '\"');
    return document.querySelector('.async-table-form[data-target="#' + escapedId + '"]');
  }

  function rerenderClientTable(target, requestedPage) {
    if (!target) {
      return false;
    }
    var form = formForTableTarget(target);
    var page = requestedPage || target.dataset.clientPage || 1;
    var sortKey = target.dataset.clientSortKey || "";
    var sortDirection = Number(target.dataset.clientSortDirection || 1);
    if (sortKey) {
      return applyClientSort(target, form, sortKey, sortDirection, page);
    }
    return applyClientFilters(target, form, page);
  }


  function emptyLabelForClientTable(target, form) {
    if (form && form.dataset.emptyLabel) {
      return form.dataset.emptyLabel;
    }
    var owner = target ? target.closest("[data-empty-label]") : null;
    return owner && owner.dataset.emptyLabel ? owner.dataset.emptyLabel : "No hay datos";
  }

  function ensureClientEmptyRow(target, form) {
    if (!target) {
      return null;
    }
    var existing = target.querySelector("[data-client-empty]");
    if (existing) {
      var existingCell = existing.querySelector("td, th");
      if (existingCell && !existingCell.textContent.trim()) {
        existingCell.textContent = emptyLabelForClientTable(target, form);
      }
      return existing;
    }
    var tbody = target.querySelector("[data-client-page-rows]");
    if (!tbody) {
      return null;
    }
    var columnCount = target.querySelectorAll("thead th").length || 1;
    var row = document.createElement("tr");
    row.setAttribute("data-client-empty", "");
    row.hidden = true;
    var cell = document.createElement("td");
    cell.className = "table-empty-cell";
    cell.colSpan = columnCount;
    cell.textContent = emptyLabelForClientTable(target, form);
    row.appendChild(cell);
    tbody.appendChild(row);
    return row;
  }

  function renderClientPagination(target, form, requestedPage) {
    var nav = target.querySelector("[data-client-pagination]");
    if (!nav) {
      return false;
    }
    var rows = Array.prototype.slice.call(target.querySelectorAll("[data-client-row]"));
    var visibleRows = rows.filter(function (row) {
      return row.dataset.clientFilterHidden !== "1";
    });
    var emptyRow = ensureClientEmptyRow(target, form);
    var initialSize = parseInt(nav.dataset.initialPageSize || "25", 10);
    var pageSize = pageSizeForForm(form, initialSize);
    var pageCount = Math.max(1, Math.ceil(visibleRows.length / pageSize));
    var page = parseInt(requestedPage || target.dataset.clientPage || "1", 10);
    if (!Number.isFinite(page) || page < 1) {
      page = 1;
    }
    if (page > pageCount) {
      page = pageCount;
    }
    var start = (page - 1) * pageSize;
    var end = start + pageSize;
    var visibleIndex = 0;
    rows.forEach(function (row) {
      if (row.dataset.clientFilterHidden === "1") {
        row.hidden = true;
        return;
      }
      row.hidden = visibleIndex < start || visibleIndex >= end;
      visibleIndex += 1;
    });
    if (emptyRow) {
      emptyRow.hidden = visibleRows.length > 0;
    }
    nav.hidden = visibleRows.length <= pageSize;
    nav.querySelectorAll("[data-client-page='previous']").forEach(function (button) {
      button.disabled = page <= 1 || visibleRows.length === 0;
    });
    nav.querySelectorAll("[data-client-page='next']").forEach(function (button) {
      button.disabled = page >= pageCount || visibleRows.length === 0;
    });
    var status = nav.querySelector("[data-client-page-status]");
    if (status) {
      status.textContent = visibleRows.length ? page + " / " + pageCount : "0 / 0";
    }
    target.dataset.clientPage = String(page);
    target.dataset.clientPaginationReady = "1";
    return true;
  }

  function clientSortButtonLabel(button) {
    return button.dataset.baseLabel || button.textContent.replace(/\s+[\u2191\u2193]$/, "");
  }

  function clientSortValue(row, key, type) {
    var value = row.dataset[key] || "";
    if (type === "number") {
      var number = Number(value || 0);
      return Number.isFinite(number) ? number : 0;
    }
    return String(value).toLocaleLowerCase();
  }

  function updateClientSortButtons(target) {
    var sortKey = target.dataset.clientSortKey || "";
    var direction = Number(target.dataset.clientSortDirection || 1);
    target.querySelectorAll("[data-client-sort]").forEach(function (button) {
      if (!button.dataset.baseLabel) {
        button.dataset.baseLabel = clientSortButtonLabel(button);
      }
      var active = button.dataset.clientSort === sortKey;
      button.classList.toggle("is-sorted", active);
      button.classList.toggle("is-asc", active && direction > 0);
      button.classList.toggle("is-desc", active && direction < 0);
      button.textContent = clientSortButtonLabel(button) + (active ? (direction > 0 ? " \u2191" : " \u2193") : "");
    });
  }

  function applyClientSort(target, form, key, direction, requestedPage) {
    var tbody = target.querySelector("[data-client-page-rows]");
    if (!tbody || !key) {
      return false;
    }
    var button = target.querySelector('[data-client-sort="' + key + '"]');
    var type = button ? button.dataset.sortType : "";
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("[data-client-row]"));
    rows.sort(function (left, right) {
      var a = clientSortValue(left, key, type);
      var b = clientSortValue(right, key, type);
      if (a < b) {
        return -1 * direction;
      }
      if (a > b) {
        return 1 * direction;
      }
      return 0;
    });
    rows.forEach(function (row) {
      tbody.appendChild(row);
    });
    target.dataset.clientSortKey = key;
    target.dataset.clientSortDirection = String(direction);
    updateClientSortButtons(target);
    updateClientFilterState(target, form);
    renderClientPagination(target, form, requestedPage || 1);
    return true;
  }

  function initClientSorting(target, form) {
    target.querySelectorAll("[data-client-sort]").forEach(function (button) {
      if (!button.dataset.baseLabel) {
        button.dataset.baseLabel = clientSortButtonLabel(button);
      }
      if (button.dataset.clientSortBound === "1") {
        return;
      }
      button.dataset.clientSortBound = "1";
      button.addEventListener("click", function () {
        var key = button.dataset.clientSort;
        var currentKey = target.dataset.clientSortKey || "";
        var currentDirection = Number(target.dataset.clientSortDirection || 1);
        var nextDirection = currentKey === key ? currentDirection * -1 : 1;
        applyClientSort(target, form, key, nextDirection, 1);
      });
    });
    updateClientSortButtons(target);
  }

  function initClickableRows(target) {
    target.querySelectorAll("[data-row-href]").forEach(function (row) {
      if (row.dataset.rowClickBound === "1") {
        return;
      }
      row.dataset.rowClickBound = "1";
      row.addEventListener("click", function (event) {
        if (event.target.closest("a, button, input, select, textarea, label")) {
          return;
        }
        window.location.href = row.dataset.rowHref;
      });
      row.addEventListener("keydown", function (event) {
        if (event.target.closest("a, button, input, select, textarea, label")) {
          return;
        }
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        window.location.href = row.dataset.rowHref;
      });
    });
  }

  function initClientPagination(target, form, requestedPage) {
    if (!target) {
      return;
    }
    applyClientFilters(target, form, requestedPage || 1);
    if (target.dataset.clientPaginationDelegated !== "1") {
      target.dataset.clientPaginationDelegated = "1";
      target.addEventListener("click", function (event) {
        var button = event.target.closest("button[data-client-page]");
        if (!button || !target.contains(button)) {
          return;
        }
        event.preventDefault();
        if (button.disabled) {
          return;
        }
        var current = parseInt(target.dataset.clientPage || "1", 10);
        if (!Number.isFinite(current) || current < 1) {
          current = 1;
        }
        var next = button.dataset.clientPage === "next" ? current + 1 : current - 1;
        renderClientPagination(target, formForTableTarget(target) || form, next);
      });
    }
  }

  var TASK_TABLE_ACTIVE_POLL_MS = 5000;
  var TASK_TABLE_IDLE_POLL_MS = 60000;
  var ASYNC_TABLE_DYNAMIC_FILTER_MS = 250;

  function padLocalTimePart(value) {
    return String(value).padStart(2, "0");
  }

  function formatBrowserLocalDate(date, format) {
    var year = date.getFullYear();
    var month = padLocalTimePart(date.getMonth() + 1);
    var day = padLocalTimePart(date.getDate());
    var hours = padLocalTimePart(date.getHours());
    var minutes = padLocalTimePart(date.getMinutes());
    var seconds = padLocalTimePart(date.getSeconds());
    if (format === "time") {
      return hours + ":" + minutes + ":" + seconds;
    }
    return year + "-" + month + "-" + day + " " + hours + ":" + minutes + ":" + seconds;
  }

  function initLocalDateTimes(root) {
    (root || document).querySelectorAll("[data-local-datetime]").forEach(function (element) {
      if (element.dataset.localDatetimeApplied === "1") {
        return;
      }
      var epoch = Number(element.dataset.localDatetime || "");
      if (!Number.isFinite(epoch) || epoch <= 0) {
        return;
      }
      var date = new Date(epoch * 1000);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      element.textContent = formatBrowserLocalDate(date, element.dataset.localDatetimeFormat || "datetime");
      element.title = date.toLocaleString();
      element.dataset.localDatetimeApplied = "1";
    });
  }


  var TASK_DETAIL_POLL_MS = 1000;

  function taskStatusLabel(panel, status) {
    if (!status) {
      return "-";
    }
    return configTaskLabel(panel || document.body, status) || status;
  }

  function shouldStickTaskLogToBottom(container) {
    if (!container) {
      return false;
    }
    return container.scrollHeight - container.scrollTop - container.clientHeight < 80;
  }

  function updateTaskDetailFromPayload(panel, data) {
    var code = document.querySelector("[data-task-log-output]");
    var logBox = document.querySelector("[data-task-log]");
    var status = document.querySelector("[data-task-status-text]");
    var returncode = document.querySelector("[data-task-returncode]");
    var cancelForm = document.querySelector("[data-task-cancel-form]");
    if (status) {
      status.textContent = taskStatusLabel(panel, data.status);
      status.className = "status-text " + configTaskKey(data.status || "");
    }
    if (returncode) {
      returncode.textContent = data.returncode === null || data.returncode === undefined ? "-" : String(data.returncode);
    }
    if (code && typeof data.output_delta === "string") {
      var stickToDeltaBottom = shouldStickTaskLogToBottom(logBox);
      if (data.output_reset) {
        code.textContent = data.output_delta || "Sin salida todavia.";
      } else if (data.output_delta) {
        code.textContent += data.output_delta;
      }
      if (panel && data.output_offset !== undefined && data.output_offset !== null) {
        panel.dataset.logOffset = String(data.output_offset);
      }
      if (stickToDeltaBottom && logBox) {
        logBox.scrollTop = logBox.scrollHeight;
      }
    } else if (code && typeof data.output === "string") {
      var nextOutput = data.output || "Sin salida todavia.";
      var stickToBottom = shouldStickTaskLogToBottom(logBox);
      if (code.textContent !== nextOutput) {
        code.textContent = nextOutput;
        if (stickToBottom && logBox) {
          logBox.scrollTop = logBox.scrollHeight;
        }
      }
    }
    if (cancelForm && !data.is_active) {
      cancelForm.hidden = true;
    }
    if (panel) {
      panel.dataset.active = data.is_active ? "1" : "0";
    }
  }

  function taskDetailStatusUrl(panel, statusUrl) {
    var url;
    try {
      url = new URL(statusUrl, window.location.href);
    } catch (error) {
      return statusUrl;
    }
    if (panel && panel.dataset.logOffset !== undefined) {
      url.searchParams.set("since", panel.dataset.logOffset || "0");
    }
    return url.toString();
  }

  function initTaskDetailLog(root) {
    var scope = root || document;
    var panel = scope.querySelector ? scope.querySelector("[data-task-detail]") : null;
    if (!panel || panel.dataset.taskDetailBound === "1") {
      return;
    }
    panel.dataset.taskDetailBound = "1";
    var statusUrl = panel.dataset.statusUrl || "";
    if (!statusUrl || panel.dataset.active !== "1") {
      return;
    }
    var stopped = false;

    function poll() {
      if (stopped || !document.body.contains(panel)) {
        return;
      }
      fetch(taskDetailStatusUrl(panel, statusUrl), { headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" } })
        .then(parseJsonResponse)
        .then(function (data) {
          updateTaskDetailFromPayload(panel, data);
          if (data.is_active) {
            window.setTimeout(poll, TASK_DETAIL_POLL_MS);
          } else {
            stopped = true;
            refreshTaskTables();
          }
        })
        .catch(function () {
          window.setTimeout(poll, TASK_DETAIL_POLL_MS * 2);
        });
    }
    window.setTimeout(poll, TASK_DETAIL_POLL_MS);
  }

  function taskTableMarker(target) {
    return target ? target.querySelector("[data-task-table-snapshot]") : null;
  }

  function taskTableHasActive(target) {
    var marker = taskTableMarker(target);
    return marker && marker.dataset.hasActiveTasks === "1";
  }

  function updateTaskTableState(target) {
    var marker = taskTableMarker(target);
    if (!marker) {
      return;
    }
    target.dataset.taskTableSnapshot = marker.dataset.taskTableSnapshot || "";
    target.dataset.taskTableHasActive = marker.dataset.hasActiveTasks || "0";
  }

  function htmlTaskTableSnapshot(html) {
    var template = document.createElement("template");
    template.innerHTML = html.trim();
    var marker = template.content.querySelector("[data-task-table-snapshot]");
    return marker ? marker.dataset.taskTableSnapshot || "" : "";
  }

  function scheduleTaskTablePolling(form) {
    if (!isTaskTableForm(form)) {
      return;
    }
    if (form._taskPollingTimer) {
      window.clearTimeout(form._taskPollingTimer);
    }
    if (!document.body.contains(form)) {
      return;
    }
    var target = tableTarget(form);
    var active = taskTableHasActive(target);
    var delay = active ? TASK_TABLE_ACTIVE_POLL_MS : TASK_TABLE_IDLE_POLL_MS;
    form._taskPollingTimer = window.setTimeout(function () {
      if (!document.body.contains(form)) {
        return;
      }
      loadTable(form, null, { preserveClientPage: true, silent: true });
    }, delay);
  }

  function loadTable(form, page, options) {
    var target = tableTarget(form);
    if (!target) {
      return;
    }
    options = options || {};
    var preserveClientState = options.preserveClientPage;
    var silent = Boolean(options.silent);
    var clientPage = preserveClientState ? target.dataset.clientPage : null;
    var clientSortKey = preserveClientState ? target.dataset.clientSortKey : null;
    var clientSortDirection = preserveClientState ? Number(target.dataset.clientSortDirection || 1) : 1;
    var params = formParams(form, page);
    var url = form.dataset.url + (params.toString() ? "?" + params.toString() : "");
    if (!silent) {
      setLoading(target, form.dataset.loading || "Cargando tabla...");
    }
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (response) {
        if (!response.ok) {
          return response.text().then(function (text) {
            throw new Error(text || response.statusText);
          });
        }
        return response.text();
      })
      .then(function (html) {
        var nextSnapshot = silent ? htmlTaskTableSnapshot(html) : "";
        if (silent && nextSnapshot && target.dataset.taskTableSnapshot === nextSnapshot) {
          return;
        }
        target.innerHTML = html;
        updateTaskTableState(target);
        initLocalDateTimes(target);
        initSelect2(target);
        initConfigTaskActions(target);
        initClientSorting(target, form);
        initClickableRows(target);
        if (!clientSortKey || !applyClientSort(target, form, clientSortKey, clientSortDirection, clientPage || 1)) {
          initClientPagination(target, form, clientPage || 1);
        }
      })
      .catch(function () {
        if (!silent) {
          setError(target, form.dataset.error || "La tabla no se pudo cargar. Reintenta en unos segundos.");
        }
      })
      .finally(function () {
        scheduleTaskTablePolling(form);
      });
  }

  function refreshTaskTables() {
    document.querySelectorAll(".async-table-form").forEach(function (form) {
      var url = form.dataset.url || "";
      if (url.indexOf("/tasks/table/") >= 0 || url.indexOf("/configs/tasks/table/") >= 0) {
        loadTable(form, null, { preserveClientPage: true, silent: true });
      }
    });
  }

  function refreshConfigTables() {
    document.querySelectorAll(".async-table-form").forEach(function (form) {
      var url = form.dataset.url || "";
      if (url.indexOf("/configs/table/") >= 0) {
        loadTable(form, null, { preserveClientPage: true, silent: true });
      }
    });
  }

  function isTaskTableForm(form) {
    var url = form.dataset.url || "";
    return url.indexOf("/tasks/table/") >= 0 || url.indexOf("/configs/tasks/table/") >= 0;
  }

  function configBootstrapIsActive() {
    var overlay = document.querySelector("[data-config-bootstrap]");
    return Boolean(overlay && overlay.dataset.ready !== "1");
  }

  function updateConfigBootstrapText(overlay, data) {
    var message = overlay.querySelector("[data-bootstrap-message]");
    var progress = overlay.querySelector("[data-bootstrap-progress]");
    if (message) {
      message.textContent = data && data.ready
        ? (overlay.dataset.doneLabel || "Configuración inicial cargada. Continuando...")
        : (overlay.dataset.loadingLabel || "Cargando configuración inicial desde los TOML...");
    }
    if (progress && data) {
      progress.textContent = String(data.stored_count || 0) + " / " + String(data.expected_count || 0)
        + " · " + String(data.missing_count || 0) + " pendientes";
    }
  }

  function initConfigBootstrap(root) {
    var overlay = (root || document).querySelector("[data-config-bootstrap]");
    if (!overlay || overlay.dataset.started === "1") {
      return;
    }
    overlay.dataset.started = "1";
    updateConfigBootstrapText(overlay, null);
    var tokenInput = overlay.querySelector("input[name='csrfmiddlewaretoken']");
    fetch(overlay.dataset.bootstrapUrl || window.location.href, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": tokenInput ? tokenInput.value : ""
      }
    }).then(parseJsonResponse).then(function (data) {
      updateConfigBootstrapText(overlay, data);
      if (data.ready) {
        overlay.dataset.ready = "1";
        window.setTimeout(function () { window.location.reload(); }, 250);
      }
    }).catch(function (error) {
      var message = overlay.querySelector("[data-bootstrap-message]");
      overlay.classList.add("is-error");
      if (message) {
        message.textContent = (overlay.dataset.errorLabel || "No se pudo cargar la configuración inicial.") + " " + (error.message || "");
      }
    });
  }

  function initAsyncTables(root) {
    (root || document).querySelectorAll(".async-table-form").forEach(function (form) {
      if (form.dataset.asyncTableBound === "1") {
        return;
      }
      if (form.dataset.deferBootstrap === "1" && configBootstrapIsActive()) {
        return;
      }
      form.dataset.asyncTableBound = "1";
      loadTable(form);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (form.dataset.clientSideFilter === "1") {
          applyClientFilters(tableTarget(form), form, 1);
          return;
        }
        loadTable(form);
      });
      form.addEventListener("reset", function () {
        window.setTimeout(function () {
          if (window.jQuery && window.jQuery.fn.select2) {
            window.jQuery(form).find("select[data-select2]").val(null).trigger("change");
          }
          if (form.dataset.clientSideFilter === "1") {
            applyClientFilters(tableTarget(form), form, 1);
            return;
          }
          loadTable(form);
        }, 0);
      });
      if (form.dataset.dynamicFilter === "1") {
        var scheduleDynamicFilter = function (delay) {
          if (form._dynamicFilterTimer) {
            window.clearTimeout(form._dynamicFilterTimer);
          }
          form._dynamicFilterTimer = window.setTimeout(function () {
            var target = tableTarget(form);
            if (form.dataset.clientSideFilter === "1") {
              applyClientFilters(target, form, 1);
              return;
            }
            loadTable(form);
          }, delay);
        };
        form.querySelectorAll("input[type='search']").forEach(function (input) {
          if (input.dataset.dynamicFilterBound === "1") {
            return;
          }
          input.dataset.dynamicFilterBound = "1";
          input.addEventListener("input", function () {
            scheduleDynamicFilter(ASYNC_TABLE_DYNAMIC_FILTER_MS);
          });
        });
        form.querySelectorAll("select[data-dynamic-filter-control]").forEach(function (select) {
          if (select.dataset.dynamicFilterBound === "1") {
            return;
          }
          select.dataset.dynamicFilterBound = "1";
          select.addEventListener("change", function () {
            scheduleDynamicFilter(0);
          });
        });
      }
      form.querySelectorAll("select[name='page_size']").forEach(function (select) {
        select.addEventListener("change", function () {
          var target = tableTarget(form);
          if (target && applyClientFilters(target, form, 1)) {
            return;
          }
          loadTable(form);
        });
      });
      var target = tableTarget(form);
      if (!target) {
        return;
      }
      target.addEventListener("click", function (event) {
        var link = event.target.closest(".pagination a[data-page]");
        if (!link) {
          return;
        }
        event.preventDefault();
        loadTable(form, link.dataset.page);
      });
    });
  }

  function initMap() {
    var mapEl = document.querySelector("[data-area-map]");
    if (!mapEl || !window.L) {
      return;
    }
    var query = mapEl.dataset.query;
    var map = window.L.map(mapEl).setView([20, 0], 2);
    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }).addTo(map);

    mapEl.classList.add("is-loading");
    fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(query))
      .then(function (response) { return response.json(); })
      .then(function (results) {
        if (!results || !results.length) {
          mapEl.classList.add("map-empty");
          return;
        }
        var item = results[0];
        var lat = parseFloat(item.lat);
        var lon = parseFloat(item.lon);
        map.setView([lat, lon], 8);
        window.L.marker([lat, lon]).addTo(map).bindPopup(item.display_name).openPopup();
      })
      .catch(function () {
        mapEl.classList.add("map-empty");
      })
      .finally(function () {
        mapEl.classList.remove("is-loading");
      });
  }

  function commonsFileUrl(filename, width) {
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" +
      encodeURIComponent(filename).replace(/%20/g, "_") +
      "?width=" + (width || 360);
  }

  function commonsFilePageUrl(filename) {
    return "https://commons.wikimedia.org/wiki/File:" +
      encodeURIComponent(filename).replace(/%20/g, "_");
  }

  function encodeIdentityPath(value) {
    return String(value || "")
      .replace(/^\/+/, "")
      .split("/")
      .map(function (part) { return encodeURIComponent(part); })
      .join("/");
  }

  function visualIdentityDetailUrl(kind, filename) {
    return "/identity/" + encodeURIComponent(kind || "image") + "/" + encodeIdentityPath(filename) + "/";
  }

  function visualIdentityEntityUrl(kind, entityType, entityKey) {
    if (!entityType || !entityKey) {
      return "";
    }
    return "/identity/" + encodeURIComponent(kind || "image") +
      "/entity/" + encodeURIComponent(entityType) + "/" + encodeIdentityPath(entityKey) + "/";
  }

  function assetIdentityDetailUrl(asset, kind) {
    if (!asset) {
      return "";
    }
    var entityUrl = visualIdentityEntityUrl(
      kind || asset.kind || "image",
      asset.entity_type || "",
      asset.entity_key || ""
    );
    if (entityUrl) {
      return entityUrl;
    }
    var target = asset.commons_filename || asset.local_path || "";
    if (target) {
      return visualIdentityDetailUrl(kind || asset.kind || "image", target);
    }
    return asset.source_url || assetImageUrl(asset, 1600) || "";
  }

  function openAssetDetailNewWindow(asset, kind, fallbackUrl) {
    var url = assetIdentityDetailUrl(asset, kind) || fallbackUrl || assetImageUrl(asset, 1600);
    if (url) {
      window.open(url, "_blank", "noopener");
    }
  }

  function assetImageUrl(asset, width) {
    if (!asset) {
      return "";
    }
    if (asset.commons_filename) {
      return commonsFileUrl(asset.commons_filename, width || 360);
    }
    if (asset.remote_url) {
      return asset.remote_url;
    }
    if (asset.image_url && !/^\/media\//.test(String(asset.image_url))) {
      return asset.image_url;
    }
    if (asset.local_url) {
      return asset.local_url;
    }
    if (asset.image_url) {
      return asset.image_url;
    }
    return "";
  }

  function storedVisualAssetImageUrl(asset) {
    if (!asset) {
      return "";
    }
    return asset.image_url || asset.remote_url || asset.local_url || "";
  }

  function openStoredAssetPreview(asset, fallbackLabel, kind) {
    var previewSrc = storedVisualAssetImageUrl(asset);
    if (!previewSrc) {
      return;
    }
    openImagePreview({
      filename: asset.commons_filename || asset.local_path || "",
      label: fallbackLabel || asset.entity_name || asset.commons_filename || asset.local_path || kind || "image",
      kind: kind || asset.kind || "image",
      previewSrc: previewSrc,
      fullSrc: asset.remote_url || asset.image_url || previewSrc,
      detailUrl: assetIdentityDetailUrl(asset, kind || asset.kind || "image") || previewSrc
    }, fallbackLabel, kind);
  }

  function ensureVisualPlaceholder(container, kind) {
    if (!container) {
      return null;
    }
    var placeholder = container.querySelector(".visual-placeholder") || container.querySelector(".muted");
    if (!placeholder) {
      placeholder = document.createElement("span");
      container.appendChild(placeholder);
    }
    placeholder.className = "visual-placeholder visual-placeholder-" + (kind || "flag");
    placeholder.setAttribute("aria-hidden", "true");
    placeholder.hidden = false;
    return placeholder;
  }

  function hideBrokenVisualImage(image) {
    if (!image) {
      return;
    }
    image.hidden = true;
    image.removeAttribute("src");
    image.onclick = null;
  }

  function openAssetPreview(asset, fallbackLabel, kind) {
    if (!asset) {
      return;
    }
    var previewSrc = assetImageUrl(asset, 900);
    var fullSrc = assetImageUrl(asset, 1600) || previewSrc;
    if (!previewSrc && asset.source_url) {
      previewSrc = asset.source_url;
      fullSrc = asset.source_url;
    }
    if (!previewSrc) {
      return;
    }
    openImagePreview({
      filename: asset.commons_filename || asset.local_path || "",
      label: fallbackLabel || asset.entity_name || asset.commons_filename || asset.local_path || kind || "image",
      kind: kind || asset.kind || "image",
      previewSrc: previewSrc,
      fullSrc: fullSrc,
      detailUrl: assetIdentityDetailUrl(asset, kind || asset.kind || "image") || fullSrc
    }, fallbackLabel, kind);
  }

  function openImagePreview(options, label, kind) {
    if (!options) {
      return;
    }
    if (typeof options === "string") {
      options = {
        filename: options,
        label: label,
        kind: kind
      };
    }
    var filename = options.filename || "";
    var previewSrc = options.previewSrc || (filename ? commonsFileUrl(filename, 900) : "");
    var fullSrc = options.fullSrc || (filename ? commonsFileUrl(filename, 1600) : previewSrc);
    var detailUrl = options.detailUrl || (filename ? visualIdentityDetailUrl(options.kind || "image", filename) : "");
    if (!previewSrc) {
      return;
    }
    var overlay = document.createElement("div");
    overlay.className = "image-preview-overlay";
    overlay.tabIndex = -1;

    var dialog = document.createElement("div");
    dialog.className = "image-preview-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");

    var header = document.createElement("div");
    header.className = "image-preview-header";
    var title = document.createElement("strong");
    title.textContent = options.label || filename || options.kind || "image";
    var closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "secondary";
    closeButton.setAttribute("aria-label", "Cerrar");
    closeButton.textContent = "x";
    header.appendChild(title);
    header.appendChild(closeButton);

    var image = document.createElement("img");
    image.src = previewSrc;
    image.alt = options.label || filename || options.kind || "";
    image.loading = "eager";

    var actions = document.createElement("div");
    actions.className = "image-preview-actions";
    var pageButton = document.createElement("button");
    pageButton.type = "button";
    pageButton.className = "secondary";
    pageButton.textContent = "Ficha";
    var fullButton = document.createElement("button");
    fullButton.type = "button";
    fullButton.textContent = "Imagen completa";
    actions.appendChild(pageButton);
    actions.appendChild(fullButton);

    function closePreview() {
      document.removeEventListener("keydown", onKeydown);
      overlay.remove();
    }

    function onKeydown(event) {
      if (event.key === "Escape") {
        closePreview();
      }
    }

    image.addEventListener("click", function () {
      window.open(detailUrl || fullSrc, "_blank", "noopener");
    });
    pageButton.addEventListener("click", function () {
      window.open(detailUrl || fullSrc, "_blank", "noopener");
    });
    fullButton.addEventListener("click", function () {
      window.open(fullSrc, "_blank", "noopener");
    });
    closeButton.addEventListener("click", closePreview);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        closePreview();
      }
    });
    document.addEventListener("keydown", onKeydown);

    dialog.appendChild(header);
    dialog.appendChild(image);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    closeButton.focus();
  }

  function uniqueValues(values) {
    var seen = {};
    return values.filter(function (value) {
      value = (value || "").toString().trim();
      if (!value || seen[value]) {
        return false;
      }
      seen[value] = true;
      return true;
    });
  }

  function normalizeLanguage(value) {
    var language = (value || "es").toLowerCase().replace("_", "-");
    if (language === "sr-latn" || language === "sr-latn-rs") {
      return "sr-el";
    }
    return language.split("-")[0];
  }

  function wikidataLanguages() {
    return uniqueValues([normalizeLanguage(document.documentElement.lang), "es", "en", "fr", "de", "ru", "it", "sr", "sr-el", "ar"]);
  }

  function wikidataQueryVariants(query) {
    query = (query || "").trim();
    return uniqueValues([query, query.split(",")[0].trim()]);
  }

  function readableResponseText(text, fallback) {
    var value = String(text || "").replace(/^\uFEFF/, "").trim();
    if (!value) {
      return fallback || "Error";
    }
    value = value.replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    return value.slice(0, 320) || fallback || "Error";
  }

  function parseJsonResponse(response) {
    return response.text().then(function (text) {
      var clean = String(text || "").replace(/^\uFEFF/, "").trim();
      var data = null;
      if (clean) {
        try {
          data = JSON.parse(clean);
        } catch (error) {
          var readable = readableResponseText(clean, "La respuesta del servidor no es JSON.");
          if (/^</.test(clean)) {
            readable = "El servidor devolvió una página HTML en vez de JSON. Recarga la página e inténtalo de nuevo. Detalle: " + readable;
          }
          throw new Error(readable);
        }
      } else {
        data = {};
      }
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || data.detail || response.statusText || "Error");
      }
      return data;
    });
  }

  function fetchJson(url) {
    return fetch(url).then(parseJsonResponse);
  }

  function formatNumber(value) {
    try {
      return new Intl.NumberFormat(document.documentElement.lang || "es").format(value || 0);
    } catch (error) {
      return String(value || 0);
    }
  }

  function formatPercent(value, total) {
    if (!total) {
      return "0%";
    }
    return ((value / total) * 100).toFixed(1) + "%";
  }

  function formatPercentNumber(value) {
    var percent = Number(value || 0);
    if (!Number.isFinite(percent)) {
      percent = 0;
    }
    return Math.max(0, Math.min(100, percent)).toFixed(2) + "%";
  }

  function chartColors() {
    return [
      "#0f766e",
      "#2563eb",
      "#b45309",
      "#7c3aed",
      "#be123c",
      "#15803d",
      "#c2410c",
      "#0891b2",
      "#a21caf",
      "#4d7c0f",
      "#4338ca",
      "#ca8a04",
      "#dc2626",
      "#0284c7",
      "#65a30d",
      "#db2777",
      "#0d9488",
      "#ea580c",
      "#9333ea",
      "#256d85",
      "#854d0e",
      "#1d4ed8",
      "#16a34a",
      "#e11d48"
    ];
  }

  function assignRowColors(rows) {
    var colors = chartColors();
    (rows || []).forEach(function (row, index) {
      row.color = row.color || colors[index] || "hsl(" + Math.round((index * 137.508) % 360) + " 72% 46%)";
    });
    return rows || [];
  }

  function colorByLabel(rows) {
    var lookup = {};
    (rows || []).forEach(function (row) {
      if (row.name) {
        lookup[row.name] = row.color;
      }
      if (row.label) {
        lookup[row.label] = row.color;
      }
    });
    return lookup;
  }

  function withChartColors(payload, colors) {
    if (!payload || !payload.items) {
      return payload;
    }
    return Object.assign({}, payload, {
      items: payload.items.map(function (item) {
        return Object.assign({}, item, {
          color: colors[item.label] || colors[item.name] || item.color
        });
      })
    });
  }

  function normalizeChartItems(data) {
    var rawItems = (data && data.items) || (data && data.countries) || [];
    return rawItems.map(function (item) {
      var value = item.value;
      if (value === undefined) {
        value = item.population;
      }
      if (value === undefined) {
        value = item.total;
      }
      value = Number(value || 0);
      return {
        key: item.key || item.code || item.label || "",
        code: item.code || item.key || "",
        label: item.label || item.name || item.code || item.key || "",
        value: Number.isFinite(value) ? value : 0,
        width: item.width,
        color: item.color,
        detailUrl: item.detail_url || item.detailUrl || ""
      };
    }).filter(function (item) {
      return item.value > 0;
    });
  }

  function chartTotal(data, items) {
    var total = Number(data && data.total ? data.total : 0);
    if (total > 0) {
      return total;
    }
    return items.reduce(function (sum, item) {
      return sum + item.value;
    }, 0);
  }

  function donutSegments(items, total, otherLabel, maxSegments, colorLimit) {
    var colors = chartColors();
    var limit = Number(maxSegments || 0);
    var visibleColorLimit = Number(colorLimit || 0);
    var visibleItems = limit > 0 && limit < items.length ? items.slice(0, limit) : items;
    var otherValue = limit > 0 && limit < items.length ? items.slice(limit).reduce(function (sum, item) {
      return sum + Number(item.value || 0);
    }, 0) : 0;
    var segments = visibleItems.map(function (item, index) {
      return {
        key: item.key || item.code || item.label,
        code: item.code,
        label: item.label,
        value: item.value,
        color: item.color || (visibleColorLimit > 0 && index >= visibleColorLimit ? "#cbd5e1" : colors[index % colors.length]),
        detailUrl: item.detailUrl
      };
    });
    if (otherValue > 0) {
      segments.push({
        key: "other",
        code: "other",
        label: otherLabel || "Otros paises",
        value: otherValue,
        color: "#94a3b8",
        detailUrl: ""
      });
    }
    return segments.filter(function (segment) {
      return total && segment.value > 0;
    });
  }

  function polarToCartesian(centerX, centerY, radius, angleInDegrees) {
    var angleInRadians = (angleInDegrees - 90) * Math.PI / 180;
    return {
      x: centerX + (radius * Math.cos(angleInRadians)),
      y: centerY + (radius * Math.sin(angleInRadians))
    };
  }

  function donutSlicePath(centerX, centerY, outerRadius, innerRadius, startAngle, endAngle) {
    var outerStart = polarToCartesian(centerX, centerY, outerRadius, endAngle);
    var outerEnd = polarToCartesian(centerX, centerY, outerRadius, startAngle);
    var innerStart = polarToCartesian(centerX, centerY, innerRadius, startAngle);
    var innerEnd = polarToCartesian(centerX, centerY, innerRadius, endAngle);
    var largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
    return [
      "M", outerStart.x, outerStart.y,
      "A", outerRadius, outerRadius, 0, largeArcFlag, 0, outerEnd.x, outerEnd.y,
      "L", innerStart.x, innerStart.y,
      "A", innerRadius, innerRadius, 0, largeArcFlag, 1, innerEnd.x, innerEnd.y,
      "Z"
    ].join(" ");
  }

  function showEmptyChart(container) {
    container.innerHTML = "";
    var empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = container.dataset.empty || "Sin datos";
    container.appendChild(empty);
  }

  function renderDonutChart(container, data) {
    if (container._chartTooltip && container._chartTooltip.parentNode) {
      container._chartTooltip.parentNode.removeChild(container._chartTooltip);
    }
    container._chartTooltip = null;

    var items = normalizeChartItems(data);
    var total = chartTotal(data, items);
    if (!items.length || !total) {
      showEmptyChart(container);
      return;
    }

    var segments = donutSegments(
      items,
      total,
      container.dataset.otherLabel,
      Number(container.dataset.maxSegments || 0),
      Number(container.dataset.colorLimit || 0)
    );
    var colorByKey = {};
    segments.forEach(function (segment) {
      colorByKey[segment.key] = segment.color;
    });

    container.innerHTML = "";
    var layout = document.createElement("div");
    layout.className = "population-pie-layout";

    var chart = document.createElement("div");
    chart.className = "population-pie";
    chart.setAttribute("role", "img");

    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("aria-hidden", "true");

    var center = document.createElement("div");
    center.className = "population-pie-center";
    var totalLabel = document.createElement("span");
    totalLabel.textContent = container.dataset.centerLabel || "Total";
    var totalValue = document.createElement("strong");
    totalValue.textContent = formatNumber(total);
    center.appendChild(totalLabel);
    center.appendChild(totalValue);

    var tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.hidden = true;
    container._chartTooltip = tooltip;
    document.body.appendChild(tooltip);

    function setCenter(label, value) {
      totalLabel.textContent = label;
      totalValue.textContent = formatNumber(value);
    }

    function positionTooltip(event) {
      if (tooltip.hidden) {
        return;
      }
      var padding = 12;
      var offset = 14;
      var x = event.clientX + offset;
      var y = event.clientY + offset;
      var width = tooltip.offsetWidth || 0;
      var height = tooltip.offsetHeight || 0;
      if (x + width + padding > window.innerWidth) {
        x = event.clientX - width - offset;
      }
      if (y + height + padding > window.innerHeight) {
        y = event.clientY - height - offset;
      }
      tooltip.style.left = Math.max(padding, Math.round(x)) + "px";
      tooltip.style.top = Math.max(padding, Math.round(y)) + "px";
    }

    function selectSegment(segment) {
      tooltip.hidden = true;
      if (!segment.detailUrl) {
        return;
      }
      container.dispatchEvent(new CustomEvent("ciudades:chart-item-click", {
        bubbles: true,
        detail: {
          item: segment,
          url: segment.detailUrl
        }
      }));
    }

    var cursor = 0;
    segments.forEach(function (segment) {
      var start = cursor;
      var share = segment.value / total;
      cursor += share * 360;
      var end = Math.min(cursor, start + 359.999);
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", donutSlicePath(50, 50, 48, 24, start, end));
      path.setAttribute("fill", segment.color);
      path.setAttribute("tabindex", "0");
      path.dataset.label = segment.label;
      path.dataset.value = segment.value;
      if (segment.detailUrl) {
        path.classList.add("is-clickable");
      }

      var tooltipLabel = segment.label + " - " + formatNumber(segment.value) + " (" + formatPercent(segment.value, total) + ")";
      path.setAttribute("aria-label", tooltipLabel);

      path.addEventListener("mouseenter", function (event) {
        setCenter(segment.label, segment.value);
        tooltip.textContent = tooltipLabel;
        tooltip.hidden = false;
        positionTooltip(event);
      });
      path.addEventListener("mousemove", positionTooltip);
      path.addEventListener("mouseleave", function () {
        setCenter(container.dataset.centerLabel || "Total", total);
        tooltip.hidden = true;
      });
      path.addEventListener("focus", function () {
        setCenter(segment.label, segment.value);
      });
      path.addEventListener("blur", function () {
        setCenter(container.dataset.centerLabel || "Total", total);
      });
      path.addEventListener("click", function () {
        selectSegment(segment);
      });
      path.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectSegment(segment);
        }
      });
      svg.appendChild(path);
    });

    chart.appendChild(svg);
    chart.appendChild(center);

    var sidePanel = document.createElement("div");
    sidePanel.className = "population-country-panel";

    var listRows = [];
    var list = document.createElement("div");
    list.className = "population-country-list";
    items.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "population-country-row";
      if (item.detailUrl) {
        row.classList.add("is-clickable");
        row.tabIndex = 0;
        row.addEventListener("click", function () {
          container.dispatchEvent(new CustomEvent("ciudades:chart-item-click", {
            bubbles: true,
            detail: {
              item: item,
              url: item.detailUrl
            }
          }));
        });
        row.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            row.click();
          }
        });
      }

      var marker = document.createElement("span");
      marker.className = "population-country-marker";
      marker.style.background = colorByKey[item.key] || "#94a3b8";

      var name = document.createElement("span");
      name.className = "population-country-name";
      name.textContent = item.label || item.code;

      var value = document.createElement("strong");
      value.textContent = formatNumber(item.value || 0);

      var percent = document.createElement("span");
      percent.className = "population-country-percent";
      percent.textContent = formatPercent(item.value || 0, total);

      row.appendChild(marker);
      row.appendChild(name);
      row.appendChild(value);
      row.appendChild(percent);
      list.appendChild(row);
      listRows.push({
        row: row,
        text: [item.label, item.code].join(" ").toLowerCase()
      });
    });

    layout.appendChild(chart);
    if (container.dataset.hideList !== "true") {
      if (container.dataset.searchLabel || container.dataset.searchPlaceholder) {
        var searchLabel = document.createElement("label");
        searchLabel.className = "chart-list-search";
        searchLabel.textContent = container.dataset.searchLabel || "";
        var searchInput = document.createElement("input");
        searchInput.type = "search";
        searchInput.placeholder = container.dataset.searchPlaceholder || "";
        searchInput.addEventListener("input", function () {
          var query = searchInput.value.trim().toLowerCase();
          listRows.forEach(function (entry) {
            entry.row.hidden = Boolean(query && entry.text.indexOf(query) === -1);
          });
        });
        searchLabel.appendChild(searchInput);
        sidePanel.appendChild(searchLabel);
      }
      sidePanel.appendChild(list);
      layout.appendChild(sidePanel);
    }
    container.appendChild(layout);
  }

  function renderBarChart(container, data) {
    var items = normalizeChartItems(data);
    if (!items.length) {
      showEmptyChart(container);
      return;
    }
    var maximum = items.reduce(function (max, item) {
      return Math.max(max, item.value);
    }, 0);
    container.innerHTML = "";
    var chart = document.createElement("div");
    chart.className = "bar-chart";
    items.forEach(function (item) {
      var width = item.width;
      if (width === undefined) {
        width = maximum ? Math.max(2, Math.round(item.value * 100 / maximum)) : 0;
      }
      var row = document.createElement("div");
      row.className = "bar-row";

      var label = document.createElement("span");
      label.className = "bar-label";
      label.textContent = item.label;
      label.title = item.label;

      var track = document.createElement("div");
      track.className = "bar-track";
      var fill = document.createElement("span");
      fill.style.width = width + "%";
      track.appendChild(fill);

      var value = document.createElement("strong");
      value.textContent = formatNumber(item.value);

      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(value);
      chart.appendChild(row);
    });
    container.appendChild(chart);
  }

  function renderDataChart(container, data) {
    var type = (data && data.type) || container.dataset.chartType || "bar";
    if (type === "donut") {
      renderDonutChart(container, data);
    } else {
      renderBarChart(container, data);
    }
  }

  function renderDonutListTable(eventRoot, parent, payload, title, sharedRows) {
    var items = normalizeChartItems(payload);
    var total = chartTotal(payload, items);
    var panel = document.createElement("div");
    panel.className = "dashboard-population-table";
    var heading = document.createElement("h3");
    heading.textContent = title;
    panel.appendChild(heading);

    var searchInput = null;
    if (!sharedRows) {
      var searchLabel = document.createElement("label");
      searchLabel.className = "chart-list-search";
      searchLabel.textContent = eventRoot.dataset.searchLabel || "";
      searchInput = document.createElement("input");
      searchInput.type = "search";
      searchInput.placeholder = eventRoot.dataset.searchPlaceholder || "";
      searchLabel.appendChild(searchInput);
      panel.appendChild(searchLabel);
    }

    var list = document.createElement("div");
    list.className = "population-country-list";
    var segments = donutSegments(items, total || 1, eventRoot.dataset.otherLabel, Number(eventRoot.dataset.maxSegments || 0), 10);
    var colorByKey = {};
    segments.forEach(function (segment) {
      colorByKey[segment.key] = segment.color;
    });
    var rows = sharedRows || [];
    items.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "population-country-row";
      if (item.detailUrl) {
        row.classList.add("is-clickable");
        row.tabIndex = 0;
        row.addEventListener("click", function () {
          eventRoot.dispatchEvent(new CustomEvent("ciudades:chart-item-click", {
            bubbles: true,
            detail: {
              item: item,
              url: item.detailUrl
            }
          }));
        });
        row.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            row.click();
          }
        });
      }
      var marker = document.createElement("span");
      marker.className = "population-country-marker";
      marker.style.background = colorByKey[item.key] || "#94a3b8";
      var name = document.createElement("span");
      name.className = "population-country-name";
      name.textContent = item.label || item.code || "";
      var value = document.createElement("strong");
      value.textContent = formatNumber(item.value || 0);
      var percent = document.createElement("span");
      percent.className = "population-country-percent";
      percent.textContent = formatPercent(item.value || 0, total);
      row.appendChild(marker);
      row.appendChild(name);
      row.appendChild(value);
      row.appendChild(percent);
      list.appendChild(row);
      rows.push({
        row: row,
        text: [item.label, item.code].join(" ").toLowerCase()
      });
    });
    if (searchInput) {
      searchInput.addEventListener("input", function () {
        var query = searchInput.value.trim().toLowerCase();
        rows.forEach(function (entry) {
          entry.row.hidden = Boolean(query && entry.text.indexOf(query) === -1);
        });
      });
    }
    panel.appendChild(list);
    parent.appendChild(panel);
  }

  function keyForChartItem(item) {
    return item && (item.key || item.code || item.label) || "";
  }

  function topColorMap(items, limit) {
    var colors = chartColors();
    var map = {};
    (items || []).slice().sort(function (left, right) {
      return Number(right.value || 0) - Number(left.value || 0);
    }).slice(0, limit).forEach(function (item, index) {
      map[keyForChartItem(item)] = item.color || colors[index % colors.length];
    });
    return map;
  }

  function sharedTopColorMap(groups, limit) {
    var colors = chartColors();
    var selected = [];
    var seen = {};
    (groups || []).forEach(function (items) {
      (items || []).slice().sort(function (left, right) {
        return Number(right.value || 0) - Number(left.value || 0);
      }).slice(0, limit).forEach(function (item) {
        var key = keyForChartItem(item);
        if (!key || seen[key]) {
          return;
        }
        seen[key] = true;
        selected.push(key);
      });
    });
    var map = {};
    selected.forEach(function (key, index) {
      map[key] = colors[index % colors.length];
    });
    return map;
  }

  function withSharedCountryColors(payload, colors) {
    if (!payload || !payload.items) {
      return payload;
    }
    return Object.assign({}, payload, {
      items: payload.items.map(function (item) {
        var key = keyForChartItem(item);
        return Object.assign({}, item, {
          color: colors[key] || "#cbd5e1"
        });
      })
    });
  }

  function chartItemRawValue(item) {
    if (!item) {
      return 0;
    }
    var value = item.value;
    if (value === undefined) {
      value = item.population;
    }
    if (value === undefined) {
      value = item.total;
    }
    if (value === undefined) {
      value = item.area_km2 || item.area;
    }
    return Number(value || 0);
  }

  function withSharedCountryColorsAndOther(payload, colors, otherLabel) {
    if (!payload || !payload.items) {
      return payload;
    }
    var otherValue = 0;
    var visibleItems = [];
    payload.items.forEach(function (item) {
      var key = keyForChartItem(item);
      var value = chartItemRawValue(item);
      if (colors[key]) {
        visibleItems.push(Object.assign({}, item, {
          color: colors[key]
        }));
      } else {
        otherValue += value;
      }
    });
    if (otherValue > 0) {
      visibleItems.push({
        key: "other",
        code: "other",
        label: otherLabel || "Otros paises",
        value: otherValue,
        color: "#94a3b8",
        detailUrl: ""
      });
    }
    return Object.assign({}, payload, {
      total: visibleItems.reduce(function (total, item) {
        return total + chartItemRawValue(item);
      }, 0),
      items: visibleItems
    });
  }

  function itemMapByKey(items) {
    var map = {};
    (items || []).forEach(function (item) {
      map[keyForChartItem(item)] = item;
    });
    return map;
  }

  function renderDashboardCombinedCountryTable(container, charts, sharedColors) {
    var populationItems = normalizeChartItems(charts.population || { items: [] });
    var areaItems = normalizeChartItems(charts.area || { items: [] });
    var populationByKey = itemMapByKey(populationItems);
    var areaByKey = itemMapByKey(areaItems);
    var populationTotal = chartTotal(charts.population, populationItems);
    var areaTotal = chartTotal(charts.area, areaItems);
    var rows = [];
    var seen = {};

    populationItems.concat(areaItems).forEach(function (item) {
      var key = keyForChartItem(item);
      if (!key || seen[key]) {
        return;
      }
      seen[key] = true;
      var population = populationByKey[key] || {};
      var area = areaByKey[key] || {};
      rows.push({
        key: key,
        label: population.label || area.label || population.code || area.code || "",
        code: population.code || area.code || "",
        detailUrl: population.detailUrl || area.detailUrl || "",
        population: Number(population.value || 0),
        area: Number(area.value || 0),
        population_percent: populationTotal ? Number(population.value || 0) / populationTotal * 100 : 0,
        area_percent: areaTotal ? Number(area.value || 0) / areaTotal * 100 : 0,
        color: sharedColors[key] || ""
      });
    });

    var panel = document.createElement("div");
    panel.className = "dashboard-population-table dashboard-population-combined";

    var searchLabel = document.createElement("label");
    searchLabel.className = "chart-list-search dashboard-country-search";
    searchLabel.textContent = container.dataset.searchLabel || "";
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = container.dataset.searchPlaceholder || "";
    searchLabel.appendChild(searchInput);
    panel.appendChild(searchLabel);

    var wrap = document.createElement("div");
    wrap.className = "table-wrap subtle dashboard-combined-wrap";
    var table = document.createElement("table");
    table.className = "compact-table dashboard-combined-table";
    var thead = document.createElement("thead");
    var header = document.createElement("tr");
    var columns = [
      ["color", ""],
      ["label", container.dataset.countryLabel || "Pais"],
      ["population", "POB"],
      ["population_percent", "% POB"],
      ["area", "KM2"],
      ["area_percent", "% KM2"]
    ];
    columns.forEach(function (column) {
      var th = document.createElement("th");
      if (column[0] === "color") {
        th.className = "color-column";
        th.setAttribute("aria-label", container.dataset.colorLabel || "Color");
        header.appendChild(th);
        return;
      }
      var button = document.createElement("button");
      button.type = "button";
      button.className = "table-sort-button";
      button.dataset.sort = column[0];
      button.textContent = column[1];
      th.appendChild(button);
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    wrap.appendChild(table);
    panel.appendChild(wrap);

    var sortKey = "population";
    var sortDirection = -1;

    function combinedComparable(row, key) {
      var value = row[key];
      if (value === null || value === undefined) {
        return "";
      }
      return typeof value === "number" ? value : String(value).toLowerCase();
    }

    function updateCombinedSortButtons() {
      table.querySelectorAll("[data-sort]").forEach(function (button) {
        var active = button.dataset.sort === sortKey;
        var column = columns.filter(function (item) {
          return item[0] === button.dataset.sort;
        })[0];
        button.classList.toggle("is-sorted", active);
        button.classList.toggle("is-desc", active && sortDirection < 0);
        button.classList.toggle("is-asc", active && sortDirection > 0);
        renderSortButtonLabel(button, column ? column[1] : button.dataset.sort, active, sortDirection);
      });
    }

    function attachCountryRowEvents(row, entry) {
      if (entry.detailUrl) {
        row.className = "is-clickable";
        row.tabIndex = 0;
        row.addEventListener("click", function () {
          container.dispatchEvent(new CustomEvent("ciudades:chart-item-click", {
            bubbles: true,
            detail: {
              item: entry,
              url: entry.detailUrl
            }
          }));
        });
        row.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            row.click();
          }
        });
      }
    }

    function drawCombinedRows() {
      var query = searchInput.value.trim().toLowerCase();
      var visibleRows = rows.filter(function (entry) {
        return !query || [entry.label, entry.code].join(" ").toLowerCase().indexOf(query) !== -1;
      }).sort(function (left, right) {
        var a = combinedComparable(left, sortKey);
        var b = combinedComparable(right, sortKey);
        if (a < b) {
          return -1 * sortDirection;
        }
        if (a > b) {
          return 1 * sortDirection;
        }
        return String(left.label).localeCompare(String(right.label));
      });

      tbody.innerHTML = "";
      if (!visibleRows.length) {
        var emptyRow = document.createElement("tr");
        var td = document.createElement("td");
        td.colSpan = columns.length;
        td.textContent = container.dataset.emptyLabel || "";
        emptyRow.appendChild(td);
        tbody.appendChild(emptyRow);
        updateCombinedSortButtons();
        return;
      }

      visibleRows.forEach(function (entry) {
        var row = document.createElement("tr");
        attachCountryRowEvents(row, entry);
        var colorCell = document.createElement("td");
        if (entry.color) {
          var marker = document.createElement("span");
          marker.className = "table-color-dot";
          marker.title = container.dataset.countryLabel || "Pais";
          marker.style.background = entry.color;
          colorCell.appendChild(marker);
        }
        row.appendChild(colorCell);
        [
          entry.label,
          formattedNumberOrDash(entry.population),
          formatPercent(entry.population, populationTotal),
          formattedNumberOrDash(entry.area),
          formatPercent(entry.area, areaTotal)
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          row.appendChild(td);
        });
        tbody.appendChild(row);
      });
      updateCombinedSortButtons();
    }

    table.querySelectorAll("[data-sort]").forEach(function (button) {
      button.addEventListener("click", function () {
        var nextKey = button.dataset.sort;
        if (sortKey === nextKey) {
          sortDirection *= -1;
        } else {
          sortKey = nextKey;
          sortDirection = 1;
        }
        drawCombinedRows();
      });
    });

    searchInput.addEventListener("input", function () {
      drawCombinedRows();
    });

    drawCombinedRows();
    container.appendChild(panel);
  }

  function renderDashboardPopulationCharts(container, payload) {
    var charts = payload && payload.charts;
    if (!charts) {
      renderDataChart(container, payload);
      return;
    }
    container.innerHTML = "";
    var visualGrid = document.createElement("div");
    visualGrid.className = "dashboard-population-chart-grid dashboard-population-visuals";
    var populationItems = normalizeChartItems(charts.population || { items: [] });
    var areaItems = normalizeChartItems(charts.area || { items: [] });
    var sharedColors = sharedTopColorMap([populationItems, areaItems], 10);
    var entries = [
      [container.dataset.populationLabel || "Poblacion", withSharedCountryColorsAndOther(charts.population, sharedColors, container.dataset.otherLabel || "")],
      [container.dataset.areaLabel || "Terreno", withSharedCountryColorsAndOther(charts.area, sharedColors, container.dataset.otherLabel || "")]
    ];
    entries.forEach(function (entry) {
      var panel = document.createElement("div");
      panel.className = "dashboard-population-chart";
      var title = document.createElement("h3");
      title.textContent = entry[0];
      var chart = document.createElement("div");
      chart.dataset.chartType = "donut";
      chart.dataset.otherLabel = container.dataset.otherLabel || "";
      chart.dataset.maxSegments = "";
      chart.dataset.colorLimit = "10";
      chart.dataset.centerLabel = entry[0];
      chart.dataset.hideList = "true";
      renderDataChart(chart, entry[1] || { type: "donut", items: [] });
      panel.appendChild(title);
      panel.appendChild(chart);
      visualGrid.appendChild(panel);
    });
    container.appendChild(visualGrid);
    renderDashboardCombinedCountryTable(container, charts, sharedColors);
  }

  var chartDataCache = {};

  function fetchChartData(url) {
    if (!chartDataCache[url]) {
      chartDataCache[url] = fetchJson(url);
    }
    return chartDataCache[url];
  }

  function chartPayloadForContainer(payload, container) {
    var key = container.dataset.chartKey;
    if (key && payload && payload.charts) {
      return payload.charts[key] || { type: container.dataset.chartType || "bar", items: [] };
    }
    return payload || { type: container.dataset.chartType || "bar", items: [] };
  }

  function loadDataChart(container) {
    var url = container.dataset.url;
    if (!url) {
      return;
    }
    setLoading(container, container.dataset.loading || "Cargando datos...");
    fetchChartData(url)
      .then(function (payload) {
        if (container.dataset.dashboardPopulation !== undefined && payload && payload.charts) {
          renderDashboardPopulationCharts(container, payload);
        } else {
          renderDataChart(container, chartPayloadForContainer(payload, container));
        }
      })
      .catch(function () {
          setError(container, container.dataset.error || "No se pudieron cargar los paises.");
      });
  }

  function initDataCharts(root) {
    (root || document).querySelectorAll("[data-chart-widget]").forEach(loadDataChart);
  }

  window.CiudadesCharts = {
    load: function (target) {
      if (target && target.matches && target.matches("[data-chart-widget]")) {
        loadDataChart(target);
      } else {
        initDataCharts(target || document);
      }
    },
    render: renderDataChart,
    renderBar: renderBarChart,
    renderDonut: renderDonutChart
  };

  function valueOrDash(value) {
    return value === null || value === undefined || value === "" ? "-" : value;
  }

  function formattedNumberOrDash(value) {
    return value === null || value === undefined || value === "" ? "-" : formatNumber(value);
  }

  function detailLabel(container, key, fallback) {
    return container.dataset[key] || fallback;
  }

  function appendTextDefinition(list, label, value, marker) {
    var dt = document.createElement("dt");
    dt.textContent = label;
    var dd = document.createElement("dd");
    dd.textContent = valueOrDash(value);
    if (marker) {
      dd.setAttribute(marker, "");
    }
    list.appendChild(dt);
    list.appendChild(dd);
  }

  function renderCountryGeneralPanel(panel, data, labels) {
    var country = data.country || {};
    var heading = document.createElement("h2");
    heading.textContent = labels.generalTitle;
    panel.appendChild(heading);
    var loading = createStatusElement("table-loading", panel.dataset.loading || "Cargando pais...", true);
    var content = document.createElement("div");
    content.hidden = true;
    panel.appendChild(loading);

    var media = document.createElement("div");
    media.className = "country-visual-grid";
    [
      ["flag", labels.flagLabel],
      ["coat", labels.coatLabel]
    ].forEach(function (slot) {
      var card = document.createElement("div");
      card.className = "country-visual-card";
      card.setAttribute("data-country-" + slot[0], "");
      var title = document.createElement("strong");
      title.textContent = slot[1];
      var image = document.createElement("img");
      image.alt = slot[1];
      image.hidden = true;
      var empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = "-";
      card.appendChild(title);
      card.appendChild(image);
      card.appendChild(empty);
      media.appendChild(card);
    });
    content.appendChild(media);

    var list = document.createElement("dl");
    list.className = "detail-list country-detail-list";
    appendTextDefinition(list, labels.nameLabel, country.name);
    appendTextDefinition(list, labels.officialNameLabel, country.official_name, "data-country-official");
    appendTextDefinition(list, labels.officialLanguageLabel, "", "data-country-official-language");
    appendTextDefinition(list, labels.capitalLabel, (country.capital || [])[0], "data-country-capital");
    appendTextDefinition(list, labels.populationLabel, formattedNumberOrDash(country.population));
    appendTextDefinition(list, labels.areaLabel, formattedNumberOrDash(country.area_km2));
    appendTextDefinition(list, labels.densityLabel, formattedNumberOrDash(country.density));
    appendTextDefinition(list, labels.subdivisionCountLabel, formattedNumberOrDash(country.subdivision_count));
    content.appendChild(list);
    panel.appendChild(content);
    function reveal() {
      loading.remove();
      content.hidden = false;
    }
    Promise.resolve(renderStoredCountryIdentity(panel, country)).then(reveal).catch(reveal);
  }

  function renderCountryTablePanel(panel, data, labels, baseUrl, target) {
    var heading = document.createElement("h2");
    heading.textContent = labels.tableTitle;
    panel.appendChild(heading);

    var controls = document.createElement("div");
    controls.className = "country-table-controls";

    var levelLabel = document.createElement("label");
    levelLabel.textContent = labels.levelLabel;
    var levelSelect = document.createElement("select");
    (data.levels || []).forEach(function (level) {
      var option = document.createElement("option");
      option.value = level.value;
      option.textContent = level.label + (level.entity_type ? " \u00b7 " + level.entity_type : "");
      option.selected = String(level.value) === String(data.selected_level);
      levelSelect.appendChild(option);
    });
    levelSelect.addEventListener("change", function () {
      loadCountryTableOnly(panel, baseUrl, levelSelect.value, target);
    });
    levelLabel.appendChild(levelSelect);

    var filterLabel = document.createElement("label");
    filterLabel.textContent = labels.filterLabel;
    var filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = labels.filterPlaceholder;
    filterLabel.appendChild(filter);

    var pageSizeLabel = document.createElement("label");
    pageSizeLabel.textContent = labels.pageSizeLabel;
    var pageSizeSelect = document.createElement("select");
    [10, 25, 50, 100].forEach(function (size) {
      var option = document.createElement("option");
      option.value = size;
      option.textContent = size;
      option.selected = size === 25;
      pageSizeSelect.appendChild(option);
    });
    pageSizeLabel.appendChild(pageSizeSelect);

    controls.appendChild(levelLabel);
    controls.appendChild(filterLabel);
    panel.appendChild(controls);

    var tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap subtle country-data-table-wrap";
    var table = document.createElement("table");
    table.className = "compact-table country-data-table";
    var thead = document.createElement("thead");
    var headerRow = document.createElement("tr");
    var columns = [
      ["name", labels.nameLabel],
      ["entity_type", labels.entityTypeLabel],
      ["population", "POB"],
      ["population_percent", "% POB"],
      ["area_km2", "KM2"],
      ["area_percent", "% KM2"],
      ["density", "DENS"],
      ["parent", labels.parentLabel]
    ];
    columns.forEach(function (column) {
      var th = document.createElement("th");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "table-sort-button";
      button.textContent = column[1];
      button.dataset.sort = column[0];
      th.appendChild(button);
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    panel.appendChild(tableWrap);

    var pagination = document.createElement("div");
    pagination.className = "country-table-pagination";
    var previousButton = document.createElement("button");
    previousButton.type = "button";
    previousButton.className = "secondary";
    previousButton.textContent = labels.previousLabel;
    var pageStatus = document.createElement("span");
    var nextButton = document.createElement("button");
    nextButton.type = "button";
    nextButton.className = "secondary";
    nextButton.textContent = labels.nextLabel;
    pagination.appendChild(previousButton);
    pagination.appendChild(pageStatus);
    pagination.appendChild(nextButton);
    pagination.appendChild(pageSizeLabel);
    panel.appendChild(pagination);

    var rows = (data.table && data.table.rows) || [];
    var sortKey = "name";
    var sortDirection = 1;
    var page = 1;
    var pageSize = 25;

    function comparable(row, key) {
      var value = row[key];
      if (value === null || value === undefined) {
        return "";
      }
      return typeof value === "number" ? value : String(value).toLowerCase();
    }

    function sortColumnLabel() {
      var match = columns.filter(function (column) {
        return column[0] === sortKey;
      })[0];
      return match ? match[1] : sortKey;
    }

    function updateSortButtons() {
      table.querySelectorAll("[data-sort]").forEach(function (button) {
        var active = button.dataset.sort === sortKey;
        button.classList.toggle("is-sorted", active);
        button.classList.toggle("is-desc", active && sortDirection < 0);
        button.classList.toggle("is-asc", active && sortDirection > 0);
        var column = columns.filter(function (item) {
          return item[0] === button.dataset.sort;
        })[0];
        renderSortButtonLabel(button, column ? column[1] : button.dataset.sort, active, sortDirection);
      });
    }

    function drawRows() {
      var query = filter.value.trim().toLowerCase();
      var filtered = rows.filter(function (row) {
        if (!query) {
          return true;
        }
        return [row.name, row.parent, row.entity_type].some(function (value) {
          return String(value || "").toLowerCase().indexOf(query) !== -1;
        });
      }).sort(function (left, right) {
        var a = comparable(left, sortKey);
        var b = comparable(right, sortKey);
        if (a < b) {
          return -1 * sortDirection;
        }
        if (a > b) {
          return 1 * sortDirection;
        }
        return 0;
      });
      var totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.min(Math.max(1, page), totalPages);
      var visible = filtered.slice((page - 1) * pageSize, page * pageSize);

      tbody.innerHTML = "";
      if (!visible.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = columns.length;
        emptyCell.className = "muted";
        emptyCell.textContent = labels.noData;
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
      }
      visible.forEach(function (row) {
        var tr = document.createElement("tr");
        [
          row.name,
          row.entity_type || "-",
          formattedNumberOrDash(row.population),
          formatPercentNumber(row.population_percent),
          formattedNumberOrDash(row.area_km2),
          formatPercentNumber(row.area_percent),
          formattedNumberOrDash(row.density),
          row.parent || "-"
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      previousButton.disabled = page <= 1;
      nextButton.disabled = page >= totalPages;
      pageStatus.textContent = labels.sortedByLabel + " " + sortColumnLabel() + " - " + page + "/" + totalPages + " - " + formatNumber(filtered.length);
      updateSortButtons();
    }

    filter.addEventListener("input", function () {
      page = 1;
      drawRows();
    });
    previousButton.addEventListener("click", function () {
      page -= 1;
      drawRows();
    });
    nextButton.addEventListener("click", function () {
      page += 1;
      drawRows();
    });
    pageSizeSelect.addEventListener("change", function () {
      pageSize = Number(pageSizeSelect.value || 25);
      page = 1;
      drawRows();
    });
    table.querySelectorAll("[data-sort]").forEach(function (button) {
      button.addEventListener("click", function () {
        var nextKey = button.dataset.sort;
        if (sortKey === nextKey) {
          sortDirection *= -1;
        } else {
          sortKey = nextKey;
          sortDirection = 1;
        }
        page = 1;
        drawRows();
      });
    });
    drawRows();
  }

  function relativeUrlFrom(url) {
    var parsed = new URL(url, window.location.origin);
    return parsed.pathname + parsed.search + parsed.hash;
  }

  function scrollToCountryTable(target) {
    if (!target) {
      return;
    }
    var table = target && target.querySelector(".country-data-table-wrap");
    (table || target).scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function loadCountryTableOnly(panel, url, level, target) {
    if (!panel || !url) {
      return;
    }
    var labels = countryDetailLabels(target);
    setLoading(panel, target.dataset.loading || "Cargando pais...");
    var finalUrl = new URL(url, window.location.origin);
    finalUrl.searchParams.set("level", level);
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        panel.innerHTML = "";
        renderCountryTablePanel(panel, data, labels, url, target);
        scrollToCountryTable(target);
      })
      .catch(function () {
        setError(panel, target.dataset.error || "No se pudo cargar el pais.");
      });
  }

  function chartPayloadFromShareRows(rows, valueKey, percentKey) {
    return {
      type: "donut",
      items: (rows || []).map(function (row, index) {
        var value = Number(row[valueKey] || 0);
        if (!Number.isFinite(value) || value <= 0) {
          value = Number(row[percentKey] || 0);
        }
        return {
          key: (row.name || "row") + "-" + index,
          label: row.name || "",
          value: Number.isFinite(value) ? value : 0,
          color: row.color
        };
      }).filter(function (item) {
        return item.value > 0;
      })
    };
  }

  function renderShareChartPair(parent, entries, className) {
    var charts = document.createElement("div");
    charts.className = className || "country-first-order-charts";
    entries.forEach(function (entry) {
      var chart = document.createElement("div");
      chart.className = "country-first-order-chart";
      chart.dataset.chartType = "donut";
      chart.dataset.centerLabel = entry[0];
      chart.dataset.hideList = "true";
      renderDataChart(chart, entry[1] || { type: "donut", items: [] });
      charts.appendChild(chart);
    });
    parent.appendChild(charts);
    return charts;
  }

  function appendShareSummaryTable(parent, rows, labels, extraWrapClass) {
    var compact = Boolean(extraWrapClass && extraWrapClass.indexOf("subdivision-share-wrap") !== -1);
    var searchLabel = document.createElement("label");
    searchLabel.className = "chart-list-search table-search-control";
    searchLabel.textContent = labels.filterLabel || "Filtrar";
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = labels.filterPlaceholder || "Filtrar por nombre o tipo";
    searchLabel.appendChild(searchInput);
    parent.appendChild(searchLabel);

    var wrap = document.createElement("div");
    wrap.className = "table-wrap subtle first-order-summary-wrap" + (extraWrapClass ? " " + extraWrapClass : "");
    var table = document.createElement("table");
    table.className = "compact-table first-order-summary-table";
    var thead = document.createElement("thead");
    var header = document.createElement("tr");
    var columns = [
      ["color", ""],
      ["name", labels.nameLabel],
      ["entity_type", labels.entityTypeLabel],
      ["population", "POB"],
      ["population_percent", "% POB"],
      ["area_km2", "KM2"],
      ["area_percent", "% KM2"]
    ];
    columns.forEach(function (column) {
      var th = document.createElement("th");
      if (column[0] === "color") {
        th.className = "color-column";
        th.setAttribute("aria-label", labels.colorLabel || "Color");
        header.appendChild(th);
        return;
      }
      var button = document.createElement("button");
      button.type = "button";
      button.className = "table-sort-button";
      button.dataset.sort = column[0];
      button.textContent = column[1];
      th.appendChild(button);
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    var dataRows = (rows || []).slice();
    var sortKey = "name";
    var sortDirection = 1;

    function shareComparable(row, key) {
      var value = row[key];
      if (value === null || value === undefined) {
        return "";
      }
      return typeof value === "number" ? value : String(value).toLowerCase();
    }

    function shareSearchText(row) {
      return [row.name, row.entity_type, row.population, row.population_percent, row.area_km2, row.area_percent]
        .map(function (value) { return String(value || "").toLowerCase(); })
        .join(" ");
    }

    function updateShareSortButtons() {
      table.querySelectorAll("[data-sort]").forEach(function (button) {
        var active = button.dataset.sort === sortKey;
        var column = columns.filter(function (item) {
          return item[0] === button.dataset.sort;
        })[0];
        button.classList.toggle("is-sorted", active);
        button.classList.toggle("is-desc", active && sortDirection < 0);
        button.classList.toggle("is-asc", active && sortDirection > 0);
        renderSortButtonLabel(button, column ? column[1] : button.dataset.sort, active, sortDirection);
      });
    }

    function drawShareRows() {
      var query = searchInput.value.trim().toLowerCase();
      var sortedRows = dataRows.filter(function (row) {
        return !query || shareSearchText(row).indexOf(query) !== -1;
      }).sort(function (left, right) {
        var a = shareComparable(left, sortKey);
        var b = shareComparable(right, sortKey);
        if (a < b) {
          return -1 * sortDirection;
        }
        if (a > b) {
          return 1 * sortDirection;
        }
        return String(left.name || "").localeCompare(String(right.name || ""));
      });
      tbody.innerHTML = "";
      if (!sortedRows.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = columns.length;
        emptyCell.className = "muted table-empty-cell";
        emptyCell.textContent = labels.noData;
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
        updateShareSortButtons();
        return;
      }
      sortedRows.forEach(function (rowData) {
        var row = document.createElement("tr");
        var colorCell = document.createElement("td");
        var marker = document.createElement("span");
        marker.className = "table-color-dot";
        marker.style.background = rowData.color || "#94a3b8";
        colorCell.appendChild(marker);
        row.appendChild(colorCell);
        [
          rowData.name,
          rowData.entity_type || "-",
          formattedNumberOrDash(rowData.population),
          formatPercentNumber(rowData.population_percent),
          formattedNumberOrDash(rowData.area_km2),
          formatPercentNumber(rowData.area_percent)
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          td.title = value;
          row.appendChild(td);
        });
        tbody.appendChild(row);
      });
      updateShareSortButtons();
    }

    table.querySelectorAll("[data-sort]").forEach(function (button) {
      button.addEventListener("click", function () {
        var nextKey = button.dataset.sort;
        if (sortKey === nextKey) {
          sortDirection *= -1;
        } else {
          sortKey = nextKey;
          sortDirection = 1;
        }
        drawShareRows();
      });
    });

    searchInput.addEventListener("input", function () {
      drawShareRows();
    });

    drawShareRows();
    wrap.appendChild(table);
    parent.appendChild(wrap);
  }

  function renderCountryChartsPanel(panel, data, labels) {
    var heading = document.createElement("h2");
    heading.textContent = labels.chartTitle;
    panel.appendChild(heading);

    var cards = (data.first_order && data.first_order.cards) || [];
    if (!cards.length) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = labels.noData;
      panel.appendChild(empty);
      return;
    }

    assignRowColors(cards);
    var colors = colorByLabel(cards);
    var layout = document.createElement("div");
    layout.className = "first-order-visual-layout first-order-visual-layout-stacked";
    var charts = document.createElement("div");
    charts.className = "first-order-chart-stack";
    var tablePanel = document.createElement("div");
    tablePanel.className = "population-country-panel first-order-color-panel";
    var searchLabel = document.createElement("label");
    searchLabel.className = "chart-list-search";
    searchLabel.textContent = labels.filterLabel;
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = labels.filterPlaceholder;
    searchLabel.appendChild(searchInput);
    tablePanel.appendChild(searchLabel);

    var tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap subtle first-order-color-legend-wrap";
    var table = document.createElement("table");
    table.className = "compact-table first-order-legend-table";
    var thead = document.createElement("thead");
    var header = document.createElement("tr");
    var columns = [
      ["color", ""],
      ["name", labels.nameLabel],
      ["entity_type", labels.entityTypeLabel],
      ["population", "POB"],
      ["population_percent", "% POB"],
      ["area_km2", "KM2"],
      ["area_percent", "% KM2"]
    ];
    columns.forEach(function (column) {
      var th = document.createElement("th");
      if (column[0] === "color") {
        th.className = "color-column";
        th.setAttribute("aria-label", labels.colorLabel || "Color");
        header.appendChild(th);
        return;
      }
      var button = document.createElement("button");
      button.type = "button";
      button.className = "table-sort-button";
      button.dataset.sort = column[0];
      button.textContent = column[1];
      th.appendChild(button);
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    tablePanel.appendChild(tableWrap);

    var dataRows = cards.slice();
    var sortKey = "name";
    var sortDirection = 1;

    function firstOrderComparable(row, key) {
      var value = row[key];
      if (value === null || value === undefined) {
        return "";
      }
      return typeof value === "number" ? value : String(value).toLowerCase();
    }

    function updateFirstOrderSortButtons() {
      table.querySelectorAll("[data-sort]").forEach(function (button) {
        var active = button.dataset.sort === sortKey;
        var column = columns.filter(function (item) {
          return item[0] === button.dataset.sort;
        })[0];
        button.classList.toggle("is-sorted", active);
        button.classList.toggle("is-desc", active && sortDirection < 0);
        button.classList.toggle("is-asc", active && sortDirection > 0);
        renderSortButtonLabel(button, column ? column[1] : button.dataset.sort, active, sortDirection);
      });
    }

    function drawFirstOrderRows() {
      var query = searchInput.value.trim().toLowerCase();
      var visibleRows = dataRows.filter(function (card) {
        return !query || [card.name, card.entity_type].join(" ").toLowerCase().indexOf(query) !== -1;
      }).sort(function (left, right) {
        var a = firstOrderComparable(left, sortKey);
        var b = firstOrderComparable(right, sortKey);
        if (a < b) {
          return -1 * sortDirection;
        }
        if (a > b) {
          return 1 * sortDirection;
        }
        return String(left.name).localeCompare(String(right.name));
      });

      tbody.innerHTML = "";
      if (!visibleRows.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = columns.length;
        emptyCell.textContent = labels.noData;
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
        updateFirstOrderSortButtons();
        return;
      }

      visibleRows.forEach(function (card) {
        var row = document.createElement("tr");
        var colorCell = document.createElement("td");
        var marker = document.createElement("span");
        marker.className = "table-color-dot";
        marker.style.background = card.color || "#94a3b8";
        colorCell.appendChild(marker);
        row.appendChild(colorCell);
        [
          card.name,
          card.entity_type || "-",
          formattedNumberOrDash(card.population),
          formatPercentNumber(card.population_percent),
          formattedNumberOrDash(card.area_km2),
          formatPercentNumber(card.area_percent)
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          row.appendChild(td);
        });
        tbody.appendChild(row);
      });
      updateFirstOrderSortButtons();
    }

    table.querySelectorAll("[data-sort]").forEach(function (button) {
      button.addEventListener("click", function () {
        var nextKey = button.dataset.sort;
        if (sortKey === nextKey) {
          sortDirection *= -1;
        } else {
          sortKey = nextKey;
          sortDirection = 1;
        }
        drawFirstOrderRows();
      });
    });

    searchInput.addEventListener("input", function () {
      drawFirstOrderRows();
    });

    renderShareChartPair(charts, [
      [labels.populationLabel, withChartColors(data.first_order && data.first_order.population_chart, colors)],
      [labels.areaLabel, withChartColors(data.first_order && data.first_order.area_chart, colors)]
    ], "country-first-order-charts");
    layout.appendChild(charts);
    layout.appendChild(tablePanel);
    drawFirstOrderRows();
    panel.appendChild(layout);
  }

  function percentMeter(percent) {
    var wrapper = document.createElement("span");
    wrapper.className = "percent-meter";
    var track = document.createElement("span");
    track.className = "percent-meter-track";
    var fill = document.createElement("span");
    fill.style.width = Math.max(0, Math.min(100, percent)) + "%";
    var value = document.createElement("strong");
    value.textContent = percent.toFixed(2) + "%";
    track.appendChild(fill);
    wrapper.appendChild(track);
    wrapper.appendChild(value);
    return wrapper;
  }

  function miniShare(label, percent) {
    var item = document.createElement("div");
    item.className = "mini-share-item";
    var pie = document.createElement("span");
    pie.className = "mini-share-pie";
    pie.style.background = "conic-gradient(var(--accent) 0 " + percent + "%, var(--chart-empty) " + percent + "% 100%)";
    var text = document.createElement("span");
    text.textContent = label + " \u00b7 " + percent.toFixed(2) + "%";
    item.appendChild(pie);
    item.appendChild(text);
    return item;
  }

  function percentPieValue(percent) {
    var value = Math.max(0, Math.min(100, Number(percent || 0)));
    var wrapper = document.createElement("span");
    wrapper.className = "percent-pie-value";
    var pie = document.createElement("span");
    pie.className = "mini-share-pie";
    pie.style.background = "conic-gradient(var(--accent) 0 " + value + "%, var(--chart-empty) " + value + "% 100%)";
    var text = document.createElement("strong");
    text.textContent = value.toFixed(2) + "%";
    wrapper.appendChild(pie);
    wrapper.appendChild(text);
    return wrapper;
  }

  function renderCountryShareCards(target, data, labels) {
    var cards = ((data.first_order && data.first_order.cards) || []).filter(function (card) {
      return (card.children || []).length > 0;
    });
    if (!cards.length) {
      return;
    }

    var section = document.createElement("section");
    section.className = "panel country-share-section";
    var heading = document.createElement("h2");
    heading.textContent = labels.cardsTitle;
    section.appendChild(heading);

    var grid = document.createElement("div");
    grid.className = "country-share-grid";
    cards.forEach(function (card) {
      var item = document.createElement("article");
      item.className = "country-share-card";
      var title = document.createElement("h3");
      title.textContent = card.name;
      var meta = document.createElement("p");
      meta.className = "muted";
      meta.textContent = card.entity_type || "";
      item.appendChild(title);
      item.appendChild(meta);

      var children = card.children || [];
      assignRowColors(children);
      renderShareChartPair(item, [
        [labels.populationLabel, chartPayloadFromShareRows(children, "population", "population_percent")],
        [labels.areaLabel, chartPayloadFromShareRows(children, "area_km2", "area_percent")]
      ], "country-first-order-charts country-share-card-charts");
      appendShareSummaryTable(item, children, labels, "subdivision-share-wrap");
      grid.appendChild(item);
    });
    section.appendChild(grid);
    target.appendChild(section);
  }

  function countryDetailLabels(target) {
    return {
      generalTitle: detailLabel(target, "generalTitle", "Datos generales del pais"),
      tableTitle: detailLabel(target, "tableTitle", "Tabla de datos"),
      chartTitle: detailLabel(target, "chartTitle", "Subdivisiones de primer orden"),
      cardsTitle: detailLabel(target, "cardsTitle", "Porcentaje por subdivision de primer orden"),
      childrenTitle: detailLabel(target, "childrenTitle", "Subdivisiones directas"),
      levelLabel: detailLabel(target, "levelLabel", "Nivel"),
      filterLabel: detailLabel(target, "filterLabel", "Filtrar"),
      filterPlaceholder: detailLabel(target, "filterPlaceholder", "Filtrar por nombre o padre"),
      pageSizeLabel: detailLabel(target, "pageSizeLabel", "Filas por pagina"),
      populationLabel: detailLabel(target, "populationLabel", "Poblacion"),
      areaLabel: detailLabel(target, "areaLabel", "Terreno"),
      densityLabel: detailLabel(target, "densityLabel", "Densidad"),
      parentLabel: detailLabel(target, "parentLabel", "Padre"),
      nameLabel: detailLabel(target, "nameLabel", "Nombre"),
      colorLabel: detailLabel(target, "colorLabel", "Color"),
      entityTypeLabel: detailLabel(target, "entityTypeLabel", "Tipo"),
      officialNameLabel: detailLabel(target, "officialNameLabel", "Nombre oficial"),
      officialLanguageLabel: detailLabel(target, "officialLanguageLabel", "Idioma oficial"),
      capitalLabel: detailLabel(target, "capitalLabel", "Capital"),
      subdivisionCountLabel: detailLabel(target, "subdivisionCountLabel", "Numero de subdivisiones"),
      flagLabel: detailLabel(target, "flagLabel", "Bandera"),
      coatLabel: detailLabel(target, "coatLabel", "Escudo"),
      openLabel: detailLabel(target, "openLabel", "Ver datos"),
      previousLabel: detailLabel(target, "previousLabel", "Anterior"),
      nextLabel: detailLabel(target, "nextLabel", "Siguiente"),
      sortedByLabel: detailLabel(target, "sortedByLabel", "Ordenado por"),
      noData: detailLabel(target, "noData", detailLabel(target, "empty", ""))
    };
  }

  function renderCountryDetail(target, data, baseUrl) {
    var labels = countryDetailLabels(target);
    target.innerHTML = "";
    target.hidden = false;

    var grid = document.createElement("div");
    grid.className = "country-detail-grid";
    var general = document.createElement("article");
    general.className = "panel country-detail-panel";
    var table = document.createElement("article");
    table.className = "panel country-detail-panel";
    var charts = document.createElement("article");
    charts.className = "panel country-detail-panel";

    renderCountryGeneralPanel(general, data, labels);
    renderCountryTablePanel(table, data, labels, baseUrl, target);
    renderCountryChartsPanel(charts, data, labels);

    grid.appendChild(general);
    grid.appendChild(table);
    grid.appendChild(charts);
    target.appendChild(grid);
    renderCountryShareCards(target, data, labels);
  }

  function loadCountryDetail(target, url, level) {
    if (!target || !url) {
      return;
    }
    target.hidden = false;
    setLoading(target, target.dataset.loading || "Cargando pais...");
    var finalUrl = new URL(url, window.location.origin);
    if (level !== undefined && level !== null && level !== "") {
      finalUrl.searchParams.set("level", level);
    }
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        renderCountryDetail(target, data, url);
        scrollToCountryTable(target);
      })
      .catch(function () {
        setError(target, target.dataset.error || "No se pudo cargar el pais.");
      });
  }

  function dashboardCountryDetailUrl(baseUrl, item) {
    var code = item && (item.code || item.key || item.country_code || item.country || item.slug);
    if (!baseUrl || !code) {
      return "";
    }
    return String(baseUrl).replace("__country__", encodeURIComponent(String(code)));
  }

  function initDashboardCountryDetail(root) {
    var scope = root || document;
    var detailTarget = scope.querySelector("[data-country-detail]") || document.querySelector("[data-country-detail]");
    if (!detailTarget) {
      return;
    }
    scope.querySelectorAll("[data-dashboard-population]").forEach(function (container) {
      if (container.dataset.countryDetailBound === "1") {
        return;
      }
      container.dataset.countryDetailBound = "1";
      container.addEventListener("ciudades:chart-item-click", function (event) {
        var detail = event.detail || {};
        var item = detail.item || {};
        var url = detail.url || dashboardCountryDetailUrl(container.dataset.countryDetailBaseUrl, item);
        if (!url) {
          return;
        }
        event.preventDefault();
        loadCountryDetail(detailTarget, url, item.level);
      });
    });
  }

  function renderStoredCountryIdentity(panel, country) {
    var visualAssets = (country && country.visual_assets) || {};
    var storedCoat = (country && country.coat_asset) || visualAssets.coat || (country && country.seal_asset) || visualAssets.seal;
    var storedCoatKind = ((country && country.coat_asset) || visualAssets.coat)
      ? "coat"
      : (((country && country.seal_asset) || visualAssets.seal) ? "seal" : "coat");
    if (!country) {
      return Promise.resolve();
    }
    var flagSlot = panel.querySelector("[data-country-flag]");
    var coatSlot = panel.querySelector("[data-country-coat]");
    var imagePromises = [];

    function showAssetPlaceholder(slot, kind) {
      if (!slot) {
        return;
      }
      var image = slot.querySelector("img");
      hideBrokenVisualImage(image);
      ensureVisualPlaceholder(slot, kind);
    }

    function showAsset(slot, asset, kind) {
      var url = storedVisualAssetImageUrl(asset);
      if (!slot || !url) {
        showAssetPlaceholder(slot, kind);
        return false;
      }
      var image = slot.querySelector("img");
      var empty = slot.querySelector(".muted, .visual-placeholder");
      if (image) {
        image.hidden = false;
        image.style.visibility = "hidden";
      }
      var loaded = new Promise(function (resolve) {
        image.onload = function () {
          image.hidden = false;
          image.style.visibility = "";
          if (empty) {
            empty.hidden = true;
          }
          resolve();
        };
        image.onerror = function () {
          image.style.visibility = "";
          showAssetPlaceholder(slot, kind);
          resolve();
        };
      });
      imagePromises.push(loaded);
      image.dataset.fullSrc = asset.remote_url || asset.image_url || url;
      image.src = url;
      image.onclick = function () {
        openStoredAssetPreview(asset, (country && country.name) || kind, kind);
      };
      image.style.cursor = "pointer";
      image.title = "Ver imagen" + (asset.status ? " · " + (asset.source ? (asset.source + " · " + asset.status) : asset.status) : "");
      return true;
    }

    showAsset(flagSlot, country.flag_asset || visualAssets.flag, "flag");
    showAsset(coatSlot, storedCoat, storedCoatKind);

    return Promise.all(imagePromises).then(function () {
      return true;
    });
  }

  function showStatsFlagAsset(slot, asset, label) {
    var url = storedVisualAssetImageUrl(asset);
    if (!slot || !url) {
      return false;
    }
    slot.innerHTML = "";
    var image = document.createElement("img");
    image.alt = label || "";
    image.loading = "lazy";
    image.hidden = false;
    image.style.visibility = "hidden";
    image.onload = function () {
      image.hidden = false;
      image.style.visibility = "";
    };
    image.onerror = function () {
      image.style.visibility = "";
      showStatsFlagPlaceholder(slot);
    };
    image.src = url;
    slot.appendChild(image);
    return true;
  }

  function showStatsFlagPlaceholder(slot) {
    if (!slot) {
      return;
    }
    slot.innerHTML = "";
    ensureVisualPlaceholder(slot, "flag");
  }

  function renderStatsCountryGrid(container, payload) {
    var countries = (payload && payload.countries) || [];
    container.innerHTML = "";
    if (!countries.length) {
      showEmptyChart(container);
      return;
    }
    var grid = document.createElement("div");
    grid.className = "stats-country-grid";
    countries.forEach(function (country) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "stats-country-card";
      card.dataset.detailUrl = country.detail_url || "";

      var flag = document.createElement("span");
      flag.className = "stats-country-flag";
      flag.dataset.statsCountryCode = country.code || "";
      var visualAssets = country.visual_assets || {};
      var storedFlag = country.flag_asset || visualAssets.flag;
      if (showStatsFlagAsset(flag, storedFlag, country.label || country.code)) {
        flag.dataset.statsCountryStored = "1";
      } else {
        showStatsFlagPlaceholder(flag);
      }
      var name = document.createElement("strong");
      name.textContent = country.label || country.code;
      var metrics = document.createElement("span");
      metrics.className = "stats-country-card-metrics";
      var population = document.createElement("span");
      population.textContent = (container.dataset.populationLabel || "Poblacion") + ": " + formattedNumberOrDash(country.population);
      var area = document.createElement("span");
      area.textContent = (container.dataset.areaLabel || "Terreno") + ": " + formattedNumberOrDash(country.area_km2);
      metrics.appendChild(population);
      metrics.appendChild(area);
      card.appendChild(flag);
      card.appendChild(name);
      card.appendChild(metrics);
      card.addEventListener("click", function () {
        var target = document.querySelector("[data-stats-country-detail]");
        if (target && card.dataset.detailUrl) {
          loadStatsCountryDetail(target, card.dataset.detailUrl, container);
        }
      });
      grid.appendChild(card);
    });
    container.appendChild(grid);
  }

  function statsChildRowsFromCountry(data) {
    return ((data.first_order && data.first_order.cards) || []).map(function (row) {
      return {
        id: row.id,
        name: row.name,
        entity_type: row.entity_type,
        population: row.population,
        population_percent: row.population_percent,
        area_km2: row.area_km2,
        area_percent: row.area_percent,
        density: row.density,
        child_count: row.child_count,
        detail_url: row.detail_url
      };
    });
  }

  function normalizedEntityType(value) {
    var text = String(value || "").trim();
    if (text.normalize) {
      text = text.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
    return text.toLowerCase();
  }

  function pluralizeEntityType(value) {
    var clean = String(value || "").trim();
    if (!clean) {
      return "";
    }
    var key = normalizedEntityType(clean);
    var language = (document.documentElement.lang || "es").toLowerCase();
    var spanishOverrides = {
      "autonomous community": "Comunidades aut\u00f3nomas",
      "comunidad autonoma": "Comunidades aut\u00f3nomas",
      "province": "Provincias",
      "provincia": "Provincias",
      "municipality": "Municipios",
      "municipio": "Municipios",
      "county": "Condados",
      "region": "Regiones",
      "region administrativa": "Regiones administrativas",
      "department": "Departamentos",
      "departamento": "Departamentos",
      "district": "Distritos",
      "distrito": "Distritos",
      "commune": "Comunas",
      "comuna": "Comunas",
      "state": "Estados",
      "estado": "Estados",
      "governorate": "Gobernaciones",
      "parish": "Parroquias",
      "pais": "Pa\u00edses"
    };
    var englishOverrides = {
      "autonomous community": "Autonomous Communities",
      "province": "Provinces",
      "municipality": "Municipalities",
      "county": "Counties",
      "region": "Regions",
      "department": "Departments",
      "district": "Districts",
      "commune": "Communes",
      "state": "States",
      "governorate": "Governorates",
      "parish": "Parishes",
      "country": "Countries"
    };
    if (language.indexOf("es") === 0 && spanishOverrides[key]) {
      return spanishOverrides[key];
    }
    if (language.indexOf("en") === 0 && englishOverrides[key]) {
      return englishOverrides[key];
    }
    if (/s$/i.test(clean) && key !== "pais") {
      return clean;
    }
    if (/z$/i.test(clean)) {
      return clean.slice(0, -1) + "ces";
    }
    if (/i\u00f3n$/i.test(clean)) {
      return clean.slice(0, -3) + "iones";
    }
    if (/^[A-Za-z][A-Za-z\s-]*$/.test(clean)) {
      if (/[^aeiou]y$/i.test(clean)) {
        return clean.slice(0, -1) + "ies";
      }
      if (/(s|x|ch|sh)$/i.test(clean)) {
        return clean + "es";
      }
      return clean + "s";
    }
    if (/[aeiou\u00e1\u00e9\u00f3]$/i.test(clean)) {
      return clean + "s";
    }
    return clean + "es";
  }

  function statsChildrenTitle(rows, fallback) {
    var counts = {};
    (rows || []).forEach(function (row) {
      var type = String(row.entity_type || "").trim();
      if (type) {
        counts[type] = (counts[type] || 0) + 1;
      }
    });
    var types = Object.keys(counts).sort(function (left, right) {
      if (counts[left] !== counts[right]) {
        return counts[right] - counts[left];
      }
      return left.localeCompare(right);
    });
    if (!types.length) {
      return fallback;
    }
    return types.map(pluralizeEntityType).join(" y ");
  }

  function chartPayloadFromStatsRows(rows, valueKey) {
    return {
      type: "donut",
      items: (rows || []).map(function (row, index) {
        return {
          key: row.id || (row.name || "row") + "-" + index,
          label: row.name || "",
          value: Number(row[valueKey] || 0),
          color: row.color,
          detailUrl: row.detail_url || ""
        };
      }).filter(function (item) {
        return Number.isFinite(item.value) && item.value > 0;
      })
    };
  }

  function hasPositiveStatsValue(rows, valueKey) {
    return (rows || []).some(function (row) {
      var value = Number(row[valueKey] || 0);
      return Number.isFinite(value) && value > 0;
    });
  }

  function renderStatsChildrenCharts(panel, rows, labels, openHandler) {
    var entries = [];
    if (hasPositiveStatsValue(rows, "population")) {
      entries.push([labels.populationLabel, chartPayloadFromStatsRows(rows, "population")]);
    }
    if (hasPositiveStatsValue(rows, "area_km2")) {
      entries.push([labels.areaLabel, chartPayloadFromStatsRows(rows, "area_km2")]);
    }
    if (!entries.length) {
      return;
    }
    var charts = renderShareChartPair(
      panel,
      entries,
      "country-first-order-charts stats-children-charts" + (entries.length === 1 ? " stats-children-charts-one" : "")
    );
    charts.addEventListener("ciudades:chart-item-click", function (event) {
      if (!event.detail || !event.detail.url || !openHandler) {
        return;
      }
      event.stopPropagation();
      openHandler({ detail_url: event.detail.url });
    });
  }

  function renderStatsChildrenPanel(panel, title, rows, labels, openHandler) {
    var preparedRows = assignRowColors((rows || []).slice());
    panel.innerHTML = "";

    if (!preparedRows.length) {
      panel.classList.add("stats-empty-only-panel");
      var empty = document.createElement("p");
      empty.className = "empty stats-empty-state";
      empty.textContent = labels.noData;
      panel.appendChild(empty);
      return;
    }

    panel.classList.remove("stats-empty-only-panel");
    var heading = document.createElement("h2");
    heading.textContent = title || statsChildrenTitle(preparedRows, labels.childrenTitle);
    panel.appendChild(heading);

    renderStatsChildrenCharts(panel, preparedRows, labels, openHandler);

    var searchLabel = document.createElement("label");
    searchLabel.className = "chart-list-search table-search-control";
    searchLabel.textContent = labels.filterLabel || "Filtrar";
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = labels.filterPlaceholder || "Filtrar por nombre o tipo";
    searchLabel.appendChild(searchInput);
    panel.appendChild(searchLabel);

    var wrap = document.createElement("div");
    wrap.className = "table-wrap subtle stats-children-wrap";
    var table = document.createElement("table");
    table.className = "compact-table stats-children-table";
    var thead = document.createElement("thead");
    var header = document.createElement("tr");
    var columns = [
      ["color", ""],
      ["name", labels.nameLabel],
      ["entity_type", labels.entityTypeLabel],
      ["population", "POB"],
      ["population_percent", "% POB"],
      ["area_km2", "KM2"],
      ["area_percent", "% KM2"],
      ["density", "DENS"],
      ["child_count", "Subd."]
    ];
    columns.forEach(function (column) {
      var th = document.createElement("th");
      if (column[0] === "color") {
        th.className = "color-column";
        th.setAttribute("aria-label", labels.colorLabel || "Color");
        header.appendChild(th);
        return;
      }
      var button = document.createElement("button");
      button.type = "button";
      button.className = "table-sort-button";
      button.dataset.sort = column[0];
      button.textContent = column[1];
      th.appendChild(button);
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    var sortKey = "name";
    var sortDirection = 1;

    function statsComparable(row, key) {
      var value = row[key];
      if (value === null || value === undefined) {
        return "";
      }
      return typeof value === "number" ? value : String(value).toLowerCase();
    }

    function statsSearchText(row) {
      return [row.name, row.entity_type, row.population, row.population_percent, row.area_km2, row.area_percent, row.density, row.child_count]
        .map(function (value) { return String(value || "").toLowerCase(); })
        .join(" ");
    }

    function updateStatsSortButtons() {
      table.querySelectorAll("[data-sort]").forEach(function (button) {
        var active = button.dataset.sort === sortKey;
        var column = columns.filter(function (item) {
          return item[0] === button.dataset.sort;
        })[0];
        button.classList.toggle("is-sorted", active);
        button.classList.toggle("is-desc", active && sortDirection < 0);
        button.classList.toggle("is-asc", active && sortDirection > 0);
        renderSortButtonLabel(button, column ? column[1] : button.dataset.sort, active, sortDirection);
      });
    }

    function drawStatsRows() {
      var query = searchInput.value.trim().toLowerCase();
      var visibleRows = preparedRows.filter(function (row) {
        return !query || statsSearchText(row).indexOf(query) !== -1;
      }).sort(function (left, right) {
        var a = statsComparable(left, sortKey);
        var b = statsComparable(right, sortKey);
        if (a < b) {
          return -1 * sortDirection;
        }
        if (a > b) {
          return 1 * sortDirection;
        }
        return String(left.name || "").localeCompare(String(right.name || ""));
      });

      tbody.innerHTML = "";
      if (!visibleRows.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = columns.length;
        emptyCell.className = "muted table-empty-cell";
        emptyCell.textContent = labels.noData;
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
        updateStatsSortButtons();
        return;
      }

      visibleRows.forEach(function (item) {
        var row = document.createElement("tr");
        if (item.detail_url && openHandler) {
          row.classList.add("is-clickable");
          row.tabIndex = 0;
          row.addEventListener("click", function () {
            openHandler(item);
          });
          row.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              row.click();
            }
          });
        }

        var colorCell = document.createElement("td");
        var marker = document.createElement("span");
        marker.className = "table-color-dot";
        marker.style.background = item.color || "#94a3b8";
        colorCell.appendChild(marker);
        row.appendChild(colorCell);

        [
          item.name || "-",
          item.entity_type || "-",
          formattedNumberOrDash(item.population),
          formatPercentNumber(item.population_percent),
          formattedNumberOrDash(item.area_km2),
          formatPercentNumber(item.area_percent),
          formattedNumberOrDash(item.density),
          formattedNumberOrDash(item.child_count)
        ].forEach(function (value, index) {
          var td = document.createElement("td");
          if (index === 0) {
            td.className = "stats-child-name-cell";
            var name = document.createElement("strong");
            name.textContent = value;
            td.appendChild(name);
          } else {
            td.textContent = value;
          }
          td.title = value;
          row.appendChild(td);
        });
        tbody.appendChild(row);
      });
      updateStatsSortButtons();
    }

    table.querySelectorAll("[data-sort]").forEach(function (button) {
      button.addEventListener("click", function () {
        var nextKey = button.dataset.sort;
        if (sortKey === nextKey) {
          sortDirection *= -1;
        } else {
          sortKey = nextKey;
          sortDirection = 1;
        }
        drawStatsRows();
      });
    });

    searchInput.addEventListener("input", function () {
      drawStatsRows();
    });

    drawStatsRows();
    wrap.appendChild(table);
    panel.appendChild(wrap);
  }

  function trimStatsAreaStack(stack, depth) {
    Array.prototype.slice.call(stack.querySelectorAll("[data-stats-area-depth]")).forEach(function (panel) {
      if (Number(panel.dataset.statsAreaDepth || 0) > depth) {
        panel.remove();
      }
    });
  }

  function renderStatsAreaPanel(panel, payload, labels, stack, depth, source) {
    var area = payload.area || {};
    panel.innerHTML = "";
    var grid = document.createElement("div");
    grid.className = "stats-country-detail-grid stats-area-detail-grid";
    var basicPanel = document.createElement("article");
    basicPanel.className = "panel stats-area-basic-panel";
    basicPanel.dataset.loading = source.dataset.loading || "";
    var childrenPanel = document.createElement("article");
    childrenPanel.className = "panel stats-country-first-level-panel";

    renderCountryGeneralPanel(basicPanel, { country: area }, Object.assign({}, labels, {
      generalTitle: area.name || labels.generalTitle
    }));

    renderStatsChildrenPanel(childrenPanel, null, payload.children || [], labels, function (item) {
      loadStatsAreaDetail(stack, item.detail_url, labels, source, depth);
    });
    grid.appendChild(basicPanel);
    grid.appendChild(childrenPanel);
    panel.appendChild(grid);
  }

  function loadStatsAreaDetail(stack, url, labels, source, depth) {
    if (!stack || !url) {
      return;
    }
    trimStatsAreaStack(stack, depth);
    var panel = document.createElement("div");
    panel.className = "stats-area-panel";
    panel.dataset.statsAreaDepth = String(depth + 1);
    var loadingPanel = document.createElement("article");
    loadingPanel.className = "panel";
    setLoading(loadingPanel, source.dataset.loading || "Cargando pais...");
    panel.appendChild(loadingPanel);
    stack.appendChild(panel);
    fetchJson(url)
      .then(function (payload) {
        renderStatsAreaPanel(panel, payload, labels, stack, depth + 1, source);
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function () {
        setError(loadingPanel, source.dataset.error || "No se pudo cargar el pais.");
      });
  }

  function loadStatsCountryDetail(target, url, source) {
    setLoading(target, target.dataset.loading || "Cargando pais...");
    target.hidden = false;
    fetchJson(url)
      .then(function (data) {
        var labels = countryDetailLabels(source || target);
        target.innerHTML = "";
        var grid = document.createElement("div");
        grid.className = "stats-country-detail-grid";

        var basicPanel = document.createElement("article");
        basicPanel.className = "panel stats-country-basic-panel";
        basicPanel.dataset.loading = target.dataset.loading || "";
        renderCountryGeneralPanel(basicPanel, data, labels);

        var firstLevelPanel = document.createElement("article");
        firstLevelPanel.className = "panel stats-country-first-level-panel";

        var stack = document.createElement("div");
        stack.className = "stats-area-stack";

        renderStatsChildrenPanel(firstLevelPanel, null, statsChildRowsFromCountry(data), labels, function (item) {
          loadStatsAreaDetail(stack, item.detail_url, labels, target, 0);
        });

        grid.appendChild(basicPanel);
        grid.appendChild(firstLevelPanel);
        target.appendChild(grid);
        target.appendChild(stack);
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function () {
        setError(target, target.dataset.error || "No se pudo cargar el pais.");
      });
  }

  function readStatsCountryCache(url) {
    try {
      var raw = window.sessionStorage.getItem(STATS_COUNTRY_CACHE_KEY) || "";
      var cached = raw ? JSON.parse(raw) : null;
      if (cached && cached.url === url && cached.payload) {
        return cached.payload;
      }
    } catch (error) {}
    return null;
  }

  function writeStatsCountryCache(url, payload) {
    try {
      window.sessionStorage.setItem(STATS_COUNTRY_CACHE_KEY, JSON.stringify({
        url: url,
        payload: payload
      }));
    } catch (error) {}
  }

  function initStatsCountries(root) {
    var container = (root || document).querySelector("[data-stats-countries]");
    if (!container) {
      return;
    }
    var cachedPayload = readStatsCountryCache(container.dataset.url);
    setLoading(container, container.dataset.loading || "Cargando datos...");
    fetchJson(container.dataset.url)
      .then(function (payload) {
        writeStatsCountryCache(container.dataset.url, payload);
        renderStatsCountryGrid(container, payload);
      })
      .catch(function () {
        if (cachedPayload) {
          renderStatsCountryGrid(container, cachedPayload);
        } else {
          setError(container, container.dataset.error || "No se pudieron cargar los paises.");
        }
      });
  }

  function searchWikidataEntity(query, languages) {
    var variants = wikidataQueryVariants(query);
    var searchLanguages = uniqueValues(languages.concat(["en", "es"]));

    function attempt(variantIndex, languageIndex) {
      if (variantIndex >= variants.length) {
        return Promise.reject(new Error("no wikidata result"));
      }
      if (languageIndex >= searchLanguages.length) {
        return attempt(variantIndex + 1, 0);
      }
      var url = "https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&origin=*&limit=1&language=" +
        encodeURIComponent(searchLanguages[languageIndex]) + "&search=" + encodeURIComponent(variants[variantIndex]);
      return fetchJson(url).then(function (search) {
        if (search.search && search.search.length) {
          return search.search[0].id;
        }
        return attempt(variantIndex, languageIndex + 1);
      });
    }

    return attempt(0, 0);
  }

  function fetchWikidataEntities(ids, props, languages) {
    ids = uniqueValues(ids);
    if (!ids.length) {
      return Promise.resolve({});
    }
    var chunks = [];
    for (var start = 0; start < ids.length; start += 50) {
      chunks.push(ids.slice(start, start + 50));
    }
    return Promise.all(chunks.map(function (chunk) {
      var url = "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&origin=*&ids=" +
        encodeURIComponent(chunk.join("|")) + "&props=" + encodeURIComponent(props) +
        "&languages=" + encodeURIComponent((languages || ["en"]).join("|"));
      return fetchJson(url).then(function (data) {
        return data.entities || {};
      });
    })).then(function (items) {
      return items.reduce(function (merged, entities) {
        Object.keys(entities || {}).forEach(function (id) {
          merged[id] = entities[id];
        });
        return merged;
      }, {});
    });
  }

  function claimEntityIds(claims, property) {
    return (claims[property] || []).map(function (claim) {
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!value || value["entity-type"] !== "item") {
        return "";
      }
      return value.id || ("Q" + value["numeric-id"]);
    }).filter(Boolean);
  }

  function claimTextValues(claims, property, languages) {
    var values = (claims[property] || []).map(function (claim) {
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!value) {
        return null;
      }
      if (typeof value === "string") {
        return { language: "", text: value };
      }
      return {
        language: value.language || "",
        text: value.text || value.value || ""
      };
    }).filter(function (value) {
      return value && value.text;
    });
    values.sort(function (left, right) {
      var leftIndex = languages.indexOf(left.language);
      var rightIndex = languages.indexOf(right.language);
      leftIndex = leftIndex === -1 ? 999 : leftIndex;
      rightIndex = rightIndex === -1 ? 999 : rightIndex;
      return leftIndex - rightIndex;
    });
    return uniqueValues(values.map(function (value) {
      return value.text;
    }));
  }

  function labelForEntity(entity, languages) {
    if (!entity || !entity.labels) {
      return "";
    }
    for (var index = 0; index < languages.length; index += 1) {
      var label = entity.labels[languages[index]];
      if (label && label.value) {
        return label.value;
      }
    }
    return "";
  }

  function appendDefinition(list, label, value, href) {
    if (!list || !value) {
      return false;
    }
    var dt = document.createElement("dt");
    dt.textContent = label;
    var dd = document.createElement("dd");
    if (href) {
      var link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = value;
      dd.appendChild(link);
    } else {
      dd.textContent = value;
    }
    list.appendChild(dt);
    list.appendChild(dd);
    return true;
  }

  function renderTranslations(entity, languages, list) {
    var added = false;
    if (!entity || !entity.labels || !list) {
      return false;
    }
    languages.forEach(function (language) {
      var label = entity.labels[language];
      if (label && label.value) {
        added = appendDefinition(list, language.toUpperCase(), label.value) || added;
      }
    });
    return added;
  }

  function renderImageSlots(claims, slots, status, noImagesMessage) {
    var shown = false;
    var slotKinds = {
      P41: "flag",
      P94: "coat",
      P242: "locator"
    };
    Object.keys(slots).forEach(function (property) {
      var slot = slots[property];
      var claim = claims[property] && claims[property][0];
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!slot || !value) {
        return;
      }
      var kind = slotKinds[property] || "image";
      var placeholder = ensureVisualPlaceholder(slot, kind);
      var image = slot.querySelector("img");
      if (!image) {
        image = document.createElement("img");
        slot.appendChild(image);
      }
      image.hidden = false;
      image.style.visibility = "hidden";
      image.onload = function () {
        image.hidden = false;
        image.style.visibility = "";
        if (placeholder) {
          placeholder.hidden = true;
        }
      };
      image.onerror = function () {
        image.style.visibility = "";
        hideBrokenVisualImage(image);
        ensureVisualPlaceholder(slot, kind);
      };
      image.src = commonsFileUrl(value, 420);
      image.alt = value;
      image.onclick = function () {
        openImagePreview({
          filename: value,
          label: value,
          kind: kind,
          previewSrc: commonsFileUrl(value, 900),
          fullSrc: commonsFileUrl(value, 1600),
          detailUrl: visualIdentityDetailUrl(kind, value)
        }, value, kind);
      };
      image.style.cursor = "pointer";
      image.title = "Ver imagen";
      slot.hidden = false;
      shown = true;
    });
    if (status) {
      if (shown) {
        status.hidden = true;
      } else {
        setInlineStatus(status, noImagesMessage, false);
      }
    }
    return shown;
  }

  function renderClaimFacts(claims, entities, languages, list, labels) {
    var added = false;
    [
      ["P17", labels.country],
      ["P36", labels.capital],
      ["P131", labels.location]
    ].forEach(function (item) {
      var names = claimEntityIds(claims, item[0]).map(function (id) {
        return labelForEntity(entities[id], languages);
      }).filter(Boolean);
      if (names.length) {
        added = appendDefinition(list, item[1], uniqueValues(names).join(", ")) || added;
      }
    });
    return added;
  }

  function parseRelatedPlaces(raw) {
    if (!raw) {
      return [];
    }
    try {
      var places = JSON.parse(raw);
      return Array.isArray(places) ? places : [];
    } catch (error) {
      return [];
    }
  }

  function renderRelatedPlaces(places, languages, list) {
    if (!places.length || !list) {
      return Promise.resolve(false);
    }
    var rendered = false;
    var searches = places.slice(0, 8).map(function (place) {
      return searchWikidataEntity(place.query || place.name, languages)
        .then(function (id) {
          return fetchWikidataEntities([id], "labels", languages).then(function (entities) {
            var entity = entities[id];
            var label = labelForEntity(entity, languages);
            if (!label) {
              return false;
            }
            rendered = appendDefinition(
              list,
              place.kind || place.name,
              label,
              "https://www.wikidata.org/wiki/" + id
            ) || rendered;
            return true;
          });
        })
        .catch(function () {
          return false;
        });
    });
    return Promise.all(searches).then(function () {
      return rendered;
    });
  }

  function initVisualIdentity() {
    var panel = document.querySelector("[data-visual-identity]");
    if (!panel) {
      return;
    }
    var query = panel.dataset.query;
    var languages = wikidataLanguages();
    var status = panel.querySelector("[data-visual-status]");
    var knowledgeStatus = panel.querySelector("[data-knowledge-status]");
    var translationList = panel.querySelector("[data-translation-list]");
    var factList = panel.querySelector("[data-fact-list]");
    var relatedList = panel.querySelector("[data-related-place-list]");
    var noImagesMessage = panel.dataset.noImagesMessage || "No se encontraron imagenes automaticas. Usa las busquedas externas.";
    var noWikidataMessage = panel.dataset.noWikidataMessage || "No se encontraron traducciones o capitales automaticas en Wikidata.";
    var slots = {
      P41: panel.querySelector("[data-visual-slot='flag']"),
      P94: panel.querySelector("[data-visual-slot='coat']"),
      P242: panel.querySelector("[data-visual-slot='locator']")
    };
    var labels = {
      country: panel.dataset.countryLabel || "Pais en Wikidata",
      capital: panel.dataset.capitalLabel || "Capitales en Wikidata",
      location: panel.dataset.locationLabel || "Region superior en Wikidata",
      source: panel.dataset.sourceLabel || "Ficha Wikidata"
    };
    var relatedPlaces = parseRelatedPlaces(panel.dataset.relatedPlaces);
    if (status) {
      setInlineStatus(status, status.textContent || "Buscando imagenes en Wikidata...", true);
    }
    if (knowledgeStatus) {
      setInlineStatus(knowledgeStatus, knowledgeStatus.textContent || "Buscando nombres traducidos, paises y capitales...", true);
    }

    var mainKnowledge = searchWikidataEntity(query, languages)
      .then(function (id) {
        return fetchWikidataEntities([id], "claims|labels", languages).then(function (entities) {
          var entity = entities[id];
          var claims = entity && entity.claims ? entity.claims : {};
          var targetIds = claimEntityIds(claims, "P17")
            .concat(claimEntityIds(claims, "P36"))
            .concat(claimEntityIds(claims, "P131"));

          renderImageSlots(claims, slots, status, noImagesMessage);

          return fetchWikidataEntities(targetIds, "labels", languages).then(function (targetEntities) {
            var translated = renderTranslations(entity, languages, translationList);
            var facts = renderClaimFacts(claims, targetEntities, languages, factList, labels);
            var source = appendDefinition(factList, labels.source, id, "https://www.wikidata.org/wiki/" + id);
            return translated || facts || source;
          });
        });
      })
      .catch(function () {
        if (status) {
          setInlineStatus(status, noImagesMessage, false);
        }
        return false;
      });

    var relatedKnowledge = renderRelatedPlaces(relatedPlaces, languages, relatedList);
    Promise.all([mainKnowledge, relatedKnowledge]).then(function (results) {
      if (!knowledgeStatus) {
        return;
      }
      if (results.some(Boolean)) {
        knowledgeStatus.hidden = true;
      } else {
        setInlineStatus(knowledgeStatus, noWikidataMessage, false);
      }
    }).catch(function () {
      if (knowledgeStatus) {
        setInlineStatus(knowledgeStatus, noWikidataMessage, false);
      }
    });
  }

  function initConfigTables(root) {
    (root || document).querySelectorAll("[data-config-table]").forEach(function (container) {
      var input = container.querySelector("[data-config-table-search]");
      var table = container.querySelector("table");
      var tbody = table && table.querySelector("tbody");
      if (!input || !table || !tbody) {
        return;
      }
      var sortKey = "country";
      var sortDirection = 1;
      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-config-row]"));

      function comparable(row, key) {
        var value = row.dataset[key] || "";
        if (["pages", "cities", "rows"].indexOf(key) !== -1) {
          return Number(value || 0);
        }
        return String(value).toLowerCase();
      }

      function buttonLabel(button) {
        return button.dataset.baseLabel || button.textContent.replace(/\s+[\u2191\u2193]$/, "");
      }

      table.querySelectorAll("[data-sort]").forEach(function (button) {
        button.dataset.baseLabel = buttonLabel(button);
        button.addEventListener("click", function () {
          var nextKey = button.dataset.sort;
          if (sortKey === nextKey) {
            sortDirection *= -1;
          } else {
            sortKey = nextKey;
            sortDirection = 1;
          }
          draw();
        });
      });

      function updateButtons() {
        table.querySelectorAll("[data-sort]").forEach(function (button) {
          var active = button.dataset.sort === sortKey;
          button.classList.toggle("is-sorted", active);
          button.classList.toggle("is-asc", active && sortDirection > 0);
          button.classList.toggle("is-desc", active && sortDirection < 0);
          renderSortButtonLabel(button, buttonLabel(button), active, sortDirection);
        });
      }

      function draw() {
        var query = input.value.trim().toLowerCase();
        var visible = rows.filter(function (row) {
          return !query || String(row.dataset.search || "").toLowerCase().indexOf(query) !== -1;
        }).sort(function (left, right) {
          var a = comparable(left, sortKey);
          var b = comparable(right, sortKey);
          if (a < b) {
            return -1 * sortDirection;
          }
          if (a > b) {
            return 1 * sortDirection;
          }
          return String(left.dataset.country || "").localeCompare(String(right.dataset.country || ""));
        });
        rows.forEach(function (row) { row.remove(); });
        var empty = tbody.querySelector("[data-empty-row]");
        if (empty) {
          empty.remove();
        }
        visible.forEach(function (row) { tbody.appendChild(row); });
        if (!visible.length) {
          empty = document.createElement("tr");
          empty.dataset.emptyRow = "1";
          var cell = document.createElement("td");
          cell.colSpan = 7;
          cell.className = "table-empty-cell";
          cell.textContent = container.dataset.emptyLabel || "";
          empty.appendChild(cell);
          tbody.appendChild(empty);
        }
        updateButtons();
      }

      input.addEventListener("input", draw);
      container.addEventListener("config-table-row-updated", function () {
        rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-config-row]"));
        draw();
      });
      draw();
      scheduleActiveConfigRowRefresh(container);
    });
  }

  function configTaskKey(status) {
    return String(status || "").replace(/[^a-z]/g, "");
  }

  function configTaskLabel(container, status) {
    container = container || document.body;
    var key = configTaskKey(status);
    if (key === "pending" || key === "none") {
      return container.dataset.pendingLabel || "Por Validar";
    }
    if (key === "validating") {
      return container.dataset.validatingLabel || "Validando";
    }
    if (key === "validated") {
      return container.dataset.validatedLabel || "Validado";
    }
    if (key === "populating") {
      return container.dataset.populatingLabel || "Populando";
    }
    if (key === "populated") {
      return container.dataset.populatedLabel || "Populado";
    }
    if (key === "stopped") {
      return container.dataset.stoppedLabel || "Parado";
    }
    if (key === "invalid") {
      return container.dataset.invalidLabel || "Fallo";
    }
    if (key === "running") {
      return container.dataset.runningLabel || status;
    }
    if (key === "queued") {
      return container.dataset.queuedLabel || status;
    }
    if (key === "succeeded") {
      return container.dataset.succeededLabel || status;
    }
    if (key === "failed") {
      return container.dataset.failedLabel || "Fallo";
    }
    if (key === "cancelled") {
      return container.dataset.cancelledLabel || status;
    }
    if (key === "viewtasklog") {
      return container.dataset.logLabel || "Ver registro";
    }
    return status || "";
  }

  function configTaskClass(status) {
    var key = configTaskKey(status);
    if (key === "none" || !key) {
      key = "pending";
    }
    if (key === "cancelled") {
      key = "stopped";
    }
    if (key === "invalid") {
      key = "failed";
    }
    return "config-status-" + key;
  }

  function configTaskIcon(status) {
    var key = configTaskKey(status);
    if (["succeeded", "validated", "populated"].indexOf(key) !== -1) {
      return "\u2713";
    }
    if (key === "cancelled") {
      return "\u23f8";
    }
    if (["failed", "invalid"].indexOf(key) !== -1) {
      return "\u00d7";
    }
    if (key === "stopped") {
      return "\u23f8";
    }

    if (key === "queued") {
      return "\u25cc";
    }
    if (["running", "validating", "populating"].indexOf(key) !== -1) {
      return "\u2026";
    }
    return "i";
  }

  function appendStatusIcon(parent, status) {
    var icon = document.createElement("span");
    icon.className = "status-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = configTaskIcon(status);
    parent.appendChild(icon);
    return icon;
  }

  function isConfigLoadingStatus(status) {
    return ["validating", "populating", "running", "queued"].indexOf(configTaskKey(status)) !== -1;
  }

  function appendConfigLoadingLabel(parent, label) {
    var text = document.createElement("span");
    text.className = "config-status-label";
    text.textContent = label || "";
    parent.appendChild(text);

    var dots = document.createElement("span");
    dots.className = "config-status-dots";
    dots.setAttribute("aria-hidden", "true");
    parent.appendChild(dots);
    setConfigLoadingDotsText(dots);
    ensureConfigLoadingDots(parent);
  }

  function normalizeConfigStatus(status) {
    var key = configTaskKey(status);
    if (!key || key === "none") {
      return "pending";
    }
    if (key === "cancelled") {
      return "stopped";
    }
    if (key === "invalid") {
      return "failed";
    }
    return key;
  }


  var CONFIG_TOAST_MAX_VISIBLE = 3;
  var CONFIG_TOAST_DONE_VISIBLE_MS = 1000;
  var CONFIG_TOAST_AFTER_INTERACTION_VISIBLE_MS = 4000;
  var CONFIG_TOAST_SELECTION_RECHECK_MS = 1200;
  var CONFIG_TASK_POLL_MS = 450;
  var CONFIG_ACTIVE_ROW_REFRESH_MS = 1500;
  var CONFIG_LOADING_DOTS_STEP_MS = 600;
  var CONFIG_LOADING_DOTS_CYCLE = ["", ".", "..", "..."];
  var CONFIG_TOAST_ENTER_MS = 540;
  var CONFIG_TOAST_EXIT_MS = 580;
  var CONFIG_TOAST_REPLENISH_DELAY_MS = 280;
  var configToastVisible = [];
  var configToastQueue = [];
  var configLoadingDotsIndex = 0;
  var configLoadingDotsTimer = null;

  function configLoadingDotsText() {
    return CONFIG_LOADING_DOTS_CYCLE[configLoadingDotsIndex] || "";
  }

  function setConfigLoadingDotsText(dot) {
    if (!dot) {
      return;
    }
    dot.textContent = configLoadingDotsText();
  }

  function updateConfigLoadingDots(root) {
    Array.prototype.slice.call((root || document).querySelectorAll(".config-status-dots")).forEach(setConfigLoadingDotsText);
  }

  function ensureConfigLoadingDots(root) {
    updateConfigLoadingDots(root || document);
    if (configLoadingDotsTimer) {
      return;
    }
    configLoadingDotsTimer = window.setInterval(function () {
      var dots = document.querySelectorAll(".config-status-dots");
      if (!dots.length) {
        window.clearInterval(configLoadingDotsTimer);
        configLoadingDotsTimer = null;
        return;
      }
      configLoadingDotsIndex = (configLoadingDotsIndex + 1) % CONFIG_LOADING_DOTS_CYCLE.length;
      updateConfigLoadingDots(document);
    }, CONFIG_LOADING_DOTS_STEP_MS);
  }

  function maybeKeepConfigLoadingCell(taskCell, status, detailUrl, isActive) {
    if (!taskCell || !isConfigLoadingStatus(status)) {
      return false;
    }
    var node = taskCell.firstElementChild;
    if (!node || !node.classList || !node.classList.contains("config-status-loading")) {
      return false;
    }
    if (normalizeConfigStatus(taskCell.dataset.renderedStatus || "") !== status) {
      return false;
    }
    var expectedDetailUrl = detailUrl || "";
    var currentDetailUrl = taskCell.dataset.renderedDetailUrl || "";
    if (currentDetailUrl !== expectedDetailUrl) {
      return false;
    }
    var currentActive = taskCell.dataset.renderedActive === "1";
    if (currentActive !== Boolean(isActive)) {
      return false;
    }
    setConfigLoadingDotsText(node.querySelector(".config-status-dots"));
    ensureConfigLoadingDots(taskCell);
    return true;
  }

  function rememberConfigTaskCell(taskCell, status, detailUrl, isActive) {
    if (!taskCell) {
      return;
    }
    taskCell.dataset.renderedStatus = status || "";
    taskCell.dataset.renderedDetailUrl = detailUrl || "";
    taskCell.dataset.renderedActive = isActive ? "1" : "0";
  }

  function initConfigLoadingDots(root) {
    if ((root || document).querySelector(".config-status-dots")) {
      ensureConfigLoadingDots(root || document);
    }
  }

  function ensureConfigToastStack() {
    var stack = document.querySelector(".config-toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "config-toast-stack";
      stack.setAttribute("aria-live", "polite");
      document.body.appendChild(stack);
    }
    return stack;
  }

  function removeConfigToastFromList(list, toast) {
    var index = list.indexOf(toast);
    if (index >= 0) {
      list.splice(index, 1);
    }
  }

  function configToastCssPx(stack, property, fallback) {
    var value = window.getComputedStyle(stack).getPropertyValue(property);
    var parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function configToastMetrics(stack) {
    return {
      height: configToastCssPx(stack, "--config-toast-height", 184),
      gap: configToastCssPx(stack, "--config-toast-gap", 12)
    };
  }

  function configToastSlotY(stack, index) {
    var metrics = configToastMetrics(stack);
    return index * (metrics.height + metrics.gap);
  }

  function layoutVisibleConfigToasts() {
    var stack = ensureConfigToastStack();
    configToastVisible.forEach(function (toast, index) {
      if (!toast || !toast.element || toast.isDismissing) {
        return;
      }
      toast.slotIndex = index;
      toast.element.style.setProperty("--toast-y", configToastSlotY(stack, index) + "px");
      toast.element.style.setProperty("--toast-x", "0px");
      toast.element.style.setProperty("--toast-opacity", "1");
      toast.element.style.zIndex = String(CONFIG_TOAST_MAX_VISIBLE - index);
    });
  }

  function showNextQueuedConfigToast() {
    while (configToastVisible.length < CONFIG_TOAST_MAX_VISIBLE && configToastQueue.length) {
      showConfigToast(configToastQueue.shift());
    }
  }

  function showConfigToast(toast) {
    if (!toast || toast.isVisible) {
      return;
    }
    var stack = ensureConfigToastStack();
    var slotIndex = configToastVisible.length;
    toast.slotIndex = slotIndex;
    toast.isVisible = true;
    configToastVisible.push(toast);
    toast.element.classList.remove("is-dismissing");
    toast.element.classList.add("is-entering");
    toast.element.style.setProperty("--toast-y", configToastSlotY(stack, slotIndex) + "px");
    toast.element.style.setProperty("--toast-x", "var(--config-toast-offscreen-x)");
    toast.element.style.setProperty("--toast-opacity", "0");
    stack.appendChild(toast.element);
    layoutVisibleConfigToasts();
    window.setTimeout(function () {
      if (toast.element) {
        toast.element.classList.remove("is-entering");
      }
    }, CONFIG_TOAST_ENTER_MS);
    if (toast.dismissDelay) {
      scheduleConfigToastDismiss(toast, toast.dismissDelay);
    }
  }

  function enqueueConfigToast(toast) {
    if (configToastVisible.length < CONFIG_TOAST_MAX_VISIBLE) {
      showConfigToast(toast);
    } else {
      configToastQueue.push(toast);
    }
  }

  function dismissConfigToast(toast, immediate) {
    if (!toast || toast.isDismissing) {
      return;
    }
    if (toast.dismissTimer) {
      window.clearTimeout(toast.dismissTimer);
      toast.dismissTimer = null;
    }
    var wasVisible = configToastVisible.indexOf(toast) !== -1;
    removeConfigToastFromList(configToastQueue, toast);
    removeConfigToastFromList(configToastVisible, toast);
    toast.isVisible = false;
    function finishRemoval() {
      if (toast.element && toast.element.parentNode) {
        toast.element.parentNode.removeChild(toast.element);
      }
    }
    if (toast.element && toast.element.parentNode && !immediate) {
      var stack = toast.element.parentNode;
      var metrics = configToastMetrics(stack);
      toast.element.classList.remove("is-entering");
      stack.appendChild(toast.element);
      toast.element.style.setProperty("--toast-exit-y", (0 - metrics.height - metrics.gap) + "px");
      toast.element.style.setProperty("--toast-x", "0px");
      toast.element.style.setProperty("--toast-opacity", "1");
      toast.element.style.zIndex = "100";
      toast.isDismissing = true;
      toast.element.classList.add("is-dismissing");
      if (wasVisible) {
        layoutVisibleConfigToasts();
        window.setTimeout(showNextQueuedConfigToast, CONFIG_TOAST_REPLENISH_DELAY_MS);
      }
      window.setTimeout(finishRemoval, CONFIG_TOAST_EXIT_MS);
    } else {
      finishRemoval();
      if (wasVisible) {
        layoutVisibleConfigToasts();
        showNextQueuedConfigToast();
      }
    }
  }

  function configToastHasFocus(toast) {
    return Boolean(toast && toast.element && toast.element.contains(document.activeElement));
  }

  function configToastContainsSelection(toast) {
    if (!toast || !toast.element || !window.getSelection) {
      return false;
    }
    var selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount < 1) {
      return false;
    }
    for (var i = 0; i < selection.rangeCount; i += 1) {
      var range = selection.getRangeAt(i);
      try {
        if (range.intersectsNode(toast.element)) {
          return true;
        }
      } catch (error) {
        var ancestor = range.commonAncestorContainer;
        if (ancestor && toast.element.contains(ancestor.nodeType === 1 ? ancestor : ancestor.parentNode)) {
          return true;
        }
      }
    }
    return false;
  }

  function pauseConfigToastDismiss(toast) {
    if (!toast || !toast.dismissTimer) {
      return;
    }
    window.clearTimeout(toast.dismissTimer);
    toast.dismissTimer = null;
  }

  function resumeConfigToastDismissAfterInteraction(toast) {
    if (!toast || !toast.dismissDelay || !toast.isVisible || toast.isDismissing) {
      return;
    }
    scheduleConfigToastDismiss(toast, CONFIG_TOAST_AFTER_INTERACTION_VISIBLE_MS, true);
  }

  function scheduleConfigToastDismiss(toast, delay, keepDismissDelay) {
    if (!toast) {
      return;
    }
    var effectiveDelay = delay || toast.dismissDelay || CONFIG_TOAST_DONE_VISIBLE_MS;
    if (!keepDismissDelay) {
      toast.dismissDelay = effectiveDelay;
    }
    if (!toast.isVisible) {
      return;
    }
    if (toast.dismissTimer) {
      window.clearTimeout(toast.dismissTimer);
    }
    toast.dismissTimer = window.setTimeout(function () {
      toast.dismissTimer = null;
      if (toast.isInteracting || configToastHasFocus(toast) || configToastContainsSelection(toast)) {
        scheduleConfigToastDismiss(toast, CONFIG_TOAST_SELECTION_RECHECK_MS, true);
        return;
      }
      dismissConfigToast(toast);
    }, effectiveDelay);
  }

  function createConfigToast(title, message, container) {
    var toast = document.createElement("div");
    toast.className = "config-task-toast";
    var header = document.createElement("div");
    header.className = "config-task-toast-header";
    var titleWrap = document.createElement("div");
    titleWrap.className = "config-task-toast-title";
    var icon = document.createElement("span");
    icon.className = "config-task-toast-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = configTaskIcon("running");
    var strong = document.createElement("strong");
    strong.textContent = title;
    titleWrap.appendChild(icon);
    titleWrap.appendChild(strong);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "quiet";
    close.textContent = "\u00d7";
    header.appendChild(titleWrap);
    header.appendChild(close);
    var status = document.createElement("p");
    status.className = "muted config-task-toast-status";
    status.textContent = message || "";
    var detailLink = document.createElement("a");
    detailLink.className = "button secondary config-task-log-link";
    detailLink.textContent = configTaskLabel(container || document.body, "view_task_log") || "Ver registro";
    detailLink.target = "_blank";
    detailLink.rel = "noopener";
    detailLink.hidden = true;
    toast.appendChild(header);
    toast.appendChild(status);
    toast.appendChild(detailLink);
    var toastData = {
      element: toast,
      status: status,
      detailLink: detailLink,
      icon: icon,
      isVisible: false,
      dismissDelay: 0,
      dismissTimer: null,
      isDismissing: false,
      isInteracting: false
    };
    toast.addEventListener("mouseenter", function () {
      toastData.isInteracting = true;
      pauseConfigToastDismiss(toastData);
    });
    toast.addEventListener("mouseleave", function () {
      toastData.isInteracting = false;
      resumeConfigToastDismissAfterInteraction(toastData);
    });
    toast.addEventListener("focusin", function () {
      toastData.isInteracting = true;
      pauseConfigToastDismiss(toastData);
    });
    toast.addEventListener("focusout", function () {
      window.setTimeout(function () {
        if (!configToastHasFocus(toastData)) {
          toastData.isInteracting = false;
          resumeConfigToastDismissAfterInteraction(toastData);
        }
      }, 0);
    });
    toast.addEventListener("copy", function () {
      resumeConfigToastDismissAfterInteraction(toastData);
    });
    close.addEventListener("click", function () { dismissConfigToast(toastData, true); });
    enqueueConfigToast(toastData);
    return toastData;
  }

  function setConfigToastStatus(toast, status, message) {
    if (!toast) {
      return;
    }
    if (toast.dismissTimer) {
      window.clearTimeout(toast.dismissTimer);
      toast.dismissTimer = null;
      toast.dismissDelay = 0;
    }
    if (toast.icon) {
      toast.icon.textContent = configTaskIcon(status);
    }
    if (toast.status) {
      toast.status.textContent = message || status || "";
    }
  }

  function csrfFromForm(form) {
    var input = form.querySelector("input[name='csrfmiddlewaretoken']");
    return input ? input.value : "";
  }

  function submitterActionUrl(form, button) {
    if (button && button.getAttribute && button.hasAttribute("formaction")) {
      var buttonAction = button.getAttribute("formaction") || "";
      return button.formAction || new URL(buttonAction, window.location.href).toString();
    }
    if (form && form.getAttribute) {
      var action = form.getAttribute("action") || "";
      if (action) {
        return form.action || new URL(action, window.location.href).toString();
      }
    }
    return "";
  }

  function isConfigTaskUrl(url) {
    try {
      var parsed = new URL(url, window.location.href);
      return /\/configs\/[^/]+\/task\/[^/]+\/?$/.test(parsed.pathname);
    } catch (error) {
      return false;
    }
  }

  function updateConfigRow(summaryUrl, tableContainer) {
    if (!summaryUrl) {
      return Promise.resolve();
    }
    return fetch(summaryUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (response) { return response.ok ? parseJsonResponse(response) : null; })
      .then(function (data) {
        if (!data || !data.slug) {
          return;
        }
        var row = document.querySelector('[data-config-row="' + data.slug + '"]');
        if (!row) {
          return;
        }
        row.dataset.country = data.country_label || "";
        row.dataset.countryCode = data.country_code || "";
        row.dataset.search = [data.country_label, data.country_code].join(" ");
        row.dataset.pages = data.pages;
        row.dataset.cities = data.cities;
        row.dataset.rows = data.rows;
        var normalizedStatus = normalizeConfigStatus(data.status_filter || data.task_status || "pending");
        row.dataset.task = normalizedStatus;
        row.dataset.status = normalizedStatus;
        row.dataset.canResume = data.can_resume ? "1" : "0";
        var fields = {
          country: data.country_label,
          pages: data.pages,
          cities: data.cities,
          rows: data.rows
        };
        Object.keys(fields).forEach(function (key) {
          var cell = row.querySelector('[data-field="' + key + '"]');
          if (cell) {
            cell.textContent = fields[key];
          }
        });
        renderConfigTaskCell(
          row.querySelector('[data-field="task"]'),
          data.error ? "failed" : normalizeConfigStatus(data.task_status || data.status_filter || "pending"),
          data.task_url || "",
          tableContainer || row,
          Boolean(data.task_is_active)
        );
        updateConfigActionButtons(row, normalizedStatus, data);
        var asyncPanel = row.closest(".async-table-panel");
        if (asyncPanel) {
          rerenderClientTable(asyncPanel);
        }
        if (tableContainer) {
          tableContainer.dispatchEvent(new CustomEvent("config-table-row-updated"));
          scheduleActiveConfigRowRefresh(tableContainer);
        } else {
          scheduleActiveConfigRowRefresh(row.closest("[data-config-table]"));
        }
      });
  }

  function configRowIsLoading(row) {
    return Boolean(row && ["validating", "populating", "running", "queued"].indexOf(
      normalizeConfigStatus(row.dataset.task || row.dataset.status || "")
    ) !== -1);
  }

  function configRowSummaryUrl(row) {
    if (!row) {
      return "";
    }
    var source = row.querySelector("[data-summary-url]");
    return source ? (source.dataset.summaryUrl || "") : "";
  }

  function activeConfigRows(container) {
    return Array.prototype.slice.call((container || document).querySelectorAll("[data-config-row]")).filter(configRowIsLoading);
  }

  function scheduleActiveConfigRowRefresh(container) {
    container = container || document.querySelector("[data-config-table]");
    if (!container || activeConfigRows(container).length < 1) {
      return;
    }
    if (container._configActiveRowRefreshTimer) {
      return;
    }
    container._configActiveRowRefreshTimer = window.setTimeout(function () {
      container._configActiveRowRefreshTimer = null;
      refreshActiveConfigRows(container);
    }, CONFIG_ACTIVE_ROW_REFRESH_MS);
  }

  function refreshActiveConfigRows(container) {
    container = container || document.querySelector("[data-config-table]");
    if (!container) {
      return Promise.resolve();
    }
    var refreshes = activeConfigRows(container).map(function (row) {
      return updateConfigRow(configRowSummaryUrl(row), container);
    });
    if (!refreshes.length) {
      return Promise.resolve();
    }
    return Promise.all(refreshes).finally(function () {
      if (activeConfigRows(container).length) {
        scheduleActiveConfigRowRefresh(container);
      }
    });
  }

  function renderConfigTaskCell(taskCell, status, detailUrl, container, isActive) {
    if (!taskCell) {
      return;
    }
    status = normalizeConfigStatus(status || "pending");
    detailUrl = detailUrl || "";
    if (maybeKeepConfigLoadingCell(taskCell, status, detailUrl, isActive)) {
      return;
    }
    taskCell.innerHTML = "";
    var cssClass = configTaskClass(status);
    var label = configTaskLabel(container || taskCell, status);
    var node;
    if (detailUrl && isActive) {
      node = document.createElement("a");
      node.href = detailUrl;
    } else {
      node = document.createElement("span");
    }
    node.className = "status " + cssClass;
    if (isConfigLoadingStatus(status)) {
      node.classList.add("config-status-loading");
      node.setAttribute("aria-label", label);
      appendConfigLoadingLabel(node, label);
    } else if (["validated", "populated"].indexOf(status) === -1) {
      appendStatusIcon(node, status);
      node.appendChild(document.createTextNode(label));
    } else {
      node.appendChild(document.createTextNode(label));
    }
    taskCell.appendChild(node);
    rememberConfigTaskCell(taskCell, status, detailUrl, isActive);
    if (isConfigLoadingStatus(status)) {
      ensureConfigLoadingDots(taskCell);
    }
  }

  function canValidateConfigStatus(status) {
    var key = normalizeConfigStatus(status || "pending");
    return ["validated", "populated", "validating", "populating", "running", "queued", "stopped"].indexOf(key) === -1;
  }

  function canScrapeConfigStatus(status) {
    var key = normalizeConfigStatus(status || "pending");
    return ["validated", "populated", "stopped"].indexOf(key) !== -1;
  }

  function updateConfigActionButtons(row, status, data) {
    if (!row) {
      return;
    }
    var key = normalizeConfigStatus(status || row.dataset.status || row.dataset.task || "pending");
    var validateForm = row.querySelector('[data-config-action-form="validate"]');
    var scrapeForm = row.querySelector('[data-config-action-form="scrape"]');
    var resumeForm = row.querySelector('[data-config-action-form="resume"]');
    var stopForm = row.querySelector('[data-config-action-form="stop"]');
    var validateButton = row.querySelector('[data-config-action="validate"]');
    var scrapeButton = row.querySelector('[data-config-action="scrape"]');
    var resumeButton = row.querySelector('[data-config-action="resume"]');
    var stopButton = row.querySelector('[data-config-action="stop"]');
    var showValidate = ["pending", "failed"].indexOf(key) !== -1;
    var showValidateBusy = key === "validating";
    var showScrape = ["validated", "populated", "stopped"].indexOf(key) !== -1;
    var showScrapeBusy = key === "populating";
    var canResume = row.dataset.canResume === "1";
    var showResume = key === "stopped" && canResume;
    var showStop = ["validating", "populating", "running", "queued"].indexOf(key) !== -1;

    if (data && Object.prototype.hasOwnProperty.call(data, "can_validate")) {
      showValidate = Boolean(data.can_validate);
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_resume")) {
      canResume = Boolean(data.can_resume);
      row.dataset.canResume = canResume ? "1" : "0";
      showResume = key === "stopped" && canResume;
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_scrape")) {
      showScrape = Boolean(data.can_scrape);
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_stop")) {
      showStop = Boolean(data.can_stop);
    }
    if (showResume) {
      showScrape = false;
    }

    if (validateButton) {
      validateButton.disabled = showValidateBusy;
      if (showValidateBusy) {
        validateButton.setAttribute("disabled", "disabled");
      } else {
        validateButton.removeAttribute("disabled");
      }
    }
    if (scrapeButton) {
      scrapeButton.disabled = showScrapeBusy;
      if (showScrapeBusy) {
        scrapeButton.setAttribute("disabled", "disabled");
      } else {
        scrapeButton.removeAttribute("disabled");
      }
      if (scrapeButton.dataset.defaultLabel) {
        scrapeButton.textContent = scrapeButton.dataset.defaultLabel;
      }
    }
    if (resumeButton) {
      resumeButton.disabled = false;
      resumeButton.removeAttribute("disabled");
    }
    if (validateForm) {
      validateForm.hidden = !(showValidate || showValidateBusy);
    }
    if (scrapeForm) {
      scrapeForm.hidden = !(showScrape || showScrapeBusy);
    }
    if (resumeForm) {
      resumeForm.hidden = !showResume;
    }
    if (stopButton) {
      stopButton.disabled = false;
      stopButton.removeAttribute("disabled");
    }
    if (stopForm) {
      stopForm.hidden = !showStop;
    }
  }

  function cssEscape(value) {
    value = String(value || "");
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(value);
    }
    return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function applyConfigProgress(progress, detailUrl, container) {
    if (!progress || typeof progress !== "object") {
      return;
    }
    var tableContainer = container || document.querySelector("[data-config-table]") || document.body;
    var changed = false;
    Object.keys(progress).forEach(function (slug) {
      var item = progress[slug];
      if (!item || typeof item !== "object") {
        return;
      }
      var status = normalizeConfigStatus(item.status || "pending");
      var row = document.querySelector('[data-config-row="' + cssEscape(slug) + '"]');
      if (!row || !status) {
        return;
      }
      row.dataset.task = status;
      row.dataset.status = status || "none";
      renderConfigTaskCell(
        row.querySelector('[data-field="task"]'),
        status,
        detailUrl || "",
        tableContainer,
        ["validating", "populating", "running", "queued"].indexOf(normalizeConfigStatus(status)) !== -1
      );
      updateConfigActionButtons(row, status);
      changed = true;
    });
    if (changed) {
      var asyncPanel = document.querySelector("#config-table-panel");
      if (asyncPanel) {
        rerenderClientTable(asyncPanel);
      }
    }
    if (changed && tableContainer && tableContainer.dispatchEvent) {
      tableContainer.dispatchEvent(new CustomEvent("config-table-row-updated"));
      scheduleActiveConfigRowRefresh(tableContainer);
    }
  }

  function pollConfigTask(statusUrl, toast, button, summaryUrl, tableContainer) {
    function poll() {
      if (!statusUrl) {
        setConfigToastStatus(toast, "failed", "No se recibió la URL de estado de la tarea.");
        toast.element.classList.add("is-error");
        if (button && !button.closest("[data-config-row]")) {
          button.disabled = false;
        }
        scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
        return;
      }
      fetch(statusUrl, { headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" } })
        .then(parseJsonResponse)
        .then(function (data) {
          setConfigToastStatus(toast, data.status, configTaskLabel(tableContainer || document.body, data.status));
          if (toast.detailLink && data.detail_url) {
            toast.detailLink.href = data.detail_url;
            toast.detailLink.hidden = false;
          }
          applyConfigProgress(data.config_progress, data.detail_url, tableContainer);
          if (data.is_active) {
            window.setTimeout(poll, CONFIG_TASK_POLL_MS);
            return;
          }
          toast.element.classList.add(data.status === "succeeded" ? "is-success" : "is-error");
          if (button && !button.closest("[data-config-row]")) {
            button.disabled = false;
          }
          updateConfigRow(summaryUrl, tableContainer).finally(function () {
            if (!summaryUrl) {
              refreshConfigTables();
            }
            refreshTaskTables();
            scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
          });
        })
        .catch(function (error) {
          setConfigToastStatus(toast, "failed", error.message || configTaskLabel(tableContainer || document.body, "failed"));
          toast.element.classList.add("is-error");
          if (button && !button.closest("[data-config-row]")) {
            button.disabled = false;
          }
          scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
        });
    }
    poll();
  }

  function initConfigTaskActions(root) {
    function bindConfigTaskForm(form) {
      if (!form || form._configTaskBound) {
        return;
      }
      form._configTaskBound = true;
      form.addEventListener("click", function (event) {
        var target = event.target && event.target.closest ? event.target.closest("[data-config-action]") : null;
        if (target && target.form === form) {
          form._lastConfigTaskButton = target;
        }
      }, true);
      form.addEventListener("submit", function (event) {
        var submitter = event.submitter || form._lastConfigTaskButton || document.activeElement;
        var button = submitter && submitter.matches && submitter.matches("[data-config-action]")
          ? submitter
          : null;
        var explicitTaskForm = form.hasAttribute("data-config-task-form");
        var explicitTaskButton = button && button.matches && button.matches("[data-config-action]");
        if (!explicitTaskForm && !explicitTaskButton) {
          return;
        }
        if (explicitTaskForm && !button) {
          button = form.querySelector("[data-config-action]") || form.querySelector("button[type='submit']");
        }
        event.preventDefault();
        var actionUrl = submitterActionUrl(form, button);
        var tableContainer = form.closest("[data-config-table]");
        if (!isConfigTaskUrl(actionUrl)) {
          var badUrlError = "La acción de la tarea no apunta a la API de tareas. Recarga la página con Ctrl+F5.";
          var badToast = createConfigToast(button ? button.textContent : "Tarea", badUrlError, tableContainer || document.body);
          badToast.element.classList.add("is-error");
          setConfigToastStatus(badToast, "failed", badUrlError);
          scheduleConfigToastDismiss(badToast, CONFIG_TOAST_DONE_VISIBLE_MS);
          return;
        }
        var actionRow = form.closest("[data-config-row]");
        if (actionRow) {
          var actionKind = form.dataset.configActionForm || "";
          var pendingStatus = (actionKind === "scrape" || actionKind === "resume") ? "populating" : (actionKind === "stop" ? "stopped" : "validating");
          actionRow.dataset.task = pendingStatus;
          actionRow.dataset.status = pendingStatus;
          renderConfigTaskCell(
            actionRow.querySelector('[data-field="task"]'),
            pendingStatus,
            "",
            tableContainer || actionRow,
            pendingStatus !== "stopped"
          );
          updateConfigActionButtons(actionRow, pendingStatus);
          scheduleActiveConfigRowRefresh(tableContainer || actionRow.closest("[data-config-table]"));
        } else if (button) {
          button.disabled = true;
        }
        var actionLabel = form.dataset.actionLabel || (button && button.dataset.actionLabel) || (button ? button.textContent : "");
        var toast = createConfigToast(actionLabel, tableContainer && tableContainer.dataset.startedLabel ? tableContainer.dataset.startedLabel : actionLabel, tableContainer || document.body);
        fetch(actionUrl, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfFromForm(form)
          }
        }).then(parseJsonResponse).then(function (data) {
          setConfigToastStatus(toast, data.status || "running", data.label || actionLabel);
          if (toast.detailLink && data.detail_url) {
            toast.detailLink.href = data.detail_url;
            toast.detailLink.hidden = false;
          }
          refreshTaskTables();
          if (data.summary_url || form.dataset.summaryUrl || (button && button.dataset.summaryUrl)) {
            updateConfigRow(form.dataset.summaryUrl || (button && button.dataset.summaryUrl) || data.summary_url, tableContainer);
          }
          pollConfigTask(data.status_url, toast, button, form.dataset.summaryUrl || (button && button.dataset.summaryUrl) || data.summary_url, tableContainer);
        }).catch(function (error) {
          setConfigToastStatus(toast, "failed", error.message || configTaskLabel(tableContainer || document.body, "failed"));
          toast.element.classList.add("is-error");
          if (button && !button.closest("[data-config-row]")) {
            button.disabled = false;
          }
          scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
        });
      });
    }

    (root || document).querySelectorAll("[data-config-task-form]").forEach(bindConfigTaskForm);
    (root || document).querySelectorAll("[data-config-action]").forEach(function (button) {
      bindConfigTaskForm(button.form);
    });
  }

  function activateConfigTab(editor, name) {
    if (!editor || !name) {
      return;
    }
    var selectedButton = editor.querySelector('[data-config-tab="' + name + '"]');
    if (!selectedButton) {
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

  function initConfigEditor(root) {
    initConfigTabs(root);
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
        initManualPages(editor);
        initCityTransfer(editor, sourceEntities);
        initConfigGenerator(editor);
        initAiLoginLinks(editor);
      } catch (error) {
        window.console && window.console.error && window.console.error("Error inicializando configuraci\u00f3n", error);
      }
      var params = new URLSearchParams(window.location.search || "");
      var tab = params.get("tab") || editor.dataset.restoreTab || "";
      if (tab) {
        activateConfigTab(editor, tab);
      }
    });
  }

  function initManualPages(editor) {
    var tbody = editor.querySelector("[data-manual-pages]");
    var add = editor.querySelector("[data-add-page-row]");
    if (!tbody || !add) {
      return;
    }
    function bindRemove(row) {
      var remove = row.querySelector("[data-remove-page-row]");
      if (remove) {
        remove.addEventListener("click", function () {
          if (tbody.querySelectorAll("tr").length > 1) {
            row.remove();
          }
        });
      }
    }
    Array.prototype.slice.call(tbody.querySelectorAll("tr")).forEach(bindRemove);
    add.addEventListener("click", function () {
      var first = tbody.querySelector("tr");
      if (!first) {
        return;
      }
      var clone = first.cloneNode(true);
      clone.querySelectorAll("input").forEach(function (input) {
        input.value = input.name === "page_level" ? "1" : "";
      });
      clone.querySelectorAll("select").forEach(function (select) { select.value = "table"; });
      bindRemove(clone);
      tbody.appendChild(clone);
    });
  }

  function initCityTransfer(editor, sourceEntities) {
    var transfer = editor.querySelector("[data-city-transfer]");
    if (!transfer) {
      return;
    }
    var availableBody = transfer.querySelector("[data-transfer-available]");
    var selectedBody = transfer.querySelector("[data-transfer-selected]");
    var hidden = editor.querySelector("[data-selected-city-entities]");
    if (!availableBody || !selectedBody) {
      return;
    }
    sourceEntities = normalizeEntities(sourceEntities);
    var sourceLevelOptions = selectOptions(transfer.querySelector('[data-transfer-filter="level"]'));
    var sourceParentOptions = null;
    var sourceParentOptionsByLevel = {};
    var selected = [];

    function normalizeEntities(entities) {
      return (entities || []).map(function (entity) {
        entity.id = String(entity.id || "");
        entity.parent_key = String(entity.parent_key || entity.raw_parent || entity.parent || "");
        entity.parent_level = entity.parent_level === null || entity.parent_level === undefined ? "" : String(entity.parent_level);
        entity.entity_type_filter = String(entity.entity_type_filter || entity.entity_type || entity.raw_entity_type || "");
        return entity;
      }).filter(function (entity) { return entity.id; });
    }

    function readSelectedFromHidden() {
      try {
        selected = JSON.parse(hidden ? hidden.value : "[]") || [];
      } catch (error) {
        selected = [];
      }
      selected = selected.map(function (id) { return String(id); }).filter(Boolean);
      selected = uniqueValues(selected);
    }
    readSelectedFromHidden();

    function setTransferMessage(body, message, loading) {
      body.innerHTML = "";
      var row = document.createElement("tr");
      var cell = document.createElement("td");
      cell.colSpan = 4;
      cell.className = "table-empty-cell";
      if (loading) {
        var spinner = document.createElement("span");
        spinner.className = "loading-spinner";
        spinner.setAttribute("aria-hidden", "true");
        cell.appendChild(spinner);
        cell.appendChild(document.createTextNode(message));
        cell.classList.add("inline-loading");
      } else {
        cell.textContent = message;
      }
      row.appendChild(cell);
      body.appendChild(row);
    }

    function populateSelect(select, values, disableWhenEmpty) {
      if (!select) {
        return;
      }
      var hasSelect2 = window.jQuery && window.jQuery.fn && window.jQuery.fn.select2 && window.jQuery(select).data("select2");
      if (hasSelect2) {
        window.jQuery(select).select2("destroy");
      }
      values = values || [];
      var requireValue = select.dataset.requireValue === "true";
      var current = select.value || select.dataset.restoreValue || "";
      var emptyLabel = select.dataset.emptyOption || "";
      select.innerHTML = "";
      if (!values.length && disableWhenEmpty) {
        select.disabled = true;
        if (window.jQuery && window.jQuery.fn && window.jQuery.fn.select2 && window.jQuery(select).data("select2")) {
          window.jQuery(select).val(null).trigger("change.select2");
        }
        return;
      }
      select.disabled = false;
      if (!requireValue) {
        var empty = document.createElement("option");
        empty.value = "";
        empty.textContent = emptyLabel;
        select.appendChild(empty);
      }
      values.forEach(function (entry) {
        var option = document.createElement("option");
        option.value = String(entry.value);
        option.textContent = entry.label;
        select.appendChild(option);
      });
      if (requireValue && !current && values.length) {
        current = String(values[0].value);
      }
      select.value = current;
      if (select.value !== current) {
        select.value = requireValue && values.length ? String(values[0].value) : "";
      }
      if (window.jQuery && window.jQuery.fn && window.jQuery.fn.select2 && select.matches("select[data-select2]")) {
        window.jQuery(select).select2({
          width: "100%",
          placeholder: window.jQuery(select).data("placeholder") || "",
          allowClear: !requireValue
        });
      }
    }

    function selectOptions(select) {
      if (!select) {
        return [];
      }
      return Array.prototype.slice.call(select.options || []).filter(function (option) {
        return String(option.value || "").trim();
      }).map(function (option) {
        return { value: option.value, label: option.textContent || option.value };
      });
    }

    function fallbackLevels() {
      var levels = {};
      sourceEntities.forEach(function (entity) {
        var key = String(entity.level || "").trim();
        if (!key) {
          return;
        }
        if (!levels[key]) {
          levels[key] = { count: 0, types: {} };
        }
        levels[key].count += 1;
        var type = String(entity.entity_type || "").trim();
        if (type) {
          levels[key].types[type] = (levels[key].types[type] || 0) + 1;
        }
      });
      return Object.keys(levels).sort(function (left, right) { return Number(left) - Number(right); }).map(function (level) {
        var types = Object.keys(levels[level].types).sort(function (left, right) {
          var diff = levels[level].types[right] - levels[level].types[left];
          return diff || left.localeCompare(right);
        });
        var label = types.length ? types.join("/") : level;
        return { value: level, label: label + " (" + levels[level].count + ")" };
      });
    }

    function fallbackParents(level) {
      var seen = {};
      var parents = [];
      var levelValue = String(level || "").trim();
      if (levelValue === "1") {
        return parents;
      }
      sourceEntities.forEach(function (entity) {
        if (levelValue && String(entity.level || "") !== levelValue) {
          return;
        }
        if (String(entity.parent_level || "") === "0") {
          return;
        }
        var value = String(entity.parent_key || "").trim();
        if (!value || seen[value]) {
          return;
        }
        seen[value] = true;
        parents.push({ value: value, label: entity.parent || entity.raw_parent || value });
      });
      parents.sort(function (left, right) { return String(left.label || "").localeCompare(String(right.label || "")); });
      return parents;
    }

    function parentOptionsForLevel(level) {
      var levelValue = String(level || "").trim();
      if (levelValue && sourceParentOptionsByLevel && sourceParentOptionsByLevel[levelValue]) {
        return sourceParentOptionsByLevel[levelValue];
      }
      if (!levelValue && sourceParentOptions) {
        return sourceParentOptions;
      }
      return fallbackParents(levelValue);
    }

    function refreshParentFilter() {
      var levelFilter = transfer.querySelector('[data-transfer-filter="level"]');
      var levelValue = levelFilter ? levelFilter.value.trim() : "";
      populateSelect(transfer.querySelector('[data-transfer-filter="parent"]'), parentOptionsForLevel(levelValue), true);
      if (levelValue === "1") {
        var parentFilter = transfer.querySelector('[data-transfer-filter="parent"]');
        if (parentFilter) {
          parentFilter.value = "";
        }
      }
    }

    function populateTransferFilters(levels, parents, parentsByLevel) {
      sourceLevelOptions = levels && levels.length ? levels : (sourceLevelOptions && sourceLevelOptions.length ? sourceLevelOptions : fallbackLevels());
      sourceParentOptions = parents || fallbackParents();
      sourceParentOptionsByLevel = parentsByLevel || {};
      populateSelect(transfer.querySelector('[data-transfer-filter="level"]'), sourceLevelOptions, true);
      refreshParentFilter();
      initSelect2(transfer);
    }

    function selectedSet() {
      return selected.reduce(function (acc, id) { acc[id] = true; return acc; }, {});
    }
    function rowText(entity) {
      return [entity.level, entity.name, entity.parent, entity.raw_name, entity.raw_parent, entity.entity_type]
        .join(" ").toLowerCase();
    }
    function syncHidden() {
      if (hidden) {
        hidden.value = JSON.stringify(selected);
      }
    }
    function appendRow(body, entity, actionText, action) {
      var tr = document.createElement("tr");
      var level = document.createElement("td");
      level.textContent = entity.level;
      var name = document.createElement("td");
      name.innerHTML = "";
      var strong = document.createElement("strong");
      strong.textContent = entity.name || entity.raw_name || "";
      name.appendChild(strong);
      if (entity.entity_type) {
        var small = document.createElement("small");
        small.textContent = entity.entity_type;
        name.appendChild(document.createElement("br"));
        name.appendChild(small);
      }
      var parent = document.createElement("td");
      parent.textContent = entity.parent || "-";
      var actionCell = document.createElement("td");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "quiet";
      button.textContent = actionText;
      button.addEventListener("click", function () { action(entity); });
      actionCell.appendChild(button);
      tr.appendChild(level);
      tr.appendChild(name);
      tr.appendChild(parent);
      tr.appendChild(actionCell);
      body.appendChild(tr);
    }
    function draw() {
      var set = selectedSet();
      var levelFilter = transfer.querySelector('[data-transfer-filter="level"]');
      var nameFilter = transfer.querySelector('[data-transfer-filter="name"]');
      var parentFilter = transfer.querySelector('[data-transfer-filter="parent"]');
      var levelValue = levelFilter ? levelFilter.value.trim() : "";
      var nameValue = nameFilter ? nameFilter.value.trim().toLowerCase() : "";
      var parentValue = parentFilter ? parentFilter.value.trim() : "";
      availableBody.innerHTML = "";
      selectedBody.innerHTML = "";
      sourceEntities.filter(function (entity) {
        if (set[entity.id]) {
          return false;
        }
        if (levelValue && String(entity.level) !== levelValue) {
          return false;
        }
        if (nameValue && rowText(entity).indexOf(nameValue) === -1) {
          return false;
        }
        if (parentValue && String(entity.parent_key || entity.parent || entity.raw_parent || "") !== parentValue) {
          return false;
        }
        return true;
      }).forEach(function (entity) {
        appendRow(availableBody, entity, "\u2192", function (item) {
          selected.push(String(item.id));
          selected = uniqueValues(selected);
          syncHidden();
          draw();
        });
      });
      sourceEntities.filter(function (entity) { return set[entity.id]; }).forEach(function (entity) {
        appendRow(selectedBody, entity, "\u00d7", function (item) {
          selected = selected.filter(function (id) { return String(id) !== String(item.id); });
          syncHidden();
          draw();
        });
      });
      if (!availableBody.children.length) {
        setTransferMessage(availableBody, editor.dataset.emptyLabel || "");
      }
      if (!selectedBody.children.length) {
        setTransferMessage(selectedBody, editor.dataset.emptyLabel || "");
      }
    }

    function setSourceEntities(entities, levels, parents, parentsByLevel) {
      sourceEntities = normalizeEntities(entities);
      selected = selected.filter(function (id) {
        return sourceEntities.some(function (entity) { return String(entity.id) === String(id); });
      });
      populateTransferFilters(levels, parents, parentsByLevel);
      syncHidden();
      draw();
    }

    transfer.querySelectorAll("[data-transfer-filter]").forEach(function (input) {
      function handleFilterChange() {
        if (input.dataset.transferFilter === "level") {
          refreshParentFilter();
        }
        draw();
      }
      input.addEventListener("input", handleFilterChange);
      input.addEventListener("change", handleFilterChange);
    });
    if (hidden) {
      hidden.addEventListener("input", function () {
        readSelectedFromHidden();
        draw();
      });
      hidden.addEventListener("change", function () {
        readSelectedFromHidden();
        draw();
      });
    }

    sourceEntities = normalizeEntities(sourceEntities);
    if (sourceEntities.length) {
      setSourceEntities(sourceEntities, null, null, null);
    } else {
      setTransferMessage(availableBody, editor.dataset.loadingLabel || "", true);
      setTransferMessage(selectedBody, editor.dataset.emptyLabel || "");
    }
    if (editor.dataset.sourceEntitiesUrl) {
      fetchJson(editor.dataset.sourceEntitiesUrl)
        .then(function (payload) {
          setSourceEntities(payload.entities || [], payload.levels || [], payload.parents || [], payload.parents_by_level || {});
        })
        .catch(function () {
          if (!sourceEntities.length) {
            setTransferMessage(availableBody, editor.dataset.errorLabel || editor.dataset.emptyLabel || "");
          }
        });
    } else if (!sourceEntities.length) {
      setSourceEntities(sourceEntities, null, null, null);
    }
  }

  function initAiLoginLinks(editor) {
    var provider = editor.querySelector("[data-ai-provider]");
    var link = editor.querySelector("[data-ai-login-link]");
    if (!provider || !link) {
      return;
    }
    function updateLink() {
      if (link.getAttribute("aria-disabled") === "true") {
        return;
      }
      var base = link.href.split("?")[0];
      link.href = base + "?provider=" + encodeURIComponent(provider.value || "chatgpt");
    }
    provider.addEventListener("change", updateLink);
    link.addEventListener("click", function (event) {
      if (link.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
      }
    });
    updateLink();
  }

  function initConfigGenerator(editor) {
    var button = editor.querySelector("[data-generate-config]");
    var output = editor.querySelector("[data-generated-config]");
    var log = editor.querySelector("[data-generate-log]");
    if (!button || !output || !log) {
      return;
    }
    var generatorLoading = createStatusElement("table-loading", button.textContent + "...", true);
    generatorLoading.hidden = true;
    if (log.parentNode) {
      log.parentNode.insertBefore(generatorLoading, log.nextSibling);
    }
    button.addEventListener("click", function () {
      if (!editor.dataset.generateUrl) {
        return;
      }
      button.disabled = true;
      generatorLoading.hidden = false;
      log.value = "\u23f3 " + button.textContent + "...";
      fetch(editor.dataset.generateUrl, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfFromDocument()
        }
      }).then(function (response) {
        return response.text().then(function (text) {
          var data = {};
          try {
            data = text ? JSON.parse(text) : {};
          } catch (error) {
            throw new Error(text || response.statusText);
          }
          if (!response.ok || !data.ok) {
            throw new Error(data.error || response.statusText);
          }
          return data;
        });
      }).then(function (data) {
        output.value = data.content || "";
        log.value = "\u2705 " + (data.log || "");
      }).catch(function (error) {
        log.value = "\u274c " + (error.message || "Error");
      }).finally(function () {
        button.disabled = false;
        generatorLoading.hidden = true;
      });
    });
  }

  function csrfFromDocument() {
    var input = document.querySelector("input[name='csrfmiddlewaretoken']");
    return input ? input.value : "";
  }


  var LANGUAGE_STATE_PREFIX = "ciudades_del_mundo_language_state:";

  function languageStateKey() {
    return LANGUAGE_STATE_PREFIX + window.location.pathname + window.location.search;
  }

  function isRestorableForm(form) {
    return form && !form.matches(".language-form, .theme-form") && !form.closest(".language-form, .theme-form");
  }

  function collectFormState(form) {
    var values = {};
    Array.prototype.slice.call(form.elements || []).forEach(function (field) {
      if (!field.name || field.name === "csrfmiddlewaretoken" || field.disabled) {
        return;
      }
      if (!values[field.name]) {
        values[field.name] = [];
      }
      values[field.name].push({
        type: (field.type || field.tagName || "").toLowerCase(),
        value: field.value,
        checked: !!field.checked
      });
    });
    return values;
  }

  function savePageStateBeforeLanguageChange() {
    try {
      var forms = Array.prototype.slice.call(document.querySelectorAll("form")).filter(isRestorableForm).map(collectFormState);
      var activeTab = "";
      var activeTabButton = document.querySelector("[data-config-tab].is-active");
      if (activeTabButton) {
        activeTab = activeTabButton.dataset.configTab || "";
      }
      var looseFields = Array.prototype.slice.call(document.querySelectorAll("[data-config-table-search]")).map(function (field) {
        return field.value || "";
      });
      window.sessionStorage.setItem(languageStateKey(), JSON.stringify({
        forms: forms,
        looseFields: looseFields,
        activeTab: activeTab,
        scrollY: window.scrollY || 0
      }));
    } catch (error) {}
  }

  function ensureRepeatedManualRows(form, values) {
    var expected = Math.max(
      (values.page_level || []).length,
      (values.page_url || []).length,
      (values.page_source || []).length
    );
    if (expected <= 1) {
      return;
    }
    var tbody = form.querySelector("[data-manual-pages]");
    if (!tbody) {
      return;
    }
    while (tbody.querySelectorAll("tr").length < expected) {
      var first = tbody.querySelector("tr");
      if (!first) {
        break;
      }
      var clone = first.cloneNode(true);
      clone.querySelectorAll("input, select, textarea").forEach(function (field) {
        if (field.type === "checkbox" || field.type === "radio") {
          field.checked = false;
        } else {
          field.value = "";
        }
      });
      tbody.appendChild(clone);
    }
  }

  function restoreFormValues(form, values) {
    ensureRepeatedManualRows(form, values || {});
    Object.keys(values || {}).forEach(function (name) {
      var escapedName = window.CSS && window.CSS.escape ? window.CSS.escape(name) : String(name).replace(/"/g, '\"');
      var fields = Array.prototype.slice.call(form.querySelectorAll('[name="' + escapedName + '"]'));
      values[name].forEach(function (saved, index) {
        var field = fields[index];
        if (!field) {
          return;
        }
        if (saved.type === "checkbox" || saved.type === "radio") {
          field.checked = !!saved.checked;
        } else {
          field.value = saved.value;
          if (field.tagName === "SELECT" && field.value !== saved.value) {
            field.dataset.restoreValue = saved.value;
          }
        }
        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });
  }

  function restorePageStateAfterLanguageChange() {
    var raw = "";
    try {
      raw = window.sessionStorage.getItem(languageStateKey()) || "";
    } catch (error) {
      raw = "";
    }
    if (!raw) {
      return;
    }
    var state = null;
    try {
      state = JSON.parse(raw);
      window.sessionStorage.removeItem(languageStateKey());
    } catch (error) {
      return;
    }
    var forms = Array.prototype.slice.call(document.querySelectorAll("form")).filter(isRestorableForm);
    (state.forms || []).forEach(function (values, index) {
      if (forms[index]) {
        restoreFormValues(forms[index], values);
      }
    });
    Array.prototype.slice.call(document.querySelectorAll("[data-config-table-search]")).forEach(function (field, index) {
      if (!state.looseFields || state.looseFields[index] === undefined) {
        return;
      }
      field.value = state.looseFields[index];
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
    if (state.activeTab) {
      document.querySelectorAll("[data-config-editor]").forEach(function (editor) {
        editor.dataset.restoreTab = state.activeTab;
      });
    }
    if (typeof state.scrollY === "number") {
      window.setTimeout(function () { window.scrollTo(0, state.scrollY); }, 0);
    }
  }

  function initLanguageStatePreservation(root) {
    (root || document).querySelectorAll("[data-language-form]").forEach(function (form) {
      form.addEventListener("submit", savePageStateBeforeLanguageChange);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initLanguageStatePreservation(document);
    initConfigTabs(document);
    try {
      restorePageStateAfterLanguageChange();
    } catch (error) {
      window.console && window.console.warn && window.console.warn("No se pudo restaurar el estado tras cambiar idioma", error);
    }
    initThemeSelector(document);
    initSelect2(document);
    initLocalDateTimes(document);
    initTaskDetailLog(document);
    initConfigBootstrap(document);
    initAsyncTables(document);
    initConfigTables(document);
    initConfigLoadingDots(document);
    initConfigTaskActions(document);
    initConfigEditor(document);
    initDataCharts(document);
    initDashboardCountryDetail(document);
    initStatsCountries(document);
    initMap();
    initVisualIdentity();
  });
})();
