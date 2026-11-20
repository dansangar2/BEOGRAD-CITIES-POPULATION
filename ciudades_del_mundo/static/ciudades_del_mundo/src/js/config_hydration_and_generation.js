  function runPageInitializer(label, callback) {
    try {
      callback();
    } catch (error) {
      window.console && window.console.error && window.console.error("Error inicializando " + label, error);
    }
  }

  function updateConfigCityBuilder(form, builderData) {
    var builderElement = form ? form.querySelector("[data-config-city-builder]") : null;
    var hidden = form ? form.querySelector("[data-config-cities-json]") : null;
    var loaded = form ? form.querySelector("[data-config-cities-loaded]") : null;
    if (!form || !builderElement || !hidden) {
      return false;
    }
    bindConfigCityBuilder(form);
    var data = normalizeConfigCityBuilderData(builderData);
    form._configCityBuilderState = {
      data: data,
      sections: []
    };
    if (loaded) {
      loaded.value = data.enabled ? "1" : "0";
    }
    form._configCityBuilderState.sections = form._configCityBuilderState.data.sections.slice();
    return renderConfigCityBuilder(form, { notify: false });
  }

  function updateManualConfigCities(form, rows, builderData) {
    var section = form ? form.querySelector("[data-config-cities-section]") : null;
    if (!section) {
      return;
    }
    var builderVisible = updateConfigCityBuilder(form, builderData || {});
    section.hidden = !builderVisible;
  }

  function updateManualConfigForm(editor, manual) {
    var form = editor ? editor.querySelector(".config-manual-form") : null;
    var tbody = form ? form.querySelector("[data-manual-pages]") : null;
    if (!form || !tbody || !manual) {
      return;
    }
    var country = form.querySelector('[name="manual_country_code"]');
    var legalSubdivision = form.querySelector('[name="manual_legal_subdivision"]');
    if (country) {
      country.value = manual.country_code || "";
    }
    if (legalSubdivision) {
      legalSubdivision.value = manual.legal_subdivision || "";
    }
    updateManualVisualAssetsForm(form, manual.visual_assets || {});
    updateManualAssetOverridesForm(form, manual.asset_overrides || []);
    updateManualConfigCities(form, manual.config_cities || [], manual.city_builder || {});
    var pages = Array.isArray(manual.pages) ? manual.pages : [];
    if (!pages.length) {
      pages = [{ source: "admin", paths: ["admin"], force_highest_level: "" }];
    }
    while (tbody.querySelectorAll("tr").length < pages.length) {
      var template = tbody.querySelector("tr");
      if (!template) {
        break;
      }
      var clone = template.cloneNode(true);
      clone.querySelectorAll("[data-remove-page-row]").forEach(function (button) {
        delete button.dataset.bound;
      });
      clone.querySelectorAll("[data-toggle-page-enabled]").forEach(function (button) {
        delete button.dataset.bound;
      });
      delete clone.dataset.pagePathReady;
      resetManualPageRow(clone);
      bindManualPageRow(clone, tbody);
      tbody.appendChild(clone);
    }
    while (tbody.querySelectorAll("tr").length > pages.length && tbody.querySelectorAll("tr").length > 1) {
      tbody.querySelector("tr:last-child").remove();
    }
    Array.prototype.slice.call(tbody.querySelectorAll("tr")).forEach(function (row, index) {
      var page = pages[index] || {};
      var source = row.querySelector('[name="page_source"]');
      var lowest = row.querySelector('[name="page_force_highest_level"], [name="page_lowest_level"], [name="page_level"]');
      var parentLevel = row.querySelector('[name="page_parent_level"]');
      if (source) {
        var nextSource = page.source || "admin";
        source.value = nextSource;
        if (source.value !== nextSource) {
          source.value = "cities";
        }
      }
      if (lowest) {
        var forcedLevel = page.force_highest_level;
        lowest.value = forcedLevel === undefined || forcedLevel === null ? "" : String(forcedLevel);
      }
      if (parentLevel) {
        var forcedParentLevel = page.parent_level;
        parentLevel.value = forcedParentLevel === undefined || forcedParentLevel === null ? "" : String(forcedParentLevel);
      }
      setManualPageInputField(row, "page_enabled", manualPageValue(page, "enabled", "true"));
      setCheckboxFallbackField(row, "page_include_infosection", manualPageValue(page, "include_infosection", "true"));
      setCheckboxFallbackField(row, "page_include_major_subdivision", manualPageValue(page, "include_major_subdivision", manualPageValue(page, "include_admin1", "true")));
      setCheckboxFallbackField(row, "page_include_minor_subdivision", manualPageValue(page, "include_minor_subdivision", manualPageValue(page, "include_admin2", "true")));
      setCheckboxFallbackField(row, "page_include_cities", manualPageValue(page, "include_cities", "true"));
      setCheckboxFallbackField(row, "page_sum_to_root", manualPageValue(page, "sum_to_root", "false"));
      setManualPageInputField(row, "page_repeat_infosection", manualPageValue(page, "repeat_infosection", ""));
      setManualPageInputField(row, "page_repeat_major_subdivision", manualPageValue(page, "repeat_major_subdivision", manualPageValue(page, "repeat_admin1", "")));
      setManualPageInputField(row, "page_repeat_minor_subdivision", manualPageValue(page, "repeat_minor_subdivision", manualPageValue(page, "repeat_admin2", "")));
      setManualPageInputField(row, "page_repeat_cities", manualPageValue(page, "repeat_cities", ""));
      var paths = Array.isArray(page.paths) && page.paths.length ? page.paths : splitPagePathsText(page.paths_text || "");
      setPagePathBoxes(row, paths.length ? paths : [""]);
      bindManualPageRow(row, tbody);
      updatePageConfigVisibility(row);
      updateManualPageEnabledState(row);
    });
  }

  function updateRawConfigForm(editor, content) {
    var rawTextarea = editor ? editor.querySelector('[data-raw-config-form] textarea[name="content"]') : null;
    if (!rawTextarea) {
      return;
    }
    rawTextarea.value = content || "";
    rawTextarea.dispatchEvent(new Event("input", { bubbles: true }));
    rawTextarea.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function updateConfigEditorFromGeneratedContent(editor, content, manual, options) {
    options = options || {};
    var output = editor ? editor.querySelector("[data-generated-config]") : null;
    if (output) {
      output.value = content || "";
      if (options.updateGeneratedPreview !== false) {
        output.dispatchEvent(new Event("input", { bubbles: true }));
        output.dispatchEvent(new Event("change", { bubbles: true }));
        var preview = output.closest("[data-generated-config-form]");
        if (preview) {
          preview.hidden = false;
        }
      }
    }
    updateRawConfigForm(editor, content || "");
    updateManualConfigForm(editor, manual);
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
      var hasSelect2 = Boolean(getSelect2Instance(select));
      if (hasSelect2) {
        destroySelect2(select);
      }
      values = values || [];
      var requireValue = select.dataset.requireValue === "true";
      var current = select.value || select.dataset.restoreValue || "";
      var emptyLabel = select.dataset.emptyOption || "";
      select.innerHTML = "";
      if (!values.length && disableWhenEmpty) {
        select.disabled = true;
        if (getSelect2Instance(select)) {
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
      if (select.matches("select[data-select2]")) {
        initSelect2Element(select);
        refreshSelect2OpenHandler(select);
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
        updateConfigEditorFromGeneratedContent(editor, data.content || "", data.manual || null);
        log.value = "\u2705 " + (data.log || "");
      }).catch(function (error) {
        log.value = "\u274c " + (error.message || "Error");
      }).finally(function () {
        button.disabled = false;
        generatorLoading.hidden = true;
      });
    });
  }

