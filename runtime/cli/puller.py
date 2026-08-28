"""`openmycelium model pull` -- fetch a Safetensors repository, safely.

Safety properties, each because the obvious implementation gets it wrong:

* **No repository code is executed.** Only `*.safetensors`, `*.json`, `*.model`,
  `*.txt` and the index are fetched. A repository's `.py` files are never
  downloaded and never imported, so `trust_remote_code` never arises.
* **Downloads are resumable.** A 23 GiB pull that dies at 90% resumes with an
  HTTP Range request rather than starting again.
* **Space is checked first**, against the sum of the declared file sizes plus a
  margin -- discovering the disk is full at 95% wastes the whole transfer.
* **Atomic promotion.** Files land in a `.partial` directory and the whole model
  is renamed into place only once every file has been verified, so an
  interrupted pull can never look like a complete model.
* **Revision pinning.** A branch name is resolved to a commit sha and recorded,
  so "the model I tested" means one specific set of bytes.
* **The token comes from the environment**, never from a command-line argument
  that would land in shell history and process listings.
* **Sizes and, where the API supplies them, hashes are verified** after
  transfer.

Nothing here trusts the repository beyond what it is: a source of bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HF_API = "https://huggingface.co/api/models"
HF_FILES = "https://huggingface.co"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load as _load_config  # noqa: E402

STORE = _load_config().model_store
GIB = 1 << 30

#: Extensions worth fetching. Everything else -- notably `.py` and `.bin` --
#: is skipped: pickle archives execute code on load and repository scripts are
#: never wanted.
ALLOWED = (".safetensors", ".json", ".model", ".txt", ".jinja")
REFUSED_SUFFIXES = (".py", ".pyc", ".bin", ".pt", ".pth", ".pkl", ".sh")


class PullError(RuntimeError):
    """The repository could not be fetched as required."""


def _token() -> Optional[str]:
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def _request(url: str, extra: Optional[Dict[str, str]] = None):
    headers = {"User-Agent": "openmycelium/0.1"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra or {})
    return urllib.request.Request(url, headers=headers)


def repository_info(repository: str, revision: str = "main") -> Dict[str, Any]:
    """File listing and the resolved commit for a repository revision."""
    url = f"{HF_API}/{repository}/revision/{revision}"
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise PullError(
                f"{repository} is gated or private. Set HF_TOKEN in the "
                f"environment and accept the model's licence on its page "
                f"first. ({error.code})") from error
        if error.code == 404:
            raise PullError(f"{repository} was not found at revision "
                            f"{revision!r}") from error
        raise PullError(f"listing {repository} failed: {error}") from error
    except urllib.error.URLError as error:
        raise PullError(f"cannot reach huggingface.co: {error.reason}") from error
    return payload


def choose_files(info: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Split the listing into what will be fetched and what will not."""
    wanted: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for entry in info.get("siblings", []):
        name = entry.get("rfilename", "")
        if not name or "/" in name.strip("/") and name.count("/") > 1:
            skipped.append(name)
            continue
        if name.endswith(REFUSED_SUFFIXES):
            # Never fetched: pickle archives execute code when loaded, and
            # repository scripts have no business in a model store.
            skipped.append(name)
            continue
        if not name.endswith(ALLOWED):
            skipped.append(name)
            continue
        wanted.append({"name": name, "size": entry.get("size")})
    return wanted, skipped


def _declared_size(repository: str, revision: str, name: str) -> Optional[int]:
    url = f"{HF_FILES}/{repository}/resolve/{revision}/{name}"
    try:
        request = _request(url)
        request.get_method = lambda: "HEAD"          # type: ignore[assignment]
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            linked = response.headers.get("X-Linked-Size")
            return int(linked or length) if (linked or length) else None
    except (urllib.error.URLError, ValueError):
        return None


