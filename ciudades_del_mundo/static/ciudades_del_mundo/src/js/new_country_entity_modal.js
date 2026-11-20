  function initNewCountryEntityDialog(root) {
    var scope = root || document;
    var dialog = scope.querySelector("[data-new-country-entity-dialog]");
    if (dialog && !dialog._entityReady) {
      dialog._entityReady = true;
      var frame = dialog.querySelector("[data-new-country-entity-frame]");
      var create = dialog.querySelector("[data-new-country-entity-create]");
      var chooser = dialog.querySelector("[data-new-country-kind-chooser]");
      var targetUrl;
      function frameForm() {
        return frame.contentDocument?.querySelector("form[data-new-country-embedded]");
      }
      frame.addEventListener("load", function () {
        create.disabled = !frameForm();
      });
      create.addEventListener("click", function () {
        frameForm()?.requestSubmit();
      });
      chooser.addEventListener("click", function (event) {
        var choice = event.target.closest("[data-new-country-kind]");
        if (!choice || !targetUrl) { return; }
        targetUrl.searchParams.set("kind", choice.dataset.newCountryKind);
        chooser.hidden = true;
        frame.hidden = create.hidden = false;
        frame.src = targetUrl.toString();
      });
      var opener = null;
      document.addEventListener("click", function (event) {
        var button = event.target.closest && event.target.closest("[data-new-country-entity-open]");
        if (!button) { return; }
        event.preventDefault();
        opener = button;
        create.disabled = true;
        var editing = button.dataset.editEntity === "1";
        var title = dialog.querySelector("#new-country-entity-dialog-title");
        title.textContent = editing ? title.dataset.editTitle : title.dataset.createTitle;
        create.textContent = editing ? create.dataset.saveLabel : create.dataset.createLabel;
        var url = new URL(button.dataset.createUrl, window.location.origin);
        if (url.origin !== window.location.origin) { return; }
        url.searchParams.set("embedded", "1");
        targetUrl = url;
        dialog.dataset.parentTreeId = button.dataset.parentTreeId || "";
        dialog.dataset.ancestorTreeIds = button.dataset.ancestorTreeIds || "[]";
        chooser.hidden = editing;
        frame.hidden = create.hidden = !editing;
        if (editing) { frame.src = url.toString(); }
        dialog.showModal();
        if (!editing) { chooser.querySelector("button").focus(); }
      });
      dialog.querySelector("[data-new-country-entity-close]").addEventListener("click", function () { dialog.close(); });
      dialog.addEventListener("close", function () {
        frame.src = "about:blank";
        if (opener && opener.isConnected) { opener.focus(); }
      });
      window.addEventListener("message", function (event) {
        if (event.origin !== window.location.origin || event.source !== frame.contentWindow || !dialog.open || !event.data || event.data.type !== "new-country-entity-created") { return; }
        var detail = { parentTreeId: dialog.dataset.parentTreeId || "", treeId: event.data.tree_id || "", ancestorTreeIds: JSON.parse(dialog.dataset.ancestorTreeIds || "[]") };
        dialog.close();
        document.dispatchEvent(new CustomEvent("new-country-entity-created", { detail: detail }));
      });
    }
    scope.querySelectorAll("[data-new-country-id-preview]").forEach(function (preview) {
      var codeInput = preview.closest("form").querySelector("input[name='slug']");
      function updateCode() {
        var prefix = preview.dataset.parentCode || "";
        var local = normalizedUppercaseCodeInputValue(codeInput.value, true);
        preview.textContent = (preview.dataset.idLabel || "ID") + ": " + prefix + (local ? "-" + local : "");
      }
      codeInput.addEventListener("input", updateCode);
      updateCode();
    });
    scope.querySelectorAll("form[data-new-country-embedded]").forEach(function (form) {
      if (form._entitySubmitReady) { return; }
      form._entitySubmitReady = true;
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (form._entitySaving) { return; }
        var errorBox = form.querySelector("[data-new-country-entity-error]");
        errorBox.hidden = true;
        var body = new FormData(form);
        form._entitySaving = true;
        var frame = window.frameElement;
        var createButton = frame?.closest("dialog")?.querySelector("[data-new-country-entity-create]");
        if (createButton) { createButton.disabled = true; }
        var buttons = Array.prototype.slice.call(form.querySelectorAll("button[type='submit']"));
        buttons.forEach(function (button) { button.disabled = true; });
        fetch(window.location.href, {
          method: "POST", body: body, credentials: "same-origin",
          headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrfFromForm(form) }
        }).then(parseJsonResponseText).then(function (payload) {
          window.parent.postMessage({ type: "new-country-entity-created", tree_id: payload.tree_id, country: payload.country }, window.location.origin);
        }).catch(function (error) {
          errorBox.textContent = error.message;
          errorBox.hidden = false;
          errorBox.scrollIntoView({ block: "nearest" });
        }).finally(function () {
          form._entitySaving = false;
          if (createButton) { createButton.disabled = false; }
          buttons.forEach(function (button) { button.disabled = false; });
        });
      });
    });
  }
