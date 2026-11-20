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
      var idLabel = tree.dataset.idLabel || "ID";
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
        td.colSpan = 8;
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

      function fetchTreeRows(parentId, level, page, pageSize, focus) {
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
        if (focus) { url.searchParams.set("focus", focus); }
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

        var nameCell = textCell(
          "new-country-tree-name-cell " + (rowData.tree_depth_class || ("new-country-tree-depth-" + Math.min(depth, 5))),
          rowData.entity_display_name || ""
        );
        var code = document.createElement("code");
        code.className = "new-country-tree-entity-code";
        if (rowData.is_assigned_subdivision) {
          code.classList.add("new-country-assigned-code");
        }
        code.textContent = idLabel + ": " + (rowData.full_display_code || rowData.display_code || "");
        nameCell.appendChild(code);
        tr.appendChild(nameCell);

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

        var actionColumn = document.createElement("td");
        actionColumn.className = "new-country-tree-action-cell";
        var actionCell = document.createElement("div");
        actionCell.className = "new-country-tree-action-buttons";
        actionColumn.appendChild(actionCell);
        if (rowData.modal_edit_url) {
          var editButton = document.createElement("button");
          editButton.type = "button";
          editButton.className = "secondary";
          editButton.setAttribute("data-new-country-entity-open", "");
          editButton.dataset.editEntity = "1";
          editButton.dataset.createUrl = rowData.modal_edit_url;
          editButton.dataset.parentTreeId = rowData.tree_parent || "";
          editButton.dataset.ancestorTreeIds = JSON.stringify(rowData.ancestor_tree_ids || []);
          editButton.textContent = tree.dataset.editLabel;
          actionCell.appendChild(editButton);
        }
        if (rowData.new_url) {
          var newButton = document.createElement("button");
          newButton.type = "button";
          newButton.className = "secondary";
          newButton.setAttribute("data-new-country-entity-open", "");
          newButton.dataset.createUrl = rowData.new_url;
          newButton.dataset.parentTreeId = rowData.tree_id || "";
          newButton.dataset.ancestorTreeIds = JSON.stringify(rowData.ancestor_tree_ids || []);
          newButton.textContent = tree.dataset.newLabel;
          actionCell.appendChild(newButton);
        }
        if (rowData.clone_url) {
          appendActionForm(actionCell, rowData.clone_url, "group-division-clone-form", "group-division-clone-button", cloneLabel, cloneTitle, "");
        }
        if (rowData.delete_url) {
          appendActionForm(actionCell, rowData.delete_url, "group-division-delete-form", "group-division-delete-button", deleteLabel, deleteTitle, deleteConfirm);
        }
        if (!rowData.modal_edit_url && !rowData.new_url && !rowData.clone_url && !rowData.delete_url) {
          var empty = document.createElement("span");
          empty.className = "new-country-tree-empty-action";
          empty.setAttribute("aria-hidden", "true");
          empty.textContent = "-";
          actionCell.appendChild(empty);
        }
        tr.appendChild(actionColumn);
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

      function loadRootRows(requestedPage, focus) {
        if (!treeUrl || !tbody) {
          render();
          return;
        }
        var level = levelSelect ? levelSelect.value : "";
        treePage = requestedPage || 1;
        tree.dataset.treeLevelMode = level ? "1" : "0";
        setBodyStatus(loadingLabel, "table-loading", true);
        return fetchTreeRows("", level, treePage, currentPageSize(), focus)
          .then(function (payload) {
            if (payload && payload.pagination) { treePage = Number(payload.pagination.page) || 1; }
            if (payload && Array.isArray(payload.levels) && levelSelect) {
              var selected = levelSelect.value;
              while (levelSelect.options.length > 1) { levelSelect.remove(1); }
              payload.levels.forEach(function (item) {
                var option = document.createElement("option");
                option.value = String(item.value);
                option.textContent = "NV " + item.value + " (" + item.count + ")";
                levelSelect.appendChild(option);
              });
              levelSelect.value = selected;
            }
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
        return fetchTreeRows(parentId, "", 1, 100)
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
      document.addEventListener("new-country-entity-created", function (event) {
        var ancestors = (event.detail.ancestorTreeIds || []).slice();
        var parent = rowById(event.detail.parentTreeId || "");
        var path = [];
        while (parent) {
          path.unshift(parent.dataset.treeId);
          parent = rowById(parent.dataset.treeParent || "");
        }
        var reopen = ancestors.concat(path).filter(function (value, index, list) { return list.indexOf(value) === index; });
        if (levelSelect) { levelSelect.value = ""; }
        loadRootRows(1, reopen[0] || event.detail.treeId).then(function () {
          return reopen.reduce(function (promise, treeId) {
            return promise.then(function () {
              var row = rowById(treeId);
              return row ? loadChildRows(row, row.querySelector("[data-new-country-config-tree-toggle]")) : null;
            });
          }, Promise.resolve());
        });
      });
      tree.addEventListener("new-country-entity-deleted", function () {
        loadRootRows(treePage);
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

