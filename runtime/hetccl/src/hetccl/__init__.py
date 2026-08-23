"""OpenMycelium HetCCL public API."""

from .collective import CollectiveConfig, HetCCLCollective
from .discovery import CapabilityReport, Device, discover_capabilities
from .kvcache import KVCacheBroker, KVCacheClient, KVCacheDescriptor, KVCacheRecord
from .metal import MetalAdapter, MetalCapabilities
from .planner import CollectivePlan, plan_collective
from .router import HetRouter, InferenceNode, InferenceRequest, InferenceRoute, StageAssignment
from .server import Coordinator
from .serving import OpenAICompatibleAdapter, ServingCapabilities, VLLMServingAdapter
from .speculative import DraftProposal, SpeculativeDecoder, SpeculativeStep

__all__ = [
    "CapabilityReport",
    "CollectiveConfig",
    "CollectivePlan",
    "Coordinator",
    "Device",
    "HetCCLCollective",
    "HetRouter",
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

__version__ = "0.2.0"
