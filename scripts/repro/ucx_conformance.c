/* Two-sided UCP mixed-memory conformance harness.
 *
 * ucx_perftest runs both memory types inside one process, which is not the
 * deployment being tested: in a real mixed cluster the NVIDIA node has no ROCm
 * installed and the AMD node has no CUDA. This harness runs sender and
 * receiver as separate processes, each selecting its allocation backend
 * independently at runtime, so neither ever loads the other vendor's runtime.
 *
 * Backends are resolved with dlopen rather than linked, so one binary runs on
 * a CUDA-only host, a ROCm-only host, or a host with both.
 *
 * Every transfer is verified byte for byte against a position-dependent
 * pattern, so a truncated, shifted, or zero-filled result fails rather than
 * reporting success on completion alone.
 *
 *   sender:   ucx_conformance --role client --mem cuda --peer 127.0.0.1 --size 1048576
 *   receiver: ucx_conformance --role server --mem rocm --size 1048576
 */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include <ucp/api/ucp.h>

/* ---------------------------------------------------------------- backends */

typedef enum { MEM_HOST, MEM_CUDA, MEM_ROCM } mem_kind;

typedef struct {
    mem_kind kind;
    void *handle;                                   /* dlopen handle          */
    int (*dev_malloc)(void **, size_t);
    int (*dev_free)(void *);
    int (*dev_memcpy)(void *, const void *, size_t, int);
    int to_device_kind;                             /* vendor's H2D enum      */
    int to_host_kind;                               /* vendor's D2H enum      */
    const char *name;
} backend_t;

/* cudaMemcpyHostToDevice=1, DeviceToHost=2; hipMemcpyHostToDevice=1,
 * hipMemcpyDeviceToHost=2. The enums coincide, but they are kept per-backend
 * so a divergence would be a one-line fix rather than a silent corruption. */
static int backend_open(backend_t *b, mem_kind kind) {
    memset(b, 0, sizeof(*b));
    b->kind = kind;
    if (kind == MEM_HOST) { b->name = "host"; return 0; }

    const char *libs[3] = {NULL, NULL, NULL};
    const char *pfx;
    if (kind == MEM_CUDA) {
        libs[0] = "libcudart.so.12"; libs[1] = "libcudart.so"; pfx = "cuda";
        b->name = "cuda";
    } else {
        libs[0] = "libamdhip64.so"; libs[1] = "libamdhip64.so.6";
        libs[2] = "libamdhip64.so.5"; pfx = "hip";
        b->name = "rocm";
    }
    for (int i = 0; i < 3 && libs[i]; i++) {
        b->handle = dlopen(libs[i], RTLD_LAZY | RTLD_LOCAL);
        if (b->handle) break;
    }
    if (!b->handle) {
        fprintf(stderr, "backend %s unavailable: %s\n", b->name, dlerror());
        return -1;
    }
    char sym[64];
    snprintf(sym, sizeof(sym), "%sMalloc", pfx);
    *(void **)(&b->dev_malloc) = dlsym(b->handle, sym);
    snprintf(sym, sizeof(sym), "%sFree", pfx);
    *(void **)(&b->dev_free) = dlsym(b->handle, sym);
    snprintf(sym, sizeof(sym), "%sMemcpy", pfx);
    *(void **)(&b->dev_memcpy) = dlsym(b->handle, sym);
    if (!b->dev_malloc || !b->dev_free || !b->dev_memcpy) {
        fprintf(stderr, "backend %s missing symbols\n", b->name);
        return -1;
    }
    b->to_device_kind = 1;
    b->to_host_kind   = 2;
    return 0;
}

static void *backend_alloc(backend_t *b, size_t bytes) {
    if (b->kind == MEM_HOST) return calloc(1, bytes);
    void *p = NULL;
    if (b->dev_malloc(&p, bytes) != 0) return NULL;
    return p;
}

static void backend_free(backend_t *b, void *p) {
    if (!p) return;
    if (b->kind == MEM_HOST) free(p); else b->dev_free(p);
}

static int backend_to_device(backend_t *b, void *dst, const void *src, size_t n) {
    if (b->kind == MEM_HOST) { memcpy(dst, src, n); return 0; }
    return b->dev_memcpy(dst, src, n, b->to_device_kind);
}

static int backend_to_host(backend_t *b, void *dst, const void *src, size_t n) {
    if (b->kind == MEM_HOST) { memcpy(dst, src, n); return 0; }
    return b->dev_memcpy(dst, src, n, b->to_host_kind);
}

