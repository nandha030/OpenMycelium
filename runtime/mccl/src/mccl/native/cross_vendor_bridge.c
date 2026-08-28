/* OpenMycelium cross-vendor host-staged bridge: NVIDIA VRAM <-> AMD VRAM.
 *
 *   NVIDIA VRAM --cudaMemcpyAsync--> pinned host chunk
 *               --TCP/shared memory-->
 *   pinned host chunk --hipMemcpyAsync--> AMD VRAM
 *
 * Device memory is never handed to the transport. That is not a stylistic
 * choice: on ROCm-for-WSL (DXG path) hipMalloc memory is not CPU-accessible at
 * all, and any transport that memcpys into it faults. Staging through pinned
 * host memory is the only qualified path until UCX grows a memory-domain
 * capability that forces receive staging, or implements its ROCm copy through
 * hipMemcpyAsync when host access is unavailable.
 *
 * Design points:
 *   pinned pool        N reusable pinned host chunks per side, allocated once
 *   double buffering   copy engine works on one chunk while the wire moves another
 *   chunking           1-16 MiB, so a transfer of any size has bounded memory
 *   event-driven       stream events gate reuse, not blanket synchronisation
 *   credit-based       receiver acks each consumed chunk; sender holds a window
 *   checksums          CRC32 per chunk, enabled during qualification runs
 *
 * Backends are resolved with dlopen, so the NVIDIA side never loads HIP and
 * the AMD side never loads CUDA -- matching a real mixed deployment where each
 * host has only its own vendor runtime installed.
 */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define MEMCPY_H2D 1
#define MEMCPY_D2H 2
#define MAX_CHUNKS 8

/* ------------------------------------------------------------- vendor shim */

typedef struct {
    const char *vendor;
    void *lib;
    int (*dev_malloc)(void **, size_t);
    int (*dev_free)(void *);
    int (*host_alloc)(void **, size_t, unsigned);
    int (*host_free)(void *);
    int (*memcpy_sync)(void *, const void *, size_t, int);
    int (*memcpy_async)(void *, const void *, size_t, int, void *);
    int (*memset_sync)(void *, int, size_t);
    int (*stream_create)(void **);
    int (*stream_sync)(void *);
    int (*event_create)(void **);
    int (*event_record)(void *, void *);
    int (*event_query)(void *);
    int (*event_sync)(void *);
} vendor_t;

static void *sym(void *lib, const char *pfx, const char *name) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%s%s", pfx, name);
    return dlsym(lib, buf);
}

static int vendor_open(vendor_t *v, const char *vendor) {
    memset(v, 0, sizeof(*v));
    v->vendor = vendor;
    const char *cands[4] = {0};
    const char *pfx, *host_alloc_name, *host_free_name;

    if (!strcmp(vendor, "cuda")) {
        cands[0] = "libcudart.so.12"; cands[1] = "libcudart.so";
        pfx = "cuda"; host_alloc_name = "HostAlloc"; host_free_name = "FreeHost";
    } else {
        cands[0] = "libamdhip64.so"; cands[1] = "libamdhip64.so.7";
        cands[2] = "libamdhip64.so.6";
        pfx = "hip";  host_alloc_name = "HostMalloc"; host_free_name = "HostFree";
    }
    for (int i = 0; i < 4 && cands[i]; i++) {
        if ((v->lib = dlopen(cands[i], RTLD_LAZY | RTLD_LOCAL))) break;
    }
    if (!v->lib) { fprintf(stderr, "cannot load %s runtime: %s\n", vendor, dlerror()); return -1; }

    *(void **)&v->dev_malloc    = sym(v->lib, pfx, "Malloc");
    *(void **)&v->dev_free      = sym(v->lib, pfx, "Free");
    *(void **)&v->host_alloc    = sym(v->lib, pfx, host_alloc_name);
    *(void **)&v->host_free     = sym(v->lib, pfx, host_free_name);
    *(void **)&v->memcpy_sync   = sym(v->lib, pfx, "Memcpy");
    *(void **)&v->memcpy_async  = sym(v->lib, pfx, "MemcpyAsync");
    *(void **)&v->memset_sync   = sym(v->lib, pfx, "Memset");
    *(void **)&v->stream_create = sym(v->lib, pfx, "StreamCreate");
    *(void **)&v->stream_sync   = sym(v->lib, pfx, "StreamSynchronize");
    *(void **)&v->event_create  = sym(v->lib, pfx, "EventCreate");
    *(void **)&v->event_record  = sym(v->lib, pfx, "EventRecord");
    *(void **)&v->event_query   = sym(v->lib, pfx, "EventQuery");
    *(void **)&v->event_sync    = sym(v->lib, pfx, "EventSynchronize");

    if (!v->dev_malloc || !v->host_alloc || !v->memcpy_async ||
        !v->stream_create || !v->event_create || !v->event_record) {
        fprintf(stderr, "%s runtime is missing required symbols\n", vendor);
        return -1;
    }
    return 0;
}

