/* LD_PRELOAD guard: fail if the transport ever calls a global device sync.
 *
 * Timing alone cannot prove that per-slot events are doing the work -- a
 * `cudaDeviceSynchronize` hidden in the copy path would still produce correct
 * bytes and plausible throughput while serialising everything. This interposes
 * the global synchronisation entry points and aborts on first use.
 *
 * Per-stream and per-event synchronisation stay allowed: those are the intended
 * mechanism. Only device-wide and context-wide barriers are rejected.
 *
 *   gcc -shared -fPIC sync_guard.c -o sync_guard.so -ldl
 *   LD_PRELOAD=./sync_guard.so ./bridge ...
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static void reject(const char *symbol) {
    fprintf(stderr,
            "SYNC-GUARD VIOLATION: %s was called. Global device synchronisation "
            "is not permitted; use per-slot events.\n", symbol);
    fflush(stderr);
    _exit(97);
}

int cudaDeviceSynchronize(void)  { reject("cudaDeviceSynchronize");  return 0; }
int hipDeviceSynchronize(void)   { reject("hipDeviceSynchronize");   return 0; }
int cudaThreadSynchronize(void)  { reject("cudaThreadSynchronize");  return 0; }
int hipDeviceWaitEvent(void)     { reject("hipDeviceWaitEvent");     return 0; }
int cuCtxSynchronize(void)       { reject("cuCtxSynchronize");       return 0; }

/* A self-test hook so the guard itself is proven to fire, rather than a clean
 * run being mistaken for enforcement that never engaged. */
__attribute__((constructor)) static void announce(void) {
    if (getenv("SYNC_GUARD_SELFTEST")) {
        fprintf(stderr, "sync guard: self-test requested\n");
        reject("cudaDeviceSynchronize (self-test)");
    }
}
