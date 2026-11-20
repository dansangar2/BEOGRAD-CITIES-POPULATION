package org.beograd;

import org.beograd.infrastructure.SqliteDatabase;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Path;
import java.sql.DriverManager;
import static org.junit.jupiter.api.Assertions.*;

class PersistenceTest {
    @TempDir Path tmp;
    @Test void neverCreatesMissingOrRelativeDatabases() {
        assertThrows(IllegalArgumentException.class,()->new SqliteDatabase(tmp.resolve("missing.db").toString()));
        assertFalse(java.nio.file.Files.exists(tmp.resolve("missing.db")));
        assertThrows(IllegalArgumentException.class,()->new SqliteDatabase("relative.db"));
    }
    @Test void readsRelationsAndRollsBackWithoutSchemaGeneration() throws Exception {
        Path db=tmp.resolve("relations.db");
        try(var c=DriverManager.getConnection("jdbc:sqlite:"+db);var s=c.createStatement()) {
            s.execute("PRAGMA foreign_keys=ON");
            for(String name:new String[]{"adminarea","nuevoadminarea","scrapingconfig","dynamictranslation"})
                s.execute("CREATE TABLE ciudades_del_mundo_"+name+"(id TEXT PRIMARY KEY)");
            s.execute("CREATE TABLE relation(parent TEXT REFERENCES ciudades_del_mundo_adminarea(id), child TEXT)");
            c.setAutoCommit(false);
            s.execute("INSERT INTO ciudades_del_mundo_adminarea VALUES('aa/\u5e02')");
            s.execute("INSERT INTO relation VALUES('aa/\u5e02','child')");
            try(var r=s.executeQuery("SELECT child FROM relation JOIN ciudades_del_mundo_adminarea ON parent=id")) { assertTrue(r.next());assertEquals("child",r.getString(1)); }
            c.rollback();
            try(var r=s.executeQuery("SELECT count(*) FROM relation")) { assertTrue(r.next());assertEquals(0,r.getInt(1)); }
            assertThrows(java.sql.SQLException.class,()->s.execute("INSERT INTO relation VALUES('missing','bad')"));
        }
        var database=new SqliteDatabase(db.toString());
        try(var c=database.open();var s=c.createStatement()) {
            assertThrows(java.sql.SQLException.class,()->s.execute("DELETE FROM relation"));
            try(var r=s.executeQuery("SELECT count(*) FROM relation")) {assertTrue(r.next());assertEquals(0,r.getInt(1));}
        }
    }
}