/* --------------------------------------------------------------- utilities */

static uint32_t crc32_of(const uint8_t *p, size_t n) {
    static uint32_t table[256];
    static int ready = 0;
    if (!ready) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
        ready = 1;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = table[(c ^ p[i]) & 0xff] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

static void pattern_fill(uint8_t *p, size_t n, size_t offset) {
    for (size_t i = 0; i < n; i++) p[i] = (uint8_t)(((offset + i) * 31u + 7u) & 0xff);
}

static long pattern_check(const uint8_t *p, size_t n, size_t offset) {
    for (size_t i = 0; i < n; i++) {
        if (p[i] != (uint8_t)(((offset + i) * 31u + 7u) & 0xff)) return (long)i;
    }
    return -1;
}

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int xfer(int fd, void *buf, size_t n, int sending) {
    size_t done = 0;
    while (done < n) {
        ssize_t r = sending ? send(fd, (char *)buf + done, n - done, 0)
                            : recv(fd, (char *)buf + done, n - done, 0);
        if (r <= 0) return -1;
        done += (size_t)r;
    }
    return 0;
}

static int tcp_accept(int port) {
    int srv = socket(AF_INET, SOCK_STREAM, 0), on = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port),
                            .sin_addr.s_addr = INADDR_ANY};
    if (bind(srv, (struct sockaddr *)&a, sizeof(a)) || listen(srv, 1)) return -1;
    int fd = accept(srv, NULL, NULL);
    close(srv);
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));
    return fd;
}

static int tcp_connect(const char *host, int port) {
    for (int i = 0; i < 100; i++) {
        int fd = socket(AF_INET, SOCK_STREAM, 0), on = 1;
        struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(port)};
        inet_pton(AF_INET, host, &a.sin_addr);
        if (connect(fd, (struct sockaddr *)&a, sizeof(a)) == 0) {
            setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));
            return fd;
        }
        close(fd);
        usleep(100000);
    }
    return -1;
}

/* ----------------------------------------------------------- chunk framing */

typedef struct {
    uint64_t sequence;
    uint64_t offset;
    uint32_t length;
    uint32_t crc;          /* 0 when checksums are disabled */
} chunk_header_t;

/* One pinned staging slot plus the event that says its copy has retired. */
typedef struct {
    void *host;
    void *event;
    int in_flight;
} slot_t;

/* --------------------------------------------------------------- transfers */