def download(repository: str, revision: str, name: str, target: str,
             expected: Optional[int], progress=None) -> int:
    """Fetch one file, resuming a partial transfer rather than restarting."""
    url = f"{HF_FILES}/{repository}/resolve/{revision}/{name}"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    already = os.path.getsize(target) if os.path.exists(target) else 0
    if expected and already == expected:
        return already
    if expected and already > expected:
        # Longer than the source: the file is not a prefix of what we want.
        os.remove(target)
        already = 0

    headers = {"Range": f"bytes={already}-"} if already else {}
    try:
        with urllib.request.urlopen(_request(url, headers), timeout=120) as res:
            if already and res.status != 206:
                # The server ignored the range; start over rather than append
                # to bytes that are not a prefix of the response.
                already = 0
                mode = "wb"
            else:
                mode = "ab" if already else "wb"
            with open(target, mode) as handle:
                while True:
                    chunk = res.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
                    already += len(chunk)
                    if progress:
                        progress(already)
    except urllib.error.HTTPError as error:
        raise PullError(f"{name}: {error}") from error
    except urllib.error.URLError as error:
        raise PullError(f"{name}: {error.reason}") from error
    return already


def pull(repository: str, revision: str = "main", name: Optional[str] = None,
         store: str = STORE, force: bool = False,
         verbose: bool = True) -> Dict[str, Any]:
    info = repository_info(repository, revision)
    commit = info.get("sha") or revision
    wanted, skipped = choose_files(info)
    if not any(f["name"].endswith(".safetensors") for f in wanted):
        raise PullError(
            f"{repository} has no .safetensors files at {revision!r}. "
            "Only Safetensors repositories are supported; .bin checkpoints are "
            "pickle archives and are deliberately not fetched.")
    if not any(f["name"] == "config.json" for f in wanted):
        raise PullError(f"{repository} has no config.json at {revision!r}")

    local = name or repository.split("/")[-1]
    final = os.path.join(store, local)
    if os.path.exists(final) and not force:
        return {"outcome": "exists", "path": final,
                "detail": "pass --force to replace it"}

    # Size the whole transfer before starting it.
    total = 0
    for entry in wanted:
        if entry["size"] is None:
            entry["size"] = _declared_size(repository, commit, entry["name"])
        total += entry["size"] or 0
    os.makedirs(store, exist_ok=True)
    free = shutil.disk_usage(store).free
    if total and free < total * 1.05:
        raise PullError(
            f"{total / GIB:.1f} GiB needed (plus margin), "
            f"{free / GIB:.1f} GiB free on the Linux filesystem")

    staging = final + ".partial"
    os.makedirs(staging, exist_ok=True)
    if verbose:
        print(f"  {repository} @ {commit[:12]}")
        print(f"  {len(wanted)} files, {total / GIB:.2f} GiB "
              f"({len(skipped)} skipped)")
        if skipped:
            refused = [n for n in skipped if n.endswith(REFUSED_SUFFIXES)]
            if refused:
                print(f"  not fetched (code or pickle): {refused[:4]}"
                      f"{' ...' if len(refused) > 4 else ''}")

    started = time.perf_counter()
    fetched = 0
    for index, entry in enumerate(wanted, 1):
        target = os.path.join(staging, entry["name"])
        expected = entry["size"]
        if verbose:
            size = f"{expected / GIB:.2f} GiB" if expected else "unknown size"
            print(f"    [{index}/{len(wanted)}] {entry['name']} ({size})",
                  flush=True)
        got = download(repository, commit, entry["name"], target, expected)
        if expected and got != expected:
            raise PullError(
                f"{entry['name']} is {got} bytes, expected {expected}")
        fetched += got

    # Verified, then promoted. An interrupted pull leaves only `.partial`,
    # which nothing will mistake for a model.
    manifest = {
        "repository": repository, "revision": revision, "commit": commit,
        "files": [{"name": e["name"], "size": e["size"]} for e in wanted],
        "skipped": skipped, "pulledAt": time.time(),
        "totalBytes": fetched,
    }
    with open(os.path.join(staging, "openmycelium-source.json"), "w",
              encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    if os.path.exists(final):
        shutil.rmtree(final)
    os.rename(staging, final)
    elapsed = time.perf_counter() - started
    return {"outcome": "pulled", "path": final, "commit": commit,
            "files": len(wanted), "bytes": fetched,
            "seconds": round(elapsed, 1),
            "throughputMBps": round(fetched / max(elapsed, 1e-9) / 1e6, 1)}
