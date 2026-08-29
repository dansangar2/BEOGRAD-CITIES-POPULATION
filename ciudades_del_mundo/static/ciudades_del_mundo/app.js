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

  function getSelect2Instance(select) {
    if (!select || !window.jQuery || !window.jQuery.fn || !window.jQuery.fn.select2) {
      return null;
    }
    var instance = window.jQuery(select).data("select2");
    return instance && typeof instance === "object" && typeof instance.destroy === "function" ? instance : null;
  }

  function normalizeSearchText(value) {
    var text = String(value || "");
    if (text.normalize) {
      text = text.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
    return text.toLocaleLowerCase();
  }

  function select2NormalizedMatcher(params, data) {
    var term = normalizeSearchText(params && params.term);
    if (!term) {
      return data;
    }
    if (data && data.children && data.children.length) {
      var children = data.children.map(function (child) {
        return select2NormalizedMatcher(params, child);
      }).filter(Boolean);
      if (children.length) {
        var match = window.jQuery ? window.jQuery.extend(true, {}, data) : Object.assign({}, data);
        match.children = children;
        return match;
      }
    }
    if (normalizeSearchText(data && data.text).indexOf(term) !== -1) {
      return data;
    }
    return null;
  }

  var openNativeSearchSelect = null;

  function nativeSearchSelectOptions(element) {
    return Array.prototype.slice.call((element && element.options) || []).filter(function (option) {
      return String(option.value || "").trim();
    }).map(function (option) {
      return { value: String(option.value), label: option.textContent || option.value };
    });
  }

  function nativeSearchSelectLabel(element) {
    var selected = Array.prototype.slice.call((element && element.options) || []).find(function (option) {
      return option.value === element.value;
    });
    return selected ? selected.textContent : element.dataset.placeholder || "";
  }

  function closeNativeSearchSelect(element) {
    var widget = element && element.__nativeSearchWidget;
    if (!widget) {
      return;
    }
    widget.classList.remove("is-open");
    widget.querySelector(".group-search-select-menu").hidden = true;
    widget.querySelector(".group-search-select-trigger").setAttribute("aria-expanded", "false");
    if (openNativeSearchSelect === element) {
      openNativeSearchSelect = null;
    }
  }

  function closeOpenNativeSearchSelect(exceptElement) {
    if (openNativeSearchSelect && openNativeSearchSelect !== exceptElement) {
      closeNativeSearchSelect(openNativeSearchSelect);
    }
  }

  function ensureNativeSearchSelect(element) {
    if (!element) {
      return null;
    }
    if (element.__nativeSearchWidget) {
      return element.__nativeSearchWidget;
    }
    element.classList.add("group-native-select");
    var widget = document.createElement("div");
    widget.className = "group-search-select";
    widget.innerHTML = [
      '<button type="button" class="group-search-select-trigger" aria-haspopup="listbox" aria-expanded="false"></button>',
      '<div class="group-search-select-menu" hidden>',
      '<input type="search" class="group-search-select-input" autocomplete="off">',
      '<div class="group-search-select-options" role="listbox"></div>',
      '</div>'
    ].join("");
    element.insertAdjacentElement("afterend", widget);
    element.__nativeSearchWidget = widget;
    var trigger = widget.querySelector(".group-search-select-trigger");
    var input = widget.querySelector(".group-search-select-input");
    trigger.addEventListener("click", function () {
      if (element.disabled) {
        return;
      }
      if (widget.classList.contains("is-open")) {
        closeNativeSearchSelect(element);
      } else {
        openNativeSearchSelectDropdown(element);
      }
    });
    input.addEventListener("input", function () {
      renderNativeSearchSelectOptions(element);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeNativeSearchSelect(element);
        trigger.focus();
      }
    });
    return widget;
  }

  function visibleNativeSearchSelectOptions(element) {
    var widget = ensureNativeSearchSelect(element);
    var input = widget ? widget.querySelector(".group-search-select-input") : null;
    var query = normalizeSearchText(input ? input.value : "");
    var options = nativeSearchSelectOptions(element);
    if (!query) {
      return options;
    }
    return options.filter(function (option) {
      return normalizeSearchText((option.label || "") + " " + (option.value || "")).indexOf(query) !== -1;
    });
  }

  function renderNativeSearchSelectOptions(element) {
    var widget = ensureNativeSearchSelect(element);
    if (!widget) {
      return;
    }
    var list = widget.querySelector(".group-search-select-options");
    list.innerHTML = "";
    var options = visibleNativeSearchSelectOptions(element);
    if (!options.length) {
      var empty = document.createElement("div");
      empty.className = "group-search-select-empty";
      empty.textContent = element.dataset.emptyResultsLabel || "Sin datos.";
      list.appendChild(empty);
      return;
    }
    options.forEach(function (option) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "group-search-select-option";
      button.textContent = option.label;
      button.dataset.value = option.value;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(option.value) === String(element.value) ? "true" : "false");
      button.addEventListener("click", function () {
        element.value = option.value;
        element.dispatchEvent(new Event("change", { bubbles: true }));
        closeNativeSearchSelect(element);
        syncNativeSearchSelect(element);
      });
      list.appendChild(button);
    });
  }

  function syncNativeSearchSelect(element) {
    var widget = ensureNativeSearchSelect(element);
    if (!widget) {
      return;
    }
    var trigger = widget.querySelector(".group-search-select-trigger");
    trigger.textContent = nativeSearchSelectLabel(element);
    trigger.disabled = element.disabled;
    widget.classList.toggle("is-disabled", element.disabled);
    if (element.disabled) {
      closeNativeSearchSelect(element);
    }
    if (widget.classList.contains("is-open")) {
      renderNativeSearchSelectOptions(element);
    }
  }

  function openNativeSearchSelectDropdown(element) {
    var widget = ensureNativeSearchSelect(element);
    if (!widget || element.disabled) {
      return;
    }
    closeOpenNativeSearchSelect(element);
    openNativeSearchSelect = element;
    widget.classList.add("is-open");
    widget.querySelector(".group-search-select-menu").hidden = false;
    widget.querySelector(".group-search-select-trigger").setAttribute("aria-expanded", "true");
    var input = widget.querySelector(".group-search-select-input");
    input.value = "";
    input.placeholder = element.dataset.placeholder || "";
    renderNativeSearchSelectOptions(element);
    window.setTimeout(function () {
      input.focus();
    }, 0);
  }

  document.addEventListener("click", function (event) {
    if (openNativeSearchSelect && openNativeSearchSelect.__nativeSearchWidget && !openNativeSearchSelect.__nativeSearchWidget.contains(event.target)) {
      closeNativeSearchSelect(openNativeSearchSelect);
    }
  });

  function cleanupSelect2State(select) {
    if (!select || !window.jQuery) {
      return;
    }
    window.jQuery(select).removeData("select2");
    select.classList.remove("select2-hidden-accessible");
    select.removeAttribute("aria-hidden");
    select.removeAttribute("tabindex");
    var next = select.nextElementSibling;
    if (next && next.classList.contains("select2-container")) {
      next.remove();
    }
  }

  function destroySelect2(select) {
    if (!getSelect2Instance(select)) {
      if (select && (select.classList.contains("select2-hidden-accessible") || (select.nextElementSibling && select.nextElementSibling.classList.contains("select2-container")))) {
        cleanupSelect2State(select);
      }
      return;
    }
    try {
      window.jQuery(select).select2("destroy");
    } catch (error) {
      cleanupSelect2State(select);
    }
  }

  function initSelect2Element(element) {
    if (!element || getSelect2Instance(element)) {
      return;
    }
    if (element.classList.contains("select2-hidden-accessible") || (element.nextElementSibling && element.nextElementSibling.classList.contains("select2-container"))) {
      cleanupSelect2State(element);
    }
    var select = window.jQuery(element);
    var forceSearch = select.data("select2Search") === "always";
    var requireValue = select.data("requireValue") === true || select.data("requireValue") === "true";
    try {
      select.select2({
        width: "100%",
        placeholder: select.data("placeholder") || "",
        allowClear: !requireValue,
        minimumResultsForSearch: 0,
        matcher: select2NormalizedMatcher,
        dropdownCssClass: forceSearch ? "select2-dropdown-search-visible" : ""
      });
    } catch (error) {
      cleanupSelect2State(element);
      return;
    }
    if (forceSearch) {
      select.off("select2:open.globalSearch").on("select2:open.globalSearch", function () {
        window.setTimeout(function () {
          var search = document.querySelector(".select2-container--open .select2-search__field");
          if (search) {
            search.placeholder = select.data("placeholder") || "";
            search.focus();
          }
        }, 0);
      });
    }
  }

  function initSelect2(root) {
    if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.select2) {
      return;
    }
    var scope = window.jQuery(root || document);
    var selects = scope.find("select[data-select2]");
    if (scope.is && scope.is("select[data-select2]")) {
      selects = scope.add(selects);
    }
    selects.each(function () {
      initSelect2Element(this);
    });
  }

  function refreshSelect2Element(select) {
    if (!select || !select.matches("select[data-select2]")) {
      return;
    }
    destroySelect2(select);
    initSelect2Element(select);
  }

  function refreshSelect2OpenHandler(select) {
    if (!select || !window.jQuery || !getSelect2Instance(select)) {
      return;
    }
    var selectElement = window.jQuery(select);
    var forceSearch = selectElement.data("select2Search") === "always";
    if (!forceSearch) {
      return;
    }
    selectElement.off("select2:open.globalSearch").on("select2:open.globalSearch", function () {
      window.setTimeout(function () {
        var search = document.querySelector(".select2-container--open .select2-search__field");
        if (search) {
          search.placeholder = selectElement.data("placeholder") || "";
          search.focus();
        }
      });
    });
  }

  window.CiudadesSelect2 = {
    init: initSelect2,
    refresh: refreshSelect2Element
  };

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
    var levelSelect = form.querySelector("select[name='level']");
    if (levelSelect) {
      var selectedLevel = normalizedClientValue(levelSelect.value);
      if (selectedLevel) {
        var rowLevel = normalizedClientValue(row.dataset.level || "");
        if (rowLevel !== selectedLevel) {
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

  function clientPaginationPages(page, pageCount) {
    var pages = [];
    if (pageCount <= 7) {
      for (var simplePage = 1; simplePage <= pageCount; simplePage += 1) {
        pages.push(simplePage);
      }
      return pages;
    }
    if (page <= 4) {
      for (var earlyPage = 1; earlyPage <= 5; earlyPage += 1) {
        pages.push(earlyPage);
      }
      pages.push("ellipsis", pageCount);
      return pages;
    }
    if (page >= pageCount - 3) {
      pages.push(1, "ellipsis");
      for (var latePage = pageCount - 4; latePage <= pageCount; latePage += 1) {
        pages.push(latePage);
      }
      return pages;
    }
    return [1, "ellipsis", page - 1, page, page + 1, "ellipsis", pageCount];
  }

  function renderClientPaginationNav(nav, page, pageCount, visibleCount) {
    nav.innerHTML = "";
    var previous = document.createElement("button");
    previous.type = "button";
    previous.dataset.clientPage = "previous";
    previous.textContent = "<";
    previous.title = "Anterior";
    previous.setAttribute("aria-label", "Anterior");
    previous.disabled = page <= 1 || visibleCount === 0;
    nav.appendChild(previous);

    clientPaginationPages(page, pageCount).forEach(function (entry) {
      if (entry === "ellipsis") {
        var ellipsis = document.createElement("span");
        ellipsis.className = "client-page-ellipsis";
        ellipsis.textContent = "...";
        ellipsis.setAttribute("aria-hidden", "true");
        nav.appendChild(ellipsis);
        return;
      }
      var number = document.createElement("button");
      number.type = "button";
      number.dataset.clientPageNumber = String(entry);
      number.textContent = String(entry);
      number.title = "Pagina " + entry;
      number.setAttribute("aria-label", "Pagina " + entry);
      if (entry === page) {
        number.classList.add("is-current");
        number.setAttribute("aria-current", "page");
        number.disabled = true;
      }
      nav.appendChild(number);
    });

    var next = document.createElement("button");
    next.type = "button";
    next.dataset.clientPage = "next";
    next.textContent = ">";
    next.title = "Siguiente";
    next.setAttribute("aria-label", "Siguiente");
    next.disabled = page >= pageCount || visibleCount === 0;
    nav.appendChild(next);
  }

  function renderClientPagination(target, form, requestedPage) {
    var navs = Array.prototype.slice.call(target.querySelectorAll("[data-client-pagination]"));
    if (!navs.length) {
      return false;
    }
    var rows = Array.prototype.slice.call(target.querySelectorAll("[data-client-row]"));
    var visibleRows = rows.filter(function (row) {
      return row.dataset.clientFilterHidden !== "1";
    });
    var emptyRow = ensureClientEmptyRow(target, form);
    var initialSize = parseInt(navs[0].dataset.initialPageSize || "25", 10);
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
    navs.forEach(function (nav) {
      nav.hidden = visibleRows.length <= pageSize;
      renderClientPaginationNav(nav, visibleRows.length ? page : 1, visibleRows.length ? pageCount : 1, visibleRows.length);
    });
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

  function isRowNavigationInteractiveTarget(event) {
    return Boolean(event.target && event.target.closest && event.target.closest("a, button, input, select, textarea, label"));
  }

  function navigationUrl(value) {
    return typeof value === "function" ? value() : value;
  }

  var GROUP_RETURN_STATE_KEY = "ciudades_del_mundo_group_return_state";

  function writeGroupReturnState(state) {
    if (!state || !state.rowHref || !window.sessionStorage) {
      return;
    }
    try {
      window.sessionStorage.setItem(GROUP_RETURN_STATE_KEY, JSON.stringify(state));
    } catch (error) {}
  }

  function readGroupReturnState() {
    var raw = "";
    if (!window.sessionStorage) {
      return null;
    }
    try {
      raw = window.sessionStorage.getItem(GROUP_RETURN_STATE_KEY) || "";
    } catch (error) {
      return null;
    }
    if (!raw) {
      return null;
    }
    try {
      return JSON.parse(raw);
    } catch (error) {
      return null;
    }
  }

  function clearGroupReturnState() {
    if (!window.sessionStorage) {
      return;
    }
    try {
      window.sessionStorage.removeItem(GROUP_RETURN_STATE_KEY);
    } catch (error) {}
  }

  function reloadGroupListIfReturnNeedsFreshData(state) {
    if (!state || state.refreshList !== true) {
      return false;
    }
    state.refreshList = false;
    writeGroupReturnState(state);
    window.location.reload();
    return true;
  }

  function rememberGroupReturnForRow(row) {
    var detail = row ? row.closest("[data-group-country-detail]") : null;
    var table = row ? row.closest("[id]") : null;
    var href = row && row.dataset ? row.dataset.rowHref || "" : "";
    if (!detail || !href) {
      return;
    }
    writeGroupReturnState({
      country: detail.dataset.groupCountryKey || "",
      rowHref: href,
      tableId: table ? table.id || "" : "",
      table: table && table.id && table.id.indexOf("groups-divisions-table-") === 0 ? "divisions" : "groups",
      createdAt: Date.now()
    });
  }

  function markGroupReturnLinkNeedsFreshData(rowHref) {
    var link = document.querySelector("[data-group-return-link]");
    if (!link) {
      return;
    }
    rowHref = rowHref || link.dataset.groupReturnRowHref || window.location.pathname;
    if (!rowHref) {
      return;
    }
    link.dataset.groupReturnRowHref = rowHref;
    link.dataset.groupReturnRefresh = "1";
    writeGroupReturnState({
      country: link.dataset.groupReturnCountry || "",
      rowHref: rowHref,
      table: link.dataset.groupReturnTable || "divisions",
      tableId: link.dataset.groupReturnTableId || "",
      refreshList: true,
      createdAt: Date.now()
    });
  }

  function sameOriginPath(value) {
    try {
      var url = new URL(value, window.location.href);
      return url.origin === window.location.origin ? url.pathname.replace(/\/+$/, "") : "";
    } catch (error) {
      return "";
    }
  }

  function groupRowHrefMatches(row, rowHref) {
    var targetPath = sameOriginPath(rowHref);
    var rowPath = sameOriginPath(row && row.dataset ? row.dataset.rowHref || "" : "");
    return Boolean(targetPath && rowPath && targetPath === rowPath);
  }

  function openNavigationUrl(url, newTab) {
    url = String(url || "");
    if (!url) {
      return;
    }
    if (newTab) {
      window.open(url, "_blank", "noopener");
      return;
    }
    window.location.href = url;
  }

  function bindMiddleClickNavigation(element, urlGetter, shouldIgnore) {
    var lastOpenAt = 0;

    function ignored(event) {
      return typeof shouldIgnore === "function" && shouldIgnore(event);
    }

    function targetUrl() {
      return navigationUrl(urlGetter);
    }

    function hasTarget() {
      return Boolean(targetUrl());
    }

    function reserveMiddleClick(event) {
      if (!event || event.button !== 1 || ignored(event) || !hasTarget()) {
        return;
      }
      event.preventDefault();
    }

    function openFromMiddleClick(event) {
      var now;
      var url;
      if (!event || event.button !== 1 || ignored(event)) {
        return;
      }
      url = targetUrl();
      if (!url) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      now = Date.now();
      if (now - lastOpenAt < 500) {
        return;
      }
      lastOpenAt = now;
      openNavigationUrl(url, true);
    }

    if (!element || element.__middleClickNavigationBound) {
      return;
    }
    element.__middleClickNavigationBound = true;
    element.addEventListener("mousedown", reserveMiddleClick);
    element.addEventListener("mouseup", openFromMiddleClick);
    element.addEventListener("auxclick", openFromMiddleClick);
  }

  function openRowNavigation(row, newTab) {
    var href = row && row.dataset ? row.dataset.rowHref || "" : "";
    rememberGroupReturnForRow(row);
    openNavigationUrl(href, newTab);
  }

  function initClickableRows(target) {
    target.querySelectorAll("[data-row-href]").forEach(function (row) {
      if (row.dataset.rowClickBound === "1") {
        return;
      }
      row.dataset.rowClickBound = "1";
      row.addEventListener("click", function (event) {
        if (isRowNavigationInteractiveTarget(event)) {
          return;
        }
        openRowNavigation(row, event.ctrlKey || event.metaKey);
      });
      bindMiddleClickNavigation(row, function () {
        return row.dataset.rowHref || "";
      }, isRowNavigationInteractiveTarget);
      row.addEventListener("keydown", function (event) {
        if (isRowNavigationInteractiveTarget(event)) {
          return;
        }
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        openRowNavigation(row, false);
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
        var button = event.target.closest("button[data-client-page], button[data-client-page-number]");
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
        var next = parseInt(button.dataset.clientPageNumber || "", 10);
        if (!Number.isFinite(next)) {
          next = button.dataset.clientPage === "next" ? current + 1 : current - 1;
        }
        renderClientPagination(target, formForTableTarget(target) || form, next);
      });
      target.addEventListener("change", function (event) {
        var select = event.target.closest("select[data-client-page-select]");
        if (!select || !target.contains(select)) {
          return;
        }
        var next = parseInt(select.value || "1", 10);
        if (!Number.isFinite(next) || next < 1) {
          next = 1;
        }
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
    var pageButton = document.createElement("a");
    pageButton.className = "button secondary";
    pageButton.href = detailUrl || fullSrc;
    pageButton.target = "_blank";
    pageButton.rel = "noopener";
    pageButton.textContent = "Ficha";
    var fullButton = document.createElement("a");
    fullButton.className = "button";
    fullButton.href = fullSrc;
    fullButton.target = "_blank";
    fullButton.rel = "noopener";
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
      openNavigationUrl(detailUrl || fullSrc, true);
    });
    bindMiddleClickNavigation(image, function () {
      return detailUrl || fullSrc || "";
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

  function formatDecimalNoGrouping(value, decimals) {
    var number = Number(value || 0);
    var text;
    if (!Number.isFinite(number)) {
      return String(value || 0);
    }
    if (Number.isInteger(number)) {
      return String(number);
    }
    text = number.toFixed(decimals);
    return text.replace(/\.?0+$/, "").replace(".", ",");
  }

  function formatNumber(value) {
    try {
      return formatDecimalNoGrouping(value, 2);
    } catch (error) {
      return String(value || 0);
    }
  }

  function formatPercent(value, total) {
    if (!total) {
      return "0%";
    }
    return formatDecimalNoGrouping((value / total) * 100, 1) + "%";
  }

  function formatPercentNumber(value) {
    var percent = Number(value || 0);
    if (!Number.isFinite(percent)) {
      percent = 0;
    }
    return formatDecimalNoGrouping(Math.max(0, Math.min(100, percent)), 2) + "%";
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
        bindMiddleClickNavigation(path, function () {
          return segment.detailUrl || "";
        });
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
        bindMiddleClickNavigation(row, function () {
          return item.detailUrl || "";
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
        bindMiddleClickNavigation(row, function () {
          return item.detailUrl || "";
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
        bindMiddleClickNavigation(row, function () {
          return entry.detailUrl || "";
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

  function textListOrDash(value) {
    if (Array.isArray(value)) {
      var values = value.filter(function (item) {
        return String(item || "").trim() !== "";
      });
      return values.length ? values.join(", ") : "-";
    }
    return valueOrDash(value);
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
    appendTextDefinition(list, labels.capitalLabel, textListOrDash(country.capital), "data-country-capital");
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

  function hasSkippedParentLevel(row) {
    var level = Number(row && row.level);
    var parentLevel = Number(row && row.parent_level);
    return Boolean(
      row &&
      row.parent_detail_url &&
      Number.isFinite(level) &&
      Number.isFinite(parentLevel) &&
      level > parentLevel + 1
    );
  }

  function openCountryTableRowDetail(row, labels, target, areaStack) {
    if (!row || !row.detail_url || !areaStack) {
      return;
    }
    trimStatsAreaStack(areaStack, 0);
    if (hasSkippedParentLevel(row)) {
      loadStatsAreaDetail(areaStack, row.parent_detail_url, labels, target, 0)
        .then(function (payload) {
          if (!payload) {
            return null;
          }
          return loadStatsAreaDetail(areaStack, row.detail_url, labels, target, 1);
        });
      return;
    }
    loadStatsAreaDetail(areaStack, row.detail_url, labels, target, 0);
  }

  function renderCountryTablePanel(panel, data, labels, baseUrl, target, areaStack) {
    var levels = data.levels || [];
    var paginationData = (data.table && data.table.pagination) || countryBrowserPagination(data);
    var initialPageSize = countryBrowserPageSize(paginationData);
    var levelLabel = null;
    if (levels.length > 1) {
      levelLabel = document.createElement("label");
      levelLabel.textContent = labels.levelLabel;
      var levelSelect = document.createElement("select");
      levels.forEach(function (level) {
        var option = document.createElement("option");
        option.value = level.filter_value || level.value;
        option.textContent = level.label;
        if (level.count) {
          option.title = level.label + " (" + level.count + ")";
        }
        option.selected = String(option.value) === String(data.selected_filter || data.selected_level);
        levelSelect.appendChild(option);
      });
      levelSelect.addEventListener("change", function () {
        loadCountryTableOnly(panel, baseUrl, levelSelect.value, target, 1, pageSizeSelect ? pageSizeSelect.value : initialPageSize);
      });
      levelLabel.appendChild(levelSelect);
    }
    if (levelLabel) {
      var levelControls = document.createElement("div");
      levelControls.className = "country-level-controls";
      levelControls.appendChild(levelLabel);
      panel.appendChild(levelControls);
    }

    var heading = document.createElement("h2");
    heading.textContent = labels.tableTitle;
    panel.appendChild(heading);

    var controls = document.createElement("div");
    controls.className = "country-table-controls";

    var filterLabel = document.createElement("label");
    filterLabel.textContent = labels.filterLabel;
    var filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = labels.filterPlaceholder;
    filterLabel.appendChild(filter);

    var pageSizeLabel = document.createElement("label");
    pageSizeLabel.textContent = labels.pageSizeLabel;
    var pageSizeSelect = document.createElement("select");
    populateCountryBrowserPageSizes(pageSizeSelect, initialPageSize);
    pageSizeLabel.appendChild(pageSizeSelect);

    controls.appendChild(filterLabel);
    panel.appendChild(controls);

    var tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap subtle country-data-table-wrap";
    var table = document.createElement("table");
    table.className = "compact-table country-data-table";
    var thead = document.createElement("thead");
    var headerRow = document.createElement("tr");
    var rows = (data.table && data.table.rows) || [];
    var hasAnnotations = rows.some(function (row) {
      return String(row.annotations || "").trim() !== "";
    });
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
    if (hasAnnotations) {
      columns.push(["annotations", "Anotaciones"]);
    }
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

    var sortKey = "name";
    var sortDirection = 1;
    var page = paginationData ? Number(paginationData.page || 1) : 1;
    var pageSize = initialPageSize;

    function currentLevelValue() {
      return levelSelect ? levelSelect.value : selectedCountryBrowserLevel(data);
    }

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
        return [row.name, row.parent, row.entity_type, row.annotations].some(function (value) {
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
      var totalRows = paginationData ? Number(paginationData.total || 0) : filtered.length;
      var totalPages = paginationData
        ? Math.max(1, Number(paginationData.num_pages || 1))
        : Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.min(Math.max(1, page), totalPages);
      var visible = paginationData ? filtered : filtered.slice((page - 1) * pageSize, page * pageSize);

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
        if (row.detail_url && areaStack) {
          tr.classList.add("is-clickable");
          tr.tabIndex = 0;
          tr.addEventListener("click", function (event) {
            if (isRowNavigationInteractiveTarget(event)) {
              return;
            }
            openCountryTableRowDetail(row, labels, target, areaStack);
          });
          bindMiddleClickNavigation(tr, function () {
            return row.detail_url || "";
          }, isRowNavigationInteractiveTarget);
          tr.addEventListener("keydown", function (event) {
            if (isRowNavigationInteractiveTarget(event)) {
              return;
            }
            if (event.key !== "Enter" && event.key !== " ") {
              return;
            }
            event.preventDefault();
            openCountryTableRowDetail(row, labels, target, areaStack);
          });
        }
        columns.map(function (column) {
          switch (column[0]) {
            case "name":
              return row.name;
            case "entity_type":
              return row.entity_type || "-";
            case "population":
              return formattedNumberOrDash(row.population);
            case "population_percent":
              return formatPercentNumber(row.population_percent);
            case "area_km2":
              return formattedNumberOrDash(row.area_km2);
            case "area_percent":
              return formatPercentNumber(row.area_percent);
            case "density":
              return formattedNumberOrDash(row.density);
            case "parent":
              return row.parent || "-";
            case "annotations":
              return row.annotations || "-";
            default:
              return row[column[0]] || "-";
          }
        }).forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      previousButton.disabled = paginationData ? !paginationData.has_previous : page <= 1;
      nextButton.disabled = paginationData ? !paginationData.has_next : page >= totalPages;
      pageStatus.textContent = labels.sortedByLabel + " " + sortColumnLabel() + " - " + page + "/" + totalPages + " - " + formatNumber(totalRows);
      updateSortButtons();
    }

    filter.addEventListener("input", function () {
      page = 1;
      drawRows();
    });
    previousButton.addEventListener("click", function () {
      if (paginationData) {
        loadCountryTableOnly(panel, baseUrl, currentLevelValue(), target, Math.max(1, page - 1), pageSize);
        return;
      }
      page -= 1;
      drawRows();
    });
    nextButton.addEventListener("click", function () {
      if (paginationData) {
        loadCountryTableOnly(panel, baseUrl, currentLevelValue(), target, page + 1, pageSize);
        return;
      }
      page += 1;
      drawRows();
    });
    pageSizeSelect.addEventListener("change", function () {
      pageSize = Number(pageSizeSelect.value || 20);
      if (paginationData) {
        loadCountryTableOnly(panel, baseUrl, currentLevelValue(), target, 1, pageSize);
        return;
      }
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

  function countryBrowserPagination(data) {
    return (data && data.first_order && data.first_order.pagination)
      || (data && data.table && data.table.pagination)
      || null;
  }

  function countryBrowserPageSizeChoices() {
    return [20, 50, 100];
  }

  function countryBrowserPageSize(pagination) {
    var parsed = parseInt(pagination && pagination.page_size, 10);
    return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 100) : 20;
  }

  function populateCountryBrowserPageSizes(select, selectedSize) {
    if (!select) {
      return;
    }
    select.innerHTML = "";
    countryBrowserPageSizeChoices().forEach(function (size) {
      var option = document.createElement("option");
      option.value = String(size);
      option.textContent = String(size);
      option.selected = size === selectedSize;
      select.appendChild(option);
    });
  }

  function selectedCountryBrowserLevel(data) {
    var value = data ? (data.selected_filter || data.selected_level) : "";
    return value === undefined || value === null ? "" : String(value);
  }

  function applyCountryBrowserParams(finalUrl, level, page, pageSize) {
    if (level !== undefined && level !== null && level !== "") {
      finalUrl.searchParams.set("level", level);
    }
    if (page !== undefined && page !== null && page !== "") {
      finalUrl.searchParams.set("page", page);
    }
    if (pageSize !== undefined && pageSize !== null && pageSize !== "") {
      finalUrl.searchParams.set("page_size", pageSize);
    }
  }

  function scrollToCountryTable(target) {
    if (!target) {
      return;
    }
    var table = target && target.querySelector(".country-data-table-wrap");
    (table || target).scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function loadCountryTableOnly(panel, url, level, target, page, pageSize) {
    if (!panel || !url) {
      return;
    }
    if (target) {
      loadCountryDetail(target, url, level, "target", page, pageSize);
      return;
    }
    var labels = countryDetailLabels(target);
    setLoading(panel, target.dataset.loading || "Cargando pais...");
    var finalUrl = new URL(url, window.location.origin);
    applyCountryBrowserParams(finalUrl, level, page, pageSize);
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        panel.innerHTML = "";
        renderCountryTablePanel(panel, data, labels, url, target, target ? target.querySelector(".country-area-stack") : null);
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

  function renderCountryChartsPanel(panel, data, labels, target, areaStack) {
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
        if (card.detail_url && areaStack) {
          row.classList.add("is-clickable");
          row.tabIndex = 0;
          row.addEventListener("click", function (event) {
            if (isRowNavigationInteractiveTarget(event)) {
              return;
            }
            openCountryTableRowDetail(card, labels, target, areaStack);
          });
          bindMiddleClickNavigation(row, function () {
            return card.detail_url || "";
          }, isRowNavigationInteractiveTarget);
          row.addEventListener("keydown", function (event) {
            if (isRowNavigationInteractiveTarget(event)) {
              return;
            }
            if (event.key !== "Enter" && event.key !== " ") {
              return;
            }
            event.preventDefault();
            openCountryTableRowDetail(card, labels, target, areaStack);
          });
        }
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

    var areaStack = document.createElement("div");
    areaStack.className = "stats-area-stack country-area-stack";

    renderCountryGeneralPanel(general, data, labels);
    renderCountryTablePanel(table, data, labels, baseUrl, target, areaStack);
    renderCountryChartsPanel(charts, data, labels, target, areaStack);

    grid.appendChild(general);
    grid.appendChild(table);
    grid.appendChild(charts);
    target.appendChild(grid);
    target.appendChild(areaStack);
    renderCountryShareCards(target, data, labels);
  }

  function loadCountryDetail(target, url, level, scrollMode, page, pageSize) {
    if (!target || !url) {
      return;
    }
    target.hidden = false;
    setLoading(target, target.dataset.loading || "Cargando pais...");
    var finalUrl = new URL(url, window.location.origin);
    applyCountryBrowserParams(finalUrl, level, page, pageSize);
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        renderCountryDetail(target, data, url);
        if (scrollMode === "target") {
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
          scrollToCountryTable(target);
        }
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
      bindMiddleClickNavigation(image, function () {
        return assetIdentityDetailUrl(asset, kind) || image.dataset.fullSrc || url || "";
      });
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

  function appendStatsCountryCardAction(actions, url, label, text) {
    if (!actions || !url) {
      return;
    }
    var link = document.createElement("a");
    link.className = "stats-country-card-action";
    link.href = url;
    link.title = label;
    link.setAttribute("aria-label", label);
    link.textContent = text;
    actions.appendChild(link);
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
      var hasActions = Boolean(country.edit_url || country.export_excel_url);
      var card = document.createElement(hasActions ? "article" : "button");
      var main = card;
      card.className = "stats-country-card";
      if (hasActions) {
        card.classList.add("stats-country-card-editable");
        main = document.createElement("button");
        main.className = "stats-country-card-main";
        main.type = "button";
      } else {
        card.type = "button";
      }
      main.dataset.detailUrl = country.detail_url || "";

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
      main.appendChild(flag);
      main.appendChild(name);
      main.appendChild(metrics);
      main.addEventListener("click", function () {
        var target = document.querySelector("[data-stats-country-detail]");
        if (target && main.dataset.detailUrl) {
          loadStatsCountryDetail(target, main.dataset.detailUrl, container);
        }
      });
      bindMiddleClickNavigation(main, function () {
        return main.dataset.detailUrl || "";
      });
      if (hasActions) {
        var actions = document.createElement("div");
        actions.className = "stats-country-card-actions";
        appendStatsCountryCardAction(actions, country.export_excel_url, container.dataset.exportLabel || "Excel", "XLS");
        appendStatsCountryCardAction(actions, country.edit_url, container.dataset.editLabel || "Configuracion", "CFG");
        card.appendChild(actions);
        card.appendChild(main);
      }
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

  function statsChildGroupsFromCountry(data) {
    var groups = (data.first_order && data.first_order.child_groups) || [];
    if (Array.isArray(groups) && groups.length) {
      return groups.map(function (group) {
        return {
          label: group.label || null,
          children: group.children || []
        };
      });
    }
    return [{ label: null, children: statsChildRowsFromCountry(data) }];
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

  function renderCountryBrowserPaginationNav(parent, pagination, labels, onPageChange) {
    if (!parent || !pagination) {
      return;
    }
    var totalPages = Math.max(1, Number(pagination.num_pages || 1));
    var totalRows = Number(pagination.total || 0);
    if (totalPages <= 1) {
      return;
    }
    var page = Math.max(1, Number(pagination.page || 1));
    var pageSize = countryBrowserPageSize(pagination);
    var nav = document.createElement("div");
    nav.className = "country-table-pagination stats-country-table-pagination";
    var previousButton = document.createElement("button");
    previousButton.type = "button";
    previousButton.className = "secondary";
    previousButton.textContent = labels.previousLabel;
    previousButton.disabled = !pagination.has_previous;
    var status = document.createElement("span");
    status.textContent = page + "/" + totalPages + " - " + formatNumber(totalRows);
    var nextButton = document.createElement("button");
    nextButton.type = "button";
    nextButton.className = "secondary";
    nextButton.textContent = labels.nextLabel;
    nextButton.disabled = !pagination.has_next;
    previousButton.addEventListener("click", function () {
      if (onPageChange) {
        onPageChange(Math.max(1, page - 1), pageSize);
      }
    });
    nextButton.addEventListener("click", function () {
      if (onPageChange) {
        onPageChange(page + 1, pageSize);
      }
    });
    nav.appendChild(previousButton);
    nav.appendChild(status);
    nav.appendChild(nextButton);
    parent.appendChild(nav);
  }

  function renderStatsChildrenPanel(panel, title, rows, labels, openHandler, options) {
    options = options || {};
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
          bindMiddleClickNavigation(row, function () {
            return item.detail_url || "";
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
    renderCountryBrowserPaginationNav(panel, options.pagination, labels, options.onPageChange);
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
    var childGroups = Array.isArray(payload.child_groups) && payload.child_groups.length
      ? payload.child_groups
      : [{ label: null, children: payload.children || [] }];
    var splitChildren = childGroups.length > 1;

    renderCountryGeneralPanel(basicPanel, { country: area }, Object.assign({}, labels, {
      generalTitle: area.name || labels.generalTitle
    }));

    grid.appendChild(basicPanel);
    if (splitChildren) {
      grid.classList.add("has-split-children");
      basicPanel.classList.add("stats-area-basic-panel--span-split");
      var childrenStack = document.createElement("div");
      childrenStack.className = "stats-area-child-groups-stack";
      childGroups.forEach(function (group) {
        var childrenPanel = document.createElement("article");
        childrenPanel.className = "panel stats-country-first-level-panel";
        renderStatsChildrenPanel(childrenPanel, group.label || null, group.children || [], labels, function (item) {
          loadStatsAreaDetail(stack, item.detail_url, labels, source, depth);
        });
        childrenStack.appendChild(childrenPanel);
      });
      grid.appendChild(childrenStack);
    } else {
      childGroups.forEach(function (group) {
        var childrenPanel = document.createElement("article");
        childrenPanel.className = "panel stats-country-first-level-panel";
        renderStatsChildrenPanel(childrenPanel, group.label || null, group.children || [], labels, function (item) {
          loadStatsAreaDetail(stack, item.detail_url, labels, source, depth);
        });
        grid.appendChild(childrenPanel);
      });
    }
    panel.appendChild(grid);
  }

  function loadStatsAreaDetail(stack, url, labels, source, depth) {
    if (!stack || !url) {
      return Promise.resolve(null);
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
    return fetchJson(url)
      .then(function (payload) {
        renderStatsAreaPanel(panel, payload, labels, stack, depth + 1, source);
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
        return payload;
      })
      .catch(function () {
        setError(loadingPanel, source.dataset.error || "No se pudo cargar el pais.");
        return null;
      });
  }

  function renderStatsLevelControls(panel, data, labels, target, url, source) {
    var levels = data.levels || [];
    var pagination = countryBrowserPagination(data);
    if (levels.length <= 1 && !pagination) {
      return;
    }
    var controls = document.createElement("div");
    controls.className = "country-level-controls stats-country-level-controls";
    var select = null;
    var pageSizeSelect = null;
    if (levels.length > 1) {
      var label = document.createElement("label");
      label.textContent = labels.levelLabel;
      select = document.createElement("select");
      levels.forEach(function (level) {
        var option = document.createElement("option");
        option.value = level.filter_value || level.value;
        option.textContent = level.label;
        if (level.count) {
          option.title = level.label + " (" + level.count + ")";
        }
        option.selected = String(option.value) === String(data.selected_filter || data.selected_level);
        select.appendChild(option);
      });
      label.appendChild(select);
      controls.appendChild(label);
    }
    var pageSizeLabel = document.createElement("label");
    pageSizeLabel.textContent = labels.pageSizeLabel;
    pageSizeSelect = document.createElement("select");
    populateCountryBrowserPageSizes(pageSizeSelect, countryBrowserPageSize(pagination));
    pageSizeLabel.appendChild(pageSizeSelect);
    controls.appendChild(pageSizeLabel);
    function currentLevel() {
      return select ? select.value : selectedCountryBrowserLevel(data);
    }
    if (select) {
      select.addEventListener("change", function () {
        loadStatsCountryDetail(target, url, source, currentLevel(), 1, pageSizeSelect.value);
      });
    }
    pageSizeSelect.addEventListener("change", function () {
      loadStatsCountryDetail(target, url, source, currentLevel(), 1, pageSizeSelect.value);
    });
    panel.appendChild(controls);
  }

  function loadStatsCountryDetail(target, url, source, level, page, pageSize) {
    setLoading(target, target.dataset.loading || "Cargando pais...");
    target.hidden = false;
    var finalUrl = new URL(url, window.location.origin);
    applyCountryBrowserParams(finalUrl, level, page, pageSize);
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        var labels = countryDetailLabels(source || target);
        var useNewCountryContainerLayout = Boolean(
          target && target.classList && target.classList.contains("new-country-container-detail")
        );
        target.innerHTML = "";
        var grid = document.createElement("div");
        grid.className = "stats-country-detail-grid";
        if (useNewCountryContainerLayout) {
          grid.classList.add("stats-country-detail-grid--new-country-container");
        }

        var basicPanel = document.createElement("article");
        basicPanel.className = "panel stats-country-basic-panel";
        basicPanel.dataset.loading = target.dataset.loading || "";
        renderCountryGeneralPanel(basicPanel, data, labels);

        var stack = document.createElement("div");
        stack.className = "stats-area-stack";

        var childGroups = statsChildGroupsFromCountry(data);
        var splitChildren = childGroups.length > 1;
        grid.appendChild(basicPanel);
        renderStatsLevelControls(
          useNewCountryContainerLayout ? target : grid,
          data,
          labels,
          target,
          url,
          source || target
        );
        if (splitChildren) {
          grid.classList.add("stats-area-detail-grid", "has-split-children");
          basicPanel.classList.add("stats-area-basic-panel", "stats-area-basic-panel--span-split");
          var childrenStack = document.createElement("div");
          childrenStack.className = "stats-area-child-groups-stack";
          childGroups.forEach(function (group) {
            var childPanel = document.createElement("article");
            childPanel.className = "panel stats-country-first-level-panel";
            renderStatsChildrenPanel(childPanel, group.label || null, group.children || [], labels, function (item) {
              loadStatsAreaDetail(stack, item.detail_url, labels, target, 0);
            });
            childrenStack.appendChild(childPanel);
          });
          grid.appendChild(childrenStack);
        } else {
          var pagination = countryBrowserPagination(data);
          var childPanelOptions = pagination ? {
            pagination: pagination,
            onPageChange: function (nextPage, nextPageSize) {
              loadStatsCountryDetail(
                target,
                url,
                source || target,
                selectedCountryBrowserLevel(data),
                nextPage,
                nextPageSize
              );
            }
          } : {};
          var firstLevelPanel = document.createElement("article");
          firstLevelPanel.className = "panel stats-country-first-level-panel";
          renderStatsChildrenPanel(firstLevelPanel, null, childGroups[0] ? childGroups[0].children : [], labels, function (item) {
            loadStatsAreaDetail(stack, item.detail_url, labels, target, 0);
          }, childPanelOptions);
          grid.appendChild(firstLevelPanel);
        }

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
      bindMiddleClickNavigation(image, function () {
        return visualIdentityDetailUrl(kind, value) || commonsFileUrl(value, 1600);
      });
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
    if (key === "clearing") {
      return container.dataset.clearingLabel || "Limpiando";
    }
    if (key === "populated") {
      return container.dataset.populatedLabel || "Populado";
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
      return container.dataset.cancelledLabel || "Cancelado";
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
      return "\u00d7";
    }
    if (["failed", "invalid"].indexOf(key) !== -1) {
      return "\u00d7";
    }
    if (key === "queued") {
      return "\u25cc";
    }
    if (["running", "validating", "populating", "clearing"].indexOf(key) !== -1) {
      return "\u2026";
    }
    return "i";
  }

  function normalizeConfigErrorCode(code) {
    return String(code || "").trim();
  }

  function appendConfigErrorCode(parent, code, description, severity) {
    code = normalizeConfigErrorCode(code);
    if (!parent || !code) {
      return null;
    }
    var badge = document.createElement("span");
    badge.className = "config-error-code" + (severity ? " config-code-" + String(severity).toLowerCase() : "");
    badge.textContent = code;
    if (description) {
      badge.title = description;
    }
    parent.appendChild(document.createTextNode(" "));
    parent.appendChild(badge);
    return badge;
  }

  function setElementErrorInfo(element, code, description, severity) {
    code = normalizeConfigErrorCode(code);
    if (!element) {
      return;
    }
    if (code) {
      element.dataset.errorCode = code;
      element.dataset.errorDescription = description || "";
      element.dataset.errorSeverity = severity || "";
      if (description) {
        element.title = description;
      }
    } else {
      delete element.dataset.errorCode;
      delete element.dataset.errorDescription;
      delete element.dataset.errorSeverity;
    }
  }

  function isConfigLoadingStatus(status) {
    return ["validating", "populating", "clearing", "running", "queued"].indexOf(configTaskKey(status)) !== -1;
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
      return "pending";
    }
    if (key === "invalid") {
      return "failed";
    }
    return key;
  }


  var CONFIG_TOAST_MAX_VISIBLE = 4;
  var CONFIG_TOAST_DONE_VISIBLE_MS = 2600;
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
      height: configToastCssPx(stack, "--config-toast-height", 136),
      gap: configToastCssPx(stack, "--config-toast-gap", 16)
    };
  }

  function configToastElementHeight(toast, stack) {
    var metrics = configToastMetrics(stack);
    if (!toast || !toast.element) {
      return metrics.height;
    }
    var rect = toast.element.getBoundingClientRect ? toast.element.getBoundingClientRect() : null;
    return Math.ceil((rect && rect.height) || toast.element.offsetHeight || metrics.height);
  }

  function configToastSlotY(stack, index, list) {
    var metrics = configToastMetrics(stack);
    var toasts = list || configToastVisible;
    var y = 0;
    for (var i = 0; i < index; i += 1) {
      y += configToastElementHeight(toasts[i], stack) + metrics.gap;
    }
    return y;
  }

  function layoutVisibleConfigToasts() {
    var stack = ensureConfigToastStack();
    configToastVisible.forEach(function (toast, index) {
      if (!toast || !toast.element || toast.isDismissing) {
        return;
      }
      toast.slotIndex = index;
      toast.element.style.setProperty("--toast-y", configToastSlotY(stack, index, configToastVisible) + "px");
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
    toast.element.style.setProperty("--toast-y", configToastSlotY(stack, slotIndex, configToastVisible) + "px");
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

  function setConfigToastStatus(toast, status, message, errorCode, errorDescription, errorSeverity) {
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
      var code = normalizeConfigErrorCode(errorCode || "");
      toast.status.textContent = code ? (message || status || "") + " [" + code + "]" : (message || status || "");
      setElementErrorInfo(toast.status, code, errorDescription || "", errorSeverity || "");
    }
  }

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
        var button = form.querySelector("button[type='submit'], input[type='submit']");
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
      form._configAutosavePending = false;
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
      (values.page_force_highest_level || []).length,
      (values.page_parent_level || []).length,
      (values.page_lowest_level || []).length,
      (values.page_url || []).length,
      (values.page_path || []).length,
      (values.page_source || []).length,
      (values.page_include_infosection || []).length,
      (values.page_include_major_subdivision || []).length,
      (values.page_include_minor_subdivision || []).length,
      (values.page_include_cities || []).length,
      (values.page_sum_to_root || []).length,
      (values.page_repeat_infosection || []).length,
      (values.page_repeat_major_subdivision || []).length,
      (values.page_repeat_minor_subdivision || []).length,
      (values.page_repeat_cities || []).length
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
    var restoredManualPages = null;
    form.querySelectorAll("[data-page-path-hidden]").forEach(function (hidden) {
      var row = hidden.closest("tr");
      var tbody = row ? row.closest("[data-manual-pages]") : null;
      if (row) {
        setPagePathBoxes(row, splitPagePathsText(hidden.value));
        if (tbody) {
          restoredManualPages = tbody;
          bindManualPageDragContainer(tbody);
          bindManualPageRow(row, tbody);
        }
      }
    });
    if (restoredManualPages) {
      syncManualPageRowOrder(restoredManualPages);
    }
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

  function initStatsSplitChildrenLayout() {
    if (document.getElementById("stats-split-children-layout-style")) {
      return;
    }
    var style = document.createElement("style");
    style.id = "stats-split-children-layout-style";
    style.textContent = [
      ".stats-area-detail-grid.has-split-children{align-items:stretch;}",
      ".stats-area-detail-grid.has-split-children>.stats-area-basic-panel{align-self:stretch;height:100%;min-height:100%;}",
      ".stats-area-child-groups-stack{display:grid;align-self:stretch;height:100%;align-content:stretch;gap:var(--space-4,1rem);}",
      ".stats-area-child-groups-stack>.panel{margin:0;}",
      "@media (max-width:920px){.stats-area-child-groups-stack{gap:var(--space-3,.75rem);}}"
    ].join("");
    document.head.appendChild(style);
  }

  function ensureLoadingSpinner(container, className) {
    if (!container) {
      return null;
    }
    var spinner = container.querySelector("." + className);
    if (!spinner) {
      spinner = document.createElement("span");
      spinner.className = "loading-spinner " + className;
      spinner.setAttribute("aria-hidden", "true");
      container.insertBefore(spinner, container.firstChild);
    }
    spinner.hidden = false;
    return spinner;
  }

  function initLoadingSubmitForms(root) {
    (root || document).querySelectorAll("form[data-loading-submit]").forEach(function (form) {
      if (form.dataset.loadingSubmitReady === "true") {
        return;
      }
      form.dataset.loadingSubmitReady = "true";
      form.addEventListener("submit", function (event) {
        if (event.defaultPrevented) {
          return;
        }
        var button = event.submitter || form.querySelector("button[type='submit']");
        if (!button || button.disabled) {
          return;
        }
        button.disabled = true;
        button.classList.add("is-loading");
        ensureLoadingSpinner(button, "button-loading-spinner");
      });
    });
  }

  function initLoadingLinks(root) {
    (root || document).querySelectorAll("[data-loading-link]").forEach(function (link) {
      if (link.dataset.loadingLinkReady === "true") {
        return;
      }
      link.dataset.loadingLinkReady = "true";
      link.addEventListener("click", function (event) {
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          (link.target && link.target !== "_self")
        ) {
          return;
        }
        link.classList.add("is-loading");
        ensureLoadingSpinner(link.querySelector(".stats-country-flag") || link, "group-card-loading-spinner");
      });
    });
  }

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
    if (panel.dataset.groupDetailLoaded === "1") {
      resetGroupCountryDetailLoading(panel);
      initGroupDivisionDeleteForms(panel);
      initClickableRows(panel);
      return Promise.resolve(null);
    }
    if (panel._groupDetailPromise) {
      return panel._groupDetailPromise;
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

  function initNewCountryConfigTree(root) {
    (root || document).querySelectorAll("[data-new-country-config-tree]").forEach(function (tree) {
      if (tree.dataset.newCountryConfigTreeReady === "1") {
        return;
      }
      tree.dataset.newCountryConfigTreeReady = "1";
      var tbody = tree.querySelector("[data-new-country-config-tree-body]") || tree.querySelector("tbody");
      var levelSelect = tree.querySelector("[data-new-country-config-tree-level]");
      var treeUrl = tree.dataset.treeUrl || "";
      var csrfToken = tree.dataset.csrfToken || "";
      var loadingLabel = tree.dataset.loadingLabel || "Cargando entidades...";
      var emptyLabel = tree.dataset.emptyLabel || "Sin datos.";
      var errorLabel = tree.dataset.errorLabel || "No se pudieron cargar las entidades.";
      var parentLabel = tree.dataset.parentLabel || "Padre";
      var cloneLabel = tree.dataset.cloneLabel || "Clonar";
      var cloneTitle = tree.dataset.cloneTitle || "Clonar entidad";
      var deleteLabel = tree.dataset.deleteLabel || "X";
      var deleteTitle = tree.dataset.deleteTitle || "Eliminar entidad";
      var deleteConfirm = tree.dataset.deleteConfirm || "";
      var pageSizeSelect = tree.querySelector("[data-new-country-config-tree-page-size]");
      var pagination = tree.querySelector("[data-new-country-config-tree-pagination]");
      var previousPageButton = tree.querySelector("[data-new-country-config-tree-page='previous']");
      var nextPageButton = tree.querySelector("[data-new-country-config-tree-page='next']");
      var pageStatus = tree.querySelector("[data-new-country-config-tree-page-status]");
      var treePage = 1;
      var treePagination = null;
      populateCountryBrowserPageSizes(pageSizeSelect, 20);

      function rows() {
        return Array.prototype.slice.call(tree.querySelectorAll("[data-tree-id]"));
      }

      function rowById(treeId) {
        return rows().filter(function (row) {
          return row.dataset.treeId === treeId;
        })[0] || null;
      }

      function isVisibleInTree(row) {
        var parentId = row.dataset.treeParent || "";
        var parent;
        if (!parentId) {
          return true;
        }
        parent = rowById(parentId);
        if (!parent) {
          return true;
        }
        if (parent.hidden || parent.dataset.treeExpanded !== "1") {
          return false;
        }
        return isVisibleInTree(parent);
      }

      function render() {
        var expandLabel = tree.dataset.expandLabel || "";
        var collapseLabel = tree.dataset.collapseLabel || "";
        var levelMode = tree.dataset.treeLevelMode === "1";
        rows().forEach(function (row) {
          var depth = parseInt(row.dataset.treeDepth || "0", 10) || 0;
          var toggle = row.querySelector("[data-new-country-config-tree-toggle]");
          var canExpand = row.dataset.treeHasChildren === "1" && !levelMode;
          if (levelMode || depth === 0) {
            row.hidden = false;
          } else {
            row.hidden = !isVisibleInTree(row);
          }
          if (toggle) {
            var expanded = row.dataset.treeExpanded === "1";
            toggle.hidden = !canExpand;
            if (canExpand) {
              toggle.removeAttribute("aria-hidden");
            } else {
              toggle.setAttribute("aria-hidden", "true");
            }
            toggle.textContent = expanded ? "-" : "+";
            toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
            toggle.setAttribute("aria-label", expanded ? collapseLabel : expandLabel);
          }
        });
      }

      function setBodyStatus(message, className, loading) {
        if (!tbody) {
          return;
        }
        tbody.innerHTML = "";
        var tr = document.createElement("tr");
        tr.setAttribute("data-new-country-config-tree-status", "");
        var td = document.createElement("td");
        td.colSpan = 9;
        if (className) {
          td.className = className;
        }
        if (loading) {
          var spinner = document.createElement("span");
          spinner.className = "loading-spinner";
          spinner.setAttribute("aria-hidden", "true");
          td.appendChild(spinner);
          td.appendChild(document.createTextNode(" "));
        }
        td.appendChild(document.createTextNode(message || ""));
        tr.appendChild(td);
        tbody.appendChild(tr);
      }

      function currentPageSize() {
        var parsed = parseInt(pageSizeSelect ? pageSizeSelect.value : "20", 10);
        return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 100) : 20;
      }

      function updateTreePagination(paginationData) {
        treePagination = paginationData || null;
        if (!pagination) {
          return;
        }
        if (!treePagination || Number(treePagination.num_pages || 1) <= 1) {
          pagination.hidden = true;
          return;
        }
        pagination.hidden = false;
        if (previousPageButton) {
          previousPageButton.disabled = !treePagination.has_previous;
        }
        if (nextPageButton) {
          nextPageButton.disabled = !treePagination.has_next;
        }
        if (pageStatus) {
          pageStatus.textContent = String(treePagination.page || 1) + "/" + String(treePagination.num_pages || 1) + " - " + formatNumber(treePagination.total || 0);
        }
      }

      function fetchTreeRows(parentId, level, page, pageSize) {
        var url = new URL(treeUrl, window.location.origin);
        if (parentId) {
          url.searchParams.set("parent", parentId);
        }
        if (level) {
          url.searchParams.set("level", level);
        }
        if (page) {
          url.searchParams.set("page", page);
        }
        if (pageSize) {
          url.searchParams.set("page_size", pageSize);
        }
        return fetchJson(relativeUrlFrom(url.toString())).then(function (payload) {
          return payload || { rows: [], pagination: null };
        });
      }

      function hiddenInput(name, value) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value || "";
        return input;
      }

      function textCell(className, value) {
        var td = document.createElement("td");
        if (className) {
          td.className = className;
        }
        td.textContent = value || "";
        return td;
      }

      function appendActionForm(cell, url, className, buttonClass, label, title, confirmMessage) {
        var form = document.createElement("form");
        form.className = className;
        form.method = "post";
        form.action = url;
        if (confirmMessage) {
          form.dataset.confirmMessage = confirmMessage;
        }
        form.appendChild(hiddenInput("csrfmiddlewaretoken", csrfToken));
        var button = document.createElement("button");
        button.className = "secondary " + buttonClass;
        button.type = "submit";
        button.setAttribute("aria-label", title);
        button.title = title;
        button.textContent = label;
        form.appendChild(button);
        cell.appendChild(form);
      }

      function createTreeRow(rowData) {
        var tr = document.createElement("tr");
        var depth = parseInt(rowData.depth || "0", 10) || 0;
        tr.className = "new-country-config-tree-row" + (depth ? " is-child" : "") + (rowData.is_assigned_subdivision ? " is-assigned-subdivision" : "");
        tr.dataset.clientRow = "1";
        tr.dataset.treeId = rowData.tree_id || "";
        tr.dataset.treeParent = rowData.tree_parent || "";
        tr.dataset.treeDepth = String(depth);
        tr.dataset.treeHasChildren = rowData.has_children ? "1" : "0";
        if (rowData.edit_url) {
          tr.dataset.rowHref = rowData.edit_url;
          tr.tabIndex = 0;
        }

        var toggleCell = document.createElement("td");
        toggleCell.className = "new-country-tree-toggle-cell";
        var toggle = document.createElement("button");
        toggle.className = "new-country-tree-toggle";
        toggle.type = "button";
        toggle.setAttribute("data-new-country-config-tree-toggle", "");
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", tree.dataset.expandLabel || "");
        toggle.textContent = "+";
        if (!rowData.has_children) {
          toggle.hidden = true;
          toggle.setAttribute("aria-hidden", "true");
        }
        toggleCell.appendChild(toggle);
        tr.appendChild(toggleCell);

        tr.appendChild(textCell("new-country-tree-level", "NV " + (rowData.level || "")));

        var codeCell = document.createElement("td");
        codeCell.className = "new-country-tree-code-cell";
        var code = document.createElement("code");
        if (rowData.is_assigned_subdivision) {
          code.className = "new-country-assigned-code";
        }
        code.textContent = rowData.display_code || "";
        codeCell.appendChild(code);
        if (rowData.parent_display_code && !rowData.is_assigned_subdivision) {
          var parent = document.createElement("small");
          parent.textContent = parentLabel + ": " + rowData.parent_display_code;
          codeCell.appendChild(parent);
        }
        tr.appendChild(codeCell);

        tr.appendChild(textCell(
          "new-country-tree-name-cell " + (rowData.tree_depth_class || ("new-country-tree-depth-" + Math.min(depth, 5))),
          rowData.entity_display_name || ""
        ));

        var typeCell = document.createElement("td");
        typeCell.className = "new-country-tree-type-cell";
        typeCell.appendChild(document.createTextNode(rowData.entity_type || "-"));
        if (rowData.source_level !== "" && rowData.source_level !== null && rowData.source_level !== undefined && !rowData.is_assigned_subdivision) {
          var sourceLevel = document.createElement("small");
          sourceLevel.textContent = "L" + rowData.source_level;
          typeCell.appendChild(sourceLevel);
        }
        tr.appendChild(typeCell);

        tr.appendChild(textCell("numeric", rowData.entity_area_text || "-"));
        tr.appendChild(textCell("numeric", rowData.entity_population_text || "-"));
        tr.appendChild(textCell("numeric", rowData.entity_density_text || "-"));

        var actionCell = document.createElement("td");
        actionCell.className = "new-country-tree-action-cell";
        if (rowData.clone_url) {
          appendActionForm(actionCell, rowData.clone_url, "group-division-clone-form", "group-division-clone-button", cloneLabel, cloneTitle, "");
        }
        if (rowData.delete_url) {
          appendActionForm(actionCell, rowData.delete_url, "group-division-delete-form", "group-division-delete-button", deleteLabel, deleteTitle, deleteConfirm);
        }
        if (!rowData.clone_url && !rowData.delete_url) {
          var empty = document.createElement("span");
          empty.className = "new-country-tree-empty-action";
          empty.setAttribute("aria-hidden", "true");
          empty.textContent = "-";
          actionCell.appendChild(empty);
        }
        tr.appendChild(actionCell);
        return tr;
      }

      function isDescendantRow(row, parentId) {
        var currentParent = row.dataset.treeParent || "";
        while (currentParent) {
          if (currentParent === parentId) {
            return true;
          }
          var parentRow = rowById(currentParent);
          currentParent = parentRow ? (parentRow.dataset.treeParent || "") : "";
        }
        return false;
      }

      function insertionReference(parentRow) {
        var parentId = parentRow.dataset.treeId || "";
        var allRows = rows();
        var index = allRows.indexOf(parentRow);
        var reference = parentRow;
        for (var i = index + 1; i < allRows.length; i += 1) {
          if (!isDescendantRow(allRows[i], parentId)) {
            break;
          }
          reference = allRows[i];
        }
        return reference;
      }

      function insertRows(rowData, parentRow) {
        var fragment = document.createDocumentFragment();
        rowData.forEach(function (item) {
          fragment.appendChild(createTreeRow(item));
        });
        if (parentRow && parentRow.parentNode) {
          var reference = insertionReference(parentRow);
          reference.parentNode.insertBefore(fragment, reference.nextSibling);
        } else if (tbody) {
          tbody.appendChild(fragment);
        }
        initGroupDivisionDeleteForms(tbody);
        initClickableRows(tbody);
      }

      function loadRootRows(requestedPage) {
        if (!treeUrl || !tbody) {
          render();
          return;
        }
        var level = levelSelect ? levelSelect.value : "";
        treePage = requestedPage || 1;
        tree.dataset.treeLevelMode = level ? "1" : "0";
        setBodyStatus(loadingLabel, "table-loading", true);
        fetchTreeRows("", level, treePage, currentPageSize())
          .then(function (payload) {
            var rowData = (payload && payload.rows) || [];
            tbody.innerHTML = "";
            if (!rowData.length) {
              setBodyStatus(emptyLabel, "table-empty-cell", false);
              updateTreePagination(payload ? payload.pagination : null);
              return;
            }
            insertRows(rowData, null);
            updateTreePagination(payload ? payload.pagination : null);
            render();
          })
          .catch(function () {
            updateTreePagination(null);
            setBodyStatus(errorLabel, "table-error", false);
          });
      }

      function loadChildRows(row, button) {
        var parentId = row.dataset.treeId || "";
        if (tree.dataset.treeLevelMode === "1") {
          render();
          return;
        }
        if (!parentId || !treeUrl) {
          row.dataset.treeExpanded = row.dataset.treeExpanded === "1" ? "0" : "1";
          render();
          return;
        }
        if (row.dataset.treeChildrenLoaded === "1") {
          row.dataset.treeExpanded = row.dataset.treeExpanded === "1" ? "0" : "1";
          render();
          return;
        }
        row.dataset.treeLoading = "1";
        if (button) {
          button.disabled = true;
          button.textContent = "...";
        }
        fetchTreeRows(parentId, "", 1, 100)
          .then(function (payload) {
            var rowData = (payload && payload.rows) || [];
            row.dataset.treeChildrenLoaded = "1";
            row.dataset.treeExpanded = "1";
            if (rowData.length) {
              insertRows(rowData, row);
            }
            render();
          })
          .catch(function () {
            window.alert(errorLabel);
          })
          .finally(function () {
            delete row.dataset.treeLoading;
            if (button) {
              button.disabled = false;
            }
            render();
          });
      }

      tree.addEventListener("click", function (event) {
        var button = event.target && event.target.closest
          ? event.target.closest("[data-new-country-config-tree-toggle]")
          : null;
        var row;
        if (!button || !tree.contains(button)) {
          return;
        }
        row = button.closest("[data-tree-id]");
        if (!row) {
          return;
        }
        event.preventDefault();
        loadChildRows(row, button);
      });
      if (levelSelect) {
        levelSelect.addEventListener("change", function () {
          loadRootRows(1);
        });
      }
      if (pageSizeSelect) {
        pageSizeSelect.addEventListener("change", function () {
          loadRootRows(1);
        });
      }
      if (previousPageButton) {
        previousPageButton.addEventListener("click", function () {
          if (treePagination && treePagination.has_previous) {
            loadRootRows(Math.max(1, Number(treePagination.page || treePage) - 1));
          }
        });
      }
      if (nextPageButton) {
        nextPageButton.addEventListener("click", function () {
          if (treePagination && treePagination.has_next) {
            loadRootRows(Number(treePagination.page || treePage) + 1);
          }
        });
      }
      loadRootRows();
    });
  }

  function clearGroupTableFilters(form) {
    if (!form) {
      return;
    }
    var query = form.querySelector("input[type='search'][name='q']");
    var level = form.querySelector("select[name='level']");
    if (query) {
      query.value = "";
    }
    if (level) {
      level.value = "";
    }
  }

  function clientPageForRow(target, form, row) {
    var nav = target ? target.querySelector("[data-client-pagination]") : null;
    var initialSize = parseInt(nav ? nav.dataset.initialPageSize || "25" : "25", 10);
    var pageSize = pageSizeForForm(form, Number.isFinite(initialSize) && initialSize > 0 ? initialSize : 25);
    var rows = Array.prototype.slice.call(target.querySelectorAll("[data-client-row]"));
    var visibleRows = rows.filter(function (candidate) {
      return candidate.dataset.clientFilterHidden !== "1";
    });
    var index = visibleRows.indexOf(row);
    return index >= 0 ? Math.floor(index / pageSize) + 1 : 1;
  }

  function findGroupReturnRow(state) {
    var searchRoot = document;
    if (state && state.tableId) {
      searchRoot = document.getElementById(state.tableId) || document;
    }
    var rows = Array.prototype.slice.call(searchRoot.querySelectorAll("[data-row-href]"));
    var row = rows.find(function (candidate) {
      return groupRowHrefMatches(candidate, state.rowHref);
    });
    if (row || searchRoot === document) {
      return row || null;
    }
    return Array.prototype.slice.call(document.querySelectorAll("[data-row-href]")).find(function (candidate) {
      return groupRowHrefMatches(candidate, state.rowHref);
    }) || null;
  }

  function focusGroupReturnRow(row) {
    if (!row) {
      return;
    }
    row.hidden = false;
    row.classList.add("is-return-target");
    try {
      row.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (error) {
      row.scrollIntoView();
    }
    if (typeof row.focus === "function") {
      try {
        row.focus({ preventScroll: true });
      } catch (error) {
        row.focus();
      }
    }
    window.setTimeout(function () {
      row.classList.remove("is-return-target");
    }, 4500);
  }

  function restoreGroupReturnRow(state) {
    var row = findGroupReturnRow(state);
    if (!row) {
      return false;
    }
    var target = row.closest("[id]");
    var form = formForTableTarget(target);
    if (target && form) {
      updateClientFilterState(target, form);
      if (row.dataset.clientFilterHidden === "1") {
        clearGroupTableFilters(form);
        updateClientFilterState(target, form);
      }
      renderClientPagination(target, form, clientPageForRow(target, form, row));
    }
    focusGroupReturnRow(row);
    return true;
  }

  function restoreGroupReturnState(root) {
    var scope = root || document;
    var cards = Array.prototype.slice.call(scope.querySelectorAll("[data-group-country-card]"));
    var target = null;
    if (!cards.length) {
      return;
    }
    var state = readGroupReturnState();
    if (!state || !state.rowHref) {
      return;
    }
    if (reloadGroupListIfReturnNeedsFreshData(state)) {
      return;
    }
    var card = cards.find(function (candidate) {
      return candidate.dataset.groupCountryKey === state.country;
    });
    if (card) {
      target = activateGroupCountryCard(card, cards, Array.prototype.slice.call(scope.querySelectorAll("[data-group-country-detail]")));
    }
    function restoreRow() {
      if (restoreGroupReturnRow(state)) {
        clearGroupReturnState();
      }
    }
    if (target && target._groupDetailPromise) {
      target._groupDetailPromise.then(function () {
        window.setTimeout(restoreRow, 0);
      });
      return;
    }
    window.setTimeout(restoreRow, 180);
  }

  function initGroupReturnLinks(root) {
    (root || document).querySelectorAll("[data-group-return-link]").forEach(function (link) {
      if (link.dataset.groupReturnBound === "1") {
        return;
      }
      link.dataset.groupReturnBound = "1";
      link.addEventListener("click", function (event) {
        var rowHref = link.dataset.groupReturnRowHref || window.location.pathname;
        var shouldUseHistoryBack = sameOriginPath(document.referrer) === sameOriginPath(link.href) && window.history.length > 1;
        var previousState = readGroupReturnState() || {};
        writeGroupReturnState({
          country: link.dataset.groupReturnCountry || "",
          rowHref: rowHref,
          table: link.dataset.groupReturnTable || previousState.table || "divisions",
          tableId: link.dataset.groupReturnTableId || previousState.tableId || "",
          refreshList: (link.dataset.groupReturnRefresh === "1" || previousState.refreshList === true) && shouldUseHistoryBack,
          createdAt: Date.now()
        });
        if (shouldUseHistoryBack) {
          event.preventDefault();
          window.history.back();
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    runPageInitializer("select2", function () { initSelect2(document); });
    runPageInitializer("editor de configuraci\u00f3n", function () { initConfigEditor(document); });
    runPageInitializer("layout de estad\u00edsticas", initStatsSplitChildrenLayout);
    runPageInitializer("estado de idioma", function () { initLanguageStatePreservation(document); });
    runPageInitializer("formularios con carga", function () { initLoadingSubmitForms(document); });
    runPageInitializer("enlaces con carga", function () { initLoadingLinks(document); });
    runPageInitializer("borrado de divisiones en grupos", function () { initGroupDivisionDeleteForms(document); });
    runPageInitializer("arbol de entidades de pais nuevo", function () { initNewCountryConfigTree(document); });
    runPageInitializer("filas clicables", function () { initClickableRows(document); });
    runPageInitializer("tarjetas de grupos", function () { initGroupCountryCards(document); });
    runPageInitializer("campos de codigo", function () { initUppercaseCodeInputs(document); });
    runPageInitializer("tarjetas de paises nuevos", function () { initNewCountryResultCards(document); });
    runPageInitializer("pesta\u00f1as de configuraci\u00f3n", function () { initConfigTabs(document); });
    runPageInitializer("restauraci\u00f3n de idioma", function () {
      restorePageStateAfterLanguageChange();
    });
    runPageInitializer("tema", function () { initThemeSelector(document); });
    runPageInitializer("fechas locales", function () { initLocalDateTimes(document); });
    runPageInitializer("detalle de tareas", function () { initTaskDetailLog(document); });
    runPageInitializer("bootstrap de configuraciones", function () { initConfigBootstrap(document); });
    runPageInitializer("tablas as\u00edncronas", function () { initAsyncTables(document); });
    runPageInitializer("tablas de configuraci\u00f3n", function () { initConfigTables(document); });
    runPageInitializer("puntos de carga", function () { initConfigLoadingDots(document); });
    runPageInitializer("acciones de tareas", function () { initConfigTaskActions(document); });
    runPageInitializer("acciones de exportaci\u00f3n", function () { initConfigExportActions(document); });
    runPageInitializer("botones de configuraci\u00f3n", function () { refreshConfigActionButtons(document); });
    runPageInitializer("enlaces de retorno de grupos", function () { initGroupReturnLinks(document); });
    runPageInitializer("gr\u00e1ficos", function () { initDataCharts(document); });
    runPageInitializer("detalle de pa\u00eds en dashboard", function () { initDashboardCountryDetail(document); });
    runPageInitializer("estad\u00edsticas por pa\u00eds", function () { initStatsCountries(document); });
    runPageInitializer("restauraci\u00f3n de grupos", function () { restoreGroupReturnState(document); });
    runPageInitializer("mapa", initMap);
    runPageInitializer("identidad visual", initVisualIdentity);
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      restoreGroupReturnState(document);
    }
  });
})();