static int run_sender(vendor_t *v, int fd, size_t total, size_t chunk,
                      int slots, int window, int checksum) {
    void *dev = NULL, *stream = NULL;
    if (v->dev_malloc(&dev, total) != 0) { fprintf(stderr, "device alloc failed\n"); return 1; }
    if (v->stream_create(&stream) != 0)  { fprintf(stderr, "stream create failed\n"); return 1; }

    /* Seed device memory with the pattern via a staging buffer, since device
     * memory may not be writable from the CPU. */
    uint8_t *seed = malloc(chunk);
    for (size_t off = 0; off < total; off += chunk) {
        size_t n = (total - off < chunk) ? total - off : chunk;
        pattern_fill(seed, n, off);
        if (v->memcpy_sync((char *)dev + off, seed, n, MEMCPY_H2D) != 0) {
            fprintf(stderr, "seed copy failed\n"); return 1;
        }
    }
    free(seed);

    slot_t pool[MAX_CHUNKS];
    for (int i = 0; i < slots; i++) {
        if (v->host_alloc(&pool[i].host, chunk, 0) != 0) {
            fprintf(stderr, "pinned host alloc failed\n"); return 1;
        }
        v->event_create(&pool[i].event);
        pool[i].in_flight = 0;
    }

    uint64_t seq = 0, acked = 0;
    size_t offset = 0;
    double t0 = now_sec();

    while (offset < total) {
        /* Credit-based backpressure: never let more than `window` chunks be
         * outstanding, so a slow receiver throttles the copy engine rather
         * than growing an unbounded queue. */
        while (seq - acked >= (uint64_t)window) {
            uint64_t ack;
            if (xfer(fd, &ack, sizeof(ack), 0)) { fprintf(stderr, "ack channel closed\n"); return 1; }
            acked = ack;
        }

        slot_t *s = &pool[seq % slots];
        if (s->in_flight) { v->event_sync(s->event); s->in_flight = 0; }

        size_t n = (total - offset < chunk) ? total - offset : chunk;
        if (v->memcpy_async(s->host, (char *)dev + offset, n, MEMCPY_D2H, stream) != 0) {
            fprintf(stderr, "D2H async copy failed\n"); return 1;
        }
        v->event_record(s->event, stream);
        v->event_sync(s->event);          /* chunk is now in pinned host memory */
        s->in_flight = 0;

        chunk_header_t h = {.sequence = seq, .offset = offset, .length = (uint32_t)n,
                            .crc = checksum ? crc32_of(s->host, n) : 0};
        if (xfer(fd, &h, sizeof(h), 1) || xfer(fd, s->host, n, 1)) {
            fprintf(stderr, "wire send failed at seq %lu\n", (unsigned long)seq); return 1;
        }
        offset += n;
        seq++;
    }

    chunk_header_t end = {.sequence = seq, .offset = 0, .length = 0, .crc = 0};
    xfer(fd, &end, sizeof(end), 1);
    while (acked < seq) {
        uint64_t ack;
        if (xfer(fd, &ack, sizeof(ack), 0)) break;
        acked = ack;
    }
    double dt = now_sec() - t0;

    printf("{\"role\":\"sender\",\"vendor\":\"%s\",\"bytes\":%zu,\"chunk\":%zu,"
           "\"slots\":%d,\"window\":%d,\"checksum\":%s,\"chunks\":%lu,"
           "\"seconds\":%.3f,\"MBps\":%.1f}\n",
           v->vendor, total, chunk, slots, window, checksum ? "true" : "false",
           (unsigned long)seq, dt, (total / (1024.0 * 1024.0)) / (dt > 0 ? dt : 1));

    for (int i = 0; i < slots; i++) if (v->host_free) v->host_free(pool[i].host);
    v->dev_free(dev);
    return 0;
}

