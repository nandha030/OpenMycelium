/* Is hipMalloc memory directly CPU-accessible on this system?
 *
 * UCX's ROCm path assumes it is (it stores into the device pointer from the
 * host). On large-BAR bare-metal ROCm that generally holds. This probe settles
 * it for ROCm-for-WSL by attempting one CPU store, with SIGSEGV caught so the
 * answer is reported rather than crashing the process.
 */
#define __HIP_PLATFORM_AMD__
#include <hip/hip_runtime_api.h>
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>

static sigjmp_buf jump;
static void on_segv(int sig) { (void)sig; siglongjmp(jump, 1); }

int main(void) {
    void *p = NULL;
    if (hipMalloc(&p, 4096) != hipSuccess) { printf("hipMalloc failed\n"); return 2; }
    printf("hipMalloc ptr = %p\n", p);

    struct sigaction sa = {0}, old;
    sa.sa_handler = on_segv;
    sigaction(SIGSEGV, &sa, &old);
    sigaction(SIGBUS, &sa, NULL);

    if (sigsetjmp(jump, 1) == 0) {
        *(volatile unsigned char *)p = 0xAB;
        printf("RESULT host CPU store into hipMalloc memory: SUCCEEDED\n");
    } else {
        printf("RESULT host CPU store into hipMalloc memory: FAULTED\n");
    }

    if (sigsetjmp(jump, 1) == 0) {
        volatile unsigned char v = *(volatile unsigned char *)p;
        printf("RESULT host CPU load  from hipMalloc memory: SUCCEEDED (0x%02x)\n", v);
    } else {
        printf("RESULT host CPU load  from hipMalloc memory: FAULTED\n");
    }

    sigaction(SIGSEGV, &old, NULL);
    hipFree(p);
    return 0;
}
