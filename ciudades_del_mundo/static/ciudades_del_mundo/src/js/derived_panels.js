  function groupPanelCell(text, className) {
    var cell = document.createElement("td");
    if (className) {
      cell.className = className;
    }
    cell.textContent = String(text === null || text === undefined ? "" : text);
    return cell;
  }

  function groupPanelCodeCell(text) {
    var cell = document.createElement("td");
    var code = document.createElement("code");
    code.textContent = String(text || "");
    cell.appendChild(code);
    return cell;
  }

  function groupPanelCsrfInput() {
    var token = csrfFromDocument();
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrfmiddlewaretoken";
    input.value = token;
    return input;
  }

  function groupPanelActionForm(url, className, buttonClass, label, title, danger) {
    if (!url) {
      return null;
    }
    var form = document.createElement("form");
    var button = document.createElement("button");
    form.className = className;
    form.method = "post";
    form.action = url;
    form.appendChild(groupPanelCsrfInput());
    button.type = "submit";
    button.className = buttonClass;
    button.textContent = label;
    button.setAttribute("aria-label", title || label);
    button.title = title || label;
    if (danger) {
      form.dataset.confirmMessage = danger;
    }
    form.appendChild(button);
    return form;
  }

  function replaceGroupPanelRows(table, rows, renderRow, requestedPage) {
    var tbody = table ? table.querySelector("[data-client-page-rows]") : null;
    var emptyRow;
    if (!tbody) {
      return;
    }
    Array.prototype.slice.call(tbody.querySelectorAll("[data-client-row]")).forEach(function (row) {
      row.parentNode.removeChild(row);
    });
    emptyRow = tbody.querySelector("[data-client-empty]");
    rows.forEach(function (item) {
      tbody.insertBefore(renderRow(item), emptyRow || null);
    });
    table.dataset.clientPage = String(requestedPage || 1);
    initClickableRows(table);
    initGroupDivisionDeleteForms(table);
    rerenderClientTable(table, requestedPage || 1);
  }

  function renderGroupPanelGroupRow(item) {
    var row = document.createElement("tr");
    row.className = "clickable-row";
    row.tabIndex = 0;
    row.dataset.rowHref = item.href || "";
    row.dataset.clientRow = "";
    row.dataset.search = item.search_text || [item.internal_name, item.municipality_count].join(" ");
    row.appendChild(groupPanelCodeCell(item.internal_name));
    row.appendChild(groupPanelCell(item.municipality_count_text || "0", "numeric group-municipality-count"));
    return row;
  }

  function renderGroupPanelDivisionActions(item, table) {
    var cell = document.createElement("td");
    var cloneLabel = table.dataset.cloneLabel || "Clonar";
    var deleteLabel = table.dataset.deleteLabel || "Borrar";
    var confirmLabel = table.dataset.deleteConfirmLabel || "";
    var cloneForm = groupPanelActionForm(
      item.clone_url || "",
      "group-division-clone-form",
      "secondary group-division-clone-button",
      cloneLabel,
      cloneLabel,
      ""
    );
    var deleteForm = groupPanelActionForm(
      item.delete_url || "",
      "group-division-delete-form",
      "quiet danger-text group-division-delete-button",
      "\u00d7",
      deleteLabel,
      confirmLabel
    );
    cell.className = "group-division-action-cell";
    if (cloneForm) {
      cell.appendChild(cloneForm);
    }
    if (deleteForm) {
      cell.appendChild(deleteForm);
    }
    return cell;
  }

  function renderGroupPanelDivisionRow(item, table) {
    var row = document.createElement("tr");
    if (item.href) {
      row.className = "clickable-row";
      row.tabIndex = 0;
      row.dataset.rowHref = item.href;
    }
    row.dataset.clientRow = "";
    row.dataset.level = String(item.level || 0);
    row.dataset.search = item.search_text || "";
    row.appendChild(groupPanelCell(item.name || ""));
    row.appendChild(groupPanelCell(item.type_level_text || item.type_text || "-"));
    row.appendChild(groupPanelCell(item.area_text || "-", "numeric"));
    row.appendChild(groupPanelCell(item.population_text || "-", "numeric"));
    row.appendChild(renderGroupPanelDivisionActions(item, table));
    return row;
  }

  function renderDerivedSubdivisionListRow(item, table) {
    var row = document.createElement("tr");
    row.className = "clickable-row";
    row.tabIndex = 0;
    row.dataset.rowHref = item.href || "";
    row.dataset.clientRow = "";
    row.dataset.search = item.search_text || "";
    row.appendChild(groupPanelCodeCell(item.display_code || ""));
    row.appendChild(groupPanelCell(item.name || ""));
    row.appendChild(groupPanelCell(item.entity_type || "-"));
    row.appendChild(groupPanelCell(item.area_text || "-", "numeric"));
    row.appendChild(groupPanelCell(item.population_text || "-", "numeric"));
    row.appendChild(groupPanelCell(item.include_count || 0, "numeric"));
    row.appendChild(groupPanelCell(item.subtract_count || 0, "numeric"));
    row.appendChild(groupPanelCell(item.group_count || 0, "numeric"));
    row.appendChild(renderGroupPanelDivisionActions(item, table));
    return row;
  }

  function refreshGroupLevelFilter(panel, rows) {
    var select = panel ? panel.querySelector('select[name="level"]') : null;
    var current;
    if (!select) {
      return;
    }
    current = select.value || "";
    Array.prototype.slice.call(select.querySelectorAll("option:not(:first-child)")).forEach(function (option) {
      option.parentNode.removeChild(option);
    });
    (rows || []).forEach(function (level) {
      var option = document.createElement("option");
      option.value = String(level.level || "");
      option.textContent = level.label + (level.type_text && level.type_text !== "-" ? " - " + level.type_text : "");
      select.appendChild(option);
    });
    select.value = Array.prototype.slice.call(select.options).some(function (option) {
      return option.value === current;
    }) ? current : "";
  }

  function renderGroupCountryDetailPayload(panel, data) {
    var groupsTable = panel.querySelector('[id^="groups-table-"], [id^="subdivision-groups-table-"]');
    var divisionsTable = panel.querySelector('[id^="groups-divisions-table-"]');
    var subdivisionsTable = panel.querySelector('[id^="subdivisions-table-"]');
    if (Array.isArray(data.level_rows)) {
      refreshGroupLevelFilter(panel, data.level_rows);
    }
    if (groupsTable && Array.isArray(data.groups)) {
      replaceGroupPanelRows(groupsTable, data.groups, renderGroupPanelGroupRow, 1);
    }
    if (divisionsTable && Array.isArray(data.division_rows)) {
      replaceGroupPanelRows(divisionsTable, data.division_rows, function (item) {
        return renderGroupPanelDivisionRow(item, divisionsTable);
      }, 1);
    }
    if (subdivisionsTable && Array.isArray(data.subdivisions)) {
      replaceGroupPanelRows(subdivisionsTable, data.subdivisions, function (item) {
        return renderDerivedSubdivisionListRow(item, subdivisionsTable);
      }, 1);
    }
  }

  function showGroupPanelLoadError(panel, error) {
    var loading = panel ? panel.querySelector("[data-group-panel-loading]") : null;
    var panelBox = panel ? panel.querySelector("[data-group-country-panel]") : null;
    if (panelBox) {
      panelBox.classList.remove("is-loading");
    }
    if (loading) {
      loading.classList.add("table-error");
      loading.textContent = (error && error.message) || (document.body && document.body.dataset.failedLabel) || "Error";
      loading.hidden = false;
    }
  }

  function loadGroupCountryDetailPanel(card, panel, activation) {
    var url = card && card.dataset ? card.dataset.groupDetailUrl || "" : "";
    if (!url) {
      resetGroupCountryDetailLoading(panel);
      return Promise.resolve(null);
    }
    if (panel._groupDetailPromise) {
      return panel._groupDetailPromise;
    }
    if (panel.dataset.groupDetailLoaded === "1") {
      resetGroupCountryDetailLoading(panel);
      initGroupDivisionDeleteForms(panel);
      initClickableRows(panel);
    }
    panel._groupDetailPromise = fetchJson(url)
      .then(function (data) {
        renderGroupCountryDetailPayload(panel, data || {});
        panel.dataset.groupDetailLoaded = "1";
        if (!activation || card.dataset.groupCountryActivation === activation) {
          resetGroupCountryDetailLoading(panel);
        }
        return data;
      })
      .catch(function (error) {
        showGroupPanelLoadError(panel, error);
      })
      .finally(function () {
        delete panel._groupDetailPromise;
      });
    return panel._groupDetailPromise;
  }

  function resetGroupCountryDetailLoading(panel) {
    var loading;
    var panelBox;
    if (!panel) {
      return;
    }
    loading = panel.querySelector("[data-group-panel-loading]");
    panelBox = panel.querySelector("[data-group-country-panel]");
    if (loading) {
      loading.classList.remove("table-error");
      loading.hidden = true;
    }
    if (panelBox) {
      panelBox.classList.remove("is-loading");
    }
  }

  function activateGroupCountryCard(card, cards, details) {
    var targetId = card && card.dataset ? card.dataset.groupDetailTarget || "" : "";
    var target = targetId ? document.getElementById(targetId) : null;
    if (!card || !target) {
      return null;
    }
    cards = cards || Array.prototype.slice.call(document.querySelectorAll("[data-group-country-card]"));
    details = details || Array.prototype.slice.call(document.querySelectorAll("[data-group-country-detail]"));
    cards.forEach(function (item) {
      if (item !== card) {
        item.classList.remove("is-selected");
        item.setAttribute("aria-expanded", "false");
        delete item.dataset.groupCountryActivation;
      }
    });
    details.forEach(function (panel) {
      if (panel !== target) {
        resetGroupCountryDetailLoading(panel);
        panel.hidden = true;
      }
    });
    var activation = String(Date.now()) + String(Math.random());
    var loading = target.querySelector("[data-group-panel-loading]");
    var panelBox = target.querySelector("[data-group-country-panel]");
    card.dataset.groupCountryActivation = activation;
    card.classList.add("is-selected");
    card.setAttribute("aria-expanded", "true");
    target.hidden = false;
    if (loading && panelBox) {
      panelBox.classList.add("is-loading");
      loading.hidden = false;
    }
    loadGroupCountryDetailPanel(card, target, activation);
    return target;
  }

  function initGroupCountryCards(root) {
    var scope = root || document;
    var cards = Array.prototype.slice.call(scope.querySelectorAll("[data-group-country-card]"));
    if (!cards.length) {
      return;
    }
    var details = Array.prototype.slice.call(scope.querySelectorAll("[data-group-country-detail]"));
    cards.forEach(function (card) {
      if (card.dataset.groupCountryReady === "true") {
        return;
      }
      card.dataset.groupCountryReady = "true";
      card.addEventListener("click", function () {
        activateGroupCountryCard(card, cards, details);
      });
    });
  }

  function normalizedUppercaseCodeInputValue(value, finalPass) {
    var text = String(value || "");
    if (text.normalize) {
      text = text.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
    text = text.toUpperCase().replace(/[^A-Z0-9_-]+/g, "_").replace(/_+/g, "_").replace(/-+/g, "-");
    return finalPass ? text.replace(/^_+|_+$/g, "") : text;
  }

  function initUppercaseCodeInputs(root) {
    var scope = root || document;
    Array.prototype.slice.call(scope.querySelectorAll("[data-uppercase-code-input]")).forEach(function (input) {
      if (input.dataset.uppercaseCodeReady === "1") {
        return;
      }
      input.dataset.uppercaseCodeReady = "1";
      var applyValue = function (finalPass) {
        var next = normalizedUppercaseCodeInputValue(input.value, finalPass);
        if (input.value !== next) {
          input.value = next;
        }
      };
      input.addEventListener("input", function () { applyValue(false); });
      input.addEventListener("change", function () { applyValue(true); });
      if (input.form && input.form.dataset.uppercaseCodeSubmitBound !== "1") {
        input.form.dataset.uppercaseCodeSubmitBound = "1";
        input.form.addEventListener("submit", function () {
          Array.prototype.slice.call(input.form.querySelectorAll("[data-uppercase-code-input]")).forEach(function (field) {
            field.value = normalizedUppercaseCodeInputValue(field.value, true);
          });
        });
      }
      applyValue(true);
    });
  }

  function initNewCountryResultCards(root) {
    var scope = root || document;
    var cards = Array.prototype.slice.call(scope.querySelectorAll("[data-new-country-detail-card]"));
    if (!cards.length) {
      return;
    }
    cards.forEach(function (button) {
      if (button.dataset.newCountryDetailReady === "1") {
        return;
      }
      button.dataset.newCountryDetailReady = "1";
      button.addEventListener("click", function () {
        var url = button.dataset.detailUrl || "";
        var targetId = button.dataset.newCountryDetailTarget || "";
        var target = targetId ? document.getElementById(targetId) : null;
        var panel = button.closest("[data-group-country-detail]") || document;
        if (!url || !target || button.disabled) {
          return;
        }
        Array.prototype.slice.call(panel.querySelectorAll(".new-country-result-card")).forEach(function (card) {
          card.classList.remove("is-selected");
        });
        var currentCard = button.closest(".new-country-result-card");
        if (currentCard) {
          currentCard.classList.add("is-selected");
        }
        loadStatsCountryDetail(target, url, target);
      });
    });
  }

  function initGroupDivisionDeleteForms(root) {
    (root || document).querySelectorAll("form.group-division-delete-form").forEach(function (form) {
      if (form.dataset.groupDivisionDeleteBound === "1") {
        return;
      }
      form.dataset.groupDivisionDeleteBound = "1";
      form.addEventListener("submit", function (event) {
        var button;
        var confirmMessage;
        var row;
        var target;
        if (event.defaultPrevented) {
          return;
        }
        if (!window.fetch || !window.FormData) {
          return;
        }
        if (form.dataset.groupDivisionDeleting === "1") {
          event.preventDefault();
          if (event.stopImmediatePropagation) {
            event.stopImmediatePropagation();
          }
          return;
        }
        confirmMessage = form.dataset.confirmMessage || "";
        if (confirmMessage && !window.confirm(confirmMessage)) {
          event.preventDefault();
          if (event.stopImmediatePropagation) {
            event.stopImmediatePropagation();
          }
          return;
        }
        event.preventDefault();
        if (event.stopImmediatePropagation) {
          event.stopImmediatePropagation();
        }
        button = event.submitter || form.querySelector("button[type='submit']");
        row = form.closest("[data-client-row]");
        target = row ? row.closest("[id]") : form.closest("[id]");
        form.dataset.groupDivisionDeleting = "1";
        if (button) {
          button.disabled = true;
          button.setAttribute("aria-busy", "true");
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
        }).then(parseJsonResponse).then(function () {
          if (target && target.matches("[data-new-country-config-tree]")) {
            target.dispatchEvent(new Event("new-country-entity-deleted"));
            return;
          }
          if (row && row.parentNode) {
            row.parentNode.removeChild(row);
          }
          if (target) {
            rerenderClientTable(target);
          }
        }).catch(function (error) {
          delete form.dataset.groupDivisionDeleting;
          if (button) {
            button.disabled = false;
            button.removeAttribute("aria-busy");
          }
          window.alert((error && error.message) || (document.body && document.body.dataset.failedLabel) || "Error");
        });
      }, true);
    });
  }