static int run_receiver(vendor_t *v, int fd, size_t total, size_t chunk,
                        int slots, int checksum) {
    void *dev = NULL, *stream = NULL;
    if (v->dev_malloc(&dev, total) != 0) { fprintf(stderr, "device alloc failed\n"); return 1; }
    if (v->memset_sync) v->memset_sync(dev, 0, total);
    if (v->stream_create(&stream) != 0) { fprintf(stderr, "stream create failed\n"); return 1; }

    slot_t pool[MAX_CHUNKS];
    for (int i = 0; i < slots; i++) {
        if (v->host_alloc(&pool[i].host, chunk, 0) != 0) {
            fprintf(stderr, "pinned host alloc failed\n"); return 1;
        }
        v->event_create(&pool[i].event);
        pool[i].in_flight = 0;
    }

    uint64_t consumed = 0, bad_crc = 0;
    double t0 = now_sec();

    for (;;) {
        chunk_header_t h;
        if (xfer(fd, &h, sizeof(h), 0)) { fprintf(stderr, "wire recv failed\n"); return 1; }
        if (h.length == 0) break;

        slot_t *s = &pool[h.sequence % slots];
        if (s->in_flight) { v->event_sync(s->event); s->in_flight = 0; }

        if (xfer(fd, s->host, h.length, 0)) { fprintf(stderr, "payload recv failed\n"); return 1; }
        if (checksum && crc32_of(s->host, h.length) != h.crc) bad_crc++;

        /* The transport wrote into pinned HOST memory. Only the vendor runtime
         * ever touches device memory, which is what makes this safe on a
         * device that is not CPU-accessible. */
        if (v->memcpy_async((char *)dev + h.offset, s->host, h.length, MEMCPY_H2D, stream) != 0) {
            fprintf(stderr, "H2D async copy failed\n"); return 1;
        }
        v->event_record(s->event, stream);
        s->in_flight = 1;

        consumed = h.sequence + 1;
        if (xfer(fd, &consumed, sizeof(consumed), 1)) { fprintf(stderr, "ack send failed\n"); return 1; }
    }

    for (int i = 0; i < slots; i++) if (pool[i].in_flight) v->event_sync(pool[i].event);
    v->stream_sync(stream);
    double dt = now_sec() - t0;

    /* Verify the whole landed buffer through the vendor runtime. */
    uint8_t *check = malloc(chunk);
    long first_bad = -1;
    size_t bad_at = 0;
    for (size_t off = 0; off < total && first_bad < 0; off += chunk) {
        size_t n = (total - off < chunk) ? total - off : chunk;
        if (v->memcpy_sync(check, (char *)dev + off, n, MEMCPY_D2H) != 0) {
            fprintf(stderr, "verify D2H failed\n"); return 1;
        }
        long bad = pattern_check(check, n, off);
        if (bad >= 0) { first_bad = bad; bad_at = off; }
    }
    free(check);

    int ok = (first_bad < 0) && (bad_crc == 0);
    printf("{\"role\":\"receiver\",\"vendor\":\"%s\",\"bytes\":%zu,\"chunk\":%zu,"
           "\"slots\":%d,\"checksum\":%s,\"chunks\":%lu,\"badCrc\":%lu,"
           "\"seconds\":%.3f,\"MBps\":%.1f,\"verified\":%s%s}\n",
           v->vendor, total, chunk, slots, checksum ? "true" : "false",
           (unsigned long)consumed, (unsigned long)bad_crc, dt,
           (total / (1024.0 * 1024.0)) / (dt > 0 ? dt : 1),
           ok ? "true" : "false",
           ok ? "" : (first_bad >= 0 ? ",\"firstBadOffset\":0" : ""));
    if (first_bad >= 0) fprintf(stderr, "first mismatch at byte %zu+%ld\n", bad_at, first_bad);

    for (int i = 0; i < slots; i++) if (v->host_free) v->host_free(pool[i].host);
    v->dev_free(dev);
    return ok ? 0 : 1;
}

/* -------------------------------------------------------------------- main */

/* --------------------------------------------------- activation stream mode */

/* Deterministic per-transfer shape and dtype, so both sides agree without
 * negotiating and the test exercises dynamic metadata rather than one fixed
 * descriptor. Sizes deliberately straddle the credit window: most transfers are
 * far smaller than one chunk, and every 64th spans several. */
