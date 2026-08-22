#include "hetccl/adapter.h"

#include <cstdlib>
#include <cstring>
#include <new>

namespace {
thread_local const char *last_error_message = "";

hetccl_status device_count(int32_t *count) {
  if (count == nullptr) return HETCCL_STATUS_INVALID_ARGUMENT;
  *count = 1;
  return HETCCL_STATUS_OK;
}
hetccl_status device_info(int32_t index, hetccl_device_info *info) {
  if (index != 0 || info == nullptr || info->struct_size < sizeof(*info))
    return HETCCL_STATUS_INVALID_ARGUMENT;
  info->index = 0;
  info->memory_bytes = 0;
  std::strncpy(info->vendor, "cpu", sizeof(info->vendor) - 1);
  std::strncpy(info->runtime, "host", sizeof(info->runtime) - 1);
  std::strncpy(info->name, "Portable host memory", sizeof(info->name) - 1);
  return HETCCL_STATUS_OK;
}

hetccl_status set_device(int32_t index) {
  return index == 0 ? HETCCL_STATUS_OK : HETCCL_STATUS_INVALID_ARGUMENT;
}

hetccl_status allocate(size_t bytes, hetccl_memory_kind kind, void **pointer) {
  if (pointer == nullptr || kind == HETCCL_MEMORY_DEVICE)
    return HETCCL_STATUS_INVALID_ARGUMENT;
  *pointer = std::malloc(bytes == 0 ? 1 : bytes);
  return *pointer == nullptr ? HETCCL_STATUS_OUT_OF_MEMORY : HETCCL_STATUS_OK;
}

hetccl_status release(void *pointer, hetccl_memory_kind kind) {
  if (kind == HETCCL_MEMORY_DEVICE) return HETCCL_STATUS_INVALID_ARGUMENT;
  std::free(pointer);
  return HETCCL_STATUS_OK;
}

hetccl_status copy_async(void *destination, const void *source, size_t bytes,
                         hetccl_copy_kind kind, void *) {
  if (destination == nullptr || source == nullptr ||
      kind != HETCCL_COPY_HOST_TO_HOST)
    return HETCCL_STATUS_INVALID_ARGUMENT;
  std::memcpy(destination, source, bytes);
  return HETCCL_STATUS_OK;
}

hetccl_status synchronize(void *) { return HETCCL_STATUS_OK; }

hetccl_status event_create(void **event) {
  if (event == nullptr) return HETCCL_STATUS_INVALID_ARGUMENT;
  *event = new (std::nothrow) uint8_t(0);
  return *event == nullptr ? HETCCL_STATUS_OUT_OF_MEMORY : HETCCL_STATUS_OK;
}

hetccl_status event_record(void *, void *) { return HETCCL_STATUS_OK; }
hetccl_status event_synchronize(void *) { return HETCCL_STATUS_OK; }

hetccl_status event_destroy(void *event) {
  delete static_cast<uint8_t *>(event);
  return HETCCL_STATUS_OK;
}

hetccl_status reduce_sum_f32(float *destination, const float *source,
                             size_t count, void *) {
  if (destination == nullptr || source == nullptr)
    return HETCCL_STATUS_INVALID_ARGUMENT;
  for (size_t index = 0; index < count; ++index) destination[index] += source[index];
  return HETCCL_STATUS_OK;
}

const char *last_error() { return last_error_message; }
}  // namespace

extern "C" HETCCL_EXPORT hetccl_status
hetccl_adapter_query(uint32_t requested_abi, hetccl_adapter_ops *ops) {
  if (requested_abi != HETCCL_ADAPTER_ABI_VERSION || ops == nullptr ||
      ops->struct_size < sizeof(*ops))
    return HETCCL_STATUS_INVALID_ARGUMENT;
  ops->abi_version = HETCCL_ADAPTER_ABI_VERSION;
  ops->adapter_name = "hetccl-host";
  ops->runtime_name = "host";
  ops->device_count = device_count;
  ops->device_info = device_info;
  ops->set_device = set_device;
  ops->allocate = allocate;
  ops->release = release;
  ops->copy_async = copy_async;
  ops->stream_synchronize = synchronize;
  ops->event_create = event_create;
  ops->event_record = event_record;
  ops->event_synchronize = event_synchronize;
  ops->event_destroy = event_destroy;
  ops->reduce_sum_f32 = reduce_sum_f32;
  ops->last_error = last_error;
  return HETCCL_STATUS_OK;
}
