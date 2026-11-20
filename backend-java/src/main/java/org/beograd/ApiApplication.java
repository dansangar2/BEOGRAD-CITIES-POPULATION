package org.beograd;

import org.beograd.application.Geography;
import org.beograd.infrastructure.SqliteDatabase;
import org.beograd.infrastructure.SqliteGeography;
import org.beograd.infrastructure.TextCatalog;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.beans.factory.annotation.Value;

@SpringBootApplication
public class ApiApplication {
    public static void main(String[] args) { SpringApplication.run(ApiApplication.class, args); }

    @Bean SqliteDatabase database(@Value("${beograd.database}") String path) {
        return new SqliteDatabase(path);
    }
    @Bean TextCatalog catalog(@Value("${beograd.catalog}") String path) {
        return new TextCatalog(path);
    }
    @Bean Geography geography(SqliteDatabase db, TextCatalog texts) {
        return new Geography(new SqliteGeography(db), texts);
    }
}
