package org.beograd.application;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Locale;
import org.beograd.domain.CountryAggregate;
import org.beograd.domain.SourceCountry;
import org.beograd.ports.GeographyRepository;
import org.beograd.ports.Texts;

/** Use cases contain no framework types or persistence implementations. */
public final class Geography {
    private final GeographyRepository repository;
    private final Texts texts;
    public Geography(GeographyRepository repository, Texts texts) { this.repository=repository; this.texts=texts; }

    public Map<String,Object> countrySummary(String language,boolean assets,String detailPrefix) {
        var countries=new ArrayList<Map<String,Object>>();
        for(var c:repository.sourceCountries(language)) {
            SourceCountry.Root root=c.roots().stream().filter(r->r.code().equals(c.code())).findFirst().orElse(null);
            if(root==null)root=c.roots().stream().filter(r->r.annotations().toLowerCase(Locale.ROOT).contains("citypopulation section: infosection")).findFirst().orElse(null);
            if(root==null&&!texts.wikidata(c.code()).isEmpty())root=c.roots().stream().filter(r->r.wikidata().equals(texts.wikidata(c.code()))).findFirst().orElse(null);
            if(root==null&&c.roots().size()==1)root=c.roots().getFirst();
            BigInteger pop=root==null?c.roots().stream().map(SourceCountry.Root::population).reduce(BigInteger.ZERO,BigInteger::add):root.population();
            if(pop.signum()==0)continue;
            Double area=root==null?(c.roots().stream().noneMatch(r->r.area()!=null)?null:c.roots().stream().filter(r->r.area()!=null).map(SourceCountry.Root::area).reduce(java.math.BigDecimal.ZERO,java.math.BigDecimal::add).doubleValue()):(root.area()==null?null:root.area().doubleValue());
            var row=new LinkedHashMap<String,Object>();
            row.put("code",c.code());row.put("label",c.reviewedName().isEmpty()?texts.country(c.code(),root==null?"":root.name(),language):c.reviewedName());
            row.put("population",pop);row.put("area_km2",area);row.put("wikidata_id",texts.wikidata(c.code()));row.put("detail_url",detailPrefix+c.code()+"/");
            countries.add(row);
        }
        countries.sort((a,b)->((BigInteger)b.get("population")).compareTo((BigInteger)a.get("population")));
        if(assets) {
            var stored=repository.countryAssets(countries.stream().map(row->(String)row.get("code")).toList());
            for(var row:countries) {
                var visual=stored.getOrDefault((String)row.get("code"),Map.of());
                row.put("visual_assets",visual);row.put("flag_asset",visual.getOrDefault("flag",Map.of()));row.put("seal_asset",visual.getOrDefault("seal",Map.of()));
                row.put("coat_asset",visual.getOrDefault("coat",visual.getOrDefault("seal",Map.of())));
            }
        }
        BigInteger total=countries.stream().map(row->(BigInteger)row.get("population")).reduce(BigInteger.ZERO,BigInteger::add);
        double areaTotal=0;for(var row:countries)if(row.get("area_km2")!=null)areaTotal+=(Double)row.get("area_km2");
        var items=countries.stream().map(row->ordered("key",row.get("code"),"label",row.get("label"),"value",row.get("population"),"detail_url",row.get("detail_url"))).toList();
        var areaRows=new ArrayList<>(countries);areaRows.sort(Comparator.comparingDouble(row->-((Number)(row.get("area_km2")==null?0:row.get("area_km2"))).doubleValue()));
        var areaItems=areaRows.stream().filter(row->row.get("area_km2")!=null&&((Double)row.get("area_km2"))!=0).map(row->ordered("key",row.get("code"),"label",row.get("label"),"value",row.get("area_km2"),"detail_url",row.get("detail_url"))).toList();
        Object areaTotalValue=areaItems.isEmpty()?BigInteger.ZERO:Double.valueOf(areaTotal);
        return ordered("type","donut","total",total,"countries",countries,"items",items,"charts",ordered("population",ordered("type","donut","total",total,"items",items),"area",ordered("type","donut","total",areaTotalValue,"items",areaItems)));
    }

