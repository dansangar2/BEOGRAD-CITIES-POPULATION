package org.beograd.infrastructure;

import java.math.BigInteger;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.beograd.domain.CountryAggregate;
import org.beograd.domain.SourceCountry;
import org.beograd.ports.GeographyRepository;
import org.beograd.ports.MissingData;

public final class SqliteGeography implements GeographyRepository {
    private final SqliteDatabase db;
    public SqliteGeography(SqliteDatabase db) { this.db = db; }

    @Override public List<SourceCountry> sourceCountries(String language) {
        var countries=new LinkedHashMap<String,List<SourceCountry.Root>>();
        var names=new java.util.HashMap<String,String>();
        try(var c=db.open();var s=c.createStatement()) {
            // Only roots contribute to the summary. Enumerating codes from every
            // subdivision forces a table scan and can exceed the UI timeout.
            try(var r=s.executeQuery("SELECT id,country_code,code,name,annotations,data_wd,pop_latest,area_km2 FROM ciudades_del_mundo_adminarea WHERE level=0 AND parent_id IS NULL AND city_merge_status<>3 ORDER BY country_code,name")) {
                while(r.next()) {
                    String pop=r.getString(7),area=r.getString(8);
                    countries.computeIfAbsent(r.getString(2),key->new ArrayList<>()).add(new SourceCountry.Root(r.getString(1),r.getString(3),r.getString(4),r.getString(5),r.getString(6),pop==null?BigInteger.ZERO:new BigInteger(pop),area==null?null:new java.math.BigDecimal(area).setScale(2,java.math.RoundingMode.HALF_EVEN)));
                }
            }
        }catch(SQLException ex){throw SqliteDatabase.failure(ex);}
        var translated=reviewedCountryNames(List.copyOf(countries.keySet()),language);
        for(String code:countries.keySet())names.put(code,translated.getOrDefault(code.strip().toLowerCase(java.util.Locale.ROOT),""));
        return countries.entrySet().stream().map(e->new SourceCountry(e.getKey(),names.getOrDefault(e.getKey(),""),e.getValue())).toList();
    }
    @Override public Map<String,Map<String,Object>> countryAssets(List<String> countries) {
        return new StoredAssets(db).countries(countries);
    }

    @Override public List<CountryAggregate> derivedCountries() {
        var grouped = new LinkedHashMap<String, BigInteger>();
        var names = new LinkedHashMap<String, String>();
        try (var c = db.open(); var s = c.createStatement()) {
            // Same SQLite aggregation/range as Django; never fetch every source row for metadata.
            try(var r=s.executeQuery("SELECT country_code,SUM(pop_latest) FROM ciudades_del_mundo_nuevoadminarea GROUP BY country_code ORDER BY country_code")) {
                while(r.next())grouped.put(r.getString(1),r.getString(2)==null?BigInteger.ZERO:new BigInteger(r.getString(2)));
            }
            try(var r=s.executeQuery("SELECT country_code,name FROM ciudades_del_mundo_nuevoadminarea WHERE level=0 AND parent_id IS NULL ORDER BY country_code,name,id")) {
                while(r.next())names.put(r.getString(1),r.getString(2));
            }
        } catch (SQLException ex) { throw SqliteDatabase.failure(ex); }
        return grouped.entrySet().stream().map(e -> new CountryAggregate(e.getKey(), names.get(e.getKey()), e.getValue())).toList();
    }

    @Override public Map<String,String> reviewedCountryNames(List<String> countries, String language) {
        var keys=countries.stream().map(code->code.strip().toLowerCase(java.util.Locale.ROOT)).distinct().toList();
        if(keys.isEmpty())return Map.of();
        String slots=String.join(",",java.util.Collections.nCopies(keys.size(),"?"));
        try(var c=db.open();var s=c.prepareStatement("SELECT subject_key,language,text FROM ciudades_del_mundo_dynamictranslation WHERE subject_type='country' AND subject_key IN ("+slots+") AND country_code=subject_key AND field='name' AND language IN (?,?) AND is_active=1 AND needs_review=0 ORDER BY id")) {
            int i=1;for(String key:keys)s.setString(i++,key);
            s.setString(i++,language);s.setString(i,language.split("-",2)[0]);
            var byLanguage=new java.util.HashMap<String,String>();
            try(var r=s.executeQuery()){while(r.next())byLanguage.putIfAbsent(r.getString(1)+"/"+r.getString(2),r.getString(3));}
            var result=new java.util.HashMap<String,String>();
            for(String key:keys) {
                String name=byLanguage.getOrDefault(key+"/"+language,"");
                if(name.isEmpty())name=byLanguage.getOrDefault(key+"/"+language.split("-",2)[0],"");
                result.put(key,name);
            }
            return result;
        } catch (SQLException ex) { throw SqliteDatabase.failure(ex); }
    }

    @Override public String configCountry(String slug) {
        try (var c = db.open(); var s = c.prepareStatement("SELECT country_code,content FROM ciudades_del_mundo_scrapingconfig WHERE slug = ?")) {
            s.setString(1, slug);
            try (var r = s.executeQuery()) {
                if (!r.next()) throw new MissingData("No existe la configuracion '%(slug)s'.");
                String code = r.getString(1);
                if (code != null && !code.isEmpty()) return code;
                var toml=org.tomlj.Toml.parse(r.getString(2));
                return toml.hasErrors() || !(toml.get("country_code") instanceof String fromContent) || fromContent.isEmpty() ? slug : fromContent;
            }
        } catch (SQLException ex) { throw SqliteDatabase.failure(ex); }
    }

    @Override public List<Map<String, Object>> csvAreas(String country) {
        String sql = """
            SELECT a.*, p.code AS parent_code, p.name AS parent_name, p.level AS parent_level,
              (SELECT COUNT(*) FROM ciudades_del_mundo_adminarea ch WHERE ch.parent_id=a.id) AS children_count
            FROM ciudades_del_mundo_adminarea a
            LEFT JOIN ciudades_del_mundo_adminarea p ON p.id=a.parent_id
            WHERE a.country_code=? ORDER BY a.level,a.parent_id,a.name,a.code
            """;
        try (var c = db.open(); var s = c.prepareStatement(sql)) {
            s.setString(1, country);
            var result = new ArrayList<Map<String, Object>>();
            try (var r = s.executeQuery()) {
                var meta = r.getMetaData();
                while (r.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    for (int i=1; i<=meta.getColumnCount(); i++) row.put(meta.getColumnLabel(i), r.getObject(i));
                    result.add(row);
                }
            }
            // Assets are fetched once for the selected country, never once per area.
            try (var assets = c.prepareStatement("SELECT entity_key,kind,status,wikidata_id,commons_filename,local_path FROM ciudades_del_mundo_visual_asset WHERE country_code=? AND entity_type='admin_area' AND kind IN ('flag','coat')")) {
                assets.setString(1, country);
                Map<String, Map<String,Object>> byId = new java.util.HashMap<>();
                for (var row:result) byId.put(String.valueOf(row.get("id")), row);
                try (var r=assets.executeQuery()) {
                    while (r.next()) {
                        var row=byId.get(r.getString(1));
                        if (row != null) for (int i=3;i<=6;i++) row.put(r.getString(2)+"_"+r.getMetaData().getColumnName(i),r.getString(i));
                    }
                }
            }
            return result;
        } catch (SQLException ex) { throw SqliteDatabase.failure(ex); }
    }
}