static ucs_memory_type_t backend_ucs_type(mem_kind kind) {
    switch (kind) {
    case MEM_CUDA: return UCS_MEMORY_TYPE_CUDA;
    case MEM_ROCM: return UCS_MEMORY_TYPE_ROCM;
    default:       return UCS_MEMORY_TYPE_HOST;
    }
}

/* ------------------------------------------------------------- verification */

/* Position-dependent so an offset or truncated result cannot pass. */
static void pattern_fill(uint8_t *buf, size_t n) {
    for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)((i * 31u + 7u) & 0xff);
}

static long pattern_check(const uint8_t *buf, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (buf[i] != (uint8_t)((i * 31u + 7u) & 0xff)) return (long)i;
    }
    return -1;
}

/* --------------------------------------------------------------- bootstrap */

static int tcp_listen_accept(int port) {
    int srv = socket(AF_INET, SOCK_STREAM, 0), opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port),
                            .sin_addr.s_addr = INADDR_ANY};
    if (bind(srv, (struct sockaddr *)&a, sizeof(a)) || listen(srv, 1)) {
        perror("bind/listen"); return -1;
    }
    int fd = accept(srv, NULL, NULL);
    close(srv);
    return fd;
}

static int tcp_connect(const char *host, int port) {
    for (int attempt = 0; attempt < 50; attempt++) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port)};
        inet_pton(AF_INET, host, &a.sin_addr);
        if (connect(fd, (struct sockaddr *)&a, sizeof(a)) == 0) return fd;
        close(fd);
        usleep(200000);
    }
    return -1;
}

static int xfer_all(int fd, void *buf, size_t n, int sending) {
    size_t done = 0;
    while (done < n) {
        ssize_t r = sending ? send(fd, (char *)buf + done, n - done, 0)
                            : recv(fd, (char *)buf + done, n - done, 0);
        if (r <= 0) return -1;
        done += (size_t)r;
    }
    return 0;
}

/* --------------------------------------------------------------------- UCP */

static int await(ucp_worker_h worker, ucs_status_ptr_t req) {
    if (req == NULL) return 0;
    if (UCS_PTR_IS_ERR(req)) {
        fprintf(stderr, "request failed: %s\n",
                ucs_status_string(UCS_PTR_STATUS(req)));
        return -1;
    }
    ucs_status_t st;
    do {
        ucp_worker_progress(worker);
        st = ucp_request_check_status(req);
    } while (st == UCS_INPROGRESS);
    ucp_request_free(req);
    if (st != UCS_OK) {
        fprintf(stderr, "request completed with %s\n", ucs_status_string(st));
        return -1;
    }
    return 0;
}

#define TAG 0x1234ULL

