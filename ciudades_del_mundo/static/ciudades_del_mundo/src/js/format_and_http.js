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

  function readableResponseText(text, fallback) {
    var value = String(text || "").replace(/^\uFEFF/, "").trim();
    if (!value) {
      return fallback || "Error";
    }
    value = value.replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    return value.slice(0, 320) || fallback || "Error";
  }

  function parseJsonResponse(response) {
    return response.text().then(function (text) {
      var clean = String(text || "").replace(/^\uFEFF/, "").trim();
      var data = null;
      if (clean) {
        try {
          data = JSON.parse(clean);
        } catch (error) {
          var readable = readableResponseText(clean, "La respuesta del servidor no es JSON.");
          if (/^</.test(clean)) {
            readable = "El servidor devolvió una página HTML en vez de JSON. Recarga la página e inténtalo de nuevo. Detalle: " + readable;
          }
          throw new Error(readable);
        }
      } else {
        data = {};
      }
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || data.detail || response.statusText || "Error");
      }
      return data;
    });
  }

  function fetchJson(url) {
    return fetch(url).then(parseJsonResponse);
  }

  function formatDecimalNoGrouping(value, decimals) {
    var number = Number(value || 0);
    var text;
    if (!Number.isFinite(number)) {
      return String(value || 0);
    }
    if (Number.isInteger(number)) {
      return String(number);
    }
    text = number.toFixed(decimals);
    return text.replace(/\.?0+$/, "").replace(".", ",");
  }

  function formatNumber(value) {
    try {
      return formatDecimalNoGrouping(value, 2);
    } catch (error) {
      return String(value || 0);
    }
  }

  function formatPercent(value, total) {
    if (!total) {
      return "0%";
    }
    return formatDecimalNoGrouping((value / total) * 100, 1) + "%";
  }

  function formatPercentNumber(value) {
    var percent = Number(value || 0);
    if (!Number.isFinite(percent)) {
      percent = 0;
    }
    return formatDecimalNoGrouping(Math.max(0, Math.min(100, percent)), 2) + "%";
  }

