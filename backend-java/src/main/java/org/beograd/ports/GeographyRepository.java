package org.beograd.ports;

import java.util.List;
import java.util.Map;
import org.beograd.domain.CountryAggregate;
import org.beograd.domain.SourceCountry;

public interface GeographyRepository {
    List<CountryAggregate> derivedCountries();
    Map<String,String> reviewedCountryNames(List<String> countries, String language);
    String configCountry(String slug);
    List<Map<String, Object>> csvAreas(String country);
    List<SourceCountry> sourceCountries(String language);
    Map<String,Map<String,Object>> countryAssets(List<String> countries);
}
