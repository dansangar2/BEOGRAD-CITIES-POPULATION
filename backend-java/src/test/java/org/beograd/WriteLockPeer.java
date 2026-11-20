package org.beograd;

import org.beograd.infrastructure.WindowsWriteLock;
import java.nio.file.Path;
import java.time.Duration;

public class WriteLockPeer {
    public static void main(String[] args)throws Exception {
        try(var lock=new WindowsWriteLock(Path.of(args[0]),Duration.ofMillis(250))) {
            System.out.println("locked");System.out.flush();
            if(args[1].equals("hold"))System.in.read();
        }catch(java.io.IOException ex){System.exit(3);}
    }
}
