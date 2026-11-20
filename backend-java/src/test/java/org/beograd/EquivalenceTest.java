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
import static org.junit.jupiter.api.Assertions.*;

class EquivalenceTest {
    @TempDir Path tmp;
    @Test void nativeHttpMatchesFrozenPopulatedContracts() throws Exception { compare(false); }
    @Test void nativeHttpMatchesFrozenEmptyContracts() throws Exception { compare(true); }

    private void compare(boolean empty) throws Exception {
        String scenario=empty?"empty":"populated";
        Path db=ContractFixtures.database(tmp,scenario);
        var contracts=ContractFixtures.resource(scenario+"-responses.json");
        var mapper=new ObjectMapper().enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS);
        try(var context=new SpringApplicationBuilder(ApiApplication.class).run("--server.port=0","--beograd.database="+db,"--logging.level.root=WARN")) {
            int port=((ServletWebServerApplicationContext)context).getWebServer().getPort();
            var client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).build();
            for(var item:contracts.path("cases")) {
                var expected=contracts.path("responses").path(item.path("response").asText());
                String label=item.path("method").asText()+" "+item.path("path").asText()+" "+item.path("language").asText();
                var requestBuilder=HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+port+item.path("path").asText())).timeout(Duration.ofSeconds(5))
                    .header("Cookie","django_language="+item.path("language").asText()+"; csrftoken="+"a".repeat(32))
                    .header("X-CSRFToken","a".repeat(32))
                    .method(item.path("method").asText(),HttpRequest.BodyPublishers.noBody());
                if(!item.path("accept").isNull())requestBuilder.header("Accept",item.path("accept").asText("application/json"));
                var request=requestBuilder.build();
                var response=client.send(request,HttpResponse.BodyHandlers.ofByteArray());
                assertEquals(expected.path("status").asInt(),response.statusCode(),label);
                // MIME parameters are unordered and whitespace is insignificant (Tomcat canonicalizes it).
                assertEquals(org.springframework.http.MediaType.parseMediaType(expected.path("content_type").asText()),org.springframework.http.MediaType.parseMediaType(response.headers().firstValue("content-type").orElseThrow()),label);
                assertEquals(expected.path("disposition").isNull()?null:expected.path("disposition").asText(),response.headers().firstValue("content-disposition").orElse(null),label);
                if(expected.has("json")) {
                    var actual=mapper.readTree(response.body());
                    assertEquals(expected.get("json"),actual,label);
                    // Existing browser fingerprints use JSON.stringify; preserve field order too.
                    assertEquals(expected.get("json").toString(),actual.toString(),label+" cache fingerprint");
                }
                else assertArrayEquals(Base64.getDecoder().decode(expected.path("body").asText()),response.body(),label);
            }
        }
    }
}
