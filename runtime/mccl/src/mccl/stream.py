"""Length-delimited frame reader tolerant of arbitrary socket segmentation.

A TCP stream gives no framing guarantees: one `recv` may return half a header,
and one may return four whole frames. Everything here goes through a single
buffer so both take the same path.

This lives in the package rather than in a test harness because it is part of
the wire contract: a peer that frames correctly but parses naively will fail
intermittently under load, which is the worst way to find out.
"""

from __future__ import annotations

import socket
import struct
from typing import Callable, Optional, Tuple

from .collective_frame import CollectiveError, CollectiveFrame

#: Frame headers are small and bounded; anything larger is corruption or an
#: incompatible protocol, not a large tensor. Payload size is carried inside
#: the frame and validated against shape and dtype separately.
MAX_FRAME_HEADER = 4096


def encode_frame(frame: CollectiveFrame, payload: bytes) -> bytes:
    """`[u32 header_len][header][payload]`, ready for a single write."""
    if len(payload) != frame.byte_size:
        raise CollectiveError(
            f"payload is {len(payload)} bytes; frame declares {frame.byte_size}")
    blob = frame.pack()
    return struct.pack("<I", len(blob)) + blob + payload


class FrameStream:
    """Reads whole frames from a byte stream, however it is segmented."""

    def __init__(self, sock: socket.socket, recv_size: int = 65536):
        self.sock = sock
        self.recv_size = recv_size
        self.buf = bytearray()
        self.reads = 0
        self.frames = 0

    def _fill(self, need: int) -> None:
        while len(self.buf) < need:
            chunk = self.sock.recv(self.recv_size)
            self.reads += 1
            if not chunk:
                raise CollectiveError(
                    f"peer closed with {need - len(self.buf)} of {need} bytes outstanding")
            self.buf.extend(chunk)

    def take(self, count: int) -> bytes:
        if count == 0:
            return b""
        self._fill(count)
        out = bytes(self.buf[:count])
        del self.buf[:count]
        return out

    def next_frame(self) -> Tuple[CollectiveFrame, bytes]:
        length = struct.unpack("<I", self.take(4))[0]
        if not 8 <= length <= MAX_FRAME_HEADER:
            raise CollectiveError(f"implausible frame header length {length}")
        frame = CollectiveFrame.unpack(self.take(length))
        self.frames += 1
        return frame, self.take(frame.byte_size)

    @property
    def idle(self) -> bool:
        """True when no partial frame is buffered -- a safe place to stop."""
        return not self.buf
