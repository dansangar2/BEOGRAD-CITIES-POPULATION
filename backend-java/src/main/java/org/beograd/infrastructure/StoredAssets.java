package org.beograd.infrastructure;

import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** SQL-registered assets for country summaries. Never discovers files or calls providers. */
final class StoredAssets {
    private final SqliteDatabase db;
    StoredAssets(SqliteDatabase db){this.db=db;}
    Map<String,Map<String,Object>> countries(List<String> countries) {
        Map<String,Map<String,Object>> result=new LinkedHashMap<>();
        if(countries.isEmpty())return result;
        String slots=String.join(",",java.util.Collections.nCopies(countries.size(),"?"));
        try(var c=db.open();var s=c.prepareStatement("SELECT id,entity_type,entity_key,entity_name,country_code,kind,wikidata_id,commons_filename,remote_url,local_path,local_exists,source,status,error,license_name,author,attribution,source_url,updated_at FROM ciudades_del_mundo_visual_asset WHERE entity_type='country' AND entity_key IN ("+slots+")")) {
            int i=1;for(String key:countries)s.setString(i++,key);
            List<Map<String,Object>> rows=new ArrayList<>();
            try(var r=s.executeQuery()) {
                while(r.next()) {
                    var row=new LinkedHashMap<String,Object>();
                    for(int j=1;j<=r.getMetaData().getColumnCount();j++)row.put(r.getMetaData().getColumnLabel(j),r.getObject(j));
                    Object updated=row.get("updated_at");
                    if(updated!=null) {
                        String date=updated.toString().replace(' ','T');
                        int dot=date.indexOf('.');
                        if(dot>=0 && date.length()>dot+4)date=date.substring(0,dot+4);
                        row.put("updated_at",date);
                    }
                    rows.add(row);
                }
            }
            var translations=new java.util.HashMap<Long,Map<String,Object>>();
            if(!rows.isEmpty()) {
                String ids=String.join(",",java.util.Collections.nCopies(rows.size(),"?"));
                try(var t=c.prepareStatement("SELECT asset_id,language,title,description,blazon,source,needs_review FROM ciudades_del_mundo_visual_asset_translation WHERE asset_id IN ("+ids+")")) {
                    i=1;for(var row:rows)t.setLong(i++,((Number)row.get("id")).longValue());
                    try(var r=t.executeQuery()) {
                        while(r.next()) {
                            if(r.getString(2).isEmpty())continue;
                            var translation=new LinkedHashMap<String,Object>();
                            translation.put("title",r.getString(3));translation.put("description",r.getString(4));translation.put("blazon",r.getString(5));
                            translation.put("source",r.getString(6));translation.put("needs_review",r.getBoolean(7));
                            translations.computeIfAbsent(r.getLong(1),key->new LinkedHashMap<>()).put(r.getString(2),translation);
                        }
                    }
                }
            }
            for(var row:rows) {
                String kind=text(row,"kind"),key=text(row,"entity_key");
                String local=text(row,"local_path"),remote=text(row,"remote_url"),commons=text(row,"commons_filename");
                String filename=local.isEmpty()?(remote.isEmpty()?commons:remote.split("[?#]",2)[0]):local;
                filename=filename.replace('\\','/');filename=filename.substring(filename.lastIndexOf('/')+1).toLowerCase(java.util.Locale.ROOT);
                String expected=text(row,"country_code").isEmpty()?key:text(row,"country_code");
                boolean bad=kind.equalsIgnoreCase("flag")&&filename.matches("[a-z0-9_-]+_2_3\\.svg")&&!filename.equalsIgnoreCase(expected+"_2_3.svg");
                if(bad)remote="";
                else if(remote.isEmpty()&&!commons.isEmpty())remote="https://commons.wikimedia.org/wiki/Special:FilePath/"+quote(commons.replace(' ','_'),"/_()-.,'")+"?width=500";
                // Local assets are inspected only when explicitly registered in SQL.
                String localUrl=bad?"":LocalMedia.url(local);
                if(!localUrl.isEmpty()) {
                    row.put("status","downloaded");
                    if(text(row,"source").equals("auto"))row.put("source","local");
                }
                row.put("local_exists",!localUrl.isEmpty());row.put("local_url",localUrl);row.put("remote_url",remote);row.put("image_url",remote.isEmpty()?localUrl:remote);
                row.put("translations",translations.getOrDefault(((Number)row.get("id")).longValue(),Map.of()));
                var assets=result.computeIfAbsent(key,k->new LinkedHashMap<>());
                Object prior=assets.get(kind);
                if(!(prior instanceof Map<?,?> previous)||compareScore(row,previous)>0)assets.put(kind,row);
            }
            return result;
        }catch(SQLException|IllegalArgumentException ex) {
            // Existing summary contract treats unavailable optional assets as empty.
            return Map.of();
        }
    }
    private static String text(Map<String,Object> row,String key){return String.valueOf(row.getOrDefault(key,""));}
    private static int compareScore(Map<?,?> first,Map<?,?> second) {
        long[] a=score(first),b=score(second);
        for(int i=0;i<a.length;i++){int result=Long.compare(a[i],b[i]);if(result!=0)return result;}
        return 0;
    }
    private static long[] score(Map<?,?> row) {
        var value=(java.util.function.Function<String,String>)(key->row.get(key)==null?"":row.get(key).toString());
        String status=value.apply("status").toLowerCase(java.util.Locale.ROOT);
        int rating=switch(status){case "downloaded"->4;case "remote","found"->3;case "ok"->2;case "","missing","error"->0;default->1;};
        boolean remote=!value.apply("remote_url").isEmpty()||!value.apply("image_url").isEmpty(),commons=!value.apply("commons_filename").isEmpty();
        boolean local=!value.apply("local_url").isEmpty()||Boolean.TRUE.equals(row.get("local_exists"))||!value.apply("local_path").isEmpty();
        return new long[]{rating,remote||commons||local?1:0,remote?1:0,commons?1:0,value.apply("source").startsWith("shared")?0:1,((Number)row.get("id")).longValue()};
    }
    static String quote(String text,String safe) {
        var out=new StringBuilder();
        for(byte b:text.getBytes(java.nio.charset.StandardCharsets.UTF_8)) {
            int v=b&255;
            if(v>='a'&&v<='z'||v>='A'&&v<='Z'||v>='0'&&v<='9'||safe.indexOf(v)>=0||"-._~".indexOf(v)>=0)out.append((char)v);
            else out.append("%%%02X".formatted(v));
        }
        return out.toString();
    }
}
