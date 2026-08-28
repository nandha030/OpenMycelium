# 0.1.0a8 — throughput report of record

Measured on the clean-bootstrap distribution `om-clean2`, from the installed
wheel, with both GPUs qualified.

## Result

| Metric | Median | Min | Max |
|---|---|---|---|
| TTFT (ms) | **142.09** | 141.99 | 143.99 |
| Decode (tok/s) | **11.09** | 10.98 | 11.24 |
| End-to-end (tok/s) | **10.95** | 10.84 | 11.10 |
| Server load (s) | 26.01 | 24.02 | 26.02 |

Five independent server processes. Median, minimum and maximum only — a p95
from five observations is not a statistic.

Server load is reported separately and is **excluded** from every throughput
figure. It is the cost of loading 22.84 GiB across two cards, paid once per
process, and folding it into a token rate would describe neither.

## Definitions

```
TTFT              request start -> first content token, client-observed
decode tok/s      (generated - 1) / (last token - first token)
end-to-end tok/s  generated / (request start -> response complete)
```

## Identity

```
model                Mistral-Nemo-Instruct-2407
modelSha256          f3298db2f04eb440bc235a9709fb120e38a72758ae5e2b14bbefcad552ada966
fingerprint scheme   om-model-fingerprint-1
files / bytes        17 / 24,525,497,357
tokenizerIdentity    41356c55024d7285fbd00d4196f5b6683c673faf027e6f79e9fc2fbe0530cbb4
fix_mistral_regex    true
promptIdsSha256      0ff751e80550d7ac1e623c1a3c8051bd65ad976213f92fcefac11a9a10f276bc
promptTokenCount     35
eosTokenId           2
```

`om-model-fingerprint-1` walks the checkpoint recursively, sorts by POSIX
relative path, and hashes a canonical sequence of length-prefixed fields —
relative path, byte length, content — followed by the file count. It is not a
concatenation of file contents: that would let two different trees collide by
moving bytes between files, and would not notice a rename. The length prefixes
matter because without them the path `ab` with length `12` and the path `a` with
length `b12` produce the same byte sequence.

The tokenizer is loaded through the runtime's own `load_tokenizer`, which passes
`fix_mistral_regex=True`. These checkpoints ship a regex that mis-splits text
without it, and the flag changes the ids produced for identical text, so the
recorded prompt hash describes the tokenisation the server actually performed.

## Generation lengths

All five sessions generated exactly **64 tokens**, every one ending with
`finish_reason=length`. **Zero** ended by EOS. No unequal completion lengths were
compared; the report asserts equality and fails if it does not hold.

## Transport qualification

> The transport was byte-exact qualified immediately before and after the
> performance campaign. Full-payload hashing was disabled during timed sessions
> to avoid contaminating performance. The five sessions produced identical
> output, providing deterministic functional consistency but not per-session
> byte-level transport proof.

Both qualifications compared shape, dtype, strides, element count, byte count
and SHA-256 on either side of the CUDA→ROCm boundary:

```
before   2 transfers, [1, 1, 5120] torch.bfloat16, 10240 B, sha256 c1467cd3…  byteExact=True
after    2 transfers, [1, 1, 5120] torch.bfloat16, 10240 B, sha256 c1467cd3…  byteExact=True
```

All five sessions produced output digest `b054c78e787f7396f725ee7ddac96cc2`.
That is deterministic functional consistency. It is **not** byte-level transport
proof: a small corruption of the boundary activation can leave a greedy argmax
unchanged, so identical tokens do not imply identical bytes.

## Campaign invariants

The campaign is one claim assembled from seven executions — two qualifications
and five sessions. Snapshots were taken before and after and compared; every
invariant held.

```
boot ID            e65a26d3-3b3e-4e7a-844c-480fa908242a   (unchanged)
uptime             665 s -> 962 s                          (no reboot)
package version    0.1.0a8
package content    b430eff6e79cd56b30841716431014c7b566942704b8e4c1df5c9336ad43981a
placement digest   91978e6c37ec439af0d4dd477cd126bc   boundary 19, [181, 182]
CUDA torch         2.11.0+cu128
ROCm torch         2.10.0+rocm7.0
HSA runtime        dxcore  83bedf380844ae17f12ab231f2abb69a  (system-provided)
ROCm system        7.2.0
MCCL               0.2.0a2, activation protocol v1, host-staged-xvendor
prompt ids         0ff751e80550d7ac1e623c1a3c8051bd65ad976213f92fcefac11a9a10f276bc
```

Placement was re-checked per session: 5 of 5 exclusive 181/182 with zero
overlap. No transport errors, worker failures or fallback occurred.

The placement digest hashes the ownership itself — boundary layer plus sorted
tensor names per stage — not the manifest file, which carries a generated
placement id that changes every run and would make an unchanged placement look
different on every comparison.

## Superseded run

An earlier five-session run produced TTFT 142.07 ms, decode 11.13 tok/s,
end-to-end 10.99 tok/s. It is **not** the report of record: its model
fingerprint came from the pre-canonical function, its method text still carried
a claim since retracted, and it had no before-snapshot to establish the
invariants. Its numbers agree with this campaign, which is reassuring and
nothing more.

## What this is not

Aggregated capacity across two accelerators, not a unified 32 GiB address
space. One NVIDIA GPU and one AMD GPU per pipeline. Greedy decoding only.
Single request at a time. One model family measured.