typedef struct { const char *name; int elem; } dtype_t;
static const dtype_t STREAM_DTYPES[] = {
    {"f16", 2}, {"bf16", 2}, {"f32", 4}, {"f64", 8},
    {"i8", 1}, {"i32", 4}, {"i64", 8}, {"u8", 1},
};
#define STREAM_NDTYPES ((int)(sizeof(STREAM_DTYPES) / sizeof(STREAM_DTYPES[0])))
#define STREAM_MAGIC 0x584D4331u

typedef struct {
    uint32_t magic;
    uint64_t sequence;
    uint32_t dtype_index;
    uint32_t rank;
    int64_t  dims[4];
    uint64_t bytes;
    uint32_t crc;
} activation_header_t;

static void stream_descriptor(uint64_t i, activation_header_t *h, size_t cap) {
    uint64_t r = i * 6364136223846793005ULL + 1442695040888963407ULL;
    memset(h, 0, sizeof(*h));
    h->magic = STREAM_MAGIC;
    h->sequence = i;
    h->dtype_index = (uint32_t)((r >> 13) % STREAM_NDTYPES);
    h->rank = (uint32_t)(1 + ((r >> 21) % 3));
    int elem = STREAM_DTYPES[h->dtype_index].elem;

    uint64_t target = (i % 64 == 63) ? (cap / 2 + (r % (cap / 4)))
                                     : (1 + (r % (cap / 16)));
    uint64_t elems = target / (uint64_t)elem;
    if (elems == 0) elems = 1;

    if (h->rank == 1) {
        h->dims[0] = (int64_t)elems;
    } else if (h->rank == 2) {
        int64_t a = (int64_t)(1 + (r >> 33) % 64);
        h->dims[0] = a;
        h->dims[1] = (int64_t)(elems / (uint64_t)a + 1);
    } else {
        int64_t a = (int64_t)(1 + (r >> 33) % 8);
        int64_t b = (int64_t)(1 + (r >> 41) % 16);
        h->dims[0] = a;
        h->dims[1] = b;
        h->dims[2] = (int64_t)(elems / (uint64_t)(a * b) + 1);
    }
    uint64_t total = (uint64_t)elem;
    for (uint32_t d = 0; d < h->rank; d++) total *= (uint64_t)h->dims[d];
    h->bytes = total;
}

static int header_is_sane(const activation_header_t *h, size_t cap_bytes) {
    if (h->magic != STREAM_MAGIC) return 0;
    if (h->dtype_index >= (uint32_t)STREAM_NDTYPES) return 0;
    if (h->rank == 0 || h->rank > 4) return 0;
    for (uint32_t d = 0; d < h->rank; d++) if (h->dims[d] <= 0) return 0;
    if (h->bytes == 0 || h->bytes > cap_bytes) return 0;
    return 1;
}

