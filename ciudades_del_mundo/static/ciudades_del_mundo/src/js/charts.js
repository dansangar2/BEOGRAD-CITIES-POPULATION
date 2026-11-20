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

