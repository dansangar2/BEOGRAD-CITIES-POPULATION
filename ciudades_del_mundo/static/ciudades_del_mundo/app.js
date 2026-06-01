(function () {
  var THEME_STORAGE_KEY = "ciudades_del_mundo_theme";
  var THEME_EFFECTS_STORAGE_KEY = "ciudades_del_mundo_theme_effects";

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

  function initSelect2(root) {
    if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.select2) {
      return;
    }
    window.jQuery(root || document)
      .find("select[data-select2]")
      .each(function () {
        var select = window.jQuery(this);
        if (select.data("select2")) {
          return;
        }
        select.select2({
          width: "100%",
          placeholder: select.data("placeholder") || "",
          allowClear: true
        });
      });
  }

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

  function formParams(form, page) {
    var params = new URLSearchParams(new FormData(form));
    if (page) {
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

  function loadTable(form, page) {
    var target = document.querySelector(form.dataset.target);
    if (!target) {
      return;
    }
    var params = formParams(form, page);
    var url = form.dataset.url + (params.toString() ? "?" + params.toString() : "");
    setLoading(target, form.dataset.loading || "Cargando tabla...");
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
        target.innerHTML = html;
      })
      .catch(function () {
        setError(target, form.dataset.error || "La tabla no se pudo cargar. Reintenta en unos segundos.");
      });
  }

  function initAsyncTables(root) {
    (root || document).querySelectorAll(".async-table-form").forEach(function (form) {
      loadTable(form);
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        loadTable(form);
      });
      form.addEventListener("reset", function () {
        window.setTimeout(function () {
          if (window.jQuery && window.jQuery.fn.select2) {
            window.jQuery(form).find("select[data-select2]").val(null).trigger("change");
          }
          loadTable(form);
        }, 0);
      });
      var target = document.querySelector(form.dataset.target);
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

  function visualIdentityDetailUrl(kind, filename) {
    return "/identity/" + encodeURIComponent(kind || "image") + "/" + encodeURIComponent(filename) + "/";
  }

  function openImagePreview(filename, label, kind) {
    if (!filename) {
      return;
    }
    var fullSrc = commonsFileUrl(filename, 1600);
    var detailUrl = visualIdentityDetailUrl(kind, filename);
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
    title.textContent = label || filename;
    var closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "secondary";
    closeButton.setAttribute("aria-label", "Cerrar");
    closeButton.textContent = "x";
    header.appendChild(title);
    header.appendChild(closeButton);

    var image = document.createElement("img");
    image.src = commonsFileUrl(filename, 900);
    image.alt = label || filename;
    image.loading = "eager";

    var actions = document.createElement("div");
    actions.className = "image-preview-actions";
    var pageButton = document.createElement("button");
    pageButton.type = "button";
    pageButton.className = "secondary";
    pageButton.textContent = "Ficha";
    var fullButton = document.createElement("button");
    fullButton.type = "button";
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

    var imageClicks = 0;
    image.addEventListener("click", function () {
      imageClicks += 1;
      window.open(imageClicks === 1 ? detailUrl : fullSrc, "_blank", "noopener");
    });
    pageButton.addEventListener("click", function () {
      window.open(detailUrl, "_blank", "noopener");
    });
    fullButton.addEventListener("click", function () {
      window.open(fullSrc, "_blank", "noopener");
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

  function fetchJson(url) {
    return fetch(url).then(function (response) {
      if (!response.ok) {
        throw new Error(response.statusText);
      }
      return response.json();
    });
  }

  function formatNumber(value) {
    try {
      return new Intl.NumberFormat(document.documentElement.lang || "es").format(value || 0);
    } catch (error) {
      return String(value || 0);
    }
  }

  function formatPercent(value, total) {
    if (!total) {
      return "0%";
    }
    return ((value / total) * 100).toFixed(1) + "%";
  }

  function formatPercentNumber(value) {
    var percent = Number(value || 0);
    if (!Number.isFinite(percent)) {
      percent = 0;
    }
    return Math.max(0, Math.min(100, percent)).toFixed(2) + "%";
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
      "#ca8a04"
    ];
  }

  function assignRowColors(rows) {
    var colors = chartColors();
    (rows || []).forEach(function (row, index) {
      row.color = row.color || colors[index % colors.length];
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
    empty.textContent = container.dataset.empty || "Sin datos.";
    container.appendChild(empty);
  }

  function renderDonutChart(container, data) {
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

    function setCenter(label, value) {
      totalLabel.textContent = label;
      totalValue.textContent = formatNumber(value);
    }

    function positionTooltip(event) {
      var rect = chart.getBoundingClientRect();
      tooltip.style.left = (event.clientX - rect.left) + "px";
      tooltip.style.top = (event.clientY - rect.top) + "px";
    }

    function selectSegment(segment) {
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
      }

      var title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = segment.label + " - " + formatNumber(segment.value) + " (" + formatPercent(segment.value, total) + ")";
      path.appendChild(title);

      path.addEventListener("mouseenter", function (event) {
        setCenter(segment.label, segment.value);
        tooltip.textContent = title.textContent;
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
    chart.appendChild(tooltip);

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
        populationColor: sharedColors[key] || "",
        areaColor: sharedColors[key] || ""
      });
    });

    rows.sort(function (left, right) {
      return right.population - left.population || String(left.label).localeCompare(String(right.label));
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
    ["", "", container.dataset.countryLabel || "Pais", container.dataset.populationLabel || "Poblacion", "%", container.dataset.areaLabel || "Terreno", "%"].forEach(function (label) {
      var th = document.createElement("th");
      th.textContent = label;
      header.appendChild(th);
    });
    thead.appendChild(header);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    wrap.appendChild(table);
    panel.appendChild(wrap);

    var rowEntries = [];
    rows.forEach(function (entry) {
      var row = document.createElement("tr");
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
        row.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            row.click();
          }
        });
      }
      [
        { color: entry.populationColor, title: container.dataset.populationLabel || "Poblacion" },
        { color: entry.areaColor, title: container.dataset.areaLabel || "Terreno" }
      ].forEach(function (markerData) {
        var td = document.createElement("td");
        if (markerData.color) {
          var marker = document.createElement("span");
          marker.className = "table-color-dot";
          marker.title = markerData.title;
          marker.style.background = markerData.color;
          td.appendChild(marker);
        }
        row.appendChild(td);
      });
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
      rowEntries.push({
        row: row,
        text: [entry.label, entry.code].join(" ").toLowerCase()
      });
    });

    searchInput.addEventListener("input", function () {
      var query = searchInput.value.trim().toLowerCase();
      rowEntries.forEach(function (entry) {
        entry.row.hidden = Boolean(query && entry.text.indexOf(query) === -1);
      });
    });

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
      [container.dataset.populationLabel || "Poblacion", withSharedCountryColors(charts.population, sharedColors)],
      [container.dataset.areaLabel || "Terreno", withSharedCountryColors(charts.area, sharedColors)]
    ];
    entries.forEach(function (entry) {
      var panel = document.createElement("div");
      panel.className = "dashboard-population-chart";
      var title = document.createElement("h3");
      title.textContent = entry[0];
      var chart = document.createElement("div");
      chart.dataset.chartType = "donut";
      chart.dataset.otherLabel = container.dataset.otherLabel || "";
      chart.dataset.maxSegments = container.dataset.maxSegments || "";
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
        setError(container, container.dataset.error || "No se pudo cargar la grafica.");
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
    appendTextDefinition(list, labels.capitalLabel, (country.capital || [])[0], "data-country-capital");
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
    Promise.resolve(hydrateCountryIdentity(panel, country)).then(reveal).catch(reveal);
  }

  function renderCountryTablePanel(panel, data, labels, baseUrl, target) {
    var heading = document.createElement("h2");
    heading.textContent = labels.tableTitle;
    panel.appendChild(heading);

    var controls = document.createElement("div");
    controls.className = "country-table-controls";

    var levelLabel = document.createElement("label");
    levelLabel.textContent = labels.levelLabel;
    var levelSelect = document.createElement("select");
    (data.levels || []).forEach(function (level) {
      var option = document.createElement("option");
      option.value = level.value;
      option.textContent = level.label + (level.entity_type ? " · " + level.entity_type : "");
      option.selected = String(level.value) === String(data.selected_level);
      levelSelect.appendChild(option);
    });
    levelSelect.addEventListener("change", function () {
      loadCountryTableOnly(panel, baseUrl, levelSelect.value, target);
    });
    levelLabel.appendChild(levelSelect);

    var filterLabel = document.createElement("label");
    filterLabel.textContent = labels.filterLabel;
    var filter = document.createElement("input");
    filter.type = "search";
    filter.placeholder = labels.filterPlaceholder;
    filterLabel.appendChild(filter);

    var pageSizeLabel = document.createElement("label");
    pageSizeLabel.textContent = labels.pageSizeLabel;
    var pageSizeSelect = document.createElement("select");
    [10, 25, 50, 100].forEach(function (size) {
      var option = document.createElement("option");
      option.value = size;
      option.textContent = size;
      option.selected = size === 25;
      pageSizeSelect.appendChild(option);
    });
    pageSizeLabel.appendChild(pageSizeSelect);

    controls.appendChild(levelLabel);
    controls.appendChild(filterLabel);
    panel.appendChild(controls);

    var tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap subtle country-data-table-wrap";
    var table = document.createElement("table");
    table.className = "compact-table country-data-table";
    var thead = document.createElement("thead");
    var headerRow = document.createElement("tr");
    var columns = [
      ["name", labels.nameLabel],
      ["entity_type", labels.entityTypeLabel],
      ["area_km2", labels.areaLabel],
      ["area_percent", "%"],
      ["population", labels.populationLabel],
      ["population_percent", "%"],
      ["density", labels.densityLabel],
      ["parent", labels.parentLabel]
    ];
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

    var rows = (data.table && data.table.rows) || [];
    var sortKey = "name";
    var sortDirection = 1;
    var page = 1;
    var pageSize = 25;

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
        button.textContent = column ? column[1] : button.dataset.sort;
        if (active) {
          button.textContent += sortDirection > 0 ? " \u2191" : " \u2193";
        }
      });
    }

    function drawRows() {
      var query = filter.value.trim().toLowerCase();
      var filtered = rows.filter(function (row) {
        if (!query) {
          return true;
        }
        return [row.name, row.parent, row.entity_type].some(function (value) {
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
      var totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.min(Math.max(1, page), totalPages);
      var visible = filtered.slice((page - 1) * pageSize, page * pageSize);

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
        [
          row.name,
          row.entity_type || "-",
          formattedNumberOrDash(row.area_km2),
          formatPercentNumber(row.area_percent),
          formattedNumberOrDash(row.population),
          formatPercentNumber(row.population_percent),
          formattedNumberOrDash(row.density),
          row.parent || "-"
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      previousButton.disabled = page <= 1;
      nextButton.disabled = page >= totalPages;
      pageStatus.textContent = labels.sortedByLabel + " " + sortColumnLabel() + " - " + page + "/" + totalPages + " - " + formatNumber(filtered.length);
      updateSortButtons();
    }

    filter.addEventListener("input", function () {
      page = 1;
      drawRows();
    });
    previousButton.addEventListener("click", function () {
      page -= 1;
      drawRows();
    });
    nextButton.addEventListener("click", function () {
      page += 1;
      drawRows();
    });
    pageSizeSelect.addEventListener("change", function () {
      pageSize = Number(pageSizeSelect.value || 25);
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

  function scrollToCountryTable(target) {
    if (!target) {
      return;
    }
    var table = target && target.querySelector(".country-data-table-wrap");
    (table || target).scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function loadCountryTableOnly(panel, url, level, target) {
    if (!panel || !url) {
      return;
    }
    var labels = countryDetailLabels(target);
    setLoading(panel, target.dataset.loading || "Cargando pais...");
    var finalUrl = new URL(url, window.location.origin);
    finalUrl.searchParams.set("level", level);
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        panel.innerHTML = "";
        renderCountryTablePanel(panel, data, labels, url, target);
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
  }

  function appendShareSummaryTable(parent, rows, labels, extraWrapClass) {
    var wrap = document.createElement("div");
    wrap.className = "table-wrap subtle first-order-summary-wrap" + (extraWrapClass ? " " + extraWrapClass : "");
    var table = document.createElement("table");
    table.className = "compact-table first-order-summary-table";
    var thead = document.createElement("thead");
    var header = document.createElement("tr");
    var compact = Boolean(extraWrapClass && extraWrapClass.indexOf("subdivision-share-wrap") !== -1);
    var columns = [
      ["color", ""],
      ["name", labels.nameLabel],
      ["population", compact ? "Pob." : labels.populationLabel],
      ["population_percent", "%"],
      ["area_km2", compact ? "Km2" : labels.areaLabel],
      ["area_percent", "%"]
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

    function updateShareSortButtons() {
      table.querySelectorAll("[data-sort]").forEach(function (button) {
        var active = button.dataset.sort === sortKey;
        var column = columns.filter(function (item) {
          return item[0] === button.dataset.sort;
        })[0];
        button.classList.toggle("is-sorted", active);
        button.classList.toggle("is-desc", active && sortDirection < 0);
        button.classList.toggle("is-asc", active && sortDirection > 0);
        button.textContent = column ? column[1] : button.dataset.sort;
        if (active) {
          button.textContent += sortDirection > 0 ? " \u2191" : " \u2193";
        }
      });
    }

    function drawShareRows() {
      var sortedRows = dataRows.slice().sort(function (left, right) {
        var a = shareComparable(left, sortKey);
        var b = shareComparable(right, sortKey);
        if (a < b) {
          return -1 * sortDirection;
        }
        if (a > b) {
          return 1 * sortDirection;
        }
        return 0;
      });
      tbody.innerHTML = "";
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
          formattedNumberOrDash(rowData.population),
          formatPercentNumber(rowData.population_percent),
          formattedNumberOrDash(rowData.area_km2),
          formatPercentNumber(rowData.area_percent)
        ].forEach(function (value) {
          var td = document.createElement("td");
          td.textContent = value;
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

    drawShareRows();
    table.appendChild(tbody);
    wrap.appendChild(table);
    parent.appendChild(wrap);
  }

  function renderCountryChartsPanel(panel, data, labels) {
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
    var legendPanel = document.createElement("div");
    legendPanel.className = "population-country-panel first-order-color-panel";
    var searchLabel = document.createElement("label");
    searchLabel.className = "chart-list-search";
    searchLabel.textContent = labels.filterLabel;
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = labels.filterPlaceholder;
    searchLabel.appendChild(searchInput);
    legendPanel.appendChild(searchLabel);
    var legend = document.createElement("div");
    legend.className = "population-country-list first-order-color-legend";
    var legendHeader = document.createElement("div");
    legendHeader.className = "population-country-row first-order-legend-row first-order-legend-heading";
    ["", labels.nameLabel, labels.populationLabel, "%", labels.areaLabel, "%"].forEach(function (value) {
      var cell = document.createElement("span");
      cell.textContent = value;
      legendHeader.appendChild(cell);
    });
    legend.appendChild(legendHeader);
    var legendRows = [];
    cards.forEach(function (card) {
      var row = document.createElement("div");
      row.className = "population-country-row first-order-legend-row";
      var marker = document.createElement("span");
      marker.className = "population-country-marker";
      marker.style.background = card.color;
      var name = document.createElement("span");
      name.className = "population-country-name";
      name.textContent = card.name;
      var value = document.createElement("strong");
      value.textContent = formatNumber(card.population || 0);
      var percent = document.createElement("span");
      percent.className = "population-country-percent";
      percent.textContent = formatPercentNumber(card.population_percent);
      var area = document.createElement("strong");
      area.textContent = formattedNumberOrDash(card.area_km2);
      var areaPercent = document.createElement("span");
      areaPercent.className = "population-country-percent";
      areaPercent.textContent = formatPercentNumber(card.area_percent);
      row.appendChild(marker);
      row.appendChild(name);
      row.appendChild(value);
      row.appendChild(percent);
      row.appendChild(area);
      row.appendChild(areaPercent);
      legend.appendChild(row);
      legendRows.push({
        row: row,
        text: [card.name, card.entity_type].join(" ").toLowerCase()
      });
    });
    searchInput.addEventListener("input", function () {
      var query = searchInput.value.trim().toLowerCase();
      legendRows.forEach(function (entry) {
        entry.row.hidden = Boolean(query && entry.text.indexOf(query) === -1);
      });
    });
    legendPanel.appendChild(legend);
    renderShareChartPair(charts, [
      [labels.populationLabel, withChartColors(data.first_order && data.first_order.population_chart, colors)],
      [labels.areaLabel, withChartColors(data.first_order && data.first_order.area_chart, colors)]
    ], "country-first-order-charts");
    layout.appendChild(charts);
    layout.appendChild(legendPanel);
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
    text.textContent = label + " · " + percent.toFixed(2) + "%";
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
      previousLabel: detailLabel(target, "previousLabel", "Anterior"),
      nextLabel: detailLabel(target, "nextLabel", "Siguiente"),
      sortedByLabel: detailLabel(target, "sortedByLabel", "Ordenado por"),
      noData: detailLabel(target, "noData", "Sin datos.")
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

    renderCountryGeneralPanel(general, data, labels);
    renderCountryTablePanel(table, data, labels, baseUrl, target);
    renderCountryChartsPanel(charts, data, labels);

    grid.appendChild(general);
    grid.appendChild(table);
    grid.appendChild(charts);
    target.appendChild(grid);
    renderCountryShareCards(target, data, labels);
  }

  function loadCountryDetail(target, url, level) {
    if (!target || !url) {
      return;
    }
    target.hidden = false;
    setLoading(target, target.dataset.loading || "Cargando pais...");
    var finalUrl = new URL(url, window.location.origin);
    if (level !== undefined && level !== null && level !== "") {
      finalUrl.searchParams.set("level", level);
    }
    fetchJson(relativeUrlFrom(finalUrl.toString()))
      .then(function (data) {
        renderCountryDetail(target, data, url);
        scrollToCountryTable(target);
      })
      .catch(function () {
        setError(target, target.dataset.error || "No se pudo cargar el pais.");
      });
  }

  function hydrateCountryIdentity(panel, country) {
    if (!country || (!country.wikidata_query && !country.wikidata_id)) {
      return Promise.resolve();
    }
    var languages = wikidataLanguages();
    var flagSlot = panel.querySelector("[data-country-flag]");
    var coatSlot = panel.querySelector("[data-country-coat]");
    var official = panel.querySelector("[data-country-official]");
    var officialLanguage = panel.querySelector("[data-country-official-language]");
    var capital = panel.querySelector("[data-country-capital]");
    var imagePromises = [];

    function showImage(slot, filename, kind) {
      if (!slot || !filename) {
        return;
      }
      var image = slot.querySelector("img");
      var empty = slot.querySelector(".muted");
      var loaded = new Promise(function (resolve) {
        image.onload = resolve;
        image.onerror = resolve;
      });
      imagePromises.push(loaded);
      image.dataset.fullSrc = commonsFileUrl(filename, 1200);
      image.src = commonsFileUrl(filename, 220);
      image.hidden = false;
      image.onclick = function () {
        openImagePreview(filename, image.alt || filename, kind);
      };
      if (empty) {
        empty.hidden = true;
        }
    }

    function waitForIdentityImages() {
      return Promise.all(imagePromises).then(function () {
        return true;
      });
    }

    var entityPromise = country.wikidata_id ? Promise.resolve(country.wikidata_id) : searchWikidataEntity(country.wikidata_query, languages);
    return entityPromise
      .then(function (id) {
        return fetchWikidataEntities([id], "claims|labels", languages).then(function (entities) {
          var entity = entities[id];
          var claims = entity && entity.claims ? entity.claims : {};
          var officialNames = claimTextValues(claims, "P1448", languages);
          if (official && officialNames.length) {
            official.textContent = officialNames[0];
          } else if (official && labelForEntity(entity, languages)) {
            official.textContent = labelForEntity(entity, languages);
          }

          var flagClaim = claims.P41 && claims.P41[0];
          var flagValue = flagClaim && flagClaim.mainsnak && flagClaim.mainsnak.datavalue && flagClaim.mainsnak.datavalue.value;
          showImage(flagSlot, flagValue, "flag");

          var coatClaim = claims.P94 && claims.P94[0];
          var coatValue = coatClaim && coatClaim.mainsnak && coatClaim.mainsnak.datavalue && coatClaim.mainsnak.datavalue.value;
          showImage(coatSlot, coatValue, "coat");

          var capitalIds = claimEntityIds(claims, "P36");
          var languageIds = claimEntityIds(claims, "P37");
          var targetIds = capitalIds.concat(languageIds);
          if (!targetIds.length) {
            return waitForIdentityImages();
          }
          return fetchWikidataEntities(targetIds, "labels", languages).then(function (capitalEntities) {
            var names = capitalIds.map(function (capitalId) {
              return labelForEntity(capitalEntities[capitalId], languages);
            }).filter(Boolean);
            if (capital && names.length) {
              capital.textContent = uniqueValues(names)[0];
            }
            var languageNames = languageIds.map(function (languageId) {
              return labelForEntity(capitalEntities[languageId], languages);
            }).filter(Boolean);
            if (officialLanguage && languageNames.length) {
              officialLanguage.textContent = uniqueValues(languageNames).join(", ");
            }
            return waitForIdentityImages();
          });
        });
      })
      .catch(function () {});
  }

  function initDashboardCountryDetail(root) {
    var scope = root || document;
    var target = scope.querySelector("[data-country-detail]");
    if (!target) {
      return;
    }
    scope.addEventListener("ciudades:chart-item-click", function (event) {
      if (!event.detail || !event.detail.url) {
        return;
      }
      loadCountryDetail(target, event.detail.url);
    });
  }

  function flagFilenameFromEntity(entity) {
    var claims = entity && entity.claims ? entity.claims : {};
    var flagClaim = claims.P41 && claims.P41[0];
    return flagClaim && flagClaim.mainsnak && flagClaim.mainsnak.datavalue && flagClaim.mainsnak.datavalue.value;
  }

  function hydrateStatsCountryFlags(container, countries) {
    var cardsById = {};
    (countries || []).forEach(function (country) {
      if (!country.wikidata_id) {
        return;
      }
      cardsById[country.wikidata_id] = container.querySelector("[data-stats-country-flag='" + country.wikidata_id + "']");
    });
    var ids = Object.keys(cardsById);
    if (!ids.length) {
      return;
    }
    var languages = wikidataLanguages();
    for (var index = 0; index < ids.length; index += 50) {
      fetchWikidataEntities(ids.slice(index, index + 50), "claims", languages)
        .then(function (entities) {
          Object.keys(entities).forEach(function (id) {
            var slot = cardsById[id];
            var filename = flagFilenameFromEntity(entities[id]);
            if (!slot || !filename) {
              return;
            }
            slot.innerHTML = "";
            var image = document.createElement("img");
            image.alt = "";
            image.loading = "lazy";
            image.src = commonsFileUrl(filename, 180);
            slot.appendChild(image);
          });
        })
        .catch(function () {});
    }
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
      var card = document.createElement("button");
      card.type = "button";
      card.className = "stats-country-card";
      card.dataset.detailUrl = country.detail_url || "";

      var flag = document.createElement("span");
      flag.className = "stats-country-flag";
      flag.dataset.statsCountryFlag = country.wikidata_id || "";
      flag.textContent = "⚑";
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
      card.appendChild(flag);
      card.appendChild(name);
      card.appendChild(metrics);
      card.addEventListener("click", function () {
        var target = document.querySelector("[data-stats-country-detail]");
        if (target && card.dataset.detailUrl) {
          loadStatsCountryDetail(target, card.dataset.detailUrl, container);
        }
      });
      grid.appendChild(card);
    });
    container.appendChild(grid);
    hydrateStatsCountryFlags(container, countries);
  }

  function loadStatsCountryDetail(target, url, source) {
    setLoading(target, target.dataset.loading || "Cargando pais...");
    target.hidden = false;
    fetchJson(url)
      .then(function (data) {
        target.innerHTML = "";
        var panel = document.createElement("article");
        panel.className = "panel stats-country-basic-panel";
        panel.dataset.loading = target.dataset.loading || "";
        renderCountryGeneralPanel(panel, data, countryDetailLabels(source || target));
        target.appendChild(panel);
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function () {
        setError(target, target.dataset.error || "No se pudo cargar el pais.");
      });
  }

  function initStatsCountries(root) {
    var container = (root || document).querySelector("[data-stats-countries]");
    if (!container) {
      return;
    }
    setLoading(container, container.dataset.loading || "Cargando datos...");
    fetchJson(container.dataset.url)
      .then(function (payload) {
        renderStatsCountryGrid(container, payload);
      })
      .catch(function () {
        setError(container, container.dataset.error || "No se pudieron cargar los paises.");
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
    var url = "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&origin=*&ids=" +
      encodeURIComponent(ids.join("|")) + "&props=" + encodeURIComponent(props) +
      "&languages=" + encodeURIComponent(languages.join("|"));
    return fetchJson(url).then(function (data) {
      return data.entities || {};
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
    Object.keys(slots).forEach(function (property) {
      var slot = slots[property];
      var claim = claims[property] && claims[property][0];
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!slot || !value) {
        return;
      }
      var image = slot.querySelector("img");
      image.src = commonsFileUrl(value, 420);
      image.alt = value;
      slot.hidden = false;
      shown = true;
    });
    if (status) {
      if (shown) {
        status.hidden = true;
      } else {
        status.textContent = noImagesMessage;
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
          status.textContent = noImagesMessage;
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
        knowledgeStatus.textContent = noWikidataMessage;
      }
    }).catch(function () {
      if (knowledgeStatus) {
        knowledgeStatus.textContent = noWikidataMessage;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initThemeSelector(document);
    initSelect2(document);
    initAsyncTables(document);
    initDataCharts(document);
    initDashboardCountryDetail(document);
    initStatsCountries(document);
    initMap();
    initVisualIdentity();
  });
})();
