"""Frozen wire-protocol constants for openmycelium-hetccl.

FROZEN AS OF 0.2.0a1. The on-wire layouts below are a compatibility contract.
Any incompatible change to a frame layout must increment the corresponding
version and fail negotiation with a clear message -- never reinterpret an
unknown version, and never widen a struct in place.

Two independent protocols share the link:

* ACTIVATION -- point-to-point pipeline activations (`hetccl.xvendor`)
* COLLECTIVE -- broadcast and all-gather frames (`hetccl.collective_frame`)

They carry distinct magic values so a frame from one can never be parsed as the
other, which matters because both may traverse the same qualified transport.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped only for an incompatible change to the activation frame layout.
ACTIVATION_PROTOCOL_VERSION = 1
#: Bumped only for an incompatible change to the collective frame layout.
COLLECTIVE_PROTOCOL_VERSION = 1

#: Distinct magics: "XMC1" for activations, "OMC2" for collectives.
ACTIVATION_MAGIC = 0x584D4331
COLLECTIVE_MAGIC = 0x4F4D4332

#: Released transport name. A peer advertising a different name is not a peer.
TRANSPORT_NAME = "host-staged-xvendor"


class ProtocolMismatch(RuntimeError):
    """A peer speaks a protocol version this build cannot interpret."""


@dataclass(frozen=True)
class ProtocolIdentity:
    """What this build speaks, for exchange during connection setup."""

    transport: str = TRANSPORT_NAME
    activation: int = ACTIVATION_PROTOCOL_VERSION
    collective: int = COLLECTIVE_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, object]:
        return {"transport": self.transport, "activationProtocol": self.activation,
                "collectiveProtocol": self.collective}


def negotiate(local: ProtocolIdentity, remote: ProtocolIdentity) -> ProtocolIdentity:
    """Agree a protocol, or refuse loudly.

    There is deliberately no downgrade path. A newer build talking to an older
    one must fail visibly rather than guess at a layout it was not built for.
    """
    if local.transport != remote.transport:
        raise ProtocolMismatch(
            f"peer offers transport {remote.transport!r}, this build speaks "
            f"{local.transport!r}")
    if local.activation != remote.activation:
        raise ProtocolMismatch(
            f"activation protocol mismatch: local v{local.activation}, "
            f"peer v{remote.activation}; upgrade both sides to the same release")
    if local.collective != remote.collective:
        raise ProtocolMismatch(
            f"collective protocol mismatch: local v{local.collective}, "
            f"peer v{remote.collective}; upgrade both sides to the same release")
    return local
