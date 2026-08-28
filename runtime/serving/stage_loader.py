"""Load only one stage's tensors from a sharded safetensors checkpoint.

The capacity demonstration lives or dies here. If either worker materialises the
whole checkpoint and discards the half it does not own, peak memory is the full
model size per process -- which still *appears* to work on a host with enough
RAM, and quietly proves nothing about aggregating GPU capacity.

So the loader:

* opens one shard at a time and memory-maps it copy-on-write, so untouched
  tensors never become resident pages;
* builds a CPU view over the mapped bytes rather than copying them;
* moves that view to the target device and drops it before advancing;
* never assembles a state dict containing tensors it does not own.

`AutoModelForCausalLM.from_pretrained` is deliberately avoided: it may
materialise the full checkpoint or keep an extra copy, either of which would
invalidate the measurement this module exists to produce.
"""

from __future__ import annotations

import gc
import json
import mmap
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from model_inspect import GIB, MIB, DTYPE_BYTES, InspectionError, read_safetensors_header

#: safetensors dtype -> torch dtype attribute name.
TORCH_DTYPE = {
    "F64": "float64", "F32": "float32", "F16": "float16", "BF16": "bfloat16",
    "I64": "int64", "I32": "int32", "I16": "int16", "I8": "int8",
    "U8": "uint8", "BOOL": "bool",
}


class LoaderError(RuntimeError):
    """The checkpoint disagrees with the plan, or a tensor is not as declared."""


# ------------------------------------------------------------------ ownership

@dataclass
class OwnershipReport:
    total_in_checkpoint: int
    assigned: Dict[str, int]
    missing: Tuple[str, ...]          # in the checkpoint, owned by nobody
    unexpected: Tuple[str, ...]       # assigned, absent from the checkpoint
    duplicated: Tuple[str, ...]       # assigned to more than one stage

    @property
    def valid(self) -> bool:
        return not (self.missing or self.unexpected or self.duplicated)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tensorsInCheckpoint": self.total_in_checkpoint,
            "assignedPerStage": self.assigned,
            "missing": list(self.missing),
            "unexpected": list(self.unexpected),
            "duplicated": list(self.duplicated),
            "everyTensorHasExactlyOneOwner": self.valid,
        }


def validate_ownership(checkpoint_names: Iterable[str],
                       assignment: Dict[str, Sequence[str]]) -> OwnershipReport:
    """Every tensor exactly once, across all stages."""
    present: Set[str] = set(checkpoint_names)
    seen: Dict[str, int] = {}
    for names in assignment.values():
        for name in names:
            seen[name] = seen.get(name, 0) + 1

    duplicated = tuple(sorted(n for n, count in seen.items() if count > 1))
    unexpected = tuple(sorted(set(seen) - present))
    missing = tuple(sorted(present - set(seen)))
    return OwnershipReport(len(present),
                           {stage: len(names) for stage, names in assignment.items()},
                           missing, unexpected, duplicated)


# --------------------------------------------------------------- shard access

@dataclass
class ShardEntry:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    begin: int
    end: int

    @property
    def nbytes(self) -> int:
        return self.end - self.begin

    @property
    def expected_bytes(self) -> int:
        count = 1
        for dim in self.shape:
            count *= dim
        return count * DTYPE_BYTES[self.dtype]


def shard_entries(path: str) -> Tuple[Dict[str, ShardEntry], int]:
    """Tensor table plus the offset where the data block starts."""
    header = read_safetensors_header(path)
    with open(path, "rb") as handle:
        header_len = struct.unpack("<Q", handle.read(8))[0]
    data_start = 8 + header_len

    entries: Dict[str, ShardEntry] = {}
    for name, meta in header.items():
        if name == "__metadata__":
            continue
        offsets = meta.get("data_offsets") or [0, 0]
        entry = ShardEntry(name, str(meta.get("dtype", "")),
                           tuple(int(d) for d in meta.get("shape", ())),
                           int(offsets[0]), int(offsets[1]))
        if entry.dtype not in DTYPE_BYTES:
            raise LoaderError(f"{name}: unsupported dtype {entry.dtype}")
        if entry.nbytes != entry.expected_bytes:
            raise LoaderError(
                f"{name}: shard declares {entry.nbytes} bytes but shape "
                f"{entry.shape} x {entry.dtype} needs {entry.expected_bytes}")
        entries[name] = entry
    return entries, data_start


# -------------------------------------------------------------------- metrics