int main(int argc, char **argv) {
    const char *role = "server", *peer = "127.0.0.1", *memname = "host";
    size_t bytes = 4096;
    int port = 18515, detect = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--role") && i + 1 < argc)      role = argv[++i];
        else if (!strcmp(argv[i], "--mem") && i + 1 < argc)  memname = argv[++i];
        else if (!strcmp(argv[i], "--peer") && i + 1 < argc) peer = argv[++i];
        else if (!strcmp(argv[i], "--size") && i + 1 < argc) bytes = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--detect"))               detect = 1;
    }

    setvbuf(stdout, NULL, _IOLBF, 0);

    mem_kind kind = !strcmp(memname, "cuda") ? MEM_CUDA
                  : !strcmp(memname, "rocm") ? MEM_ROCM : MEM_HOST;
    int is_server = !strcmp(role, "server");

    backend_t backend;
    if (backend_open(&backend, kind) != 0) {
        printf("RESULT SKIP backend=%s unavailable\n", memname);
        return 2;
    }

    ucp_config_t *config;
    ucp_context_h context;
    ucp_worker_h worker;
    ucp_params_t params = {.field_mask = UCP_PARAM_FIELD_FEATURES,
                           .features = UCP_FEATURE_TAG};
    ucp_worker_params_t wparams = {.field_mask = UCP_WORKER_PARAM_FIELD_THREAD_MODE,
                                   .thread_mode = UCS_THREAD_MODE_SINGLE};

    if (ucp_config_read(NULL, NULL, &config) != UCS_OK) return 1;
    if (ucp_init(&params, config, &context) != UCS_OK) return 1;
    ucp_config_release(config);
    if (ucp_worker_create(context, &wparams, &worker) != UCS_OK) return 1;

    ucp_address_t *my_addr; size_t my_len;
    if (ucp_worker_get_address(worker, &my_addr, &my_len) != UCS_OK) return 1;

    int fd = is_server ? tcp_listen_accept(port) : tcp_connect(peer, port);
    if (fd < 0) { fprintf(stderr, "bootstrap failed\n"); return 1; }

    /* Exchange worker addresses; only the sender needs an endpoint. */
    uint64_t len_be = my_len;
    ucp_address_t *peer_addr = NULL;
    uint64_t peer_len = 0;
    if (is_server) {
        if (xfer_all(fd, &len_be, sizeof(len_be), 1)) return 1;
        if (xfer_all(fd, my_addr, my_len, 1)) return 1;
    } else {
        if (xfer_all(fd, &peer_len, sizeof(peer_len), 0)) return 1;
        peer_addr = malloc(peer_len);
        if (xfer_all(fd, peer_addr, peer_len, 0)) return 1;
    }

    void *devbuf = backend_alloc(&backend, bytes);
    uint8_t *hostbuf = malloc(bytes);
    if (!devbuf || !hostbuf) {
        printf("RESULT FAIL allocation of %zu bytes on %s\n", bytes, memname);
        return 1;
    }

    ucp_request_param_t rp;
    memset(&rp, 0, sizeof(rp));
    rp.op_attr_mask = UCP_OP_ATTR_FIELD_DATATYPE;
    rp.datatype     = ucp_dt_make_contig(1);
    if (!detect) {
        /* Declare the memory type rather than relying on pointer detection,
         * so detection is not a hidden variable in the first correctness run. */
        rp.op_attr_mask |= UCP_OP_ATTR_FIELD_MEMORY_TYPE;
        rp.memory_type   = backend_ucs_type(kind);
    }

    int rc = 0;
    if (is_server) {
        ucs_status_ptr_t req = ucp_tag_recv_nbx(worker, devbuf, bytes, TAG, (ucp_tag_t)-1, &rp);
        rc = await(worker, req);
        if (rc == 0) {
            memset(hostbuf, 0, bytes);
            if (backend_to_host(&backend, hostbuf, devbuf, bytes) != 0) {
                printf("RESULT FAIL device->host copy back failed\n"); rc = 1;
            } else {
                long bad = pattern_check(hostbuf, bytes);
                if (bad >= 0) {
                    printf("RESULT FAIL mem=%s size=%zu first bad byte at %ld\n",
                           memname, bytes, bad);
                    rc = 1;
                } else {
                    printf("RESULT PASS recv mem=%s size=%zu verified byte-for-byte\n",
                           memname, bytes);
                }
            }
        } else {
            printf("RESULT FAIL recv mem=%s size=%zu transfer error\n", memname, bytes);
        }
        /* Tell the sender the verdict so it can exit with a matching code. */
        uint8_t ok = (rc == 0);
        xfer_all(fd, &ok, 1, 1);
    } else {
        ucp_ep_h ep;
        ucp_ep_params_t ep_params = {.field_mask = UCP_EP_PARAM_FIELD_REMOTE_ADDRESS,
                                     .address = peer_addr};
        if (ucp_ep_create(worker, &ep_params, &ep) != UCS_OK) return 1;
        pattern_fill(hostbuf, bytes);
        if (backend_to_device(&backend, devbuf, hostbuf, bytes) != 0) {
            printf("RESULT FAIL host->device copy failed\n"); return 1;
        }
        ucs_status_ptr_t req = ucp_tag_send_nbx(ep, devbuf, bytes, TAG, &rp);
        rc = await(worker, req);
        uint8_t ok = 0;
        if (rc == 0 && xfer_all(fd, &ok, 1, 0) == 0 && ok) {
            printf("RESULT PASS send mem=%s size=%zu peer verified\n", memname, bytes);
        } else {
            printf("RESULT FAIL send mem=%s size=%zu\n", memname, bytes);
            rc = 1;
        }
        ucp_ep_destroy(ep);
    }

    backend_free(&backend, devbuf);
    free(hostbuf);
    free(peer_addr);
    ucp_worker_release_address(worker, my_addr);
    ucp_worker_destroy(worker);
    ucp_cleanup(context);
    close(fd);
    return rc;
}
