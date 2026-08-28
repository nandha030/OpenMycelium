# Docker Desktop recovery — 2026-08-28

A system-level repair performed to unblock the Open WebUI smoke. Recorded
because it was outside the smoke's scope and altered state on the Windows host.

## Symptom

Docker Desktop crashed at startup, twice, with the same class of fault: it
could not remove its own leftover AF_UNIX socket files, which Windows exposes
as zero-length reparse points and refuses to delete through any file API once
they are orphaned.

```
run 1  starting services: initializing Ingest server: listening on
       unix://C:/Users/User/AppData/Local/Docker/run/sailor-ingest.sock:
       remove ...: The file cannot be accessed by the system.

run 2  starting services: initializing Secrets Engine: listening on
       unix://C:/Users/User/AppData/Local/docker-secrets-engine/engine.sock:
       remove ...: The file cannot be accessed by the system.
```

The first set dated from 24-08-2026 21:58, an unclean shutdown four days
earlier. `Remove-Item -Force` failed on every one of them.

## What was done

Directories were **renamed**, not deleted. Docker recreates both on start.

| Original path | Renamed to |
|---|---|
| `C:\Users\User\AppData\Local\Docker\run` | `C:\Users\User\AppData\Local\Docker\run.stale-20260828-152735` |
| `C:\Users\User\AppData\Local\Docker\run` (second set) | `C:\Users\User\AppData\Local\Docker\run.stale-152953` |
| `C:\Users\User\AppData\Local\docker-secrets-engine` | `C:\Users\User\AppData\Local\docker-secrets-engine.stale-152953` |

`docker-secrets-engine` contained exactly one item, `engine.sock`, and no
secrets data. Verified before renaming.

**These three directories must not be deleted without explicit approval.** They
are retained until after a Windows reboot and a further Docker integrity check,
at which point the kernel will have released the orphaned socket objects.

## Engine state

| | Before | After |
|---|---|---|
| Engine | not running; `npipe:////./pipe/dockerDesktopLinuxEngine` absent | **29.7.2 running** |
| `docker version` | failed to connect | responds |
| Distributions | `docker-desktop` Stopped | `docker-desktop` Running |

## Integrity

- Existing containers remained listed and accessible: `open_mycelium-hetccl-coordinator-1`, `open_mycelium-openmycelium-1`, `open_mycelium-grafana-1`, `open_mycelium-nats-1`, `open_mycelium-prometheus-1`, `open_mycelium-postgres-1` — all `Exited (255) 3 days ago`, unchanged.
- Images and volumes live in the `docker-desktop` VHDX and were never touched.
- **No factory reset.** The crash dialog's "Reset to factory defaults" button was deliberately not used; it would have destroyed the above.
- **No Docker update.** Docker Desktop offered 4.88.1 (from 4.87.0, 81.4 MB). Deferred: applying it restarts the engine and would invalidate the smoke.
- Only the three directories above were modified. No container, image, volume or configuration was altered.

## Outstanding

1. Windows reboot.
2. Re-check Docker integrity: engine starts, `docker ps -a` lists the six containers, `docker images` and `docker volume ls` intact.
3. Only then consider removing the three stale directories, with approval.
