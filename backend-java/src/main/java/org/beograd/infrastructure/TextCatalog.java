package org.beograd.infrastructure;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.io.IOException;
import org.beograd.ports.Texts;

/** Shipped gettext strings; Python is not called at request time. */
public final class TextCatalog implements Texts {
    private final JsonNode data;
    public TextCatalog(String override) {
        try (var in = override == null || override.isBlank()
                ? TextCatalog.class.getResourceAsStream("/text-catalog.json")
                : Files.newInputStream(Path.of(override))) {
            if (in == null) throw new IllegalArgumentException("Missing bundled text-catalog.json resource");
            data = new ObjectMapper().readTree(in);
        } catch (IOException ex) { throw new IllegalArgumentException("Cannot read text catalog", ex); }
    }
    @Override public String translate(String message, String language) {
        String value = data.path("languages").path(language).path(message).asText("");
        if (value.isEmpty() && language.contains("-")) value = data.path("languages").path(language.split("-",2)[0]).path(message).asText("");
        return value.isEmpty() ? message : value;
    }
    @Override public String country(String code, String name, String language) {
        if (language.equals("es")) {
            String mapped = data.path("countries_es").path(code.toLowerCase(java.util.Locale.ROOT)).asText("");
            if (!mapped.isEmpty()) return mapped;
        }
        String value = name == null || name.isBlank() ? code.strip() : name.strip();
        if(code.equals("spain"))value=data.path("spain_names").path(language).path(value).asText(value);
        return translate(value.replace('_',' '), language);
    }
    @Override public String wikidata(String country) { return data.path("wikidata").path(country).asText(""); }
}