static int stream_sender(vendor_t *v, int fd, uint64_t transfers, size_t chunk,
                         int slots, int window, int checksum, long stall_at) {
    size_t cap = chunk * 4;
    void *dev = NULL, *stream = NULL;
    if (v->dev_malloc(&dev, cap) != 0) { fprintf(stderr, "device alloc failed\n"); return 1; }
    if (v->stream_create(&stream) != 0) { fprintf(stderr, "stream create failed\n"); return 1; }

    slot_t pool[MAX_CHUNKS];
    int host_allocs = 0;
    for (int i = 0; i < slots; i++) {
        if (v->host_alloc(&pool[i].host, cap, 0) != 0) {
            fprintf(stderr, "pinned host alloc failed\n"); return 1;
        }
        host_allocs++;
        v->event_create(&pool[i].event);
        pool[i].in_flight = 0;
    }

    uint8_t *seed = malloc(cap);
    uint64_t acked = 0, sent_bytes = 0, credit_waits = 0;
    double t0 = now_sec();

    for (uint64_t i = 0; i < transfers; i++) {
        activation_header_t h;
        stream_descriptor(i, &h, cap);

        while (i - acked >= (uint64_t)window) {
            uint64_t ack;
            /* Reaching here means the credit window genuinely throttled the
             * sender. The forced-backpressure test asserts this is non-zero;
             * a run that never stalls has not exercised throttling at all. */
            credit_waits++;
            if (xfer(fd, &ack, sizeof(ack), 0)) {
                fprintf(stderr, "ERR direction=send phase=credit seq=%lu: ack channel closed\n",
                        (unsigned long)i);
                return 1;
            }
            acked = ack;
        }

        if (stall_at >= 0 && (uint64_t)stall_at == i) {
            /* Write a header, then stop: the peer must time out between header
             * and payload rather than block forever. */
            fprintf(stderr, "ERR direction=send phase=header seq=%lu: deliberate stall\n",
                    (unsigned long)i);
            xfer(fd, &h, sizeof(h), 1);
            for (;;) sleep(60);
        }

        pattern_fill(seed, h.bytes, (size_t)(i * 7919));
        if (v->memcpy_sync(dev, seed, h.bytes, MEMCPY_H2D) != 0) {
            fprintf(stderr, "ERR direction=send phase=stage seq=%lu\n", (unsigned long)i);
            return 1;
        }

        slot_t *s = &pool[i % slots];
        if (s->in_flight) { v->event_sync(s->event); s->in_flight = 0; }
        if (v->memcpy_async(s->host, dev, h.bytes, MEMCPY_D2H, stream) != 0) {
            fprintf(stderr, "ERR direction=send phase=d2h seq=%lu\n", (unsigned long)i);
            return 1;
        }
        v->event_record(s->event, stream);
        v->event_sync(s->event);

        if (checksum) h.crc = crc32_of(s->host, h.bytes);
        if (xfer(fd, &h, sizeof(h), 1) || xfer(fd, s->host, h.bytes, 1)) {
            fprintf(stderr, "ERR direction=send phase=payload seq=%lu\n", (unsigned long)i);
            return 1;
        }
        sent_bytes += h.bytes;
    }

    activation_header_t end;
    memset(&end, 0, sizeof(end));
    end.magic = STREAM_MAGIC;
    end.sequence = transfers;
    xfer(fd, &end, sizeof(end), 1);
    while (acked < transfers) {
        uint64_t ack;
        if (xfer(fd, &ack, sizeof(ack), 0)) break;
        acked = ack;
    }
    double dt = now_sec() - t0;

    printf("{\"role\":\"stream-sender\",\"vendor\":\"%s\",\"transfers\":%lu,"
           "\"bytes\":%lu,\"slots\":%d,\"window\":%d,\"hostAllocs\":%d,"
           "\"pinnedBytes\":%lu,\"creditWaits\":%lu,\"seconds\":%.3f,\"MBps\":%.1f}\n",
           v->vendor, (unsigned long)transfers, (unsigned long)sent_bytes,
           slots, window, host_allocs, (unsigned long)((size_t)slots * cap),
           (unsigned long)credit_waits, dt,
           (sent_bytes / (1024.0 * 1024.0)) / (dt > 0 ? dt : 1));

    for (int i = 0; i < slots; i++) v->host_free(pool[i].host);
    v->dev_free(dev);
    free(seed);
    return 0;
}

