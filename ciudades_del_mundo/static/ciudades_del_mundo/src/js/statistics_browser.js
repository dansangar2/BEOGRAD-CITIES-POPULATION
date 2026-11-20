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
    var pagination = countryBrowserIsFirstLevel(data) ? null : countryBrowserPagination(data);
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
    if (pagination) {
      var pageSizeLabel = document.createElement("label");
      pageSizeLabel.textContent = labels.pageSizeLabel;
      pageSizeSelect = document.createElement("select");
      populateCountryBrowserPageSizes(pageSizeSelect, countryBrowserPageSize(pagination));
      pageSizeLabel.appendChild(pageSizeSelect);
      controls.appendChild(pageSizeLabel);
      pageSizeSelect.addEventListener("change", function () {
        loadStatsCountryDetail(target, url, source, currentLevel(), 1, pageSizeSelect.value);
      });
    }
    function currentLevel() {
      return select ? select.value : selectedCountryBrowserLevel(data);
    }
    if (select) {
      select.addEventListener("change", function () {
        loadStatsCountryDetail(target, url, source, currentLevel(), 1, pageSizeSelect ? pageSizeSelect.value : undefined);
      });
    }
    panel.appendChild(controls);
  }

  function loadStatsCountryDetail(target, url, source, level, page, pageSize) {
    setLoading(target, target.dataset.loading || "Cargando pais...");
    target.hidden = false;
    var finalUrl = new URL(url, window.location.origin);
    finalUrl.searchParams.set("include_children", "0");
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
          target,
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
          var pagination = countryBrowserIsFirstLevel(data) ? null : countryBrowserPagination(data);
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
    var cachedSignature = cachedPayload ? JSON.stringify(cachedPayload) : "";
    if (cachedPayload) {
      renderStatsCountryGrid(container, cachedPayload);
    } else {
      setLoading(container, container.dataset.loading || "Cargando datos...");
    }
    fetchJson(container.dataset.url)
      .then(function (payload) {
        writeStatsCountryCache(container.dataset.url, payload);
        if (!cachedPayload || JSON.stringify(payload) !== cachedSignature) {
          renderStatsCountryGrid(container, payload);
        }
      })
      .catch(function () {
        if (!cachedPayload) {
          setError(container, container.dataset.error || "No se pudieron cargar los paises.");
        }
      });
  }

