  function configTaskKey(status) {
    return String(status || "").replace(/[^a-z]/g, "");
  }

  function configTaskLabel(container, status) {
    container = container || document.body;
    var key = configTaskKey(status);
    if (key === "pending" || key === "none") {
      return container.dataset.pendingLabel || "Por Validar";
    }
    if (key === "validating") {
      return container.dataset.validatingLabel || "Validando";
    }
    if (key === "validated") {
      return container.dataset.validatedLabel || "Validado";
    }
    if (key === "populating") {
      return container.dataset.populatingLabel || "Populando";
    }
    if (key === "clearing") {
      return container.dataset.clearingLabel || "Limpiando";
    }
    if (key === "populated") {
      return container.dataset.populatedLabel || "Populado";
    }
    if (key === "invalid") {
      return container.dataset.invalidLabel || "Fallo";
    }
    if (key === "running") {
      return container.dataset.runningLabel || status;
    }
    if (key === "queued") {
      return container.dataset.queuedLabel || status;
    }
    if (key === "succeeded") {
      return container.dataset.succeededLabel || status;
    }
    if (key === "failed") {
      return container.dataset.failedLabel || "Fallo";
    }
    if (key === "cancelled") {
      return container.dataset.cancelledLabel || "Cancelado";
    }
    if (key === "viewtasklog") {
      return container.dataset.logLabel || "Ver registro";
    }
    return status || "";
  }

  function configTaskClass(status) {
    var key = configTaskKey(status);
    if (key === "none" || !key) {
      key = "pending";
    }
    if (key === "invalid") {
      key = "failed";
    }
    return "config-status-" + key;
  }

  function configTaskIcon(status) {
    var key = configTaskKey(status);
    if (["succeeded", "validated", "populated"].indexOf(key) !== -1) {
      return "\u2713";
    }
    if (key === "cancelled") {
      return "\u00d7";
    }
    if (["failed", "invalid"].indexOf(key) !== -1) {
      return "\u00d7";
    }
    if (key === "queued") {
      return "\u25cc";
    }
    if (["running", "validating", "populating", "clearing"].indexOf(key) !== -1) {
      return "\u2026";
    }
    return "i";
  }

  function normalizeConfigErrorCode(code) {
    return String(code || "").trim();
  }

  function appendConfigErrorCode(parent, code, description, severity) {
    code = normalizeConfigErrorCode(code);
    if (!parent || !code) {
      return null;
    }
    var badge = document.createElement("span");
    badge.className = "config-error-code" + (severity ? " config-code-" + String(severity).toLowerCase() : "");
    badge.textContent = code;
    if (description) {
      badge.title = description;
    }
    parent.appendChild(document.createTextNode(" "));
    parent.appendChild(badge);
    return badge;
  }

  function setElementErrorInfo(element, code, description, severity) {
    code = normalizeConfigErrorCode(code);
    if (!element) {
      return;
    }
    if (code) {
      element.dataset.errorCode = code;
      element.dataset.errorDescription = description || "";
      element.dataset.errorSeverity = severity || "";
      if (description) {
        element.title = description;
      }
    } else {
      delete element.dataset.errorCode;
      delete element.dataset.errorDescription;
      delete element.dataset.errorSeverity;
    }
  }

  function isConfigLoadingStatus(status) {
    return ["validating", "populating", "clearing", "running", "queued"].indexOf(configTaskKey(status)) !== -1;
  }

  function appendConfigLoadingLabel(parent, label) {
    var text = document.createElement("span");
    text.className = "config-status-label";
    text.textContent = label || "";
    parent.appendChild(text);

    var dots = document.createElement("span");
    dots.className = "config-status-dots";
    dots.setAttribute("aria-hidden", "true");
    parent.appendChild(dots);
    setConfigLoadingDotsText(dots);
    ensureConfigLoadingDots(parent);
  }

  function normalizeConfigStatus(status) {
    var key = configTaskKey(status);
    if (!key || key === "none") {
      return "pending";
    }
    if (key === "cancelled") {
      return "pending";
    }
    if (key === "invalid") {
      return "failed";
    }
    return key;
  }


  var CONFIG_TOAST_MAX_VISIBLE = 4;
  var CONFIG_TOAST_DONE_VISIBLE_MS = 2600;
  var CONFIG_TOAST_AFTER_INTERACTION_VISIBLE_MS = 4000;
  var CONFIG_TOAST_SELECTION_RECHECK_MS = 1200;
  var CONFIG_TASK_POLL_MS = 450;
  var CONFIG_ACTIVE_ROW_REFRESH_MS = 1500;
  var CONFIG_LOADING_DOTS_STEP_MS = 600;
  var CONFIG_LOADING_DOTS_CYCLE = ["", ".", "..", "..."];
  var CONFIG_TOAST_ENTER_MS = 540;
  var CONFIG_TOAST_EXIT_MS = 580;
  var CONFIG_TOAST_REPLENISH_DELAY_MS = 280;
  var configToastVisible = [];
  var configToastQueue = [];
  var configLoadingDotsIndex = 0;
  var configLoadingDotsTimer = null;

  function configLoadingDotsText() {
    return CONFIG_LOADING_DOTS_CYCLE[configLoadingDotsIndex] || "";
  }

  function setConfigLoadingDotsText(dot) {
    if (!dot) {
      return;
    }
    dot.textContent = configLoadingDotsText();
  }

  function updateConfigLoadingDots(root) {
    Array.prototype.slice.call((root || document).querySelectorAll(".config-status-dots")).forEach(setConfigLoadingDotsText);
  }

  function ensureConfigLoadingDots(root) {
    updateConfigLoadingDots(root || document);
    if (configLoadingDotsTimer) {
      return;
    }
    configLoadingDotsTimer = window.setInterval(function () {
      var dots = document.querySelectorAll(".config-status-dots");
      if (!dots.length) {
        window.clearInterval(configLoadingDotsTimer);
        configLoadingDotsTimer = null;
        return;
      }
      configLoadingDotsIndex = (configLoadingDotsIndex + 1) % CONFIG_LOADING_DOTS_CYCLE.length;
      updateConfigLoadingDots(document);
    }, CONFIG_LOADING_DOTS_STEP_MS);
  }

  function maybeKeepConfigLoadingCell(taskCell, status, detailUrl, isActive) {
    if (!taskCell || !isConfigLoadingStatus(status)) {
      return false;
    }
    var node = taskCell.firstElementChild;
    if (!node || !node.classList || !node.classList.contains("config-status-loading")) {
      return false;
    }
    if (normalizeConfigStatus(taskCell.dataset.renderedStatus || "") !== status) {
      return false;
    }
    var expectedDetailUrl = detailUrl || "";
    var currentDetailUrl = taskCell.dataset.renderedDetailUrl || "";
    if (currentDetailUrl !== expectedDetailUrl) {
      return false;
    }
    var currentActive = taskCell.dataset.renderedActive === "1";
    if (currentActive !== Boolean(isActive)) {
      return false;
    }
    setConfigLoadingDotsText(node.querySelector(".config-status-dots"));
    ensureConfigLoadingDots(taskCell);
    return true;
  }

  function rememberConfigTaskCell(taskCell, status, detailUrl, isActive) {
    if (!taskCell) {
      return;
    }
    taskCell.dataset.renderedStatus = status || "";
    taskCell.dataset.renderedDetailUrl = detailUrl || "";
    taskCell.dataset.renderedActive = isActive ? "1" : "0";
  }

  function initConfigLoadingDots(root) {
    if ((root || document).querySelector(".config-status-dots")) {
      ensureConfigLoadingDots(root || document);
    }
  }

  function ensureConfigToastStack() {
    var stack = document.querySelector(".config-toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "config-toast-stack";
      stack.setAttribute("aria-live", "polite");
      document.body.appendChild(stack);
    }
    return stack;
  }

  function removeConfigToastFromList(list, toast) {
    var index = list.indexOf(toast);
    if (index >= 0) {
      list.splice(index, 1);
    }
  }

  function configToastCssPx(stack, property, fallback) {
    var value = window.getComputedStyle(stack).getPropertyValue(property);
    var parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function configToastMetrics(stack) {
    return {
      height: configToastCssPx(stack, "--config-toast-height", 136),
      gap: configToastCssPx(stack, "--config-toast-gap", 16)
    };
  }

  function configToastElementHeight(toast, stack) {
    var metrics = configToastMetrics(stack);
    if (!toast || !toast.element) {
      return metrics.height;
    }
    var rect = toast.element.getBoundingClientRect ? toast.element.getBoundingClientRect() : null;
    return Math.ceil((rect && rect.height) || toast.element.offsetHeight || metrics.height);
  }

  function configToastSlotY(stack, index, list) {
    var metrics = configToastMetrics(stack);
    var toasts = list || configToastVisible;
    var y = 0;
    for (var i = 0; i < index; i += 1) {
      y += configToastElementHeight(toasts[i], stack) + metrics.gap;
    }
    return y;
  }

  function layoutVisibleConfigToasts() {
    var stack = ensureConfigToastStack();
    configToastVisible.forEach(function (toast, index) {
      if (!toast || !toast.element || toast.isDismissing) {
        return;
      }
      toast.slotIndex = index;
      toast.element.style.setProperty("--toast-y", configToastSlotY(stack, index, configToastVisible) + "px");
      toast.element.style.setProperty("--toast-x", "0px");
      toast.element.style.setProperty("--toast-opacity", "1");
      toast.element.style.zIndex = String(CONFIG_TOAST_MAX_VISIBLE - index);
    });
  }

  function showNextQueuedConfigToast() {
    while (configToastVisible.length < CONFIG_TOAST_MAX_VISIBLE && configToastQueue.length) {
      showConfigToast(configToastQueue.shift());
    }
  }

  function showConfigToast(toast) {
    if (!toast || toast.isVisible) {
      return;
    }
    var stack = ensureConfigToastStack();
    var slotIndex = configToastVisible.length;
    toast.slotIndex = slotIndex;
    toast.isVisible = true;
    configToastVisible.push(toast);
    toast.element.classList.remove("is-dismissing");
    toast.element.classList.add("is-entering");
    toast.element.style.setProperty("--toast-y", configToastSlotY(stack, slotIndex, configToastVisible) + "px");
    toast.element.style.setProperty("--toast-x", "var(--config-toast-offscreen-x)");
    toast.element.style.setProperty("--toast-opacity", "0");
    stack.appendChild(toast.element);
    layoutVisibleConfigToasts();
    window.setTimeout(function () {
      if (toast.element) {
        toast.element.classList.remove("is-entering");
      }
    }, CONFIG_TOAST_ENTER_MS);
    if (toast.dismissDelay) {
      scheduleConfigToastDismiss(toast, toast.dismissDelay);
    }
  }

  function enqueueConfigToast(toast) {
    if (configToastVisible.length < CONFIG_TOAST_MAX_VISIBLE) {
      showConfigToast(toast);
    } else {
      configToastQueue.push(toast);
    }
  }

  function dismissConfigToast(toast, immediate) {
    if (!toast || toast.isDismissing) {
      return;
    }
    if (toast.dismissTimer) {
      window.clearTimeout(toast.dismissTimer);
      toast.dismissTimer = null;
    }
    var wasVisible = configToastVisible.indexOf(toast) !== -1;
    removeConfigToastFromList(configToastQueue, toast);
    removeConfigToastFromList(configToastVisible, toast);
    toast.isVisible = false;
    function finishRemoval() {
      if (toast.element && toast.element.parentNode) {
        toast.element.parentNode.removeChild(toast.element);
      }
    }
    if (toast.element && toast.element.parentNode && !immediate) {
      var stack = toast.element.parentNode;
      var metrics = configToastMetrics(stack);
      toast.element.classList.remove("is-entering");
      stack.appendChild(toast.element);
      toast.element.style.setProperty("--toast-exit-y", (0 - metrics.height - metrics.gap) + "px");
      toast.element.style.setProperty("--toast-x", "0px");
      toast.element.style.setProperty("--toast-opacity", "1");
      toast.element.style.zIndex = "100";
      toast.isDismissing = true;
      toast.element.classList.add("is-dismissing");
      if (wasVisible) {
        layoutVisibleConfigToasts();
        window.setTimeout(showNextQueuedConfigToast, CONFIG_TOAST_REPLENISH_DELAY_MS);
      }
      window.setTimeout(finishRemoval, CONFIG_TOAST_EXIT_MS);
    } else {
      finishRemoval();
      if (wasVisible) {
        layoutVisibleConfigToasts();
        showNextQueuedConfigToast();
      }
    }
  }

  function configToastHasFocus(toast) {
    return Boolean(toast && toast.element && toast.element.contains(document.activeElement));
  }

  function configToastContainsSelection(toast) {
    if (!toast || !toast.element || !window.getSelection) {
      return false;
    }
    var selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount < 1) {
      return false;
    }
    for (var i = 0; i < selection.rangeCount; i += 1) {
      var range = selection.getRangeAt(i);
      try {
        if (range.intersectsNode(toast.element)) {
          return true;
        }
      } catch (error) {
        var ancestor = range.commonAncestorContainer;
        if (ancestor && toast.element.contains(ancestor.nodeType === 1 ? ancestor : ancestor.parentNode)) {
          return true;
        }
      }
    }
    return false;
  }

  function pauseConfigToastDismiss(toast) {
    if (!toast || !toast.dismissTimer) {
      return;
    }
    window.clearTimeout(toast.dismissTimer);
    toast.dismissTimer = null;
  }

  function resumeConfigToastDismissAfterInteraction(toast) {
    if (!toast || !toast.dismissDelay || !toast.isVisible || toast.isDismissing) {
      return;
    }
    scheduleConfigToastDismiss(toast, CONFIG_TOAST_AFTER_INTERACTION_VISIBLE_MS, true);
  }

  function scheduleConfigToastDismiss(toast, delay, keepDismissDelay) {
    if (!toast) {
      return;
    }
    var effectiveDelay = delay || toast.dismissDelay || CONFIG_TOAST_DONE_VISIBLE_MS;
    if (!keepDismissDelay) {
      toast.dismissDelay = effectiveDelay;
    }
    if (!toast.isVisible) {
      return;
    }
    if (toast.dismissTimer) {
      window.clearTimeout(toast.dismissTimer);
    }
    toast.dismissTimer = window.setTimeout(function () {
      toast.dismissTimer = null;
      if (toast.isInteracting || configToastHasFocus(toast) || configToastContainsSelection(toast)) {
        scheduleConfigToastDismiss(toast, CONFIG_TOAST_SELECTION_RECHECK_MS, true);
        return;
      }
      dismissConfigToast(toast);
    }, effectiveDelay);
  }

  function createConfigToast(title, message, container) {
    var toast = document.createElement("div");
    toast.className = "config-task-toast";
    var header = document.createElement("div");
    header.className = "config-task-toast-header";
    var titleWrap = document.createElement("div");
    titleWrap.className = "config-task-toast-title";
    var icon = document.createElement("span");
    icon.className = "config-task-toast-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = configTaskIcon("running");
    var strong = document.createElement("strong");
    strong.textContent = title;
    titleWrap.appendChild(icon);
    titleWrap.appendChild(strong);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "quiet";
    close.textContent = "\u00d7";
    header.appendChild(titleWrap);
    header.appendChild(close);
    var status = document.createElement("p");
    status.className = "muted config-task-toast-status";
    status.textContent = message || "";
    var detailLink = document.createElement("a");
    detailLink.className = "button secondary config-task-log-link";
    detailLink.textContent = configTaskLabel(container || document.body, "view_task_log") || "Ver registro";
    detailLink.target = "_blank";
    detailLink.rel = "noopener";
    detailLink.hidden = true;
    toast.appendChild(header);
    toast.appendChild(status);
    toast.appendChild(detailLink);
    var toastData = {
      element: toast,
      status: status,
      detailLink: detailLink,
      icon: icon,
      isVisible: false,
      dismissDelay: 0,
      dismissTimer: null,
      isDismissing: false,
      isInteracting: false
    };
    toast.addEventListener("mouseenter", function () {
      toastData.isInteracting = true;
      pauseConfigToastDismiss(toastData);
    });
    toast.addEventListener("mouseleave", function () {
      toastData.isInteracting = false;
      resumeConfigToastDismissAfterInteraction(toastData);
    });
    toast.addEventListener("focusin", function () {
      toastData.isInteracting = true;
      pauseConfigToastDismiss(toastData);
    });
    toast.addEventListener("focusout", function () {
      window.setTimeout(function () {
        if (!configToastHasFocus(toastData)) {
          toastData.isInteracting = false;
          resumeConfigToastDismissAfterInteraction(toastData);
        }
      }, 0);
    });
    toast.addEventListener("copy", function () {
      resumeConfigToastDismissAfterInteraction(toastData);
    });
    close.addEventListener("click", function () { dismissConfigToast(toastData, true); });
    enqueueConfigToast(toastData);
    return toastData;
  }

  function setConfigToastStatus(toast, status, message, errorCode, errorDescription, errorSeverity) {
    if (!toast) {
      return;
    }
    if (toast.dismissTimer) {
      window.clearTimeout(toast.dismissTimer);
      toast.dismissTimer = null;
      toast.dismissDelay = 0;
    }
    if (toast.icon) {
      toast.icon.textContent = configTaskIcon(status);
    }
    if (toast.status) {
      var code = normalizeConfigErrorCode(errorCode || "");
      toast.status.textContent = code ? (message || status || "") + " [" + code + "]" : (message || status || "");
      setElementErrorInfo(toast.status, code, errorDescription || "", errorSeverity || "");
    }
  }

