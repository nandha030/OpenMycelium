#ifndef OPENMYCELIUM_HETCCL_ADAPTER_H
#define OPENMYCELIUM_HETCCL_ADAPTER_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define HETCCL_EXPORT __declspec(dllexport)
#else
#define HETCCL_EXPORT __attribute__((visibility("default")))
#endif
#define HETCCL_ADAPTER_ABI_VERSION 1u

#ifdef __cplusplus
extern "C" {
#endif

typedef enum hetccl_status {
  HETCCL_STATUS_OK = 0,
  HETCCL_STATUS_INVALID_ARGUMENT = 1,
  HETCCL_STATUS_UNAVAILABLE = 2,
  HETCCL_STATUS_OUT_OF_MEMORY = 3,
  HETCCL_STATUS_RUNTIME_ERROR = 4,
  HETCCL_STATUS_NOT_QUALIFIED = 5
} hetccl_status;

typedef enum hetccl_memory_kind {
  HETCCL_MEMORY_HOST = 0,
  HETCCL_MEMORY_DEVICE = 1,
  HETCCL_MEMORY_PINNED_HOST = 2
} hetccl_memory_kind;

typedef enum hetccl_copy_kind {
  HETCCL_COPY_HOST_TO_HOST = 0,
  HETCCL_COPY_HOST_TO_DEVICE = 1,
  HETCCL_COPY_DEVICE_TO_HOST = 2,
  HETCCL_COPY_DEVICE_TO_DEVICE = 3
} hetccl_copy_kind;

typedef struct hetccl_device_info {
  uint32_t struct_size;
  int32_t index;
  uint64_t memory_bytes;
  char vendor[32];
  char runtime[32];
  char name[128];
  char architecture[64];
} hetccl_device_info;

typedef struct hetccl_adapter_ops {
  uint32_t struct_size;
  uint32_t abi_version;
  const char *adapter_name;
  const char *runtime_name;

  hetccl_status (*device_count)(int32_t *count);
  hetccl_status (*device_info)(int32_t index, hetccl_device_info *info);
  hetccl_status (*set_device)(int32_t index);
  hetccl_status (*allocate)(size_t bytes, hetccl_memory_kind kind, void **pointer);
  hetccl_status (*release)(void *pointer, hetccl_memory_kind kind);
  hetccl_status (*copy_async)(void *destination, const void *source, size_t bytes,
                              hetccl_copy_kind kind, void *stream);
  hetccl_status (*stream_synchronize)(void *stream);
  hetccl_status (*event_create)(void **event);
  hetccl_status (*event_record)(void *event, void *stream);
  hetccl_status (*event_synchronize)(void *event);
  hetccl_status (*event_destroy)(void *event);
  hetccl_status (*reduce_sum_f32)(float *destination, const float *source,
                                  size_t count, void *stream);
  const char *(*last_error)(void);
} hetccl_adapter_ops;

typedef hetccl_status (*hetccl_adapter_query_fn)(uint32_t requested_abi,
                                                 hetccl_adapter_ops *ops);

HETCCL_EXPORT hetccl_status hetccl_adapter_query(uint32_t requested_abi,
                                                 hetccl_adapter_ops *ops);

#ifdef __cplusplus
}
#endif

#endif
