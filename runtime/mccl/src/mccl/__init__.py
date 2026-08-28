"""OpenMycelium MCCL public API."""

from .collective import CollectiveConfig, MCCLCollective
from .discovery import CapabilityReport, Device, discover_capabilities
from .kvcache import KVCacheBroker, KVCacheClient, KVCacheDescriptor, KVCacheRecord
from .metal import MetalAdapter, MetalCapabilities
from .planner import CollectivePlan, plan_collective
from .router import MRouter, InferenceNode, InferenceRequest, InferenceRoute, StageAssignment

#: Deprecated. Exported by openmycelium-mccl 0.2.0a2, which is frozen into the
#: v0.1.0 release, so the name stays importable. Deliberately absent from
#: __all__: existing code keeps working, new code never sees the old branding.
from .router import MRouter as HetRouter  # noqa: F401
from .server import Coordinator
from .serving import OpenAICompatibleAdapter, ServingCapabilities, VLLMServingAdapter
from .speculative import DraftProposal, SpeculativeDecoder, SpeculativeStep

__all__ = [
    "CapabilityReport",
    "CollectiveConfig",
    "CollectivePlan",
    "Coordinator",
    "Device",
    "MCCLCollective",
    "MRouter",
    "InferenceNode",
    "InferenceRequest",
    "InferenceRoute",
    "KVCacheBroker",
    "KVCacheClient",
    "KVCacheDescriptor",
    "KVCacheRecord",
    "MetalAdapter",
    "MetalCapabilities",
    "OpenAICompatibleAdapter",
    "ServingCapabilities",
    "SpeculativeDecoder",
    "SpeculativeStep",
    "StageAssignment",
    "DraftProposal",
    "VLLMServingAdapter",
    "discover_capabilities",
    "plan_collective",
]

__version__ = "0.2.0a3"
