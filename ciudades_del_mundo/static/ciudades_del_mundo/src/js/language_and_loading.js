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

