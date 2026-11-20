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

