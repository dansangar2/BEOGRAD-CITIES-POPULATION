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
    private Process peer(Path path,String mode) throws Exception {
        String java=Path.of(System.getProperty("java.home"),"bin","java.exe").toString();
        ProcessBuilder builder=new ProcessBuilder(java,"-cp",System.getProperty("surefire.test.class.path",System.getProperty("java.class.path")),WriteLockPeer.class.getName(),path.toString(),mode);
        builder.redirectErrorStream(true);
        return builder.start();
    }
    @Test void separateJvmProcessesMutuallyExcludeOnTheSameByte() throws Exception {
        Path file=tmp.resolve("lock with spaces");
        try(var lock=new WindowsWriteLock(file,Duration.ofSeconds(2))) {
            var peer=peer(file,"try");
            try { assertTrue(peer.waitFor(10,TimeUnit.SECONDS));assertEquals(3,peer.exitValue(),new String(peer.getInputStream().readAllBytes())); }
            finally {peer.destroyForcibly();}
        }
        var peer=peer(file,"hold");
        try {
            var input=peer.inputReader();assertEquals("locked",input.readLine());
            assertThrows(java.io.IOException.class,()->new WindowsWriteLock(file,Duration.ofMillis(150)));
            peer.outputWriter().append("release\n").flush();
            assertTrue(peer.waitFor(10,TimeUnit.SECONDS));assertEquals(0,peer.exitValue());
        } finally {peer.destroyForcibly();}
        try(var lock=new WindowsWriteLock(file,Duration.ofSeconds(2))) {assertNotNull(lock);}
    }
}
