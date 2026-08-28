# Captured runtime contracts

Real output from the qualified machine, captured so the console is developed
against shapes the runtime actually produces rather than invented ones.

Device identities are placeholders — `nvidia:device-0`, `amd:device-0`. Raw
identities are kept only in qualification evidence under `release/`, where a
measurement has to remain attributable to a specific card.

`provision.dryrun.txt` is **not JSON**, despite being produced by
`provision --dry-run --json`. That command accepts the flag and prints human
text, which is a defect scheduled for v0.1.1. It is stored with a `.txt`
extension so nothing tries to parse it. Every other file here is valid JSON.
