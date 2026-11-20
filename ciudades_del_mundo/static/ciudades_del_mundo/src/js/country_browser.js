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
    var paginate = !countryBrowserIsFirstLevel(data);
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
    if (paginate) {
      panel.appendChild(pagination);
    }

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
      var visible = !paginate || paginationData ? filtered : filtered.slice((page - 1) * pageSize, page * pageSize);

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

  function countryBrowserIsFirstLevel(data) {
    var levels = (data && data.levels) || [];
    var firstLevel = levels.length ? Number(levels[0].value) : 1;
    return Number(data && data.selected_level) === firstLevel;
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

