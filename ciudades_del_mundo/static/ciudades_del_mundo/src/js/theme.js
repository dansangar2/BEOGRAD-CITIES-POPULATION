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


