package org.beograd.ports;

public class StorageFailure extends RuntimeException {
    private final boolean busy;
    public StorageFailure(Throwable cause, boolean busy) { super("Storage operation failed", cause); this.busy = busy; }
    public boolean busy() { return busy; }
}
