"""Model, tokenizer and prompt identity for the throughput report.

Run with an environment that has transformers. Prints one JSON object.

The prompt token ids hash and the tokenizer identity are computed by the
installed package's own audit helpers, not reimplemented here: a second
implementation that disagreed would make the report describe a different
tokenisation than the runtime performed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

PACKAGE = "/opt/om/venv/lib/python3.12/site-packages/openmycelium"
sys.path.insert(0, os.path.join(PACKAGE, "runtime", "serving"))

import audit           # noqa: E402
import prompt_tokens   # noqa: E402


#: Version of the fingerprint construction. Recorded alongside the digest so a
#: future change to the canonical form is visible rather than silent.
FINGERPRINT_SCHEME = "om-model-fingerprint-1"


def model_sha256(directory: str) -> tuple[str, int, int]:
    """Canonical digest over every file: relative path, byte length, content.

    Deliberately not a concatenation of file contents, which would let two
    different trees collide by moving bytes between files, and would not notice
    a rename at all.

    Each field is length-prefixed. Without that, the path "ab" with length 12
    and the path "a" with length "b12" produce the same byte sequence, so a
    crafted rename could preserve the digest.

    The walk is recursive and paths are relative and POSIX-normalised: an
    earlier version listed only the top level, which would have hashed a
    checkpoint with subdirectories incompletely while still returning a
    confident-looking digest.
    """
    digest = hashlib.sha256()
    digest.update(FINGERPRINT_SCHEME.encode())
    entries: list[tuple[str, str, int]] = []
    for root, directories, names in os.walk(directory):
        directories.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            relative = os.path.relpath(path, directory).replace(os.sep, "/")
            entries.append((relative, path, os.path.getsize(path)))

    entries.sort(key=lambda item: item[0])
    total = 0
    for relative, path, size in entries:
        name_bytes = relative.encode("utf-8")
        digest.update(len(name_bytes).to_bytes(8, "little"))
        digest.update(name_bytes)
        digest.update(size.to_bytes(8, "little"))
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(8 << 20), b""):
                digest.update(block)
        total += size
    digest.update(len(entries).to_bytes(8, "little"))
    return digest.hexdigest(), len(entries), total


def main(model_dir: str, prompt: str) -> int:
    # The runtime's own loader and templating, not a second implementation.
    # These checkpoints ship a regex that mis-splits text unless
    # fix_mistral_regex is set, and the flag changes the ids produced for
    # identical text -- so a report that tokenised differently would describe a
    # prompt the server never saw.
    tokenizer = prompt_tokens.load_tokenizer(model_dir)
    ids, templated = prompt_tokens.templated_ids(tokenizer, prompt)

    sha, files, total = model_sha256(model_dir)
    report = {
        "model": os.path.basename(model_dir),
        "modelPath": model_dir,
        "modelSha256": sha,
        "modelFingerprintScheme": FINGERPRINT_SCHEME,
        "modelFiles": files,
        "modelBytes": total,
        "tokenizerIdentity": audit.tokenizer_identity(model_dir, True),
        "fixMistralRegex": True,
        "promptIdsSha256": audit.prompt_ids_hash(ids),
        "promptTokenCount": len(ids),
        "templatedChars": len(templated),
        "eosTokenId": tokenizer.eos_token_id,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
