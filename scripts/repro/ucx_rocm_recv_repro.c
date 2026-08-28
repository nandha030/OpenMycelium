/* Minimal reproducer: ucp_tag_recv_nbx into hipMalloc memory.
 *
 * Single process, two UCP workers, loopback endpoint. Everything that the
 * larger conformance harness could contribute -- separate processes, a TCP
 * bootstrap, dlopen'd backends, signal handling in a shell wrapper -- is
 * removed, so a fault here is attributable to UCP and the ROCm UCT module.
 *
 * Three receive-buffer controls isolate whether the memory kind is the cause:
 *
 *   malloc        plain host memory        expected: pass
 *   hipHostMalloc pinned, host-accessible  expected: pass
 *   hipMalloc     ROCm device memory       suspected: crash
 *
 * The send buffer is always plain host memory with a position-dependent
 * pattern, so a truncated or shifted result fails verification rather than
 * passing on completion alone.
 *
 * Observed on ROCm-for-WSL (DXG path, no /dev/kfd), gfx1200, UCX 1.18.0:
 *   uct_rocm_copy_ep_put_short  -> SIGSEGV, "invalid permissions for mapped
 *   object", reached via ucp_mem_type_unpack / ucp_proto_rndv_handle_data.
 *
 * Build:
 *   gcc ucx_rocm_recv_repro.c -o repro \
 *       -I$UCX/include -I/opt/rocm/include -L$UCX/lib -lucp -lucs \
 *       -L/opt/rocm/lib -lamdhip64
 */

#define __HIP_PLATFORM_AMD__
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hip/hip_runtime_api.h>
#include <ucp/api/ucp.h>

#define TAG 0xC0FFEEULL

typedef enum { BUF_MALLOC, BUF_HIP_HOST, BUF_HIP_DEVICE } buf_kind;

static const char *kind_name(buf_kind k) {
    switch (k) {
    case BUF_MALLOC:     return "malloc";
    case BUF_HIP_HOST:   return "hipHostMalloc";
    default:             return "hipMalloc";
    }
}

static void fill(uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) p[i] = (uint8_t)((i * 31u + 7u) & 0xff);
}

static long verify(const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (p[i] != (uint8_t)((i * 31u + 7u) & 0xff)) return (long)i;
    }
    return -1;
}

