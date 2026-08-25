"""Metal staging adapter for portable HetCCL inference transfers."""

from __future__ import annotations

import importlib
import importlib.util
import platform
from dataclasses import asdict, dataclass
from math import prod
from typing import Any

from .protocol import ProtocolError

_TORCH_DTYPES = {
    "bf16": "bfloat16",
    "f16": "float16",
    "f32": "float32",
    "f64": "float64",
    "i8": "int8",
    "u8": "uint8",
}
_DTYPE_BYTES = {"bf16": 2, "f16": 2, "f32": 4, "f64": 8, "i8": 1, "u8": 1}


@dataclass(frozen=True)
class MetalCapabilities:
    operating_system: str
    torch_mps: bool
    mlx: bool
    executable: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MetalAdapter:
    """Convert Metal tensors to and from canonical host bytes.

    The adapter supports PyTorch MPS today. MLX is detected for placement but
    requires a model-specific cache codec because MLX cache objects are not a
    stable wire format.
    """

    def __init__(self, torch_module: Any = None):
        # `False` explicitly disables PyTorch so the byte fallback can be
        # exercised; `None` means autodetect. Collapsing the two made
        # `torch_module=False` silently import torch anyway, so the fallback
        # path was only reachable on a machine that happened to lack torch.
        self._torch_disabled = torch_module is False
        self._torch = None if self._torch_disabled else torch_module
        if self._torch is None and not self._torch_disabled:
            try:
                self._torch = importlib.import_module("torch")
            except ImportError:
                self._torch = None

    def capabilities(self) -> MetalCapabilities:
        torch_mps = self._mps_available()
        mlx = _module_available("mlx.core")
        system = platform.system().lower()
        if system != "darwin":
            reason = "Metal is available only on macOS"
        elif torch_mps:
            reason = "PyTorch MPS staging is executable"
        elif mlx:
            reason = "MLX detected; a model-specific KV-cache codec is required"
        else:
            reason = "no supported Metal framework was detected"
        return MetalCapabilities(system, torch_mps, mlx, torch_mps, reason)

    def to_host_bytes(self, tensor: Any, dtype: str | None = None) -> tuple[bytes, str, tuple[int, ...]]:
        if isinstance(tensor, (bytes, bytearray, memoryview)):
            payload = bytes(tensor)
            return payload, dtype or "u8", (len(payload),)
        if self._torch is None or not hasattr(tensor, "detach"):
            raise ProtocolError("Metal staging requires a PyTorch tensor or bytes-like payload")
        self.synchronize()
        host = tensor.detach().contiguous().cpu()
        resolved = dtype or _dtype_name(host.dtype)
        if resolved not in _TORCH_DTYPES:
            raise ProtocolError(f"unsupported Metal staging dtype {resolved}")
        shape = tuple(int(value) for value in host.shape)
        try:
            payload = host.view(self._torch.uint8).numpy().tobytes()
        except (AttributeError, RuntimeError, TypeError) as error:
            raise ProtocolError("unable to encode the Metal tensor as canonical host bytes") from error
        expected = prod(shape) * _DTYPE_BYTES[resolved]
        if len(payload) != expected:
            raise ProtocolError("encoded Metal tensor size does not match its dtype and shape")
        return payload, resolved, shape

    def from_host_bytes(self, payload: bytes, dtype: str, shape: tuple[int, ...], device: str = "mps") -> Any:
        if self._torch is None:
            if dtype == "u8" and prod(shape) == len(payload):
                return payload
            raise ProtocolError("PyTorch is required to restore typed Metal tensors")
        if dtype not in _TORCH_DTYPES or any(value <= 0 for value in shape):
            raise ProtocolError("invalid Metal tensor dtype or shape")
        expected = prod(shape) * _DTYPE_BYTES[dtype]
        if len(payload) != expected:
            raise ProtocolError(f"Metal payload is {len(payload)} bytes; tensor requires {expected}")
        torch_dtype = getattr(self._torch, _TORCH_DTYPES[dtype])
        try:
            host = self._torch.frombuffer(bytearray(payload), dtype=torch_dtype).clone().reshape(shape)
            if device == "mps" and not self._mps_available():
                raise ProtocolError("PyTorch MPS is not available on this host")
            return host.to(device)
        except ProtocolError:
            raise
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise ProtocolError("unable to restore the canonical tensor on Metal") from error

    def synchronize(self) -> None:
        if self._mps_available() and hasattr(self._torch.mps, "synchronize"):
            self._torch.mps.synchronize()

    def self_test(self) -> bool:
        if not self._mps_available():
            return False
        tensor = self._torch.arange(8, dtype=self._torch.float32, device="mps")
        payload, dtype, shape = self.to_host_bytes(tensor)
        restored = self.from_host_bytes(payload, dtype, shape)
        self.synchronize()
        return bool(self._torch.equal(tensor.cpu(), restored.cpu()))

    def _mps_available(self) -> bool:
        try:
            return bool(self._torch is not None and self._torch.backends.mps.is_available())
        except (AttributeError, RuntimeError):
            return False


def _dtype_name(dtype: Any) -> str:
    raw = str(dtype).removeprefix("torch.")
    for name, torch_name in _TORCH_DTYPES.items():
        if raw == torch_name:
            return name
    raise ProtocolError(f"unsupported tensor dtype {raw}")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
