#!/usr/bin/env bash
# Both 0.1.0a7 builds are non-releasable. The first was staged before
# validation; the second is technically sound but its version identity had
# already been spent, and two wheel hashes under one version is precisely the
# supply-chain ambiguity provenance exists to prevent.
set -uo pipefail
R=/mnt/c/Users/User/Documents/Open_Mycelium/release

cat > "$R/0.1.0a7/NONRELEASABLE.txt" <<'EOF'
0.1.0a7 (rebuilt) -- NONRELEASABLE

  wheel    8d94b04e9bd79f2652e7118175d0882e81344777b3b039c2672ad5c9ba8751b8
  content  e5f06b44439925943fe1c5c36bd953102213332d8559a190d32421f8ab6e2210

Technically validated: every gate under validation/ passed against these exact
bytes -- discovery, both refusal paths, dual-GPU probes, idempotency, reboot,
the full command chain and the authenticated API.

Not releasable all the same. The version string 0.1.0a7 had already been
attached to a different wheel (see 0.1.0a7-candidate1), and a version that maps
to two artifacts identifies nothing. Versions name immutable artifacts, not
roadmap positions.

Reissued unchanged, apart from version and provenance metadata, as 0.1.0a8.
EOF

cat >> "$R/0.1.0a7-candidate1/SUPERSEDED.txt" <<'EOF'

Reclassified: SUPERSEDED-NONRELEASABLE. Neither 0.1.0a7 build may be published
under that version string.
EOF

echo "  marked:"
head -3 "$R/0.1.0a7/NONRELEASABLE.txt" | sed 's/^/    /'
tail -3 "$R/0.1.0a7-candidate1/SUPERSEDED.txt" | sed 's/^/    /'
