#include "mccl/adapter.h"

#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
thread_local char error_buffer[256] = {0};

mccl_status status(cudaError_t result) {
  if (result == cudaSuccess) return MCCL_STATUS_OK;
  std::snprintf(error_buffer, sizeof(error_buffer), "%s", cudaGetErrorString(result));
  return result == cudaErrorMemoryAllocation ? MCCL_STATUS_OUT_OF_MEMORY : MCCL_STATUS_RUNTIME_ERROR;
}

__global__ void sum_f32(float *destination, const float *source, size_t count) {
  const size_t index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index < count) destination[index] += source[index];
}

mccl_status device_count(int32_t *count) { return count ? status(cudaGetDeviceCount(count)) : MCCL_STATUS_INVALID_ARGUMENT; }
mccl_status set_device(int32_t index) { return status(cudaSetDevice(index)); }

mccl_status device_info(int32_t index, mccl_device_info *info) {
  if (!info || info->struct_size < sizeof(*info)) return MCCL_STATUS_INVALID_ARGUMENT;
  cudaDeviceProp property{};
  cudaError_t result = cudaGetDeviceProperties(&property, index);
  if (result != cudaSuccess) return status(result);
  info->index = index;
  info->memory_bytes = property.totalGlobalMem;
  std::strncpy(info->vendor, "nvidia", sizeof(info->vendor) - 1);
  std::strncpy(info->runtime, "cuda", sizeof(info->runtime) - 1);
  std::strncpy(info->name, property.name, sizeof(info->name) - 1);
  std::snprintf(info->architecture, sizeof(info->architecture), "sm_%d%d", property.major, property.minor);
  return MCCL_STATUS_OK;
}

mccl_status allocate(size_t bytes, mccl_memory_kind kind, void **pointer) {
  if (!pointer) return MCCL_STATUS_INVALID_ARGUMENT;
  if (kind == MCCL_MEMORY_DEVICE) return status(cudaMalloc(pointer, bytes));
  if (kind == MCCL_MEMORY_PINNED_HOST) return status(cudaMallocHost(pointer, bytes));
  *pointer = std::malloc(bytes);
  return *pointer ? MCCL_STATUS_OK : MCCL_STATUS_OUT_OF_MEMORY;
}

mccl_status release(void *pointer, mccl_memory_kind kind) {
  if (kind == MCCL_MEMORY_DEVICE) return status(cudaFree(pointer));
  if (kind == MCCL_MEMORY_PINNED_HOST) return status(cudaFreeHost(pointer));
  std::free(pointer);
  return MCCL_STATUS_OK;
}

mccl_status copy_async(void *destination, const void *source, size_t bytes, mccl_copy_kind kind, void *stream) {
  static const cudaMemcpyKind kinds[] = {cudaMemcpyHostToHost, cudaMemcpyHostToDevice, cudaMemcpyDeviceToHost, cudaMemcpyDeviceToDevice};
  if (kind < MCCL_COPY_HOST_TO_HOST || kind > MCCL_COPY_DEVICE_TO_DEVICE) return MCCL_STATUS_INVALID_ARGUMENT;
  return status(cudaMemcpyAsync(destination, source, bytes, kinds[kind], static_cast<cudaStream_t>(stream)));
}

mccl_status stream_synchronize(void *stream) { return status(cudaStreamSynchronize(static_cast<cudaStream_t>(stream))); }
mccl_status event_create(void **event) { return event ? status(cudaEventCreate(reinterpret_cast<cudaEvent_t *>(event))) : MCCL_STATUS_INVALID_ARGUMENT; }
mccl_status event_record(void *event, void *stream) { return status(cudaEventRecord(static_cast<cudaEvent_t>(event), static_cast<cudaStream_t>(stream))); }
mccl_status event_synchronize(void *event) { return status(cudaEventSynchronize(static_cast<cudaEvent_t>(event))); }
mccl_status event_destroy(void *event) { return status(cudaEventDestroy(static_cast<cudaEvent_t>(event))); }

mccl_status reduce(float *destination, const float *source, size_t count, void *stream) {
  sum_f32<<<(count + 255) / 256, 256, 0, static_cast<cudaStream_t>(stream)>>>(destination, source, count);
  return status(cudaGetLastError());
}
const char *last_error() { return error_buffer; }
}  // namespace

extern "C" MCCL_EXPORT mccl_status mccl_adapter_query(uint32_t abi, mccl_adapter_ops *ops) {
  if (abi != MCCL_ADAPTER_ABI_VERSION || !ops || ops->struct_size < sizeof(*ops)) return MCCL_STATUS_INVALID_ARGUMENT;
  ops->abi_version = MCCL_ADAPTER_ABI_VERSION;
  ops->adapter_name = "mccl-cuda";
  ops->runtime_name = "cuda";
  ops->device_count = device_count; ops->device_info = device_info; ops->set_device = set_device;
  ops->allocate = allocate; ops->release = release; ops->copy_async = copy_async; ops->stream_synchronize = stream_synchronize;
  ops->event_create = event_create; ops->event_record = event_record; ops->event_synchronize = event_synchronize; ops->event_destroy = event_destroy;
  ops->reduce_sum_f32 = reduce; ops->last_error = last_error;
  return MCCL_STATUS_OK;
}
