package org.beograd;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.DeserializationFeature;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;

/** Frozen synthetic contracts; never imports or executes the archived Python backend. */
final class ContractFixtures {
    private ContractFixtures(){}
    static final ObjectMapper JSON=new ObjectMapper().enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS);
    static JsonNode resource(String name)throws Exception {
        try(var in=ContractFixtures.class.getResourceAsStream("/contracts/"+name)) {
            if(in==null)throw new IllegalStateException("Missing test contract: "+name);
            return JSON.readTree(in);
        }
    }
    static Path database(Path directory,String scenario)throws Exception {
        Path path=directory.resolve(scenario+".sqlite3");
        if(Files.exists(path))throw new IllegalArgumentException("Synthetic fixture path must be new");
        try(var c=DriverManager.getConnection("jdbc:sqlite:"+path);var statement=c.createStatement()) {
            for(var sql:resource(scenario+"-database.json"))statement.execute(sql.asText());
        }
        return path;
    }
}
