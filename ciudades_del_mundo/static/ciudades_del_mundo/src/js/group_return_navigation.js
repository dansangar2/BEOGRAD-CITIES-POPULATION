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

