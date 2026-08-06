#!/usr/bin/env python3
"""Safe operations agent: diagnoses cluster readiness and emits a plan only."""
import json, shutil
from server import discover
result=discover()
checks=[{'check':'kubectl available','ok':bool(shutil.which('kubectl'))},{'check':'accelerator detected','ok':result['count']>0},{'check':'vendor runtime detected','ok':bool(shutil.which('nvidia-smi') or shutil.which('rocm-smi'))}]
plan=['kubectl apply -f k8s/openmycelium.yaml'] if checks[0]['ok'] else ['Install kubectl and configure a cluster context, then rerun this command.']
print(json.dumps({'discovery':result,'checks':checks,'recommended_plan':plan},indent=2))
