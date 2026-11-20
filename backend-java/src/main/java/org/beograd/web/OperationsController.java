package org.beograd.web;

import org.beograd.infrastructure.SqliteDatabase;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.http.ResponseEntity;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

@RestController
public class OperationsController {
    private final SqliteDatabase db;
    private final ConfigurableApplicationContext context;
    private final String shutdownToken;
    public OperationsController(SqliteDatabase db,ConfigurableApplicationContext context,@Value("${BEOGRAD_SHUTDOWN_TOKEN:}") String token) { this.db=db;this.context=context;this.shutdownToken=token; }
    @GetMapping({"/internal/live","/internal/live/"}) public Map<String,Object> live() { return Map.of("status","up"); }
    @GetMapping({"/internal/ready","/internal/ready/"}) public Map<String,Object> ready() { db.ready(); return Map.of("status","ready"); }
    @PostMapping("/internal/shutdown") public ResponseEntity<Void> stop(@RequestHeader(value="X-Beograd-Shutdown-Token",defaultValue="") String token) {
        if(shutdownToken.isEmpty()||!MessageDigest.isEqual(shutdownToken.getBytes(StandardCharsets.UTF_8),token.getBytes(StandardCharsets.UTF_8)))return ResponseEntity.notFound().build();
        Thread.ofPlatform().daemon().name("local-shutdown").start(()->{try{Thread.sleep(100);}catch(InterruptedException ex){Thread.currentThread().interrupt();}context.close();});
        return ResponseEntity.accepted().build();
    }
}
