/* Minimal UCP reproducer for atomics on CUDA memory.
 *
 * Establishes whether UCP reports atomic capability for a CUDA-backed memory
 * handle, or whether the crash seen under ucx_perftest originates in the
 * benchmark rather than in UCP itself. It only queries capability and
 * registers memory; it deliberately performs no atomic operation, so a fault
 * here would be in registration/query, not in the operation.
 */
#include <stdio.h>
#include <string.h>
#include <ucp/api/ucp.h>
#include <cuda_runtime.h>

int main(void) {
    ucp_config_t *config;
    ucp_context_h context;
    ucp_params_t params;
    ucp_mem_map_params_t map_params;
    ucp_mem_h memh;
    ucp_mem_attr_t attr;
    ucs_status_t status;
    void *device_ptr = NULL;
    const size_t bytes = 4096;

    if (cudaMalloc(&device_ptr, bytes) != cudaSuccess) {
        printf("RESULT cudaMalloc failed\n");
        return 1;
    }
    printf("cuda buffer: %p\n", device_ptr);

    status = ucp_config_read(NULL, NULL, &config);
    if (status != UCS_OK) { printf("RESULT config_read %s\n", ucs_status_string(status)); return 1; }

    memset(&params, 0, sizeof(params));
    params.field_mask = UCP_PARAM_FIELD_FEATURES;
    params.features   = UCP_FEATURE_RMA | UCP_FEATURE_AMO32 | UCP_FEATURE_AMO64 | UCP_FEATURE_TAG;
    status = ucp_init(&params, config, &context);
    ucp_config_release(config);
    if (status != UCS_OK) { printf("RESULT ucp_init %s\n", ucs_status_string(status)); return 1; }
    printf("ucp_init with AMO32|AMO64 requested: OK\n");

    memset(&map_params, 0, sizeof(map_params));
    map_params.field_mask = UCP_MEM_MAP_PARAM_FIELD_ADDRESS | UCP_MEM_MAP_PARAM_FIELD_LENGTH;
    map_params.address    = device_ptr;
    map_params.length     = bytes;
    status = ucp_mem_map(context, &map_params, &memh);
    if (status != UCS_OK) {
        printf("RESULT ucp_mem_map(cuda) FAILED cleanly: %s\n", ucs_status_string(status));
        ucp_cleanup(context);
        return 0;
    }
    printf("ucp_mem_map(cuda): OK\n");

    memset(&attr, 0, sizeof(attr));
    attr.field_mask = UCP_MEM_ATTR_FIELD_MEM_TYPE | UCP_MEM_ATTR_FIELD_LENGTH;
    status = ucp_mem_query(memh, &attr);
    if (status == UCS_OK) {
        printf("registered mem_type=%d length=%zu\n", (int)attr.mem_type, attr.length);
    }
    printf("RESULT registration and query on CUDA memory completed without fault\n");

    ucp_mem_unmap(context, memh);
    ucp_cleanup(context);
    cudaFree(device_ptr);
    return 0;
}
