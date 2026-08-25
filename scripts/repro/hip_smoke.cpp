// Establishes that the AMD GPU actually computes under WSL, not merely that
// rocminfo enumerates it. Allocates device memory, launches a kernel that
// writes a position-dependent pattern, copies back, and verifies byte for byte.
#include <hip/hip_runtime.h>
#include <cstdio>
#include <cstdint>
#include <vector>

__global__ void fill_pattern(uint32_t *out, size_t n) {
    size_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = (uint32_t)(i * 2654435761u + 12345u);
}

#define CHECK(call) do { hipError_t e = (call); if (e != hipSuccess) { \
    printf("RESULT FAIL %s -> %s\n", #call, hipGetErrorString(e)); return 1; } } while (0)

int main() {
    int count = 0;
    CHECK(hipGetDeviceCount(&count));
    printf("hip device count: %d\n", count);
    if (count == 0) { printf("RESULT FAIL no HIP devices\n"); return 1; }

    hipDeviceProp_t prop{};
    CHECK(hipGetDeviceProperties(&prop, 0));
    printf("device 0: %s  arch=%s  CUs=%d  globalMem=%.1f GiB\n",
           prop.name, prop.gcnArchName, prop.multiProcessorCount,
           prop.totalGlobalMem / (1024.0 * 1024.0 * 1024.0));

    const size_t n = 4u << 20;              // 16 MiB of uint32
    uint32_t *dev = nullptr;
    CHECK(hipMalloc(&dev, n * sizeof(uint32_t)));
    printf("hipMalloc %zu bytes: ok\n", n * sizeof(uint32_t));

    hipLaunchKernelGGL(fill_pattern, dim3((n + 255) / 256), dim3(256), 0, 0, dev, n);
    CHECK(hipGetLastError());
    CHECK(hipDeviceSynchronize());
    printf("kernel launch + sync: ok\n");

    std::vector<uint32_t> host(n, 0);
    CHECK(hipMemcpy(host.data(), dev, n * sizeof(uint32_t), hipMemcpyDeviceToHost));

    for (size_t i = 0; i < n; i++) {
        uint32_t want = (uint32_t)(i * 2654435761u + 12345u);
        if (host[i] != want) {
            printf("RESULT FAIL mismatch at %zu: got %u want %u\n", i, host[i], want);
            return 1;
        }
    }
    CHECK(hipFree(dev));
    printf("RESULT PASS kernel output verified over %zu elements on %s\n", n, prop.gcnArchName);
    return 0;
}