    public Map<String, Object> derivedSummary(String language) {
        List<CountryAggregate> countries = new ArrayList<>(repository.derivedCountries());
        // Stable ordering preserves the SQL country-code order when populations tie.
        countries.sort(Comparator.comparing(CountryAggregate::population).reversed());
        var maximum = countries.stream().map(CountryAggregate::population).max(Comparator.naturalOrder()).orElse(BigInteger.ZERO);
        List<Map<String,Object>> items = new ArrayList<>();
        var reviewedNames=repository.reviewedCountryNames(countries.stream().filter(c->c.name()==null).map(CountryAggregate::code).toList(),language);
        for (var c:countries.subList(0,Math.min(10,countries.size()))) {
            String label = texts.country(c.code(), c.name(), language);
            if (c.name()==null) {
                String reviewed = reviewedNames.getOrDefault(c.code().strip().toLowerCase(Locale.ROOT),"");
                if (!reviewed.isBlank()) label=reviewed;
            }
            var row = new LinkedHashMap<String,Object>();
            row.put("label",label); row.put("value",c.population());
            // Python round uses ties-to-even; Java Math.round does not.
            row.put("width",chartWidth(c.population(),maximum));
            items.add(row);
        }
        return ordered("type","bar","items",items);
    }

    private static Map<String,Object> ordered(Object... pairs) {
        // Browser cache fingerprints must remain stable across JVM restarts.
        var result=new LinkedHashMap<String,Object>();
        for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);
        return result;
    }

    private static BigInteger chartWidth(BigInteger value,BigInteger maximum) {
        if(maximum.signum()==0)return BigInteger.ZERO;
        double ratio=new java.math.BigDecimal(value.multiply(BigInteger.valueOf(100)))
            .divide(new java.math.BigDecimal(maximum),new java.math.MathContext(40,java.math.RoundingMode.HALF_EVEN)).doubleValue();
        // Exact binary float value reproduces Python round(), including large/negative data.
        return new java.math.BigDecimal(ratio).setScale(0,java.math.RoundingMode.HALF_EVEN).toBigIntegerExact().max(BigInteger.TWO);
    }

    public record Csv(String filename, String text) {}
    private static final List<String> COLUMNS=List.of(
        "id","country_code","code","name","level","parent_id","parent_code","parent_name","parent_level","children_count",
        "entity_type","raw_entity_type","city_merge_status","area_km2","density","pop_latest","pop_latest_date","last_census_year",
        "url","data_wd","annotations","most_populate_city_id","flag_status","flag_wikidata_id","flag_commons_filename","flag_local_path",
        "coat_status","coat_wikidata_id","coat_commons_filename","coat_local_path","created_at","updated_at");

    public Csv configCsv(String rawSlug) {
        String slug=rawSlug.strip().toLowerCase(Locale.ROOT);
        String country=repository.configCountry(slug);
        var out=new StringBuilder("\ufeff");
        writeCsvRow(out,COLUMNS);
        for (var area:repository.csvAreas(country)) {
            List<String> cells=new ArrayList<>();
            for (String key:COLUMNS) {
                Object value=area.get(key);
                String cell=value==null ? "" : value.toString();
                if (!cell.isEmpty() && (key.equals("area_km2") || key.equals("density")))
                    cell=new java.math.BigDecimal(cell).setScale(key.equals("area_km2")?2:4,java.math.RoundingMode.HALF_EVEN).toPlainString();
                if (key.equals("pop_latest_date")) cell=cell.replace(" ","T");
                if (key.equals("created_at") || key.equals("updated_at")) {
                    cell=cell.replace('T',' ');
                    int fraction=cell.indexOf('.');
                    if (fraction>=0) cell=cell.substring(0,fraction);
                    if (!cell.isEmpty() && !cell.endsWith("+00:00")) cell+="+00:00";
                }
                cells.add(cell);
            }
            writeCsvRow(out,cells);
        }
        return new Csv((country.isEmpty()?slug:country)+"_admin_areas.csv",out.toString());
    }
    private static void writeCsvRow(StringBuilder out,List<String> cells) {
        for (int i=0;i<cells.size();i++) {
            if(i>0)out.append(',');
            String cell=cells.get(i);
            if(cell.contains(",")||cell.contains("\"")||cell.contains("\r")||cell.contains("\n")) out.append('"').append(cell.replace("\"","\"\"")).append('"');
            else out.append(cell);
        }
        out.append("\r\n");
    }
}