static int stream_receiver(vendor_t *v, int fd, size_t chunk, int slots,
                           int checksum, int device_check_every, int delay_ms) {
    size_t cap = chunk * 4;
    void *dev = NULL, *stream = NULL;
    if (v->dev_malloc(&dev, cap) != 0) { fprintf(stderr, "device alloc failed\n"); return 1; }
    if (v->stream_create(&stream) != 0) { fprintf(stderr, "stream create failed\n"); return 1; }

    slot_t pool[MAX_CHUNKS];
    int host_allocs = 0;
    for (int i = 0; i < slots; i++) {
        if (v->host_alloc(&pool[i].host, cap, 0) != 0) {
            fprintf(stderr, "pinned host alloc failed\n"); return 1;
        }
        host_allocs++;
        v->event_create(&pool[i].event);
        pool[i].in_flight = 0;
    }

    uint8_t *check = malloc(cap);
    uint64_t expected = 0, bad_payload = 0, bad_crc = 0, bad_header = 0;
    uint64_t device_checks = 0, bad_device = 0, waits = 0, total_bytes = 0;
    double t0 = now_sec();

    for (;;) {
        activation_header_t h;
        if (xfer(fd, &h, sizeof(h), 0)) {
            fprintf(stderr, "ERR direction=recv phase=header seq=%lu: peer closed\n",
                    (unsigned long)expected);
            return 1;
        }
        if (h.magic == STREAM_MAGIC && h.bytes == 0 && h.rank == 0) break;

        if (!header_is_sane(&h, cap)) {
            fprintf(stderr, "ERR direction=recv phase=header seq=%lu: malformed descriptor "
                    "(magic=%#x dtype=%u rank=%u bytes=%lu)\n",
                    (unsigned long)expected, h.magic, h.dtype_index, h.rank,
                    (unsigned long)h.bytes);
            bad_header++;
            return 2;
        }
        if (h.sequence != expected) {
            fprintf(stderr, "ERR direction=recv phase=header seq=%lu: out of order, got %lu\n",
                    (unsigned long)expected, (unsigned long)h.sequence);
            return 2;
        }

        slot_t *s = &pool[h.sequence % slots];
        if (s->in_flight) {
            /* Slot reuse must never precede its copy retiring. A pending event
             * here is the hazard this test exists to catch. */
            if (v->event_query && v->event_query(s->event) != 0) waits++;
            v->event_sync(s->event);
            s->in_flight = 0;
        }

        if (xfer(fd, s->host, h.bytes, 0)) {
            fprintf(stderr, "ERR direction=recv phase=payload seq=%lu: short read of %lu bytes\n",
                    (unsigned long)h.sequence, (unsigned long)h.bytes);
            return 1;
        }
        if (checksum && crc32_of(s->host, h.bytes) != h.crc) bad_crc++;
        if (pattern_check(s->host, h.bytes, (size_t)(h.sequence * 7919)) >= 0) bad_payload++;

        if (v->memcpy_async(dev, s->host, h.bytes, MEMCPY_H2D, stream) != 0) {
            fprintf(stderr, "ERR direction=recv phase=h2d seq=%lu\n", (unsigned long)h.sequence);
            return 1;
        }
        v->event_record(s->event, stream);
        s->in_flight = 1;

        /* The host-side check above proves the wire; this periodic round trip
         * proves the copy actually landed in device memory. */
        if (device_check_every > 0 && (h.sequence % (uint64_t)device_check_every) == 0) {
            v->event_sync(s->event);
            s->in_flight = 0;
            if (v->memcpy_sync(check, dev, h.bytes, MEMCPY_D2H) != 0) return 1;
            if (pattern_check(check, h.bytes, (size_t)(h.sequence * 7919)) >= 0) bad_device++;
            device_checks++;
        }

        total_bytes += h.bytes;
        expected = h.sequence + 1;
        /* Holding the ack back holds the sender's credits, which is how the
         * forced-backpressure test makes the window actually bind. */
        if (delay_ms > 0) usleep((useconds_t)delay_ms * 1000);
        if (xfer(fd, &expected, sizeof(expected), 1)) {
            fprintf(stderr, "ERR direction=recv phase=credit seq=%lu\n",
                    (unsigned long)h.sequence);
            return 1;
        }
    }

    for (int i = 0; i < slots; i++) if (pool[i].in_flight) v->event_sync(pool[i].event);
    v->stream_sync(stream);
    double dt = now_sec() - t0;

    int ok = (bad_payload == 0 && bad_crc == 0 && bad_device == 0 && bad_header == 0);
    printf("{\"role\":\"stream-receiver\",\"vendor\":\"%s\",\"transfers\":%lu,"
           "\"bytes\":%lu,\"slots\":%d,\"hostAllocs\":%d,\"pinnedBytes\":%lu,"
           "\"badPayload\":%lu,\"badCrc\":%lu,\"badDevice\":%lu,\"badHeader\":%lu,"
           "\"deviceChecks\":%lu,\"backpressureWaits\":%lu,"
           "\"seconds\":%.3f,\"MBps\":%.1f,\"verified\":%s}\n",
           v->vendor, (unsigned long)expected, (unsigned long)total_bytes, slots,
           host_allocs, (unsigned long)((size_t)slots * cap), (unsigned long)bad_payload,
           (unsigned long)bad_crc, (unsigned long)bad_device, (unsigned long)bad_header,
           (unsigned long)device_checks, (unsigned long)waits, dt,
           (total_bytes / (1024.0 * 1024.0)) / (dt > 0 ? dt : 1),
           ok ? "true" : "false");

    for (int i = 0; i < slots; i++) v->host_free(pool[i].host);
    v->dev_free(dev);
    free(check);
    return ok ? 0 : 1;
}

