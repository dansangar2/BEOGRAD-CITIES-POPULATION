  function configCityLabels(builderElement) {
    return {
      empty: builderElement.dataset.emptyLabel || "Sin datos.",
      remove: builderElement.dataset.removeLabel || "Quitar",
      edit: builderElement.dataset.editLabel || "Editar",
      available: builderElement.dataset.availableLabel || "Disponibles",
      selected: builderElement.dataset.selectedLabel || "Ciudad unificada",
      name: builderElement.dataset.nameLabel || "Nombre",
      code: builderElement.dataset.codeLabel || "Codigo",
      parent: builderElement.dataset.parentLabel || "Subdivisión mayor",
      selectedCount: builderElement.dataset.selectedCountLabel || "Elementos seleccionados",
      unnamed: builderElement.dataset.unnamedLabel || "Sin nombre",
      loading: builderElement.dataset.loadingLabel || "Cargando datos...",
      createTitle: builderElement.dataset.createTitleLabel || "Nueva ciudad",
      editTitle: builderElement.dataset.editTitleLabel || "Editar ciudad",
      filterName: builderElement.dataset.filterNameLabel || "Buscar por nombre",
      filterType: builderElement.dataset.filterTypeLabel || "Tipo",
      allTypes: builderElement.dataset.allTypesLabel || "Todos los tipos",
      noType: builderElement.dataset.noTypeLabel || "Sin tipo"
    };
  }

  function syncConfigCitiesJson(form, notify) {
    var hidden = form ? form.querySelector("[data-config-cities-json]") : null;
    var state = form ? form._configCityBuilderState : null;
    if (!hidden || !state) {
      return;
    }
    var payload = state.sections.map(function (section) {
      var children = state.data.children_by_parent[section.parent_id] || [];
      var childById = {};
      children.forEach(function (child) {
        childById[child.id] = child;
      });
      var selectedIds = section.selected_ids.filter(function (id) {
        return Boolean(childById[id]);
      });
      return {
        parent_id: section.parent_id,
        parent_name: section.parent_name,
        parent_label: section.parent_label,
        parent_code: section.parent_code,
        parent_level: section.parent_level,
        parent_type: section.parent_type,
        city: section.city,
        code: section.code,
        level: section.level,
        type: section.type,
        selected_ids: selectedIds,
        communes: selectedIds.map(function (id) {
          return childById[id].code || childById[id].id;
        }),
        keep_communes: section.keep_communes
      };
    });
    hidden.value = JSON.stringify(payload);
    if (notify) {
      var loaded = form.querySelector("[data-config-cities-loaded]");
      if (loaded) {
        loaded.value = "1";
      }
      notifyConfigFormChanged(hidden);
    }
  }

  function renderConfigCityTable(form) {
    var state = form ? form._configCityBuilderState : null;
    var builderElement = form ? form.querySelector("[data-config-city-builder]") : null;
    var tbody = form ? form.querySelector("[data-config-city-table]") : null;
    if (!state || !builderElement || !tbody) {
      return;
    }
    var labels = configCityLabels(builderElement);
    tbody.innerHTML = "";
    if (!state.sections.length) {
      var emptyRow = document.createElement("tr");
      emptyRow.dataset.configCityEmptyRow = "";
      var emptyCell = document.createElement("td");
      emptyCell.colSpan = 4;
      emptyCell.className = "table-empty-cell";
      emptyCell.textContent = labels.empty;
      emptyRow.appendChild(emptyCell);
      tbody.appendChild(emptyRow);
      return;
    }
    state.sections.forEach(function (section) {
      var children = state.data.children_by_parent[section.parent_id] || [];
      var childById = {};
      children.forEach(function (child) {
        childById[child.id] = child;
      });
      var row = document.createElement("tr");
      row.dataset.configCityTableRow = section.parent_id;
      if (!String(section.city || "").trim()) {
        row.classList.add("is-invalid");
      }
      var nameCell = document.createElement("td");
      nameCell.textContent = String(section.city || "").trim() || labels.unnamed;
      row.appendChild(nameCell);

      var parentCell = document.createElement("td");
      parentCell.textContent = section.parent_label || section.parent_name || "";
      row.appendChild(parentCell);

      var elementsCell = document.createElement("td");
      var elementsList = document.createElement("div");
      elementsList.className = "config-city-entity-list";
      if (section.selected_ids.length) {
        section.selected_ids.forEach(function (id) {
          var child = childById[id];
          var item = document.createElement("span");
          item.className = "config-city-entity-chip";
          item.textContent = child ? child.label : id;
          if (child && child.population_text) {
            item.title = child.population_text;
          }
          elementsList.appendChild(item);
        });
      } else {
        var emptyElements = document.createElement("span");
        emptyElements.className = "config-city-empty";
        emptyElements.textContent = labels.empty;
        elementsList.appendChild(emptyElements);
      }
      elementsCell.appendChild(elementsList);
      row.appendChild(elementsCell);

      var actionsCell = document.createElement("td");
      var actions = document.createElement("div");
      actions.className = "manual-action-buttons config-city-table-actions";
      [
        { action: "edit", label: labels.edit, className: "secondary mini-button" },
        { action: "remove", label: labels.remove, className: "quiet danger-text" }
      ].forEach(function (buttonInfo) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = buttonInfo.className;
        button.dataset.configCityTableAction = buttonInfo.action;
        button.dataset.parentId = section.parent_id;
        button.textContent = buttonInfo.label;
        actions.appendChild(button);
      });
      actionsCell.appendChild(actions);
      row.appendChild(actionsCell);
      tbody.appendChild(row);
    });
  }

  function configCityChildTypeKey(child) {
    return String((child && child.type_text) || "").trim() || "__none__";
  }

  function configCityTypeOptions(children, labels) {
    var seen = {};
    return (children || []).map(function (child) {
      var key = configCityChildTypeKey(child);
      if (seen[key]) {
        return null;
      }
      seen[key] = true;
      return { value: key, label: key === "__none__" ? labels.noType : key };
    }).filter(Boolean).sort(function (a, b) {
      return a.label.localeCompare(b.label);
    });
  }

  function configCityChildMatchesAvailableFilter(child, filters) {
    filters = filters || {};
    var query = normalizeSearchText(filters.name || "");
    if (query) {
      var haystack = normalizeSearchText([child.label, child.name, child.code, child.type_text].join(" "));
      if (haystack.indexOf(query) === -1) {
        return false;
      }
    }
    var type = String(filters.type || "");
    if (type && configCityChildTypeKey(child) !== type) {
      return false;
    }
    return true;
  }

  function renderConfigCityBadges(list, children, selectedIds, side, filters) {
    list.innerHTML = "";
    var selectedMap = {};
    selectedIds.forEach(function (id) {
      selectedMap[id] = true;
    });
    children.forEach(function (child) {
      var isSelected = Boolean(selectedMap[child.id]);
      if ((side === "selected") !== isSelected) {
        return;
      }
      if (side === "available" && !configCityChildMatchesAvailableFilter(child, filters)) {
        return;
      }
      var badge = document.createElement("button");
      badge.type = "button";
      badge.className = "group-transfer-badge config-city-badge";
      badge.dataset.configCityChild = child.id;
      badge.dataset.side = side;
      badge.textContent = child.label;
      if (child.population_text) {
        badge.title = child.population_text;
      }
      list.appendChild(badge);
    });
    if (!list.children.length) {
      var empty = document.createElement("span");
      empty.className = "config-city-empty";
      empty.textContent = list.dataset.emptyLabel || "Sin datos.";
      list.appendChild(empty);
    }
  }

  function cloneConfigCitySection(section) {
    section = section || {};
    return {
      parent_id: String(section.parent_id || ""),
      parent_name: String(section.parent_name || ""),
      parent_label: String(section.parent_label || ""),
      parent_code: String(section.parent_code || ""),
      parent_level: section.parent_level === undefined || section.parent_level === null ? "" : String(section.parent_level),
      parent_type: String(section.parent_type || ""),
      city: String(section.city || ""),
      code: String(section.code || ""),
      level: section.level === undefined || section.level === null ? "" : String(section.level),
      type: String(section.type || "City"),
      selected_ids: (section.selected_ids || []).map(function (id) { return String(id || ""); }).filter(Boolean),
      keep_communes: Boolean(section.keep_communes)
    };
  }

  function createConfigCitySectionElement(form, section, options) {
    options = options || {};
    var state = form._configCityBuilderState;
    var builderElement = form.querySelector("[data-config-city-builder]");
    var labels = configCityLabels(builderElement);
    var children = state.data.children_by_parent[section.parent_id] || [];
    var wrapper = document.createElement("div");
    wrapper.className = "config-city-section";
    if (options.flat) {
      wrapper.classList.add("is-flat");
    }
    wrapper.dataset.configCitySection = "";
    wrapper.dataset.parentId = section.parent_id;

    if (options.showHeader !== false) {
      var header = document.createElement("div");
      header.className = "config-city-section-header";
      var title = document.createElement("strong");
      title.textContent = section.parent_label;
      header.appendChild(title);
      if (options.showRemove !== false) {
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary small-button";
        remove.dataset.removeConfigCitySection = section.parent_id;
        remove.textContent = labels.remove;
        header.appendChild(remove);
      }
      wrapper.appendChild(header);
    }

    var fields = document.createElement("div");
    fields.className = "config-city-fields";
    [
      { key: "city", label: labels.name },
      { key: "code", label: labels.code }
    ].forEach(function (fieldInfo) {
      var label = document.createElement("label");
      label.textContent = fieldInfo.label;
      var input = document.createElement("input");
      input.type = "text";
      input.value = section[fieldInfo.key] || "";
      input.dataset.configCityField = fieldInfo.key;
      input.dataset.parentId = section.parent_id;
      if (fieldInfo.key === "city") {
        input.required = true;
        input.setAttribute("aria-required", "true");
        input.className = "config-city-name-input";
        input.placeholder = labels.name;
      }
      label.appendChild(input);
      fields.appendChild(label);
    });
    wrapper.appendChild(fields);

    var currentName = document.createElement("div");
    currentName.className = "config-city-current-name";
    currentName.dataset.configCityCurrentName = "";
    currentName.textContent = String(section.city || "").trim() || labels.unnamed;
    wrapper.appendChild(currentName);

    var panels = document.createElement("div");
    panels.className = "config-city-badge-panels";
    [
      { side: "available", label: labels.available },
      { side: "selected", label: labels.selected }
    ].forEach(function (panelInfo) {
      var panel = document.createElement("div");
      panel.className = "config-city-badge-panel";
      if (panelInfo.side === "available") {
        panel.classList.add("has-filters");
      }
      var panelTitle = document.createElement("span");
      panelTitle.className = "config-city-panel-label";
      panelTitle.textContent = panelInfo.label;
      panel.appendChild(panelTitle);
      if (panelInfo.side === "available") {
        var filters = document.createElement("div");
        filters.className = "config-city-available-filters";
        var search = document.createElement("input");
        search.type = "search";
        search.placeholder = labels.filterName;
        search.setAttribute("aria-label", labels.filterName);
        search.value = (state.modalFilters || {}).name || "";
        search.dataset.configCityAvailableFilter = "name";
        filters.appendChild(search);
        var typeSelect = document.createElement("select");
        typeSelect.setAttribute("aria-label", labels.filterType);
        typeSelect.dataset.configCityAvailableFilter = "type";
        var allOption = document.createElement("option");
        allOption.value = "";
        allOption.textContent = labels.allTypes;
        typeSelect.appendChild(allOption);
        configCityTypeOptions(children, labels).forEach(function (typeOption) {
          var option = document.createElement("option");
          option.value = typeOption.value;
          option.textContent = typeOption.label;
          typeSelect.appendChild(option);
        });
        typeSelect.value = (state.modalFilters || {}).type || "";
        if (typeSelect.value !== ((state.modalFilters || {}).type || "")) {
          typeSelect.value = "";
        }
        filters.appendChild(typeSelect);
        panel.appendChild(filters);
      }
      var list = document.createElement("div");
      list.className = "group-badge-list config-city-badge-list";
      list.dataset.emptyLabel = labels.empty;
      renderConfigCityBadges(list, children, section.selected_ids, panelInfo.side, state.modalFilters);
      panel.appendChild(list);
      panels.appendChild(panel);
    });
    wrapper.appendChild(panels);
    return wrapper;
  }

  function configCityModalElements(form) {
    var modal = form ? form.querySelector("[data-config-city-modal]") : null;
    return {
      modal: modal,
      title: modal ? modal.querySelector("[data-config-city-modal-title]") : null,
      parentField: modal ? modal.querySelector("[data-config-city-modal-parent-field]") : null,
      parentSelect: modal ? modal.querySelector("[data-config-city-parent-select]") : null,
      content: modal ? modal.querySelector("[data-config-city-modal-content]") : null
    };
  }

  function configCityParentOptionsForModal(state) {
    var originalParentId = state ? String(state.modalOriginalParentId || "") : "";
    var usedParents = {};
    (state.sections || []).forEach(function (section) {
      if (section.parent_id !== originalParentId) {
        usedParents[section.parent_id] = true;
      }
    });
    return (state.data.parent_options || []).filter(function (parent) {
      return !usedParents[parent.id] || parent.id === originalParentId;
    });
  }

  function populateConfigCityModalParentSelect(form, selectedParentId) {
    var state = form ? form._configCityBuilderState : null;
    var elements = configCityModalElements(form);
    var select = elements.parentSelect;
    if (!state || !select) {
      return "";
    }
    var options = configCityParentOptionsForModal(state);
    configCityDestroySelect(select);
    select.innerHTML = "";
    options.forEach(function (parent) {
      var option = document.createElement("option");
      option.value = parent.id;
      option.textContent = parent.label;
      select.appendChild(option);
    });
    select.disabled = options.length < 1;
    var selected = String(selectedParentId || "");
    if (options.length) {
      select.value = options.some(function (parent) { return parent.id === selected; }) ? selected : options[0].id;
      selected = select.value;
    } else {
      select.value = "";
      selected = "";
    }
    configCityRefreshSelect(select);
    return selected;
  }

  function renderConfigCityModalLoading(form) {
    var elements = configCityModalElements(form);
    var builderElement = form ? form.querySelector("[data-config-city-builder]") : null;
    var labels = builderElement ? configCityLabels(builderElement) : { loading: "Cargando datos..." };
    if (!elements.content) {
      return;
    }
    elements.content.innerHTML = "";
    elements.content.appendChild(createStatusElement("table-loading", labels.loading, true));
  }

  function renderConfigCityModalContent(form) {
    var state = form ? form._configCityBuilderState : null;
    var elements = configCityModalElements(form);
    if (!state || !elements.content) {
      return;
    }
    elements.content.innerHTML = "";
    if (!state.modalDraft) {
      return;
    }
    elements.content.appendChild(createConfigCitySectionElement(form, state.modalDraft, {
      flat: true,
      showHeader: false,
      showRemove: false
    }));
  }

  function updateConfigCityModalParent(form, parentId, options) {
    options = options || {};
    var state = form ? form._configCityBuilderState : null;
    var parent = state ? configCityParentById(state.data, parentId) : null;
    if (!state || !parent) {
      renderConfigCityModalContent(form);
      return;
    }
    if (!options.keepFilters) {
      state.modalFilters = { name: "", type: "" };
    }
    var previousDraft = state.modalDraft || {};
    var keepSelection = options.keepSelection && previousDraft.parent_id === parent.id;
    state.modalDraft = normalizeConfigCitySection({
      parent_id: parent.id,
      parent_name: parent.name,
      parent_label: parent.label,
      parent_code: parent.code,
      parent_level: state.data.parent_level,
      parent_type: parent.type_text,
      city: keepSelection ? previousDraft.city : parent.name,
      code: keepSelection ? previousDraft.code : (parent.code || parent.id),
      level: keepSelection ? previousDraft.level : state.data.legal_level,
      type: keepSelection ? previousDraft.type : "City",
      selected_ids: keepSelection ? previousDraft.selected_ids : []
    }, state.data);
    renderConfigCityModalLoading(form);
    ensureConfigCityChildren(form, parent.id).then(function () {
      state.modalDraft = normalizeConfigCitySection(state.modalDraft, state.data);
      renderConfigCityModalContent(form);
    }).catch(function (error) {
      window.console && window.console.error && window.console.error("No se pudieron cargar las ciudades", error);
      renderConfigCityModalContent(form);
    });
  }

  function closeConfigCityModal(form) {
    var state = form ? form._configCityBuilderState : null;
    var elements = configCityModalElements(form);
    if (!elements.modal) {
      return;
    }
    closeNativeSearchSelect(elements.parentSelect);
    elements.modal.hidden = true;
    if (state) {
      state.modalDraft = null;
      state.modalMode = "";
      state.modalOriginalParentId = "";
      state.modalOriginalIndex = -1;
    }
  }

  function openConfigCityModal(form, section) {
    var state = form ? form._configCityBuilderState : null;
    var builderElement = form ? form.querySelector("[data-config-city-builder]") : null;
    var elements = configCityModalElements(form);
    if (!state || !builderElement || !elements.modal) {
      return;
    }
    var labels = configCityLabels(builderElement);
    var editing = Boolean(section);
    state.modalMode = editing ? "edit" : "create";
    state.modalOriginalParentId = editing ? String(section.parent_id || "") : "";
    state.modalOriginalIndex = editing ? state.sections.findIndex(function (entry) {
      return entry.parent_id === state.modalOriginalParentId;
    }) : -1;
    if (elements.title) {
      elements.title.textContent = editing ? labels.editTitle : labels.createTitle;
    }
    state.modalFilters = { name: "", type: "" };
    elements.modal.hidden = false;
    var selectedParentId = populateConfigCityModalParentSelect(form, editing ? section.parent_id : "");
    if (editing) {
      state.modalDraft = cloneConfigCitySection(section);
      updateConfigCityModalParent(form, state.modalDraft.parent_id, { keepSelection: true });
    } else {
      state.modalDraft = null;
      updateConfigCityModalParent(form, selectedParentId);
    }
    window.setTimeout(function () {
      var select = elements.parentSelect;
      if (select && !select.disabled) {
        openNativeSearchSelectDropdown(select);
      } else {
        var first = elements.modal.querySelector("[data-config-city-field]");
        if (first) {
          first.focus();
        }
      }
    }, 0);
  }

  function saveConfigCityModal(form) {
    var state = form ? form._configCityBuilderState : null;
    var elements = configCityModalElements(form);
    if (!state || !state.modalDraft) {
      return;
    }
    var nameInput = elements.modal ? elements.modal.querySelector('[data-config-city-field="city"]') : null;
    if (!String(state.modalDraft.city || "").trim()) {
      if (nameInput && nameInput.reportValidity) {
        nameInput.reportValidity();
      }
      return;
    }
    var saved = normalizeConfigCitySection(state.modalDraft, state.data);
    if (!saved) {
      return;
    }
    var nextSections = state.sections.filter(function (section) {
      return section.parent_id !== state.modalOriginalParentId && section.parent_id !== saved.parent_id;
    });
    if (state.modalMode === "edit" && state.modalOriginalIndex >= 0 && state.modalOriginalIndex <= nextSections.length) {
      nextSections.splice(state.modalOriginalIndex, 0, saved);
    } else {
      nextSections.push(saved);
    }
    state.sections = nextSections;
    closeConfigCityModal(form);
    renderConfigCityBuilder(form, { notify: true });
  }

  function updateConfigCityAvailableFilter(form, field) {
    var state = form ? form._configCityBuilderState : null;
    if (!state || !field) {
      return;
    }
    var filterKey = field.dataset.configCityAvailableFilter;
    if (!filterKey) {
      return;
    }
    state.modalFilters = state.modalFilters || { name: "", type: "" };
    state.modalFilters[filterKey] = field.value || "";
    var selectionStart = typeof field.selectionStart === "number" ? field.selectionStart : null;
    renderConfigCityModalContent(form);
    var nextField = form.querySelector('[data-config-city-available-filter="' + filterKey + '"]');
    if (nextField && document.activeElement !== nextField) {
      nextField.focus();
      if (selectionStart !== null && nextField.setSelectionRange) {
        nextField.setSelectionRange(selectionStart, selectionStart);
      }
    }
  }

  function renderConfigCityBuilder(form, options) {
    options = options || {};
    var state = form ? form._configCityBuilderState : null;
    var builderElement = form ? form.querySelector("[data-config-city-builder]") : null;
    if (!state || !builderElement) {
      return false;
    }
    var enabled = state.data.enabled && state.data.parent_options.length > 0;
    builderElement.hidden = !enabled;
    if (!enabled) {
      closeConfigCityModal(form);
      syncConfigCitiesJson(form, false);
      return false;
    }
    renderConfigCityTable(form);
    var opener = form ? form.querySelector("[data-open-config-city-picker]") : null;
    if (opener) {
      opener.disabled = configCityParentOptionsForModal(state).length < 1;
    }
    syncConfigCitiesJson(form, Boolean(options.notify));
    return true;
  }

  function bindConfigCityBuilder(form) {
    var builderElement = form ? form.querySelector("[data-config-city-builder]") : null;
    if (!form || !builderElement || builderElement.dataset.configCityBuilderReady === "true") {
      return;
    }
    builderElement.dataset.configCityBuilderReady = "true";
    var opener = form.querySelector("[data-open-config-city-picker]");
    var elements = configCityModalElements(form);
    if (opener) {
      opener.addEventListener("click", function () {
        openConfigCityModal(form, null);
      });
    }
    if (elements.parentSelect) {
      elements.parentSelect.addEventListener("change", function () {
        updateConfigCityModalParent(form, String(elements.parentSelect.value || ""));
      });
    }
    if (elements.modal) {
      elements.modal.addEventListener("click", function (event) {
        if (event.target.closest("[data-close-config-city-modal]")) {
          event.preventDefault();
          closeConfigCityModal(form);
        }
      });
      elements.modal.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          closeConfigCityModal(form);
        }
      });
      var saveButton = elements.modal.querySelector("[data-save-config-city-modal]");
      if (saveButton) {
        saveButton.addEventListener("click", function () {
          saveConfigCityModal(form);
        });
      }
    }
    builderElement.addEventListener("click", function (event) {
      var tableAction = event.target.closest("[data-config-city-table-action]");
      var badge = event.target.closest("[data-config-city-child]");
      var state = form._configCityBuilderState;
      if (!state) {
        return;
      }
      if (tableAction && builderElement.contains(tableAction)) {
        event.preventDefault();
        var actionParentId = String(tableAction.dataset.parentId || "");
        if (tableAction.dataset.configCityTableAction === "remove") {
          state.sections = state.sections.filter(function (section) {
            return section.parent_id !== actionParentId;
          });
          renderConfigCityBuilder(form, { notify: true });
          return;
        }
        var sectionToEdit = state.sections.find(function (section) {
          return section.parent_id === actionParentId;
        });
        if (sectionToEdit) {
          openConfigCityModal(form, sectionToEdit);
        }
        return;
      }
      if (!badge || !builderElement.contains(badge)) {
        return;
      }
      event.preventDefault();
      var sectionElement = badge.closest("[data-config-city-section]");
      var parentId = sectionElement ? String(sectionElement.dataset.parentId || "") : "";
      var section = state.modalDraft && state.modalDraft.parent_id === parentId ? state.modalDraft : null;
      if (!section) {
        return;
      }
      var childId = String(badge.dataset.configCityChild || "");
      if (badge.dataset.side === "selected") {
        section.selected_ids = section.selected_ids.filter(function (id) { return id !== childId; });
      } else if (section.selected_ids.indexOf(childId) < 0) {
        section.selected_ids.push(childId);
      }
      renderConfigCityModalContent(form);
    });
    builderElement.addEventListener("input", function (event) {
      var availableFilter = event.target.closest("[data-config-city-available-filter]");
      if (availableFilter && builderElement.contains(availableFilter)) {
        updateConfigCityAvailableFilter(form, availableFilter);
        return;
      }
      var field = event.target.closest("[data-config-city-field]");
      var state = form._configCityBuilderState;
      if (!field || !state) {
        return;
      }
      var parentId = String(field.dataset.parentId || "");
      var section = state.modalDraft && state.modalDraft.parent_id === parentId ? state.modalDraft : null;
      if (!section) {
        return;
      }
      section[field.dataset.configCityField] = field.value || "";
      if (field.dataset.configCityField === "city") {
        var sectionElement = field.closest("[data-config-city-section]");
        var currentName = sectionElement ? sectionElement.querySelector("[data-config-city-current-name]") : null;
        if (currentName) {
          currentName.textContent = String(section.city || "").trim() || configCityLabels(builderElement).unnamed;
        }
      }
    });
    builderElement.addEventListener("change", function (event) {
      var availableFilter = event.target.closest("[data-config-city-available-filter]");
      if (availableFilter && builderElement.contains(availableFilter)) {
        updateConfigCityAvailableFilter(form, availableFilter);
      }
    });
  }
