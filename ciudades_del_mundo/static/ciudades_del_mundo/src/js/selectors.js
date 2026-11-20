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

