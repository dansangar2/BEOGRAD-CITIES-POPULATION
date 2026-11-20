package org.beograd.infrastructure;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import org.sqlite.SQLiteConfig;
import org.sqlite.SQLiteOpenMode;
import org.beograd.ports.StorageFailure;

/** Existing schema only. Opening a missing path never creates a database. */
public final class SqliteDatabase {
    private final Path path;
    public SqliteDatabase(String value) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException("BEOGRAD_DATABASE must be an absolute path to an existing SQLite database");
        path = Path.of(value);
        if (!path.isAbsolute() || !Files.isRegularFile(path)) throw new IllegalArgumentException("BEOGRAD_DATABASE must name an existing absolute file: " + value);
        ready();
    }
    public Connection open() throws SQLException {
        SQLiteConfig config = new SQLiteConfig();
        config.resetOpenMode(SQLiteOpenMode.CREATE);
        config.setReadOnly(true);
        config.setBusyTimeout(1000);
        config.enforceForeignKeys(true);
        return DriverManager.getConnection("jdbc:sqlite:" + path, config.toProperties());
    }
    public void ready() {
        try (var c = open(); var s = c.createStatement()) {
            // Metadata only; no seed import, migrations, PRAGMA journal writes or repairs.
            for (String table : new String[]{"adminarea", "nuevoadminarea", "scrapingconfig", "dynamictranslation"})
                try (var rows = s.executeQuery("SELECT 1 FROM ciudades_del_mundo_" + table + " LIMIT 0")) { }
        } catch (SQLException ex) { throw failure(ex); }
    }
    public static StorageFailure failure(SQLException ex) {
        // Extended SQLite result codes retain the base code in the low byte.
        int code = ex.getErrorCode() & 255;
        return new StorageFailure(ex, code == 5 || code == 6);
    }
}
