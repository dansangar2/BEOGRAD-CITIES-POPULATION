package org.beograd;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.DeserializationFeature;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import java.nio.file.Path;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Base64;
import java.util.ArrayList;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

class EquivalenceTest {
    @TempDir Path tmp;
    @Test void nativeHttpEqualsDjangoForSyntheticData() throws Exception { compare(false); }
    @Test void nativeHttpEqualsDjangoForEmptyDatabase() throws Exception { compare(true); }

    private void compare(boolean empty) throws Exception {
        Path root=Path.of("..").toAbsolutePath().normalize();
        Path db=tmp.resolve("fixture.sqlite3"), expected=tmp.resolve("expected.json");
        var command=new ArrayList<>(java.util.List.of(System.getenv().getOrDefault("BEOGRAD_PYTHON","py"),"-B",root.resolve("tools/characterize_java_api.py").toString(),"--database",db.toString(),"--expected",expected.toString()));
        if(empty)command.add("--empty");
        var builder=new ProcessBuilder(command).directory(root.toFile()).redirectErrorStream(true);
        builder.environment().put("PYTHONDONTWRITEBYTECODE","1");
        builder.redirectOutput(tmp.resolve("capture.log").toFile());
        var python=builder.start();
        try {
            assertTrue(python.waitFor(40,TimeUnit.SECONDS),"Python capture timed out");
            assertEquals(0,python.exitValue(),java.nio.file.Files.readString(tmp.resolve("capture.log")));
        } finally {python.destroyForcibly();}
        var mapper=new ObjectMapper().enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS);
        try(var context=new SpringApplicationBuilder(ApiApplication.class).run("--server.port=0","--beograd.database="+db,"--logging.level.root=WARN")) {
            int port=((ServletWebServerApplicationContext)context).getWebServer().getPort();
            var client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).build();
            for(var item:mapper.readTree(expected.toFile())) {
                String label=item.path("method").asText()+" "+item.path("path").asText()+" "+item.path("language").asText();
                var requestBuilder=HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+port+item.path("path").asText())).timeout(Duration.ofSeconds(5))
                    .header("X-Beograd-Language",item.path("language").asText())
                    .method(item.path("method").asText(),HttpRequest.BodyPublishers.noBody());
                if(!item.path("accept").isNull())requestBuilder.header("Accept",item.path("accept").asText("application/json"));
                var request=requestBuilder.build();
                var response=client.send(request,HttpResponse.BodyHandlers.ofByteArray());
                assertEquals(item.path("status").asInt(),response.statusCode(),label);
                // MIME parameters are unordered and whitespace is insignificant (Tomcat canonicalizes it).
                assertEquals(org.springframework.http.MediaType.parseMediaType(item.path("content_type").asText()),org.springframework.http.MediaType.parseMediaType(response.headers().firstValue("content-type").orElseThrow()),label);
                assertEquals(item.path("disposition").isNull()?null:item.path("disposition").asText(),response.headers().firstValue("content-disposition").orElse(null),label);
                if(item.has("json")) {
                    var actual=mapper.readTree(response.body());
                    assertEquals(item.get("json"),actual,label);
                    // Existing browser fingerprints use JSON.stringify; preserve field order too.
                    assertEquals(item.get("json").toString(),actual.toString(),label+" cache fingerprint");
                }
                else assertArrayEquals(Base64.getDecoder().decode(item.path("body").asText()),response.body(),label);
            }
        }
    }
}
