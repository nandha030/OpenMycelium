pkill -f "pip install --dry-run" 2>/dev/null; sleep 1
/opt/omfresh/venv/bin/python - <<'PY'
import json, subprocess, urllib.request, sys

def dists(env):
    out = subprocess.run([f"{env}/bin/pip","list","--format=json"],
                         capture_output=True, text=True).stdout
    return {d["name"].lower().replace("_","-"): d["version"] for d in json.loads(out)}

def pypi_size(name, ver):
    url=f"https://pypi.org/pypi/{name}/{ver}/json"
    try:
        d=json.load(urllib.request.urlopen(url, timeout=30))
    except Exception:
        return None
    best=None
    for f in d.get("urls", []):
        fn=f["filename"]
        if not fn.endswith(".whl"): continue
        if "x86_64" in fn or "any" in fn or "none-any" in fn:
            if "cp312" in fn or "py3-none" in fn or "abi3" in fn or "cp39" in fn:
                best=max(best or 0, f["size"])
    if best is None and d.get("urls"):
        best=max(f["size"] for f in d["urls"])
    return best

def torch_size(url):
    try:
        r=urllib.request.Request(url, headers={"Range":"bytes=0-0"})
        with urllib.request.urlopen(r, timeout=60) as h:
            cr=h.headers.get("Content-Range","")
            return int(cr.split("/")[-1]) if "/" in cr else None
    except Exception:
        return None

TORCH={
 "cuda":("torch-2.11.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl","cu128"),
 "rocm":("torch-2.10.0%2Brocm7.0-cp312-cp312-manylinux_2_28_x86_64.whl","rocm7.0"),
}
grand=0
for env,label in (("/opt/hetenv","cuda"),("/opt/rocmenv","rocm")):
    ds=dists(env)
    total=0; unknown=[]
    for n,v in sorted(ds.items()):
        if n in ("torch","pytorch-triton","pytorch-triton-rocm"):
            continue
        s=pypi_size(n,v)
        if s: total+=s
        else: unknown.append(n)
    fn,idx=TORCH[label]
    ts=torch_size(f"https://download.pytorch.org/whl/{idx}/{fn}")
    if ts: total+=ts
    for n in ("pytorch-triton","pytorch-triton-rocm"):
        if n in ds:
            s=pypi_size(n,ds[n])
            if s: total+=s
            else: unknown.append(n+" (pytorch index)")
    grand+=total
    print(f"   {label:<5} {len(ds):3d} packages   torch wheel {ts/2**30 if ts else 0:5.2f} GiB"
          f"   environment total {total/2**30:6.2f} GiB")
    if unknown: print(f"         unsized: {', '.join(unknown[:6])}")
print(f"   {'both':<5} {'':3}              "
      f"                        grand total       {grand/2**30:6.2f} GiB")
print()
print("   projection at the measured rate:")
for r in (1.48, 1.55, 1.66):
    print(f"     {r:.2f} MB/s  ->  {grand/(r*1e6)/60:5.1f} min transfer")
PY
