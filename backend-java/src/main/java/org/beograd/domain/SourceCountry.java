package org.beograd.domain;

import java.math.BigInteger;
import java.util.List;

public record SourceCountry(String code, String reviewedName, List<Root> roots) {
    public record Root(String id,String code,String name,String annotations,String wikidata,BigInteger population,java.math.BigDecimal area) {}
}
