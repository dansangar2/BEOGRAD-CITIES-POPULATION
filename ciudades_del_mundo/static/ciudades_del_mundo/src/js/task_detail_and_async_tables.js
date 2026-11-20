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
    var messageCode = document.querySelector("[data-task-message-code], [data-task-error-code]");
    var cancelForm = document.querySelector("[data-task-cancel-form]");
    var detailErrorCode = normalizeConfigErrorCode(data.error_code || "");
    if (status) {
      var detailLabel = taskStatusLabel(panel, data.status);
      status.textContent = detailLabel;
      status.className = "status-text " + configTaskKey(data.status || "");
      setElementErrorInfo(status, "", "");
    }
    if (messageCode) {
      messageCode.textContent = detailErrorCode || "-";
      messageCode.className = detailErrorCode
        ? "config-error-code config-code-" + String(data.error_severity || "info").toLowerCase()
        : "";
      setElementErrorInfo(messageCode, detailErrorCode, data.error_description || "", data.error_severity || "");
    }
    if (returncode) {
      var processReturnCode = data.returncode === null || data.returncode === undefined ? "-" : String(data.returncode);
      if (messageCode) {
        returncode.textContent = processReturnCode;
      } else {
        returncode.textContent = detailErrorCode ? detailErrorCode + (processReturnCode !== "-" ? " · " + processReturnCode : "") : processReturnCode;
        setElementErrorInfo(returncode, detailErrorCode, data.error_description || "", data.error_severity || "");
      }
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
    if (!statusUrl) {
      return;
    }
    var stopped = false;

    bindTaskCancelForm(panel);

    function poll() {
      if (stopped || !document.body.contains(panel)) {
        return;
      }
      fetch(taskDetailStatusUrl(panel, statusUrl), { headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" } })
        .then(parseJsonResponse)
        .then(function (data) {
          updateTaskDetailFromPayload(panel, data);
          if (data.is_active || data.output_has_more) {
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
    window.setTimeout(poll, 0);
  }

  function bindTaskCancelForm(panel) {
    var form = panel ? panel.querySelector("[data-task-cancel-form]") : document.querySelector("[data-task-cancel-form]");
    if (!form || form.dataset.taskCancelBound === "1") {
      return;
    }
    form.dataset.taskCancelBound = "1";
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = event.submitter || form.querySelector("button[type='submit']");
      var previousText = button ? button.textContent : "";
      var toast = createConfigToast("Cancelación", "Cancelación solicitada...", panel || document.body);
      if (button) {
        button.disabled = true;
        button.textContent = "Cancelando...";
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
        setConfigToastStatus(toast, "running", data.message || "Cancelación solicitada.");
        if (data.status_url) {
          panel.dataset.statusUrl = data.status_url;
        }
        updateTaskDetailFromPayload(panel, data);
        panel.dataset.active = data.is_active ? "1" : "0";
      }).catch(function (error) {
        setConfigToastStatus(toast, "failed", error.message || "No se pudo cancelar la tarea.");
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
        initConfigExportActions(target);
        refreshConfigActionButtons(target);
        initClientSorting(target, form);
        initGroupDivisionDeleteForms(target);
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
      if (form.dataset.localTable === "1") {
        var localTarget = tableTarget(form);
        if (!localTarget) {
          return;
        }
        initClientSorting(localTarget, form);
        initClickableRows(localTarget);
        initClientPagination(localTarget, form, 1);
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          applyClientFilters(localTarget, form, 1);
        });
        form.addEventListener("reset", function () {
          window.setTimeout(function () {
            applyClientFilters(localTarget, form, 1);
          }, 0);
        });
        form.querySelectorAll("input[type='search']").forEach(function (input) {
          input.addEventListener("input", function () {
            if (form._dynamicFilterTimer) {
              window.clearTimeout(form._dynamicFilterTimer);
            }
            form._dynamicFilterTimer = window.setTimeout(function () {
              applyClientFilters(localTarget, form, 1);
            }, ASYNC_TABLE_DYNAMIC_FILTER_MS);
          });
        });
        form.querySelectorAll("select").forEach(function (select) {
          select.addEventListener("change", function () {
            applyClientFilters(localTarget, form, 1);
          });
        });
        return;
      }
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

