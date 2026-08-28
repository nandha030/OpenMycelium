"""Where everything lives, resolved once, from the highest-priority source.

Until now these were constants pointing at one machine: `/opt/hetenv/bin/python`,
`/opt/rocmenv/bin/python`, `/opt/models`. The wheel installed anywhere and ran
nowhere else, because two of those are environments that were built by hand and
that no code creates.

Precedence, highest first:

    1. a command-line argument
    2. an environment variable
    3. the user configuration file
    4. auto-discovery
    5. a documented default

Every resolved value records **which** of those produced it, so a surprising
path can be traced to its source instead of guessed at.

One rule has no exceptions: a missing or unusable GPU runtime is an error with
instructions, never a quiet fall back to the CPU or to a single card. Falling
back would turn "this ran across two vendors" into "this ran somehow", which is
the one claim this project exists to make precisely.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:                                             # 3.11+
    import tomllib
except ImportError:                              # pragma: no cover
    tomllib = None                               # type: ignore[assignment]

APP = "openmycelium"

#: Setting -> (environment variable, documented default).
SETTINGS: Dict[str, Tuple[str, str]] = {
    "cuda_python":  ("OPENMYCELIUM_CUDA_PYTHON", ""),
    "rocm_python":  ("OPENMYCELIUM_ROCM_PYTHON", ""),
    "model_store":  ("OPENMYCELIUM_STORE", "/var/lib/openmycelium/models"),
    "state_dir":    ("OPENMYCELIUM_STATE", "/var/lib/openmycelium/state"),
    "ledger":       ("OM_XVENDOR_LEDGER",
                     "/var/lib/openmycelium/xvendor_qualification.json"),
    # No default. A hard-coded "Ubuntu-24.04" meant a Windows-side launch went
    # to whatever distribution happened to carry that name, which need not be
    # the one that was provisioned -- and it did so silently. Discovery below
    # answers this inside WSL; outside WSL an ambiguous answer is an error.
    "wsl_distro":   ("OPENMYCELIUM_WSL_DISTRO", ""),
}

#: Where a CUDA or ROCm environment is plausibly found, in preference order.
#: The historical /opt paths stay first so an existing installation keeps
#: working without being rewritten.
DISCOVERY: Dict[str, Tuple[str, ...]] = {
    "cuda_python": (
        "/opt/hetenv/bin/python",
        "/opt/openmycelium/cuda/bin/python",
        "/var/lib/openmycelium/env/cuda/bin/python",
        "~/.openmycelium/env/cuda/bin/python",
    ),
    "rocm_python": (
        "/opt/rocmenv/bin/python",
        "/opt/openmycelium/rocm/bin/python",
        "/var/lib/openmycelium/env/rocm/bin/python",
        "~/.openmycelium/env/rocm/bin/python",
    ),
    # Directories, not executables. An existing store keeps being used rather
    # than being orphaned by a change of default: the previous default held
    # 23 GB of imported weights, and silently looking somewhere else would
    # present that as "no models installed".
    "model_store": (
        "/opt/models",
        "/var/lib/openmycelium/models",
        "~/.openmycelium/models",
    ),
    "state_dir": (
        "/opt/openmycelium",
        "/var/lib/openmycelium/state",
        "~/.openmycelium/state",
    ),
    "ledger": (
        "/opt/xvendor_qualification.json",
        "/var/lib/openmycelium/xvendor_qualification.json",
    ),
}

#: Which discovery entries are directories that must merely exist, rather than
#: executables that must be runnable.
DISCOVER_AS_DIRECTORY = ("model_store", "state_dir")


class ConfigError(RuntimeError):
    """A setting could not be resolved, or names something unusable."""


def config_paths() -> List[str]:
    """User file first, then system, so a user can override the machine."""
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [
        os.environ.get("OPENMYCELIUM_CONFIG", ""),
        os.path.join(xdg, APP, "config.toml"),
        os.path.join(home, f".{APP}.toml"),
        f"/etc/{APP}/config.toml",
    ]


def _read_file() -> Tuple[Dict[str, Any], str]:
    for path in config_paths():
        if not path or not os.path.isfile(path):
            continue
        try:
            if path.endswith(".toml") and tomllib is not None:
                with open(path, "rb") as handle:
                    data = tomllib.load(handle)
            else:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
        except (OSError, ValueError) as error:
            raise ConfigError(f"{path} could not be read: {error}") from error
        return (data.get(APP, data) if isinstance(data, dict) else {}), path
    return {}, ""


#: Distributions that are infrastructure rather than somewhere to run in.
NOT_A_TARGET_DISTRO = ("docker-desktop", "docker-desktop-data", "rancher-desktop")


def running_inside_wsl() -> bool:
    try:
        with open("/proc/version", "r", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def installed_distros() -> List[str]:
    """Ask wsl.exe what exists, for the case where we are not inside one.

    Only reachable from Windows or from a WSL distribution with interop on.
    The output is UTF-16 with a BOM, which is why it is decoded explicitly
    rather than read as text.
    """
    executable = shutil.which("wsl.exe") or "/mnt/c/Windows/System32/wsl.exe"
    if not os.path.exists(executable):
        return []
    try:
        out = subprocess.run([executable, "--list", "--quiet"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    text = out.stdout.decode("utf-16-le", errors="ignore").replace("\x00", "")
    names = [line.strip() for line in text.splitlines() if line.strip()]
    return [n for n in names if n.lower() not in NOT_A_TARGET_DISTRO]


def discover_wsl_distro() -> Tuple[str, str]:
    """(distribution, origin). An empty distribution means "you must choose"."""
    inside = os.environ.get("WSL_DISTRO_NAME", "").strip()
    if inside:
        return inside, "WSL_DISTRO_NAME"
    if running_inside_wsl():
        # Inside WSL but the variable is unset, which happens under some
        # service managers. Guessing here would reintroduce the bug this
        # replaced, so say nothing rather than something plausible.
        return "", ""
    candidates = installed_distros()
    if len(candidates) == 1:
        return candidates[0], "the only installed distribution"
    return "", ""


def _provisioned_env(state_dir: str, vendor: str) -> str:
    """Where `openmycelium provision` puts an environment.

    This has to be derived from the resolved state directory rather than
    listed as a constant. It was listed as a constant, the constant said
    /var/lib/openmycelium/env/<vendor>, provision wrote to
    /var/lib/openmycelium/state/env/<vendor>, and the result was that a
    correctly provisioned CUDA environment was invisible to every other
    command -- doctor reported "interpreter missing" for an interpreter that
    existed.
    """
    if not state_dir:
        return ""
    return os.path.join(state_dir, "env", vendor, "bin", "python")


def _discover(name: str, state_dir: str = "") -> str:
    if name == "wsl_distro":
        return discover_wsl_distro()[0]
    if name == "state_dir":
        # Prefer a candidate that actually holds provisioned environments.
        # `run`, `chat` and `serve` create /opt/openmycelium as a working
        # directory, so merely existing is not evidence of being the state
        # directory -- and taking it as such hid environments that provision
        # had written somewhere else entirely.
        for candidate in DISCOVERY["state_dir"]:
            expanded = os.path.expanduser(candidate)
            if os.path.isdir(os.path.join(expanded, "env")):
                return expanded
    if name in ("cuda_python", "rocm_python"):
        vendor = "cuda" if name == "cuda_python" else "rocm"
        provisioned = _provisioned_env(state_dir, vendor)
        if provisioned and os.path.isfile(provisioned) \
                and os.access(provisioned, os.X_OK):
            return provisioned
    for candidate in DISCOVERY.get(name, ()):
        expanded = os.path.expanduser(candidate)
        if name in DISCOVER_AS_DIRECTORY:
            if os.path.isdir(expanded):
                return expanded
        elif name == "ledger":
            if os.path.isfile(expanded):
                return expanded
        elif os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
    return ""


@dataclass
class Resolved:
    value: str
    source: str            # cli | env | file | discovered | default
    origin: str = ""       # which variable or file supplied it


@dataclass
class Config:
    settings: Dict[str, Resolved] = field(default_factory=dict)
    config_file: str = ""

    def __getattr__(self, name: str) -> str:
        entry = self.__dict__.get("settings", {}).get(name)
        if entry is None:
            raise AttributeError(name)
        return entry.value

    def source_of(self, name: str) -> str:
        entry = self.settings.get(name)
        return f"{entry.source}({entry.origin})" if entry and entry.origin \
            else (entry.source if entry else "unset")

    def to_dict(self) -> Dict[str, Any]:
        return {"configFile": self.config_file,
                "settings": {k: asdict(v) for k, v in self.settings.items()}}

    def describe(self) -> List[str]:
        rows = [f"  configuration file  {self.config_file or '(none found)'}"]
        for name in SETTINGS:
            entry = self.settings.get(name)
            rows.append(f"  {name:<20} {entry.value or '(unset)':<48} "
                        f"{self.source_of(name)}")
        return rows


def load(overrides: Optional[Dict[str, Optional[str]]] = None) -> Config:
    """Resolve every setting, recording where each answer came from."""
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    file_values, file_path = _read_file()
    config = Config(config_file=file_path)

    # state_dir first: the interpreter paths are discovered relative to it, so
    # resolving them in declaration order would look in the wrong place.
    order = ["state_dir"] + [n for n in SETTINGS if n != "state_dir"]
    state_dir = ""

    for name in order:
        variable, default = SETTINGS[name]
        entry: Resolved
        if name in overrides:
            entry = Resolved(str(overrides[name]), "cli",
                             f"--{name.replace('_', '-')}")
        elif os.environ.get(variable):
            entry = Resolved(os.environ[variable], "env", variable)
        elif file_values.get(name):
            entry = Resolved(str(file_values[name]), "file", file_path)
        elif name == "wsl_distro":
            found, origin = discover_wsl_distro()
            entry = (Resolved(found, "discovered", origin) if found
                     else Resolved("", "unresolved", ""))
        else:
            found = _discover(name, state_dir)
            entry = (Resolved(found, "discovered",
                              "provisioned" if found.startswith(state_dir)
                              and state_dir else "")
                     if found else Resolved(default, "default", ""))
        config.settings[name] = entry
        if name == "state_dir":
            state_dir = entry.value
    return config


def require_wsl_distro(config: Config) -> str:
    """The distribution to act on, or an error naming the choices.

    Never falls back to a name. Picking one for the user is how a command ends
    up running against a distribution that was never provisioned.
    """
    entry = config.settings.get("wsl_distro")
    if entry and entry.value:
        return entry.value
    candidates = installed_distros()
    if candidates:
        listed = "\n".join(f"      {name}" for name in candidates)
        raise ConfigError(
            "which WSL distribution should this run in? More than one is "
            "installed and none was selected:\n\n" + listed
            + "\n\n    choose one with:\n"
              "      set OPENMYCELIUM_WSL_DISTRO=<name>       (Windows)\n"
              "      export OPENMYCELIUM_WSL_DISTRO=<name>    (Linux)\n"
              "    or put wsl_distro = \"<name>\" in the configuration file.")
    raise ConfigError(
        "no WSL distribution could be discovered, and none was configured.\n"
        "    install one with:  wsl --install -d Ubuntu-24.04\n"
        "    or select an existing one with OPENMYCELIUM_WSL_DISTRO.")


# ------------------------------------------------------------------ checking

def _probe(python: str, expect: str) -> Tuple[bool, str]:
    """Ask an interpreter what GPU runtime it actually has."""
    if not python:
        return False, "no interpreter configured"
    if not (os.path.isfile(python) and os.access(python, os.X_OK)):
        return False, f"{python} is not an executable file"
    script = ("import json,torch;"
              "print(json.dumps({'ok':torch.cuda.is_available(),"
              "'runtime':'rocm' if getattr(torch.version,'hip',None) else 'cuda',"
              "'name':torch.cuda.get_device_name(0) if torch.cuda.is_available()"
              " else '','torch':torch.__version__}))")
    try:
        out = subprocess.run([python, "-c", script], capture_output=True,
                             text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"{python} could not be run: {error}"
    lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
    if not lines:
        detail = (out.stderr or "no output").strip().splitlines()[-1:]
        return False, f"{python} has no usable torch: {detail[0] if detail else ''}"
    try:
        info = json.loads(lines[-1])
    except ValueError:
        return False, f"{python} produced unreadable probe output"
    if not info.get("ok"):
        return False, f"{python} has torch {info.get('torch')} but no visible GPU"
    if info.get("runtime") != expect:
        return False, (f"{python} is a {info.get('runtime')} build; "
                       f"a {expect} build is required here")
    return True, f"{info.get('name')} (torch {info.get('torch')})"


def require_runtimes(config: Config) -> Dict[str, str]:
    """Both GPU runtimes, or an error that says how to get them.

    Never degrades to one card or to the CPU. A partial result would still
    produce output, and that output would silently not be the thing this
    project claims to do.
    """
    problems: List[str] = []
    found: Dict[str, str] = {}
    for name, expect in (("cuda_python", "cuda"), ("rocm_python", "rocm")):
        ok, detail = _probe(getattr(config, name, ""), expect)
        if ok:
            found[name] = detail
        else:
            problems.append(f"    {name}: {detail}\n"
                            f"      source: {config.source_of(name)}")
    if problems:
        raise ConfigError(
            "both a CUDA and a ROCm environment are required, and this build "
            "will not fall back to one GPU or the CPU:\n\n"
            + "\n".join(problems)
            + "\n\n    provision them with:  openmycelium provision\n"
              "    or point at existing ones:\n"
              "      openmycelium ... --cuda-python /path/to/cuda/bin/python "
              "--rocm-python /path/to/rocm/bin/python\n"
              "    or set OPENMYCELIUM_CUDA_PYTHON and "
              "OPENMYCELIUM_ROCM_PYTHON\n"
              "    or write them into "
            + (config.config_file or config_paths()[1]))
    return found


def ensure_directories(config: Config) -> None:
    for name in ("model_store", "state_dir"):
        path = getattr(config, name, "")
        if path:
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as error:
                raise ConfigError(
                    f"{name} {path} could not be created: {error}\n"
                    f"    source: {config.source_of(name)}") from error


def add_arguments(parser: Any) -> None:
    """The overrides every command accepts, so precedence is uniform."""
    parser.add_argument("--cuda-python", dest="cuda_python", default=None)
    parser.add_argument("--rocm-python", dest="rocm_python", default=None)
    parser.add_argument("--model-store", dest="model_store", default=None)
    parser.add_argument("--state-dir", dest="state_dir", default=None)
    parser.add_argument("--ledger", dest="ledger", default=None)
    parser.add_argument("--config", dest="config_file_override", default=None)


def from_args(args: Any) -> Config:
    if getattr(args, "config_file_override", None):
        os.environ["OPENMYCELIUM_CONFIG"] = args.config_file_override
    return load({name: getattr(args, name, None) for name in SETTINGS})
