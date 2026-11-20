package org.beograd.infrastructure;

import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.io.IOException;
import java.time.Duration;

/** Windows byte [0,1), compatible with Python SQLiteWriteLock/msvcrt.
 * Not used by the read-only API. Required before enabling Java writers.
 * POSIX flock and Java fcntl locks are different; do not claim POSIX support.
 */
public final class WindowsWriteLock implements AutoCloseable {
    private final FileChannel channel;
    private final FileLock lock;
    public WindowsWriteLock(Path path, Duration timeout) throws IOException,InterruptedException {
        if (!System.getProperty("os.name").startsWith("Windows")) throw new UnsupportedOperationException("Windows byte-range protocol only");
        channel=FileChannel.open(path,StandardOpenOption.CREATE,StandardOpenOption.READ,StandardOpenOption.WRITE);
        if(channel.size()==0) channel.write(java.nio.ByteBuffer.wrap(new byte[]{0}),0);
        long deadline=System.nanoTime()+timeout.toNanos();
        FileLock acquired=null;
        try {
            while(acquired==null) {
                try { acquired=channel.tryLock(0,1,false); }
                catch (OverlappingFileLockException ex) { /* another thread owns it */ }
                if(acquired!=null)break;
                if(System.nanoTime()>=deadline)throw new IOException("SQLite write lock timeout: "+path);
                Thread.sleep(50);
            }
            lock=acquired;
        } catch (IOException|InterruptedException|RuntimeException ex) { channel.close();throw ex; }
    }
    @Override public void close() throws IOException { try { lock.release(); } finally { channel.close(); } }
}
