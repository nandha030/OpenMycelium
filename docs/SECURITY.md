# Security notes — v0.1.0 Technical Preview

This is a preview. It is designed for a single trusted machine, not for
exposure to a network you do not control.

## Threat model

**In scope.** Accidental exposure of a local API, secrets leaking into logs or
process listings, executable content arriving with a model, and a dependency
changing underneath a reproducible install.

**Out of scope.** A determined local attacker with root. Multi-tenant
isolation. Denial of service. The audit trail is sealed against accidental
corruption, not against someone who wants to forge it.

## The API

- Binds `127.0.0.1` by default.
- **Loopback requests are unauthenticated.** Anything else requires a bearer
  token, enforced before the request is parsed. Verified: missing token → 401,
  wrong token → 401, correct token → 200 over a non-loopback address.
- **No TLS.** Plaintext HTTP. If it leaves the machine, put it behind a reverse
  proxy that terminates TLS.
- One request at a time; a second gets 429 with `Retry-After`. This is
  admission control for a single-user runtime, not protection against abuse.
- No users, roles, quotas or rate limits.

### Handling the token

Pass it through the environment:

```bash
OPENMYCELIUM_TOKEN=$(cat /run/openmycelium/token) openmycelium serve --model M --host 0.0.0.0
```

Do **not** use `--token`: it puts the secret in the process command line, where
any user on the machine can read it from `ps`. The environment is readable only
by the owner through `/proc/<pid>/environ`.

Keep the secret on `tmpfs` (`/run`) with mode 600, not under `/var/log`. When
handing it to a container, pipe it on stdin — `docker -e` exposes it through
`docker inspect` to anyone who can reach the daemon.

## Models

`openmycelium model pull` refuses files that can execute:

```
.py  .pyc  .bin  .pt  .pth  .pkl  .sh
```

Safetensors only. No repository-supplied Python is executed, ever — not for
custom architectures, not for tokenizers. Downloads are pinned to a commit,
staged in `.partial`, and promoted atomically, so an interrupted fetch cannot
be mistaken for a complete one.

`model verify` compares config and shard sizes. It is not a content hash;
hashing 22.8 GiB on every check would make the command useless. The release
records a full model fingerprint separately.

## Supply chain

- Both first-party wheels are published with SHA-256, and the **installed
  content hash** is recorded separately from the wheel hash — a wheel is often
  deleted after installation, and the question that matters later is what is on
  disk now.
- `torch` comes from the vendor index; everything else from PyPI, in two
  separate pip invocations. Not `--extra-index-url`, which lets either index
  answer for any name and is how dependency confusion happens.
- The wheelhouse records the exact bytes of all 90 wheels, so a later change on
  PyPI cannot silently alter an install.
- An SBOM is published in CycloneDX 1.5 format, listing every component with
  its hash, including the AMD system packages that are **not** in the
  wheelhouse.
- The AMD repository signing key was verified byte-identical to the key already
  trusted on the reference system before any repository was added.

## Privileged operations

`openmycelium provision` builds Python environments. It deliberately **does
not** add operating-system repositories or install system-wide packages, and it
never will under that name. Adding third-party apt repositories and installing
gigabytes system-wide is authority an ordinary provisioning command should not
hold. The AMD prerequisite is documented and manual.

## Reporting

This preview has no security contact process yet. Treat it as unaudited
research software.
