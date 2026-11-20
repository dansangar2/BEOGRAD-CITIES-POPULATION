  function commonsFileUrl(filename, width) {
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" +
      encodeURIComponent(filename).replace(/%20/g, "_") +
      "?width=" + (width || 360);
  }

  function commonsFilePageUrl(filename) {
    return "https://commons.wikimedia.org/wiki/File:" +
      encodeURIComponent(filename).replace(/%20/g, "_");
  }

  function encodeIdentityPath(value) {
    return String(value || "")
      .replace(/^\/+/, "")
      .split("/")
      .map(function (part) { return encodeURIComponent(part); })
      .join("/");
  }

  function visualIdentityDetailUrl(kind, filename) {
    return "/identity/" + encodeURIComponent(kind || "image") + "/" + encodeIdentityPath(filename) + "/";
  }

  function visualIdentityEntityUrl(kind, entityType, entityKey) {
    if (!entityType || !entityKey) {
      return "";
    }
    return "/identity/" + encodeURIComponent(kind || "image") +
      "/entity/" + encodeURIComponent(entityType) + "/" + encodeIdentityPath(entityKey) + "/";
  }

  function assetIdentityDetailUrl(asset, kind) {
    if (!asset) {
      return "";
    }
    var entityUrl = visualIdentityEntityUrl(
      kind || asset.kind || "image",
      asset.entity_type || "",
      asset.entity_key || ""
    );
    if (entityUrl) {
      return entityUrl;
    }
    var target = asset.commons_filename || asset.local_path || "";
    if (target) {
      return visualIdentityDetailUrl(kind || asset.kind || "image", target);
    }
    return asset.source_url || assetImageUrl(asset, 1600) || "";
  }

  function openAssetDetailNewWindow(asset, kind, fallbackUrl) {
    var url = assetIdentityDetailUrl(asset, kind) || fallbackUrl || assetImageUrl(asset, 1600);
    if (url) {
      window.open(url, "_blank", "noopener");
    }
  }

  function assetImageUrl(asset, width) {
    if (!asset) {
      return "";
    }
    if (asset.commons_filename) {
      return commonsFileUrl(asset.commons_filename, width || 360);
    }
    if (asset.remote_url) {
      return asset.remote_url;
    }
    if (asset.image_url && !/^\/media\//.test(String(asset.image_url))) {
      return asset.image_url;
    }
    if (asset.local_url) {
      return asset.local_url;
    }
    if (asset.image_url) {
      return asset.image_url;
    }
    return "";
  }

  function storedVisualAssetImageUrl(asset) {
    if (!asset) {
      return "";
    }
    return asset.image_url || asset.remote_url || asset.local_url || "";
  }

  function openStoredAssetPreview(asset, fallbackLabel, kind) {
    var previewSrc = storedVisualAssetImageUrl(asset);
    if (!previewSrc) {
      return;
    }
    openImagePreview({
      filename: asset.commons_filename || asset.local_path || "",
      label: fallbackLabel || asset.entity_name || asset.commons_filename || asset.local_path || kind || "image",
      kind: kind || asset.kind || "image",
      previewSrc: previewSrc,
      fullSrc: asset.remote_url || asset.image_url || previewSrc,
      detailUrl: assetIdentityDetailUrl(asset, kind || asset.kind || "image") || previewSrc
    }, fallbackLabel, kind);
  }

  function ensureVisualPlaceholder(container, kind) {
    if (!container) {
      return null;
    }
    var placeholder = container.querySelector(".visual-placeholder") || container.querySelector(".muted");
    if (!placeholder) {
      placeholder = document.createElement("span");
      container.appendChild(placeholder);
    }
    placeholder.className = "visual-placeholder visual-placeholder-" + (kind || "flag");
    placeholder.setAttribute("aria-hidden", "true");
    placeholder.hidden = false;
    return placeholder;
  }

  function hideBrokenVisualImage(image) {
    if (!image) {
      return;
    }
    image.hidden = true;
    image.removeAttribute("src");
    image.onclick = null;
  }

  function openAssetPreview(asset, fallbackLabel, kind) {
    if (!asset) {
      return;
    }
    var previewSrc = assetImageUrl(asset, 900);
    var fullSrc = assetImageUrl(asset, 1600) || previewSrc;
    if (!previewSrc && asset.source_url) {
      previewSrc = asset.source_url;
      fullSrc = asset.source_url;
    }
    if (!previewSrc) {
      return;
    }
    openImagePreview({
      filename: asset.commons_filename || asset.local_path || "",
      label: fallbackLabel || asset.entity_name || asset.commons_filename || asset.local_path || kind || "image",
      kind: kind || asset.kind || "image",
      previewSrc: previewSrc,
      fullSrc: fullSrc,
      detailUrl: assetIdentityDetailUrl(asset, kind || asset.kind || "image") || fullSrc
    }, fallbackLabel, kind);
  }

  function openImagePreview(options, label, kind) {
    if (!options) {
      return;
    }
    if (typeof options === "string") {
      options = {
        filename: options,
        label: label,
        kind: kind
      };
    }
    var filename = options.filename || "";
    var previewSrc = options.previewSrc || (filename ? commonsFileUrl(filename, 900) : "");
    var fullSrc = options.fullSrc || (filename ? commonsFileUrl(filename, 1600) : previewSrc);
    var detailUrl = options.detailUrl || (filename ? visualIdentityDetailUrl(options.kind || "image", filename) : "");
    if (!previewSrc) {
      return;
    }
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
    title.textContent = options.label || filename || options.kind || "image";
    var closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "secondary";
    closeButton.setAttribute("aria-label", "Cerrar");
    closeButton.textContent = "x";
    header.appendChild(title);
    header.appendChild(closeButton);

    var image = document.createElement("img");
    image.src = previewSrc;
    image.alt = options.label || filename || options.kind || "";
    image.loading = "eager";

    var actions = document.createElement("div");
    actions.className = "image-preview-actions";
    var pageButton = document.createElement("a");
    pageButton.className = "button secondary";
    pageButton.href = detailUrl || fullSrc;
    pageButton.target = "_blank";
    pageButton.rel = "noopener";
    pageButton.textContent = "Ficha";
    var fullButton = document.createElement("a");
    fullButton.className = "button";
    fullButton.href = fullSrc;
    fullButton.target = "_blank";
    fullButton.rel = "noopener";
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

    image.addEventListener("click", function () {
      openNavigationUrl(detailUrl || fullSrc, true);
    });
    bindMiddleClickNavigation(image, function () {
      return detailUrl || fullSrc || "";
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

