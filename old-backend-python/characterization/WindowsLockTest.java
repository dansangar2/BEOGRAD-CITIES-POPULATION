package org.beograd;

import org.beograd.infrastructure.WindowsWriteLock;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledOnOs;
import org.junit.jupiter.api.condition.OS;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

@EnabledOnOs(OS.WINDOWS)
class WindowsLockTest {
    @TempDir Path tmp;
    private Process python(Path path,String mode) throws Exception {
        Path root=Path.of("..").toAbsolutePath().normalize();
        ProcessBuilder builder=new ProcessBuilder(System.getenv().getOrDefault("BEOGRAD_PYTHON","py"),"-B",root.resolve("tools/test_java_lock_peer.py").toString(),path.toString(),mode);
        builder.environment().put("PYTHONDONTWRITEBYTECODE","1");
        builder.directory(root.toFile());builder.redirectErrorStream(true);
        return builder.start();
    }
    @Test void pythonAndJavaMutuallyExcludeOnTheSameByte() throws Exception {
        Path file=tmp.resolve("lock with spaces");
        try(var lock=new WindowsWriteLock(file,Duration.ofSeconds(2))) {
            var peer=python(file,"try");
            try { assertTrue(peer.waitFor(10,TimeUnit.SECONDS));assertEquals(3,peer.exitValue(),new String(peer.getInputStream().readAllBytes())); }
            finally {peer.destroyForcibly();}
        }
        var peer=python(file,"hold");
        try {
            var input=peer.inputReader();assertEquals("locked",input.readLine());
            assertThrows(java.io.IOException.class,()->new WindowsWriteLock(file,Duration.ofMillis(150)));
            peer.outputWriter().append("release\n").flush();
            assertTrue(peer.waitFor(10,TimeUnit.SECONDS));assertEquals(0,peer.exitValue());
        } finally {peer.destroyForcibly();}
        try(var lock=new WindowsWriteLock(file,Duration.ofSeconds(2))) {assertNotNull(lock);}
    }
}