@dataclass
class LoadMetrics:
    stage: str
    device: str
    device_name: str = ""
    assigned_tensors: int = 0
    loaded_tensors: int = 0
    assigned_bytes: int = 0
    loaded_bytes: int = 0
    gpu_allocated_bytes: int = 0
    gpu_peak_bytes: int = 0
    cpu_rss_start_bytes: int = 0
    cpu_rss_peak_bytes: int = 0
    shards_opened: int = 0
    seconds: float = 0.0
    device_verified: bool = False
    verification_note: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def cpu_staging_peak_bytes(self) -> int:
        """Growth in resident memory, not absolute RSS.

        Absolute RSS includes the interpreter and the vendor runtime, which are
        hundreds of MiB before a single tensor is read. Growth is the number
        that answers whether the checkpoint was streamed or materialised.
        """
        return max(0, self.cpu_rss_peak_bytes - self.cpu_rss_start_bytes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "device": self.device,
            "deviceName": self.device_name,
            "assignedTensors": self.assigned_tensors,
            "loadedTensors": self.loaded_tensors,
            "assignedGiB": round(self.assigned_bytes / GIB, 3),
            "loadedGiB": round(self.loaded_bytes / GIB, 3),
            "gpuAllocatedGiB": round(self.gpu_allocated_bytes / GIB, 3),
            "gpuPeakGiB": round(self.gpu_peak_bytes / GIB, 3),
            "cpuStagingPeakMiB": round(self.cpu_staging_peak_bytes / MIB, 1),
            "shardsOpened": self.shards_opened,
            "seconds": round(self.seconds, 2),
            "deviceAllocationVerified": self.device_verified,
            "verificationNote": self.verification_note,
            "errors": self.errors,
        }


