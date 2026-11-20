  function searchWikidataEntity(query, languages) {
    var variants = wikidataQueryVariants(query);
    var searchLanguages = uniqueValues(languages.concat(["en", "es"]));

    function attempt(variantIndex, languageIndex) {
      if (variantIndex >= variants.length) {
        return Promise.reject(new Error("no wikidata result"));
      }
      if (languageIndex >= searchLanguages.length) {
        return attempt(variantIndex + 1, 0);
      }
      var url = "https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&origin=*&limit=1&language=" +
        encodeURIComponent(searchLanguages[languageIndex]) + "&search=" + encodeURIComponent(variants[variantIndex]);
      return fetchJson(url).then(function (search) {
        if (search.search && search.search.length) {
          return search.search[0].id;
        }
        return attempt(variantIndex, languageIndex + 1);
      });
    }

    return attempt(0, 0);
  }

  function fetchWikidataEntities(ids, props, languages) {
    ids = uniqueValues(ids);
    if (!ids.length) {
      return Promise.resolve({});
    }
    var chunks = [];
    for (var start = 0; start < ids.length; start += 50) {
      chunks.push(ids.slice(start, start + 50));
    }
    return Promise.all(chunks.map(function (chunk) {
      var url = "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&origin=*&ids=" +
        encodeURIComponent(chunk.join("|")) + "&props=" + encodeURIComponent(props) +
        "&languages=" + encodeURIComponent((languages || ["en"]).join("|"));
      return fetchJson(url).then(function (data) {
        return data.entities || {};
      });
    })).then(function (items) {
      return items.reduce(function (merged, entities) {
        Object.keys(entities || {}).forEach(function (id) {
          merged[id] = entities[id];
        });
        return merged;
      }, {});
    });
  }

  function claimEntityIds(claims, property) {
    return (claims[property] || []).map(function (claim) {
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!value || value["entity-type"] !== "item") {
        return "";
      }
      return value.id || ("Q" + value["numeric-id"]);
    }).filter(Boolean);
  }

  function claimTextValues(claims, property, languages) {
    var values = (claims[property] || []).map(function (claim) {
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!value) {
        return null;
      }
      if (typeof value === "string") {
        return { language: "", text: value };
      }
      return {
        language: value.language || "",
        text: value.text || value.value || ""
      };
    }).filter(function (value) {
      return value && value.text;
    });
    values.sort(function (left, right) {
      var leftIndex = languages.indexOf(left.language);
      var rightIndex = languages.indexOf(right.language);
      leftIndex = leftIndex === -1 ? 999 : leftIndex;
      rightIndex = rightIndex === -1 ? 999 : rightIndex;
      return leftIndex - rightIndex;
    });
    return uniqueValues(values.map(function (value) {
      return value.text;
    }));
  }

  function labelForEntity(entity, languages) {
    if (!entity || !entity.labels) {
      return "";
    }
    for (var index = 0; index < languages.length; index += 1) {
      var label = entity.labels[languages[index]];
      if (label && label.value) {
        return label.value;
      }
    }
    return "";
  }

  function appendDefinition(list, label, value, href) {
    if (!list || !value) {
      return false;
    }
    var dt = document.createElement("dt");
    dt.textContent = label;
    var dd = document.createElement("dd");
    if (href) {
      var link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = value;
      dd.appendChild(link);
    } else {
      dd.textContent = value;
    }
    list.appendChild(dt);
    list.appendChild(dd);
    return true;
  }

  function renderTranslations(entity, languages, list) {
    var added = false;
    if (!entity || !entity.labels || !list) {
      return false;
    }
    languages.forEach(function (language) {
      var label = entity.labels[language];
      if (label && label.value) {
        added = appendDefinition(list, language.toUpperCase(), label.value) || added;
      }
    });
    return added;
  }

  function renderImageSlots(claims, slots, status, noImagesMessage) {
    var shown = false;
    var slotKinds = {
      P41: "flag",
      P94: "coat",
      P242: "locator"
    };
    Object.keys(slots).forEach(function (property) {
      var slot = slots[property];
      var claim = claims[property] && claims[property][0];
      var value = claim && claim.mainsnak && claim.mainsnak.datavalue && claim.mainsnak.datavalue.value;
      if (!slot || !value) {
        return;
      }
      var kind = slotKinds[property] || "image";
      var placeholder = ensureVisualPlaceholder(slot, kind);
      var image = slot.querySelector("img");
      if (!image) {
        image = document.createElement("img");
        slot.appendChild(image);
      }
      image.hidden = false;
      image.style.visibility = "hidden";
      image.onload = function () {
        image.hidden = false;
        image.style.visibility = "";
        if (placeholder) {
          placeholder.hidden = true;
        }
      };
      image.onerror = function () {
        image.style.visibility = "";
        hideBrokenVisualImage(image);
        ensureVisualPlaceholder(slot, kind);
      };
      image.src = commonsFileUrl(value, 420);
      image.alt = value;
      image.onclick = function () {
        openImagePreview({
          filename: value,
          label: value,
          kind: kind,
          previewSrc: commonsFileUrl(value, 900),
          fullSrc: commonsFileUrl(value, 1600),
          detailUrl: visualIdentityDetailUrl(kind, value)
        }, value, kind);
      };
      bindMiddleClickNavigation(image, function () {
        return visualIdentityDetailUrl(kind, value) || commonsFileUrl(value, 1600);
      });
      image.style.cursor = "pointer";
      image.title = "Ver imagen";
      slot.hidden = false;
      shown = true;
    });
    if (status) {
      if (shown) {
        status.hidden = true;
      } else {
        setInlineStatus(status, noImagesMessage, false);
      }
    }
    return shown;
  }

  function renderClaimFacts(claims, entities, languages, list, labels) {
    var added = false;
    [
      ["P17", labels.country],
      ["P36", labels.capital],
      ["P131", labels.location]
    ].forEach(function (item) {
      var names = claimEntityIds(claims, item[0]).map(function (id) {
        return labelForEntity(entities[id], languages);
      }).filter(Boolean);
      if (names.length) {
        added = appendDefinition(list, item[1], uniqueValues(names).join(", ")) || added;
      }
    });
    return added;
  }

  function parseRelatedPlaces(raw) {
    if (!raw) {
      return [];
    }
    try {
      var places = JSON.parse(raw);
      return Array.isArray(places) ? places : [];
    } catch (error) {
      return [];
    }
  }

  function renderRelatedPlaces(places, languages, list) {
    if (!places.length || !list) {
      return Promise.resolve(false);
    }
    var rendered = false;
    var searches = places.slice(0, 8).map(function (place) {
      return searchWikidataEntity(place.query || place.name, languages)
        .then(function (id) {
          return fetchWikidataEntities([id], "labels", languages).then(function (entities) {
            var entity = entities[id];
            var label = labelForEntity(entity, languages);
            if (!label) {
              return false;
            }
            rendered = appendDefinition(
              list,
              place.kind || place.name,
              label,
              "https://www.wikidata.org/wiki/" + id
            ) || rendered;
            return true;
          });
        })
        .catch(function () {
          return false;
        });
    });
    return Promise.all(searches).then(function () {
      return rendered;
    });
  }

  function initVisualIdentity() {
    var panel = document.querySelector("[data-visual-identity]");
    if (!panel) {
      return;
    }
    var query = panel.dataset.query;
    var languages = wikidataLanguages();
    var status = panel.querySelector("[data-visual-status]");
    var knowledgeStatus = panel.querySelector("[data-knowledge-status]");
    var translationList = panel.querySelector("[data-translation-list]");
    var factList = panel.querySelector("[data-fact-list]");
    var relatedList = panel.querySelector("[data-related-place-list]");
    var noImagesMessage = panel.dataset.noImagesMessage || "No se encontraron imagenes automaticas. Usa las busquedas externas.";
    var noWikidataMessage = panel.dataset.noWikidataMessage || "No se encontraron traducciones o capitales automaticas en Wikidata.";
    var slots = {
      P41: panel.querySelector("[data-visual-slot='flag']"),
      P94: panel.querySelector("[data-visual-slot='coat']"),
      P242: panel.querySelector("[data-visual-slot='locator']")
    };
    var labels = {
      country: panel.dataset.countryLabel || "Pais en Wikidata",
      capital: panel.dataset.capitalLabel || "Capitales en Wikidata",
      location: panel.dataset.locationLabel || "Region superior en Wikidata",
      source: panel.dataset.sourceLabel || "Ficha Wikidata"
    };
    var relatedPlaces = parseRelatedPlaces(panel.dataset.relatedPlaces);
    if (status) {
      setInlineStatus(status, status.textContent || "Buscando imagenes en Wikidata...", true);
    }
    if (knowledgeStatus) {
      setInlineStatus(knowledgeStatus, knowledgeStatus.textContent || "Buscando nombres traducidos, paises y capitales...", true);
    }

    var mainKnowledge = searchWikidataEntity(query, languages)
      .then(function (id) {
        return fetchWikidataEntities([id], "claims|labels", languages).then(function (entities) {
          var entity = entities[id];
          var claims = entity && entity.claims ? entity.claims : {};
          var targetIds = claimEntityIds(claims, "P17")
            .concat(claimEntityIds(claims, "P36"))
            .concat(claimEntityIds(claims, "P131"));

          renderImageSlots(claims, slots, status, noImagesMessage);

          return fetchWikidataEntities(targetIds, "labels", languages).then(function (targetEntities) {
            var translated = renderTranslations(entity, languages, translationList);
            var facts = renderClaimFacts(claims, targetEntities, languages, factList, labels);
            var source = appendDefinition(factList, labels.source, id, "https://www.wikidata.org/wiki/" + id);
            return translated || facts || source;
          });
        });
      })
      .catch(function () {
        if (status) {
          setInlineStatus(status, noImagesMessage, false);
        }
        return false;
      });

    var relatedKnowledge = renderRelatedPlaces(relatedPlaces, languages, relatedList);
    Promise.all([mainKnowledge, relatedKnowledge]).then(function (results) {
      if (!knowledgeStatus) {
        return;
      }
      if (results.some(Boolean)) {
        knowledgeStatus.hidden = true;
      } else {
        setInlineStatus(knowledgeStatus, noWikidataMessage, false);
      }
    }).catch(function () {
      if (knowledgeStatus) {
        setInlineStatus(knowledgeStatus, noWikidataMessage, false);
      }
    });
  }

