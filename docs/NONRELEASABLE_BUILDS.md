# Non-releasable validation builds

Versions identify immutable artifacts, not roadmap features. Every wheel built
during validation gets its own version, and a wheel whose gate did not pass is
recorded here rather than renumbered or quietly rebuilt. The hashes are kept so
that a build found on a machine can always be identified, including the ones
that were wrong.

None of the builds below may be published.

## Adapter SDK milestone (0.2.0a6 – 0.3.0a3)

| Version | sha256 of the wheel | Why it is not releasable |
|---|---|---|
| `0.2.0a6` | `a76dc5ea1d61ed0dd6c550e617c8e320aed4fa1f3b5799e49af85e898870d254` | `plan` refused an unsupported architecture with prose only — no machine-readable `errorCode` — so the refusal could not be matched on. The direct-worker path had not been exercised on hardware at all. |
| `0.2.0a7` | `3051cd9b81c2a8aefbfc5f60929637c6dd79f77b5f29f46d91ce4414fc2c6da3` | `provenance` reported `placementSchemaVersion: 1` from a hard-coded constant while producers were writing schema 2. A provenance record making a false statement about its own build. |
| `0.2.0a8` | `3e99b31f4ac8cb94ef774ed46aa9d380077af31cfd7e3c44671478a85eb3055c` | Functionally the validated code at the time, but superseded by the decision that this milestone is a `0.3.0` change: it introduces the Model Adapter SDK and placement schema v2. Recorded rather than republished under a new number. |
| `0.3.0a1` | `4732ef28e03186cb3523a740e16f57eb04f756c9a2443070cd32fa5760f07ab4` | Qualification enforcement could not work: a worker runs under the vendor's interpreter, where `openmycelium` is on `PYTHONPATH` but not installed, so it could not read the wheel's content digest and every situation came out malformed. The lifecycle gate failed at step 2. |
| `0.3.0a2` | `6eeb695c6944212ecd5e44dce650e6070624678663c730b5652d6b57912b737a` | The qualification override event was emitted during manifest validation, which the audit writer correctly refuses — an event may not carry an identity taken from a manifest that has not been verified. Every override run died with `AuditError`. |
| `0.3.0a3` | `865819d0b5d929f28fae9189a06b2acd09b2eba66180acb6dff5cb18659fc0d1` | `qualify record --model NAME` passed the store name straight to the planner instead of resolving it, so a record could never be written for a model referred to by name. The lifecycle gate failed at step 3. |

Each of these was installed on the qualified machine and run. They are recorded
because "we rebuilt it and it works now" is not an account of what happened, and
because a machine still carrying one of these needs to be identifiable.

## The complete artifact

`0.3.0a4` — sha256 `0d668aefd9c9b37e5c46c2e324be9cb0895d7419421a1160cd38ac2b3d7e99cb`.

It is the first build of this milestone to pass every gate: unit suite, adapter
refusal, qualification lifecycle, installed-wheel console gate, and the hardware
comparison against the pre-refactor baseline at `d76334a`.

## Earlier precedent

`0.1.0a7` was built twice with different contents and both builds were declared
non-releasable; the validated code was reissued as `0.1.0a8`. This file
generalises that decision rather than repeating the argument each time.
