/* Qualification probe: is vendor device memory directly CPU-accessible?
 *
 * This is the fact that decides transport policy. UCX's ROCm and CUDA memory
 * domains advertise an `access` capability meaning "the host may load and
 * store this pointer directly". On large-BAR bare-metal that generally holds.
 * On ROCm-for-WSL (DXG path) it does not, and every UCX receive path that
 * memcpys into device memory faults.
 *
 * The probe answers it empirically rather than by inference: allocate device
 * memory, attempt one CPU load and one CPU store with SIGSEGV and SIGBUS
 * trapped, and report the outcome as JSON for the control plane to consume.
 *
 * Emits, per vendor:
 *   available          the runtime loaded at all
 *   allocated          a device allocation succeeded
 *   host_store_ok      a CPU store into device memory did not fault
 *   host_load_ok       a CPU load from device memory did not fault
 *   host_accessible    both of the above
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>

static sigjmp_buf jump_target;
static volatile sig_atomic_t trapped;

static void fault_handler(int sig) {
    (void)sig;
    trapped = 1;
    siglongjmp(jump_target, 1);
}

/* Returns 1 when the access completed, 0 when it faulted. */
static int try_access(volatile unsigned char *p, int write) {
    struct sigaction sa, old_segv, old_bus;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = fault_handler;
    sigaction(SIGSEGV, &sa, &old_segv);
    sigaction(SIGBUS, &sa, &old_bus);

    trapped = 0;
    int ok = 0;
    if (sigsetjmp(jump_target, 1) == 0) {
        if (write) *p = 0xAB; else (void)*p;
        ok = 1;
    }
    sigaction(SIGSEGV, &old_segv, NULL);
    sigaction(SIGBUS, &old_bus, NULL);
    return ok;
}

typedef int (*alloc_fn)(void **, size_t);
typedef int (*free_fn)(void *);

static void probe(const char *vendor, const char *const *libs, const char *pfx, int last) {
    void *lib = NULL;
    for (int i = 0; libs[i]; i++) {
        if ((lib = dlopen(libs[i], RTLD_LAZY | RTLD_LOCAL))) break;
    }
    if (!lib) {
        printf("  \"%s\": {\"available\": false}%s\n", vendor, last ? "" : ",");
        return;
    }
    char name[64];
    snprintf(name, sizeof(name), "%sMalloc", pfx);
    alloc_fn dev_malloc = (alloc_fn)dlsym(lib, name);
    snprintf(name, sizeof(name), "%sFree", pfx);
    free_fn dev_free = (free_fn)dlsym(lib, name);

    void *p = NULL;
    int allocated = dev_malloc && dev_malloc(&p, 4096) == 0 && p != NULL;
    if (!allocated) {
        printf("  \"%s\": {\"available\": true, \"allocated\": false}%s\n", vendor, last ? "" : ",");
        dlclose(lib);
        return;
    }

    int store_ok = try_access((volatile unsigned char *)p, 1);
    int load_ok  = try_access((volatile unsigned char *)p, 0);

    printf("  \"%s\": {\"available\": true, \"allocated\": true, "
           "\"host_store_ok\": %s, \"host_load_ok\": %s, \"host_accessible\": %s}%s\n",
           vendor, store_ok ? "true" : "false", load_ok ? "true" : "false",
           (store_ok && load_ok) ? "true" : "false", last ? "" : ",");

    if (dev_free) dev_free(p);
    dlclose(lib);
}

int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    static const char *cuda_libs[] = {"libcudart.so.12", "libcudart.so", NULL};
    static const char *rocm_libs[] = {"libamdhip64.so", "libamdhip64.so.7",
                                      "libamdhip64.so.6", NULL};
    printf("{\n");
    probe("cuda", cuda_libs, "cuda", 0);
    probe("rocm", rocm_libs, "hip", 1);
    printf("}\n");
    return 0;
}
