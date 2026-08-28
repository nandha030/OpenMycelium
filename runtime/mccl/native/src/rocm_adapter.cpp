#include "mccl/adapter.h"

#include <hip/hip_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
thread_local char error_buffer[256] = {0};

mccl_status status(hipError_t result) {
  if (result == hipSuccess) return MCCL_STATUS_OK;
  std::snprintf(error_buffer, sizeof(error_buffer), "%s", hipGetErrorString(result));
  return result == hipErrorOutOfMemory ? MCCL_STATUS_OUT_OF_MEMORY : MCCL_STATUS_RUNTIME_ERROR;
}

__global__ void sum_f32(float *destination, const float *source, size_t count) {
  const size_t index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index < count) destination[index] += source[index];
}

mccl_status device_count(int32_t *count) { return count ? status(hipGetDeviceCount(count)) : MCCL_STATUS_INVALID_ARGUMENT; }
mccl_status set_device(int32_t index) { return status(hipSetDevice(index)); }

mccl_status device_info(int32_t index, mccl_device_info *info) {
  if (!info || info->struct_size < sizeof(*info)) return MCCL_STATUS_INVALID_ARGUMENT;
  hipDeviceProp_t property{};
  hipError_t result = hipGetDeviceProperties(&property, index);
  if (result != hipSuccess) return status(result);
  info->index = index;
  info->memory_bytes = property.totalGlobalMem;
  std::strncpy(info->vendor, "amd", sizeof(info->vendor) - 1);
  std::strncpy(info->runtime, "rocm", sizeof(info->runtime) - 1);
  std::strncpy(info->name, property.name, sizeof(info->name) - 1);
  std::strncpy(info->architecture, property.gcnArchName, sizeof(info->architecture) - 1);
  return MCCL_STATUS_OK;
}
mccl_status allocate(size_t bytes, mccl_memory_kind kind, void **pointer) {
  if (!pointer) return MCCL_STATUS_INVALID_ARGUMENT;
  if (kind == MCCL_MEMORY_DEVICE) return status(hipMalloc(pointer, bytes));
  if (kind == MCCL_MEMORY_PINNED_HOST) return status(hipHostMalloc(pointer, bytes));
  *pointer = std::malloc(bytes);
  return *pointer ? MCCL_STATUS_OK : MCCL_STATUS_OUT_OF_MEMORY;
}

mccl_status release(void *pointer, mccl_memory_kind kind) {
  if (kind == MCCL_MEMORY_DEVICE) return status(hipFree(pointer));
  if (kind == MCCL_MEMORY_PINNED_HOST) return status(hipHostFree(pointer));
  std::free(pointer); return MCCL_STATUS_OK;
}

mccl_status copy_async(void *destination, const void *source, size_t bytes, mccl_copy_kind kind, void *stream) {
  static const hipMemcpyKind kinds[] = {hipMemcpyHostToHost, hipMemcpyHostToDevice, hipMemcpyDeviceToHost, hipMemcpyDeviceToDevice};
  if (kind < MCCL_COPY_HOST_TO_HOST || kind > MCCL_COPY_DEVICE_TO_DEVICE) return MCCL_STATUS_INVALID_ARGUMENT;
  return status(hipMemcpyAsync(destination, source, bytes, kinds[kind], static_cast<hipStream_t>(stream)));
}

mccl_status stream_synchronize(void *stream) { return status(hipStreamSynchronize(static_cast<hipStream_t>(stream))); }
mccl_status event_create(void **event) { return event ? status(hipEventCreate(reinterpret_cast<hipEvent_t *>(event))) : MCCL_STATUS_INVALID_ARGUMENT; }
mccl_status event_record(void *event, void *stream) { return status(hipEventRecord(static_cast<hipEvent_t>(event), static_cast<hipStream_t>(stream))); }
mccl_status event_synchronize(void *event) { return status(hipEventSynchronize(static_cast<hipEvent_t>(event))); }
mccl_status event_destroy(void *event) { return status(hipEventDestroy(static_cast<hipEvent_t>(event))); }

mccl_status reduce(float *destination, const float *source, size_t count, void *stream) {
  hipLaunchKernelGGL(sum_f32, dim3((count + 255) / 256), dim3(256), 0, static_cast<hipStream_t>(stream), destination, source, count);
  return status(hipGetLastError());
}
const char *last_error() { return error_buffer; }
}  // namespace

extern "C" MCCL_EXPORT mccl_status mccl_adapter_query(uint32_t abi, mccl_adapter_ops *ops) {
  if (abi != MCCL_ADAPTER_ABI_VERSION || !ops || ops->struct_size < sizeof(*ops)) return MCCL_STATUS_INVALID_ARGUMENT;
  ops->abi_version = MCCL_ADAPTER_ABI_VERSION;
  ops->adapter_name = "mccl-rocm"; ops->runtime_name = "rocm";
  ops->device_count = device_count; ops->device_info = device_info; ops->set_device = set_device;
  ops->allocate = allocate; ops->release = release; ops->copy_async = copy_async; ops->stream_synchronize = stream_synchronize;
  ops->event_create = event_create; ops->event_record = event_record; ops->event_synchronize = event_synchronize; ops->event_destroy = event_destroy;
  ops->reduce_sum_f32 = reduce; ops->last_error = last_error;
  return MCCL_STATUS_OK;
}
