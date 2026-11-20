package org.beograd;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import static org.junit.jupiter.api.Assertions.*;

class BusyFailureTest {
    @TempDir Path tmp;
    @Test void realSqliteBusyBecomesBoundedJson503AndRecovers() throws Exception {
        Path db=tmp.resolve("locked.db");
        try(var connection=DriverManager.getConnection("jdbc:sqlite:"+db);var schema=connection.createStatement()) {
            for(String table:new String[]{"adminarea","nuevoadminarea","scrapingconfig","dynamictranslation"})schema.execute("CREATE TABLE ciudades_del_mundo_"+table+"(id TEXT)");
            try(var context=new SpringApplicationBuilder(ApiApplication.class).run("--server.port=0","--beograd.database="+db,"--logging.level.root=WARN")) {
                int port=((ServletWebServerApplicationContext)context).getWebServer().getPort();
                var client=HttpClient.newHttpClient();
                schema.execute("BEGIN EXCLUSIVE");
                try {
                    for(String[] headers:new String[][]{{"Accept","application/json"},{"X-Requested-With","XMLHttpRequest"}}) {
                        var request=HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+port+"/internal/ready")).header(headers[0],headers[1]).timeout(Duration.ofSeconds(5)).build();
                        var response=client.send(request,HttpResponse.BodyHandlers.ofString());
                        assertEquals(503,response.statusCode());
                        assertTrue(response.headers().firstValue("X-Request-ID").isPresent());
                        assertTrue(response.body().contains("\"error\""));
                        assertFalse(response.body().contains("<html"));
                    }
                } finally {schema.execute("ROLLBACK");}
                var request=HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+port+"/internal/ready")).build();
                assertEquals(200,client.send(request,HttpResponse.BodyHandlers.ofString()).statusCode());
            }
        }
    }
}