/* -------------------------------------------------------------------- main */

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    const char *role = "recv", *vendor = "cuda", *peer = "127.0.0.1", *mode = "bulk";
    size_t total = 64u << 20, chunk = 4u << 20;
    int port = 21000, slots = 2, window = 2, checksum = 1;
    unsigned long long transfers = 100;
    long stall_at = -1;
    int device_check_every = 32, recv_delay_ms = 0;

    for (int i = 1; i < argc; i++) {
        if      (!strcmp(argv[i], "--role")   && i + 1 < argc) role = argv[++i];
        else if (!strcmp(argv[i], "--vendor") && i + 1 < argc) vendor = argv[++i];
        else if (!strcmp(argv[i], "--peer")   && i + 1 < argc) peer = argv[++i];
        else if (!strcmp(argv[i], "--mode")   && i + 1 < argc) mode = argv[++i];
        else if (!strcmp(argv[i], "--bytes")  && i + 1 < argc) total = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--chunk")  && i + 1 < argc) chunk = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--port")   && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--slots")  && i + 1 < argc) slots = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--window") && i + 1 < argc) window = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--transfers") && i + 1 < argc) transfers = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--stall-at") && i + 1 < argc) stall_at = strtol(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--device-check-every") && i + 1 < argc) device_check_every = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--recv-delay-ms") && i + 1 < argc) recv_delay_ms = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--no-checksum")) checksum = 0;
    }
    if (slots < 2) slots = 2;
    if (slots > MAX_CHUNKS) slots = MAX_CHUNKS;
    if (window > slots) window = slots;
    if (chunk > total && strcmp(mode, "stream") != 0) chunk = total;

    vendor_t v;
    if (vendor_open(&v, vendor) != 0) {
        printf("{\"result\":\"skip\",\"reason\":\"%s runtime unavailable\"}\n", vendor);
        return 2;
    }

    int is_recv = !strcmp(role, "recv");
    int fd = is_recv ? tcp_accept(port) : tcp_connect(peer, port);
    if (fd < 0) { fprintf(stderr, "bootstrap failed\n"); return 1; }

    int rc;
    if (!strcmp(mode, "stream")) {
        rc = is_recv ? stream_receiver(&v, fd, chunk, slots, checksum, device_check_every, recv_delay_ms)
                     : stream_sender(&v, fd, transfers, chunk, slots, window, checksum, stall_at);
    } else {
        rc = is_recv ? run_receiver(&v, fd, total, chunk, slots, checksum)
                     : run_sender(&v, fd, total, chunk, slots, window, checksum);
    }
    close(fd);
    return rc;
}
