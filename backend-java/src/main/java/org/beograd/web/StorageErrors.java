package org.beograd.web;

import jakarta.servlet.http.HttpServletRequest;
import org.beograd.infrastructure.TextCatalog;
import org.beograd.ports.StorageFailure;
import org.beograd.ports.MissingData;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import java.util.Map;

@RestControllerAdvice
public class StorageErrors {
    public static final String BUSY="La base de datos esta ocupada por una tarea de escritura. Espera unos segundos y vuelve a intentarlo.";
    private final TextCatalog texts;
    public StorageErrors(TextCatalog texts) { this.texts=texts; }
    @ExceptionHandler(StorageFailure.class)
    public ResponseEntity<?> storage(StorageFailure ex,HttpServletRequest request) {
        if (!ex.busy()) return ResponseEntity.status(503).body(Map.of("error",texts.translate("No se pudieron cargar los datos.",GeographyController.language(request))));
        return ResponseEntity.status(503).header("X-Beograd-Database-Busy","1").body(Map.of("error",texts.translate(BUSY,GeographyController.language(request))));
    }
    @ExceptionHandler(MissingData.class)
    public ResponseEntity<?> missing(MissingData ex) {
        // Django's non-debug 404 body; do not expose persistence internals.
        return ResponseEntity.status(404).header("Content-Type","text/html; charset=utf-8")
            .body("\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Not Found</title>\n</head>\n<body>\n  <h1>Not Found</h1><p>The requested resource was not found on this server.</p>\n</body>\n</html>\n");
    }
}
