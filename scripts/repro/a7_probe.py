"""Exercise the 0.1.0a7 logic from the checkout, before building a wheel.

Run in both distributions: om-clean2 is the negative control (ROCm packages
installed, no system runtime) and Ubuntu-24.04 is the working case.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/mnt/c/Users/User/Documents/Open_Mycelium/runtime/cli")

import config          # noqa: E402
import rocm_prereq     # noqa: E402

print(f"  distribution        {os.environ.get('WSL_DISTRO_NAME', '(unset)')}")
print()

print("  == distribution discovery ==")
found, origin = config.discover_wsl_distro()
print(f"    discovered          {found or '(none)'}")
print(f"    origin              {origin or '(none)'}")
print(f"    installed distros   {config.installed_distros()}")
assert found != "Ubuntu-24.04" or os.environ.get("WSL_DISTRO_NAME") == "Ubuntu-24.04", \
    "Ubuntu-24.04 was selected without being the actual distribution"
print()

print("  == resolved configuration ==")
resolved = config.load()
for line in resolved.describe():
    print(f"  {line}")
print()

print("  == ROCm prerequisite ==")
report = rocm_prereq.detect(getattr(resolved, "rocm_python", ""))
print(f"    state               {report['state']}")
for line in rocm_prereq.summary(report):
    print(line)
if report.get("detail"):
    print(f"    detail              {report['detail']}")
for line in rocm_prereq.remediation(report):
    print(line)