/* Progress both workers until both operations retire. */
static int await_both(ucp_worker_h wa, ucp_worker_h wb,
                      ucs_status_ptr_t sreq, ucs_status_ptr_t rreq) {
    int rc = 0;
    for (;;) {
        int send_done = 1, recv_done = 1;

        if (sreq != NULL && !UCS_PTR_IS_ERR(sreq)) {
            send_done = (ucp_request_check_status(sreq) != UCS_INPROGRESS);
        } else if (UCS_PTR_IS_ERR(sreq)) {
            fprintf(stderr, "send failed: %s\n", ucs_status_string(UCS_PTR_STATUS(sreq)));
            return -1;
        }
        if (rreq != NULL && !UCS_PTR_IS_ERR(rreq)) {
            recv_done = (ucp_request_check_status(rreq) != UCS_INPROGRESS);
        } else if (UCS_PTR_IS_ERR(rreq)) {
            fprintf(stderr, "recv failed: %s\n", ucs_status_string(UCS_PTR_STATUS(rreq)));
            return -1;
        }
        if (send_done && recv_done) break;

        ucp_worker_progress(wa);
        ucp_worker_progress(wb);
    }
    if (sreq != NULL && !UCS_PTR_IS_ERR(sreq)) {
        if (ucp_request_check_status(sreq) != UCS_OK) rc = -1;
        ucp_request_free(sreq);
    }
    if (rreq != NULL && !UCS_PTR_IS_ERR(rreq)) {
        if (ucp_request_check_status(rreq) != UCS_OK) rc = -1;
        ucp_request_free(rreq);
    }
    return rc;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);

    buf_kind kind = BUF_MALLOC;
    size_t bytes  = 4096;
    int detect    = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--mem") && i + 1 < argc) {
            const char *m = argv[++i];
            kind = !strcmp(m, "hiphost")   ? BUF_HIP_HOST
                 : !strcmp(m, "hipmalloc") ? BUF_HIP_DEVICE : BUF_MALLOC;
        } else if (!strcmp(argv[i], "--size") && i + 1 < argc) {
            bytes = strtoull(argv[++i], NULL, 10);
        } else if (!strcmp(argv[i], "--detect")) {
            detect = 1;
        }
    }

    printf("== repro mem=%s size=%zu tls=%s ==\n", kind_name(kind), bytes,
           getenv("UCX_TLS") ? getenv("UCX_TLS") : "(default)");

    /* ---- receive buffer, allocated three different ways ---- */
    void *rbuf = NULL;
    hipError_t herr;
    switch (kind) {
    case BUF_MALLOC:
        rbuf = calloc(1, bytes);
        break;
    case BUF_HIP_HOST:
        herr = hipHostMalloc(&rbuf, bytes, 0);
        if (herr != hipSuccess) {
            printf("RESULT SKIP hipHostMalloc: %s\n", hipGetErrorString(herr));
            return 2;
        }
        memset(rbuf, 0, bytes);
        break;
    default:
        herr = hipMalloc(&rbuf, bytes);
        if (herr != hipSuccess) {
            printf("RESULT SKIP hipMalloc: %s\n", hipGetErrorString(herr));
            return 2;
        }
        hipMemset(rbuf, 0, bytes);
        break;
    }
    if (!rbuf) { printf("RESULT FAIL allocation returned NULL\n"); return 1; }
    printf("recv buffer (%s) at %p\n", kind_name(kind), rbuf);

    uint8_t *sbuf = malloc(bytes);
    fill(sbuf, bytes);

    /* ---- UCP: one context, two workers, loopback endpoint ---- */
    ucp_config_t *config;
    ucp_context_h context;
    ucp_worker_h wsend, wrecv;
    ucp_params_t params = {.field_mask = UCP_PARAM_FIELD_FEATURES,
                           .features   = UCP_FEATURE_TAG};
    ucp_worker_params_t wp = {.field_mask  = UCP_WORKER_PARAM_FIELD_THREAD_MODE,
                              .thread_mode = UCS_THREAD_MODE_SINGLE};

    if (ucp_config_read(NULL, NULL, &config) != UCS_OK) return 1;
    if (ucp_init(&params, config, &context) != UCS_OK) return 1;
    ucp_config_release(config);
    if (ucp_worker_create(context, &wp, &wsend) != UCS_OK) return 1;
    if (ucp_worker_create(context, &wp, &wrecv) != UCS_OK) return 1;

    ucp_address_t *raddr; size_t rlen;
    if (ucp_worker_get_address(wrecv, &raddr, &rlen) != UCS_OK) return 1;

    ucp_ep_h ep;
    ucp_ep_params_t ep_params = {.field_mask = UCP_EP_PARAM_FIELD_REMOTE_ADDRESS,
                                 .address    = raddr};
    if (ucp_ep_create(wsend, &ep_params, &ep) != UCS_OK) {
        printf("RESULT FAIL ucp_ep_create\n"); return 1;
    }
    printf("two workers + loopback endpoint: ok\n");

    /* ---- post receive into the buffer under test, then send ---- */
    ucs_memory_type_t rmem = (kind == BUF_HIP_DEVICE) ? UCS_MEMORY_TYPE_ROCM
                                                      : UCS_MEMORY_TYPE_HOST;
    ucp_request_param_t rp = {0}, sp = {0};
    rp.op_attr_mask = UCP_OP_ATTR_FIELD_DATATYPE;
    rp.datatype     = ucp_dt_make_contig(1);
    if (!detect) {
        rp.op_attr_mask |= UCP_OP_ATTR_FIELD_MEMORY_TYPE;
        rp.memory_type   = rmem;
    }
    sp.op_attr_mask = UCP_OP_ATTR_FIELD_DATATYPE | UCP_OP_ATTR_FIELD_MEMORY_TYPE;
    sp.datatype     = ucp_dt_make_contig(1);
    sp.memory_type  = UCS_MEMORY_TYPE_HOST;

    printf("posting ucp_tag_recv_nbx into %s ...\n", kind_name(kind));
    ucs_status_ptr_t rreq = ucp_tag_recv_nbx(wrecv, rbuf, bytes, TAG, (ucp_tag_t)-1, &rp);
    printf("posting ucp_tag_send_nbx from host ...\n");
    ucs_status_ptr_t sreq = ucp_tag_send_nbx(ep, sbuf, bytes, TAG, &sp);

    printf("progressing both workers ...\n");
    int rc = await_both(wsend, wrecv, sreq, rreq);
    if (rc != 0) { printf("RESULT FAIL transfer did not complete cleanly\n"); return 1; }
    printf("transfer completed\n");

    /* ---- copy back and verify every byte ---- */
    uint8_t *check = malloc(bytes);
    if (kind == BUF_HIP_DEVICE) {
        herr = hipMemcpy(check, rbuf, bytes, hipMemcpyDeviceToHost);
        if (herr != hipSuccess) {
            printf("RESULT FAIL hipMemcpy D2H: %s\n", hipGetErrorString(herr));
            return 1;
        }
    } else {
        memcpy(check, rbuf, bytes);
    }

    long bad = verify(check, bytes);
    if (bad >= 0) printf("RESULT FAIL mem=%s size=%zu first bad byte at %ld\n",
                         kind_name(kind), bytes, bad);
    else          printf("RESULT PASS mem=%s size=%zu verified byte-for-byte\n",
                         kind_name(kind), bytes);

    ucp_ep_destroy(ep);
    ucp_worker_release_address(wrecv, raddr);
    ucp_worker_destroy(wrecv);
    ucp_worker_destroy(wsend);
    ucp_cleanup(context);
    free(sbuf); free(check);
    if (kind == BUF_MALLOC)          free(rbuf);
    else if (kind == BUF_HIP_HOST)   hipHostFree(rbuf);
    else                             hipFree(rbuf);
    return bad >= 0 ? 1 : 0;
}