_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _drop_pages(mapped: "mmap.mmap", offset: int, length: int) -> None:
    """Return a finished byte range to the kernel.

    `madvise` needs a page-aligned offset, so the start rounds down and the
    length grows to compensate. Best-effort: a platform without MADV_DONTNEED
    still loads correctly, it just holds more resident memory.
    """
    advise = getattr(mapped, "madvise", None)
    if advise is None or not hasattr(mmap, "MADV_DONTNEED"):
        return
    start = (offset // _PAGE) * _PAGE
    span = length + (offset - start)
    try:
        advise(mmap.MADV_DONTNEED, start, span)
    except (OSError, ValueError):
        pass


def _rss_bytes() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _rss_peak_bytes() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


# --------------------------------------------------------------------- loader

class StageLoader:
    """Streams one stage's tensors onto its device, one shard at a time."""

    def __init__(self, model_path: str, stage: str, tensor_names: Sequence[str],
                 device: str, torch_module: Any, dry_run: bool = False):
        self.model_path = model_path
        self.stage = stage
        self.wanted = set(tensor_names)
        self.device = device
        self.torch = torch_module
        self.dry_run = dry_run
        self.tensors: Dict[str, Any] = {}
        self.metrics = LoadMetrics(stage=stage, device=device)

    def _shard_map(self) -> Dict[str, List[str]]:
        index_path = os.path.join(self.model_path, "model.safetensors.index.json")
        if os.path.isfile(index_path):
            with open(index_path, "r", encoding="utf-8") as handle:
                weight_map = json.load(handle)["weight_map"]
        else:
            weight_map = {n: "model.safetensors" for n in self.wanted}
        by_shard: Dict[str, List[str]] = {}
        for name, shard in weight_map.items():
            if name in self.wanted:
                by_shard.setdefault(shard, []).append(name)
        unknown = self.wanted - set(weight_map)
        if unknown:
            raise LoaderError(
                f"{len(unknown)} assigned tensors are absent from the index, "
                f"first: {sorted(unknown)[0]}")
        return by_shard

    def load(self, limit: Optional[int] = None) -> LoadMetrics:
        torch = self.torch
        started = time.perf_counter()
        self.metrics.assigned_tensors = len(self.wanted)
        self.metrics.cpu_rss_start_bytes = _rss_bytes()

        if not self.dry_run and torch is not None and self.device.startswith("cuda"):
            torch.cuda.reset_peak_memory_stats()
            self.metrics.device_name = torch.cuda.get_device_name(0)

        loaded = 0
        for shard, names in sorted(self._shard_map().items()):
            path = os.path.join(self.model_path, shard)
            entries, data_start = shard_entries(path)
            self.metrics.shards_opened += 1

            handle = open(path, "rb")
            try:
                # Copy-on-write: writable for torch.frombuffer, private to this
                # process, and pages materialise only where actually touched.
                mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_COPY)
            except (OSError, ValueError) as error:
                handle.close()
                raise LoaderError(f"cannot map {shard}: {error}") from error

            try:
                view = memoryview(mapped)
                for name in sorted(names):
                    entry = entries.get(name)
                    if entry is None:
                        self.metrics.errors.append(f"{name} missing from {shard}")
                        continue
                    self.metrics.assigned_bytes += entry.nbytes
                    if limit is not None and loaded >= limit:
                        continue
                    if self.dry_run:
                        loaded += 1
                        self.metrics.loaded_bytes += entry.nbytes
                        continue

                    begin, end = data_start + entry.begin, data_start + entry.end
                    dtype = getattr(torch, TORCH_DTYPE[entry.dtype])
                    # A view over mapped pages; no copy until .to(device).
                    flat = torch.frombuffer(view[begin:end], dtype=dtype)
                    host = flat.reshape(entry.shape)
                    uploaded = host.to(self.device, non_blocking=False)
                    if uploaded.data_ptr() == host.data_ptr():
                        # `.to("cpu")` on a CPU tensor is a no-op, so the result
                        # still aliases the memory map. Keeping that would leave
                        # the tensor dangling once the map closes -- and the map
                        # cannot close at all while the view is exported.
                        uploaded = uploaded.clone()
                    self.tensors[name] = uploaded
                    # Drop the staging view before touching the next tensor.
                    del host, flat
                    # Without this the whole shard accumulates as resident pages
                    # -- correct, but a ~4.7 GiB peak for a 4.9 GB shard. Telling
                    # the kernel the range is finished returns it immediately, so
                    # the staging peak tracks the largest single tensor instead.
                    _drop_pages(mapped, begin, end - begin)
                    loaded += 1
                    self.metrics.loaded_bytes += entry.nbytes
                    peak = _rss_peak_bytes()
                    if peak > self.metrics.cpu_rss_peak_bytes:
                        self.metrics.cpu_rss_peak_bytes = peak
                del view
            finally:
                mapped.close()
                handle.close()
                gc.collect()

        self.metrics.loaded_tensors = loaded
        self.metrics.cpu_rss_peak_bytes = max(self.metrics.cpu_rss_peak_bytes,
                                              _rss_peak_bytes())
        if not self.dry_run and torch is not None and self.device.startswith("cuda"):
            self.metrics.gpu_allocated_bytes = torch.cuda.memory_allocated()
            self.metrics.gpu_peak_bytes = torch.cuda.max_memory_allocated()
        self.metrics.seconds = time.perf_counter() - started
        return self.metrics

    def verify_device_residency(self, sample: int = 8) -> bool:
        """Prove the weights are on the device and usable, not merely counted.

        Reading `memory_allocated` alone would not distinguish a real upload
        from an accounting artefact, so this runs an actual reduction on
        sampled tensors and checks they report the expected device.
        """
        torch = self.torch
        if self.dry_run or torch is None or not self.tensors:
            self.metrics.verification_note = "dry run; nothing was uploaded"
            return False
        names = sorted(self.tensors)[:sample]
        try:
            for name in names:
                tensor = self.tensors[name]
                if tensor.device.type != self.device.split(":")[0]:
                    self.metrics.verification_note = (
                        f"{name} is on {tensor.device}, expected {self.device}")
                    return False
                # Reduce a bounded slice, not the whole tensor. `.float()` on a
                # 1.25 GiB bf16 tensor allocates 2.5 GiB of fp32 on a card that
                # is already holding its full stage, which turns a sanity check
                # into an out-of-memory error.
                flat = tensor.detach().reshape(-1)
                probe = flat[: min(flat.numel(), 4096)].float()
                value = probe.abs().sum().item()
                del probe
                if value != value:                      # NaN
                    self.metrics.verification_note = f"{name} reduced to NaN on device"
                    return False
                if value == 0.0:
                    self.metrics.verification_note = (
                        f"{name} is entirely zero on device; the upload may not "
                        "have carried real data")
                    return False
            self.metrics.device_verified = True
            self.metrics.verification_note = (
                f"{len(names)} sampled tensors reduced on {self.metrics.device_name or self.device}")
            return True
        except Exception as error:                      # noqa: BLE001
            self.metrics.verification_note = f"{type(error).__name__}: {error}"
            return False

    def unload(self) -> int:
        """Release every device tensor and report the memory returned."""
        torch = self.torch
        before = 0
        if torch is not None and self.device.startswith("cuda") and not self.dry_run:
            before = torch.cuda.memory_allocated()
        self.tensors.clear()
        gc.collect()
        if torch is not None and self.device.startswith("cuda") and not self.dry_run:
            torch.cuda.empty_cache()
            return before - torch.cuda.memory_allocated()
        return 0
