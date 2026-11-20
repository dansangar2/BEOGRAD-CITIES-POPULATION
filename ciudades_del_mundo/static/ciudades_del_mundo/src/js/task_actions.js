  function configTaskActionKind(form, button) {
    if (form && form.dataset && form.dataset.configActionForm) {
      return form.dataset.configActionForm;
    }
    if (button && button.dataset && button.dataset.configAction) {
      return button.dataset.configAction;
    }
    return "";
  }

  function configTaskTerminalMessage(container, status, actionKind, fallbackLabel) {
    container = container || document.body;
    var key = configTaskKey(status);
    var action = configTaskKey(actionKind || fallbackLabel || "");
    var success = key === "succeeded";
    var cancelled = key === "cancelled";
    if (cancelled) {
      return container.dataset.cancelledLabel || "Cancelado";
    }
    if (action.indexOf("scrape") !== -1 || action.indexOf("popular") !== -1) {
      return success
        ? (container.dataset.scrapeSuccessLabel || "Scraping finalizado correctamente.")
        : (container.dataset.scrapeErrorLabel || "El scraping ha fallado.");
    }
    if (action.indexOf("validate") !== -1 || action.indexOf("validar") !== -1) {
      return success
        ? (container.dataset.validateSuccessLabel || "Validación finalizada correctamente.")
        : (container.dataset.validateErrorLabel || "La validación ha fallado.");
    }
    if (action.indexOf("clear") !== -1 || action.indexOf("limpiar") !== -1) {
      return success
        ? (container.dataset.clearSuccessLabel || "Limpieza finalizada correctamente.")
        : (container.dataset.clearErrorLabel || "La limpieza ha fallado.");
    }
    if (action.indexOf("asset") !== -1 || action.indexOf("escudo") !== -1 || action.indexOf("bandera") !== -1) {
      return success
        ? (container.dataset.assetsSuccessLabel || "Correcciones de escudos y banderas guardadas correctamente.")
        : (container.dataset.assetsErrorLabel || "No se pudieron guardar las correcciones de escudos y banderas.");
    }
    return success
      ? (container.dataset.taskSuccessLabel || container.dataset.finishedLabel || configTaskLabel(container, status))
      : (container.dataset.taskErrorLabel || configTaskLabel(container, status));
  }

  function parseJsonResponseText(response) {
    return response.text().then(function (text) {
      var data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (error) {
        throw new Error(text || response.statusText);
      }
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || data.message || response.statusText);
      }
      return data;
    });
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
      return [
        /\/configs\/import-toml\/?$/,
        /\/configs\/all\/task\/[^/]+\/?$/,
        /\/configs\/[^/]+\/task\/[^/]+\/?$/,
        /\/recipes\/[^/]+\/task\/[^/]+\/?$/,
        /\/new-countries\/[^/]+\/[^/]+\/configs\/[^/]+\/task\/[^/]+\/?$/,
        /\/new-countries\/import-toml\/?$/,
        /\/groups\/import-toml\/?$/,
        /\/subdivisions\/import-toml\/?$/,
        /\/subdivisions\/[^/]+\/build\/?$/
      ].some(function (pattern) {
        return pattern.test(parsed.pathname);
      });
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
        row.dataset.canValidate = data.can_validate ? "1" : "0";
        row.dataset.canScrape = data.can_scrape ? "1" : "0";
        row.dataset.canResume = data.can_resume ? "1" : "0";
        row.dataset.canClear = data.can_clear ? "1" : "0";
        row.dataset.canStop = data.can_stop ? "1" : "0";
        row.dataset.errorCode = data.error_code || "";
        row.dataset.errorSeverity = data.error_severity || "";
        row.dataset.errorDescription = data.error_description || "";
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
          Boolean(data.task_is_active),
          data.error_code || "",
          data.error_description || "",
          data.error_severity || ""
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
    return Boolean(row && ["validating", "populating", "clearing", "running", "queued"].indexOf(
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

  function shouldHideConfigStatusCode(taskCell, container) {
    var scope = container || taskCell;
    if (!scope) {
      return false;
    }
    if (scope.dataset && scope.dataset.hideStatusCode === "1") {
      return true;
    }
    if (scope.closest && scope.closest("[data-hide-status-code='1']")) {
      return true;
    }
    return !!(taskCell && taskCell.closest && taskCell.closest("[data-config-table]") && !taskCell.closest("[data-config-editor]"));
  }

  function renderConfigTaskCell(taskCell, status, detailUrl, container, isActive, errorCode, errorDescription, errorSeverity) {
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
    var hideStatusCode = shouldHideConfigStatusCode(taskCell, container);
    var code = hideStatusCode ? "" : normalizeConfigErrorCode(errorCode || (taskCell && taskCell.dataset ? taskCell.dataset.errorCode : ""));
    var description = hideStatusCode ? "" : (errorDescription || (taskCell && taskCell.dataset ? taskCell.dataset.errorDescription : ""));
    var severity = hideStatusCode ? "" : (errorSeverity || (taskCell && taskCell.dataset ? taskCell.dataset.errorSeverity : ""));
    var node;
    if (detailUrl && (isActive || status === "failed")) {
      node = document.createElement("a");
      node.href = detailUrl;
      node.target = "_blank";
      node.rel = "noopener";
    } else {
      node = document.createElement("span");
    }
    node.className = "status " + cssClass;
    if (isConfigLoadingStatus(status)) {
      node.classList.add("config-status-loading");
      node.setAttribute("aria-label", label);
      appendConfigLoadingLabel(node, label);
    } else {
      node.appendChild(document.createTextNode(label));
      if (code) {
        appendConfigErrorCode(node, code, description, severity);
      }
    }
    setElementErrorInfo(node, code, description, severity);
    if (taskCell && taskCell.dataset) {
      setElementErrorInfo(taskCell, code, description, severity);
    }
    taskCell.appendChild(node);
    rememberConfigTaskCell(taskCell, status, detailUrl, isActive);
    if (isConfigLoadingStatus(status)) {
      ensureConfigLoadingDots(taskCell);
    }
  }

  function canValidateConfigStatus(status) {
    var key = normalizeConfigStatus(status || "pending");
    return ["validated", "populated", "validating", "populating", "clearing", "running", "queued"].indexOf(key) === -1;
  }

  function canScrapeConfigStatus(status) {
    var key = normalizeConfigStatus(status || "pending");
    return ["pending", "failed", "validated", "populated"].indexOf(key) !== -1;
  }

  function configRowDatasetFlag(row, name) {
    if (!row || !row.dataset || !Object.prototype.hasOwnProperty.call(row.dataset, name)) {
      return null;
    }
    return row.dataset[name] === "1";
  }

  function updateConfigActionButtons(row, status, data) {
    if (!row) {
      return;
    }
    var key = normalizeConfigStatus(status || row.dataset.status || row.dataset.task || "pending");
    var validateForm = row.querySelector('[data-config-action-form="validate"]');
    var scrapeForm = row.querySelector('[data-config-action-form="scrape"]');
    var clearForm = row.querySelector('[data-config-action-form="clear"]');
    var stopForm = row.querySelector('[data-config-action-form="stop"]');
    var validateButton = row.querySelector('[data-config-action="validate"]');
    var scrapeButton = row.querySelector('[data-config-action="scrape"]');
    var clearButton = row.querySelector('[data-config-action="clear"]');
    var stopButton = row.querySelector('[data-config-action="stop"]');
    var datasetCanValidate = configRowDatasetFlag(row, "canValidate");
    var datasetCanScrape = configRowDatasetFlag(row, "canScrape");
    var datasetCanStop = configRowDatasetFlag(row, "canStop");
    var activeOperation = ["validating", "populating", "clearing", "running", "queued"].indexOf(key) !== -1;
    var clearBlockedByStatus = activeOperation;
    var showValidate = datasetCanValidate === null ? canValidateConfigStatus(key) : datasetCanValidate;
    var showScrape = datasetCanScrape === null ? canScrapeConfigStatus(key) : datasetCanScrape;
    var showClear = row.dataset.canClear === "1";
    var showClearBusy = false;
    var showStop = datasetCanStop === null ? activeOperation : datasetCanStop;

    if (data && Object.prototype.hasOwnProperty.call(data, "can_validate")) {
      showValidate = Boolean(data.can_validate);
      row.dataset.canValidate = showValidate ? "1" : "0";
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_resume")) {
      row.dataset.canResume = data.can_resume ? "1" : "0";
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_scrape")) {
      showScrape = Boolean(data.can_scrape);
      row.dataset.canScrape = showScrape ? "1" : "0";
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_clear")) {
      showClear = Boolean(data.can_clear) && !clearBlockedByStatus;
      row.dataset.canClear = data.can_clear ? "1" : "0";
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "can_stop")) {
      showStop = Boolean(data.can_stop);
      row.dataset.canStop = showStop ? "1" : "0";
    }
    if (activeOperation) {
      showValidate = false;
      showScrape = false;
      showClear = false;
      showStop = true;
    }
    if (validateButton) {
      validateButton.disabled = false;
      validateButton.removeAttribute("disabled");
    }
    if (scrapeButton) {
      scrapeButton.disabled = false;
      scrapeButton.removeAttribute("disabled");
      if (!scrapeButton.dataset.defaultLabel) {
        scrapeButton.dataset.defaultLabel = (scrapeButton.textContent || "").trim() || "Popular";
      }
      var labelSource = row.closest("[data-repopulate-label]") || document.querySelector("[data-repopulate-label]");
      var repopulateLabel = (scrapeButton.dataset.repopulateLabel || (labelSource && labelSource.dataset.repopulateLabel) || "Re-popular").trim();
      var nextScrapeLabel = key === "populated" ? repopulateLabel : scrapeButton.dataset.defaultLabel;
      scrapeButton.textContent = nextScrapeLabel;
      scrapeButton.dataset.actionLabel = nextScrapeLabel;
      if (scrapeForm) {
        scrapeForm.dataset.actionLabel = nextScrapeLabel;
      }
    }
    if (clearButton) {
      clearButton.disabled = showClearBusy;
      if (showClearBusy) {
        clearButton.setAttribute("disabled", "disabled");
      } else {
        clearButton.removeAttribute("disabled");
      }
    }
    if (validateForm) {
      validateForm.hidden = !showValidate;
    }
    if (scrapeForm) {
      scrapeForm.hidden = !showScrape;
    }
    if (clearForm) {
      clearForm.hidden = !showClear;
    }
    if (stopButton) {
      stopButton.disabled = false;
      stopButton.removeAttribute("disabled");
    }
    if (stopForm) {
      stopForm.hidden = !showStop;
    }
  }

  function refreshConfigActionButtons(root) {
    (root || document).querySelectorAll("[data-config-row]").forEach(function (row) {
      updateConfigActionButtons(row, row.dataset.status || row.dataset.task || "pending");
    });
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
      row.dataset.errorCode = item.error_code || row.dataset.errorCode || "";
      row.dataset.errorSeverity = item.error_severity || row.dataset.errorSeverity || "";
      row.dataset.errorDescription = item.error_description || row.dataset.errorDescription || "";
      renderConfigTaskCell(
        row.querySelector('[data-field="task"]'),
        status,
        detailUrl || "",
        tableContainer,
        ["validating", "populating", "clearing", "running", "queued"].indexOf(normalizeConfigStatus(status)) !== -1,
        item.error_code || row.dataset.errorCode || "",
        item.error_description || row.dataset.errorDescription || "",
        item.error_severity || row.dataset.errorSeverity || ""
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

  function pollConfigTask(statusUrl, toast, button, summaryUrl, tableContainer, actionKind, actionLabel) {
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
          var toastContainer = tableContainer || document.querySelector("[data-config-editor]") || document.body;
          setConfigToastStatus(toast, data.status, configTaskLabel(toastContainer, data.status), data.error_code || "", data.error_description || "", data.error_severity || "");
          if (toast.detailLink && data.detail_url) {
            toast.detailLink.href = data.detail_url;
            toast.detailLink.hidden = false;
          }
          applyConfigProgress(data.config_progress, data.detail_url, tableContainer);
          if (data.is_active) {
            window.setTimeout(poll, CONFIG_TASK_POLL_MS);
            return;
          }
          var terminalStatus = data.status || "failed";
          var terminalMessage = configTaskTerminalMessage(toastContainer, terminalStatus, actionKind, actionLabel);
          setConfigToastStatus(toast, terminalStatus, terminalMessage, data.error_code || "", data.error_description || "", data.error_severity || "");
          toast.element.classList.add(terminalStatus === "succeeded" ? "is-success" : "is-error");
          if (data.error_severity === "warning") {
            toast.element.classList.add("is-warning");
          }
          if (button && !button.closest("[data-config-row]")) {
            button.disabled = false;
          }
          updateConfigRow(summaryUrl, tableContainer).finally(function () {
            var actionKey = String(actionKind || "").toLowerCase();
            var editor = null;
            if (terminalStatus === "succeeded" && (actionKey === "scrape" || actionKey === "clear")) {
              editor = tableContainer && tableContainer.matches && tableContainer.matches("[data-config-editor]")
                ? tableContainer
                : document.querySelector("[data-config-editor]");
            }
            var editorRefresh = editor ? refreshConfigEditorData(editor) : Promise.resolve();
            editorRefresh.finally(function () {
              if (!summaryUrl) {
                refreshConfigTables();
              }
              refreshTaskTables();
              scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
            });
          });
        })
        .catch(function (error) {
          setConfigToastStatus(toast, "failed", error.message || configTaskTerminalMessage(tableContainer || document.body, "failed", actionKind, actionLabel));
          toast.element.classList.add("is-error");
          if (button && !button.closest("[data-config-row]")) {
            button.disabled = false;
          }
          scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
        });
    }
    poll();
  }

  function initConfigExportActions(root) {
    (root || document).querySelectorAll("[data-config-export-form]").forEach(function (form) {
      if (!form || form._configExportBound) {
        return;
      }
      form._configExportBound = true;
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var button = event.submitter || form.querySelector("button[type='submit'], input[type='submit']");
        var previousText = button ? button.textContent : "";
        var title = form.dataset.actionLabel || (button ? button.textContent : "Exportar TOML");
        var toast = createConfigToast(title, form.dataset.exportingLabel || "Exportando TOML...", document.body);
        if (button) {
          button.disabled = true;
          if (form.dataset.exportingLabel) {
            button.textContent = form.dataset.exportingLabel;
          }
        }
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
          setConfigToastStatus(toast, "succeeded", data.message || form.dataset.exportSuccessLabel || "TOML exportado correctamente.");
          toast.element.classList.add("is-success");
          if (form.dataset.refreshOnSuccess === "1" || data.refresh) {
            markGroupReturnLinkNeedsFreshData(data.redirect_url || "");
            window.setTimeout(function () {
              if (data.redirect_url) {
                window.location.href = data.redirect_url;
              } else {
                window.location.reload();
              }
            }, 250);
            return;
          }
          scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
        }).catch(function (error) {
          setConfigToastStatus(toast, "failed", error.message || form.dataset.exportErrorLabel || "No se pudo exportar el TOML.");
          toast.element.classList.add("is-error");
          scheduleConfigToastDismiss(toast, CONFIG_TOAST_DONE_VISIBLE_MS);
        }).finally(function () {
          if (button) {
            button.disabled = false;
            if (previousText) {
              button.textContent = previousText;
            }
          }
        });
      });
    });
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
        var actionKind = configTaskActionKind(form, button);
        var actionRow = form.closest("[data-config-row]");
        if (actionRow) {
          var currentRowStatus = normalizeConfigStatus(actionRow.dataset.status || actionRow.dataset.task || "pending");
          var pendingStatus = actionKind === "scrape"
            ? (currentRowStatus === "populated" ? "clearing" : (currentRowStatus === "validated" ? "populating" : "validating"))
            : (actionKind === "clear" ? "clearing" : (actionKind === "stop" ? currentRowStatus : "validating"));
          actionRow.dataset.task = pendingStatus;
          actionRow.dataset.status = pendingStatus;
          renderConfigTaskCell(
            actionRow.querySelector('[data-field="task"]'),
            pendingStatus,
            "",
            tableContainer || actionRow,
            ["validating", "populating", "clearing", "running", "queued"].indexOf(pendingStatus) !== -1
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
          pollConfigTask(data.status_url, toast, button, form.dataset.summaryUrl || (button && button.dataset.summaryUrl) || data.summary_url, tableContainer, actionKind, actionLabel);
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

