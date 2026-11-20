package org.beograd.ports;

public interface Texts {
    String translate(String message, String language);
    String country(String country, String name, String language);
    String wikidata(String country);
}
