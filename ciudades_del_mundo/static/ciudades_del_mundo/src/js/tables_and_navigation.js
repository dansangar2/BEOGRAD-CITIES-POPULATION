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
        var hasPopulation = Number(row.dataset.rows || 0) > 0 || rowStatus === "populated";
        if (selectedStatus === "unpopulated" ? hasPopulation : rowStatus !== selectedStatus) {
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
