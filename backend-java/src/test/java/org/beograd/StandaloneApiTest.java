package org.beograd;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import java.nio.file.Path;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import static org.junit.jupiter.api.Assertions.*;

class StandaloneApiTest {
    @TempDir Path tmp;
    @Test void archivedRoutesStayUnavailableAndJavaOwnsSecurityLanguageAndMedia()throws Exception {
        Path db=ContractFixtures.database(tmp,"populated");
        Path media=java.nio.file.Files.createDirectory(tmp.resolve("media"));
        java.nio.file.Files.writeString(media.resolve("flag.svg"),"<svg xmlns=\"http://www.w3.org/2000/svg\"/>");
        java.nio.file.Files.writeString(tmp.resolve("outside.txt"),"not public");
        try(var context=new SpringApplicationBuilder(ApiApplication.class).run("--server.port=0","--beograd.database="+db,"--beograd.media-root="+media,"--logging.level.root=WARN")) {
            int port=((ServletWebServerApplicationContext)context).getWebServer().getPort();
            String base="http://127.0.0.1:"+port;
            var client=HttpClient.newHttpClient();
            for(String path:new String[]{"/api/tasks/","/api/countries/xx/","/groups/source-data/","/configs/sample/editor-data/","/countries/","/admin/","/i18n/setlang/"}) {
                var response=client.send(HttpRequest.newBuilder(URI.create(base+path)).GET().build(),HttpResponse.BodyHandlers.ofString());
                assertEquals(404,response.statusCode(),path);assertTrue(response.headers().firstValue("X-Request-ID").isPresent());
            }
            var noToken=HttpRequest.newBuilder(URI.create(base+"/api/derived/")).POST(HttpRequest.BodyPublishers.noBody()).build();
            assertEquals(403,client.send(noToken,HttpResponse.BodyHandlers.ofString()).statusCode());
            var badOrigin=HttpRequest.newBuilder(URI.create(base+"/api/derived/")).header("Cookie","csrftoken="+"a".repeat(32)).header("X-CSRFToken","a".repeat(32)).header("Origin","http://foreign.example").POST(HttpRequest.BodyPublishers.noBody()).build();
            assertEquals(403,client.send(badOrigin,HttpResponse.BodyHandlers.ofString()).statusCode());
            var masked=HttpRequest.newBuilder(URI.create(base+"/api/derived/")).header("Cookie","csrftoken="+"a".repeat(32)).header("X-CSRFToken","b".repeat(64)).header("Origin",base).POST(HttpRequest.BodyPublishers.noBody()).build();
            assertEquals(200,client.send(masked,HttpResponse.BodyHandlers.ofString()).statusCode());
            var language=HttpRequest.newBuilder(URI.create(base+"/api/derived/")).header("Accept-Language","en-US,en;q=0.8").header("X-Beograd-Language","fr").build();
            var response=client.send(language,HttpResponse.BodyHandlers.ofString());
            assertTrue(response.body().contains("Reviewed name"));
            var preferredCookie=HttpRequest.newBuilder(URI.create(base+"/api/derived/")).header("Cookie","django_language=sr-latn").header("Accept-Language","en").build();
            assertTrue(client.send(preferredCookie,HttpResponse.BodyHandlers.ofString()).body().contains("Проверено име"));
            assertEquals(200,client.send(HttpRequest.newBuilder(URI.create(base+"/media/flag.svg")).build(),HttpResponse.BodyHandlers.ofString()).statusCode());
            int outside=client.send(HttpRequest.newBuilder(URI.create(base+"/media/%2e%2e/outside.txt")).build(),HttpResponse.BodyHandlers.ofString()).statusCode();
            assertTrue(outside==400||outside==404);
            try(var socket=new java.net.Socket("127.0.0.1",port)) {
                socket.setSoTimeout(3000);
                socket.getOutputStream().write("GET /api/countries/ HTTP/1.1\r\nHost: foreign.example\r\nConnection: close\r\n\r\n".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
                assertTrue(new java.io.BufferedReader(new java.io.InputStreamReader(socket.getInputStream())).readLine().contains("400"));
            }
        }
    }
}
