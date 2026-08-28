#ifndef OPENMYCELIUM_MCCL_ADAPTER_H
#define OPENMYCELIUM_MCCL_ADAPTER_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define MCCL_EXPORT __declspec(dllexport)
#else
#define MCCL_EXPORT __attribute__((visibility("default")))
#endif
#define MCCL_ADAPTER_ABI_VERSION 1u

#ifdef __cplusplus
extern "C" {
#endif

typedef enum mccl_status {
  MCCL_STATUS_OK = 0,
  MCCL_STATUS_INVALID_ARGUMENT = 1,
  MCCL_STATUS_UNAVAILABLE = 2,
  MCCL_STATUS_OUT_OF_MEMORY = 3,
  MCCL_STATUS_RUNTIME_ERROR = 4,
  MCCL_STATUS_NOT_QUALIFIED = 5
} mccl_status;

typedef enum mccl_memory_kind {
  MCCL_MEMORY_HOST = 0,
  MCCL_MEMORY_DEVICE = 1,
  MCCL_MEMORY_PINNED_HOST = 2
} mccl_memory_kind;

typedef enum mccl_copy_kind {
  MCCL_COPY_HOST_TO_HOST = 0,
  MCCL_COPY_HOST_TO_DEVICE = 1,
  MCCL_COPY_DEVICE_TO_HOST = 2,
  MCCL_COPY_DEVICE_TO_DEVICE = 3
} mccl_copy_kind;

typedef struct mccl_device_info {
  uint32_t struct_size;
  int32_t index;
  uint64_t memory_bytes;
  char vendor[32];
  char runtime[32];
  char name[128];
  char architecture[64];
} mccl_device_info;

typedef struct mccl_adapter_ops {
  uint32_t struct_size;
  uint32_t abi_version;
  const char *adapter_name;
  const char *runtime_name;

  mccl_status (*device_count)(int32_t *count);
  mccl_status (*device_info)(int32_t index, mccl_device_info *info);
  mccl_status (*set_device)(int32_t index);
  mccl_status (*allocate)(size_t bytes, mccl_memory_kind kind, void **pointer);
  mccl_status (*release)(void *pointer, mccl_memory_kind kind);
  mccl_status (*copy_async)(void *destination, const void *source, size_t bytes,
                              mccl_copy_kind kind, void *stream);
  mccl_status (*stream_synchronize)(void *stream);
  mccl_status (*event_create)(void **event);
  mccl_status (*event_record)(void *event, void *stream);
  mccl_status (*event_synchronize)(void *event);
  mccl_status (*event_destroy)(void *event);
  mccl_status (*reduce_sum_f32)(float *destination, const float *source,
                                  size_t count, void *stream);
  const char *(*last_error)(void);
} mccl_adapter_ops;

typedef mccl_status (*mccl_adapter_query_fn)(uint32_t requested_abi,
                                                 mccl_adapter_ops *ops);

MCCL_EXPORT mccl_status mccl_adapter_query(uint32_t requested_abi,
                                                 mccl_adapter_ops *ops);

#ifdef __cplusplus
}
#endif

#endif
