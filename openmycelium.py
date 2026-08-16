#!/usr/bin/env python3
"""Dependency-free OpenMycelium control-plane CLI."""

import argparse
import getpass
import http.cookiejar
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = os.environ.get("OPENMYCELIUM_URL", "http://127.0.0.1:8081")
STATE_DIR = pathlib.Path.home() / ".openmycelium"
COOKIE_FILE = STATE_DIR / "cookies.txt"


def client():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    if COOKIE_FILE.exists():
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, http.cookiejar.LoadError):
            pass
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def request(api_url, path, method="GET", body=None):
    opener, jar = client()
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(api_url.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=30) as result:
            payload = json.load(result)
        jar.save(ignore_discard=True, ignore_expires=True)
        try:
            os.chmod(COOKIE_FILE, 0o600)
        except OSError:
            pass
        return payload
    except urllib.error.HTTPError as error:
        try:
            message = json.load(error).get("error", error.reason)
        except (ValueError, AttributeError):
            message = error.reason
        raise RuntimeError(f"OpenMycelium returned {error.code}: {message}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Cannot reach {api_url}: {error.reason}") from error


def add_resource_commands(sub):
    cluster = sub.add_parser("cluster", help="Manage registered clusters")
    cluster_sub = cluster.add_subparsers(dest="action", required=True)
    cluster_sub.add_parser("list")
    cluster_add = cluster_sub.add_parser("add")
    cluster_add.add_argument("name")
    cluster_add.add_argument("endpoint", nargs="?", default="")
    cluster_add.add_argument("--type", default="kubernetes", choices=["kubernetes", "docker", "bare-metal"])
    cluster_add.add_argument("--kubeconfig")
    cluster_add.add_argument("--namespace", default="openmycelium-workloads")
    cluster_add.add_argument("--storage-class", default="")
    cluster_refresh = cluster_sub.add_parser("refresh")
    cluster_refresh.add_argument("id")
    cluster_nodes = cluster_sub.add_parser("nodes")
    cluster_nodes.add_argument("id")
    cluster_delete = cluster_sub.add_parser("delete")
    cluster_delete.add_argument("id")

    pool = sub.add_parser("pool", help="Manage logical accelerator pools")
    pool_sub = pool.add_subparsers(dest="action", required=True)
    pool_sub.add_parser("list")
    pool_add = pool_sub.add_parser("add")
    pool_add.add_argument("name")
    pool_add.add_argument("--vendor", default="mixed")
    pool_add.add_argument("--runtime", required=True, choices=["cuda", "rocm", "oneapi", "metal", "cpu", "compatible"])
    pool_add.add_argument("--policy", default="compatible-runtime")
    pool_add.add_argument("--selector", default="")
    pool_add.add_argument("--resource-name", default="", help="Exact extended resource advertised by the device plugin")
    pool_add.add_argument("--sharing-mode", choices=["exclusive", "mig", "time-slicing", "mps", "device-plugin"], default="exclusive")
    pool_add.add_argument("--slice-profile", default="")
    pool_add.add_argument("--sharing-replicas", type=int, default=1)
    pool_delete = pool_sub.add_parser("delete")
    pool_delete.add_argument("id")

    queue = sub.add_parser("queue", help="Manage queues and quotas")
    queue_sub = queue.add_subparsers(dest="action", required=True)
    queue_sub.add_parser("list")
    queue_add = queue_sub.add_parser("add")
    queue_add.add_argument("name")
    queue_add.add_argument("--priority", type=int, default=50)
    queue_add.add_argument("--accelerators", type=int, default=0)
    queue_add.add_argument("--memory-gb", type=int, default=0)
    queue_add.add_argument("--preemption", action="store_true")
    queue_delete = queue_sub.add_parser("delete")
    queue_delete.add_argument("id")

    fabric = sub.add_parser("fabric", help="Plan Hypha distributed tensor memory")
    fabric_sub = fabric.add_subparsers(dest="action", required=True)
    fabric_sub.add_parser("list")
    fabric_capabilities = fabric_sub.add_parser("capabilities")
    fabric_capabilities.add_argument("--cluster", required=True)
    fabric_capabilities.add_argument("--device-memory-gib", type=float, default=0)
    fabric_capabilities.add_argument("--include-host-memory", action="store_true")
    fabric_plan = fabric_sub.add_parser("plan")
    fabric_plan.add_argument("name")
    fabric_plan.add_argument("--cluster", required=True)
    fabric_plan.add_argument("--tensor", default="weights")
    fabric_plan.add_argument("--size-gib", type=float, required=True)
    fabric_plan.add_argument("--strategy", choices=["auto", "shard", "replicate"], default="auto")
    fabric_plan.add_argument("--consistency", choices=["immutable", "single-writer", "reduce", "transactional"], default="immutable")
    fabric_plan.add_argument("--device-memory-gib", type=float, default=0)
    fabric_plan.add_argument("--reserve-percent", type=float, default=10)
    fabric_plan.add_argument("--max-devices", type=int, default=16)
    fabric_plan.add_argument("--include-host-memory", action="store_true")
    fabric_delete = fabric_sub.add_parser("delete")
    fabric_delete.add_argument("id")


def add_workload_commands(sub):
    workload = sub.add_parser("workload", help="Submit and operate workloads")
    workload_sub = workload.add_subparsers(dest="action", required=True)
    workload_sub.add_parser("list")
    workload_sub.add_parser("refresh")
    submit = workload_sub.add_parser("submit")
    submit.add_argument("name")
    submit.add_argument("--kind", default="batch", choices=["training", "finetuning", "batch", "interactive", "inference", "agent"])
    submit.add_argument("--pool", default="cpu-local")
    submit.add_argument("--image", default="")
    submit.add_argument("--image-pull-secret", default="")
    submit.add_argument("--accelerators", type=int, default=0)
    submit.add_argument("--cluster", required=True)
    submit.add_argument("--namespace", default="")
    submit.add_argument("--model", default="")
    submit.add_argument("--model-version", default="", help="Governed model version ID")
    submit.add_argument("--command", dest="container_command", default="")
    submit.add_argument("--cpu", default="1")
    submit.add_argument("--memory", default="2Gi")
    submit.add_argument("--storage-gb", type=int, default=20)
    submit.add_argument("--replicas", type=int, default=1)
    submit.add_argument("--service-type", choices=["ClusterIP", "NodePort", "LoadBalancer"], default="ClusterIP")
    submit.add_argument("--port", type=int, default=8000)
    submit.add_argument("--fabric-plan", default="")
    submit.add_argument("--scheduler", choices=["kubernetes", "kueue", "volcano"], default="kubernetes")
    submit.add_argument("--queue", default="")
    submit.add_argument("--gang-min", type=int, default=0)
    submit.add_argument("--priority-class", default="")
    submit.add_argument("--topology-mode", choices=["none", "compact", "spread"], default="none")
    submit.add_argument("--topology-key", default="kubernetes.io/hostname")
    submit.add_argument("--network-mode", choices=["standard", "rdma", "infiniband"], default="standard")
    for action in ("start", "stop", "redeploy", "checkpoint"):
        command = workload_sub.add_parser(action)
        command.add_argument("id")
    workload_delete = workload_sub.add_parser("delete")
    workload_delete.add_argument("id")
    for operation in ("diagnostics", "logs", "events", "service", "manifest", "storage"):
        command = workload_sub.add_parser(operation)
        command.add_argument("id")
    workload_exec = workload_sub.add_parser("exec", help="Run a non-interactive command in the workload pod")
    workload_exec.add_argument("id")
    workload_exec.add_argument("command")
    workload_exec.add_argument("--container", default="")
    workload_probe = workload_sub.add_parser("probe", help="Probe the workload Service through the Kubernetes API")
    workload_probe.add_argument("id")
    workload_probe.add_argument("--method", choices=["GET", "POST"], default="GET")
    workload_probe.add_argument("--path", default="/")
    workload_probe.add_argument("--body", default="")
    workload_ssh = workload_sub.add_parser("ssh")
    workload_ssh.add_argument("id")
    workload_ssh_config = workload_sub.add_parser("ssh-config")
    workload_ssh_config.add_argument("id")
    workload_ssh_config.add_argument("--host", required=True)
    workload_ssh_config.add_argument("--port", type=int, default=22)
    workload_ssh_config.add_argument("--user", required=True)

    model = sub.add_parser("model", help="Use the configured Ollama runtime")
    model_sub = model.add_subparsers(dest="action", required=True)
    model_sub.add_parser("list")
    model_sub.add_parser("catalog")
    model_sub.add_parser("sync-ollama")
    deploy = model_sub.add_parser("deploy")
    deploy.add_argument("name")
    generate = model_sub.add_parser("generate")
    generate.add_argument("name")
    generate.add_argument("prompt")

    manifest = sub.add_parser("manifest", help="Preview or apply safe namespaced Kubernetes YAML")
    manifest_sub = manifest.add_subparsers(dest="action", required=True)
    for action in ("preview", "apply"):
        command = manifest_sub.add_parser(action)
        command.add_argument("--cluster", required=True)
        command.add_argument("--namespace", default="")
        command.add_argument("--file", required=True, help="YAML file path or - for stdin")


def add_agent_commands(sub):
    agent = sub.add_parser("agent", help="Manage agent definitions, flows, runs, approvals, and traces")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    agent_list = agent_sub.add_parser("list")
    agent_list.add_argument("--workspace", default="")
    agent_create = agent_sub.add_parser("create")
    agent_create.add_argument("name")
    agent_create.add_argument("--workspace", default="")
    agent_create.add_argument("--description", default="")
    agent_create.add_argument("--framework", default="generic")
    agent_create.add_argument("--image", required=True)
    agent_create.add_argument("--command", default="")
    agent_create.add_argument("--port", type=int, default=8000)
    agent_create.add_argument("--model-version", default="")
    agent_create.add_argument("--pool", default="cpu-local")
    agent_create.add_argument("--cpu", default="1")
    agent_create.add_argument("--memory", default="2Gi")
    agent_create.add_argument("--accelerators", type=int, default=0)
    agent_create.add_argument("--storage-gb", type=int, default=0)
    agent_create.add_argument("--token-budget", type=int, default=100000)
    agent_create.add_argument("--timeout", type=int, default=900)
    agent_create.add_argument("--require-approval", action="store_true")
    agent_create.add_argument("--memory-mode", choices=["run", "thread", "workspace", "none"], default="run")
    agent_delete = agent_sub.add_parser("delete")
    agent_delete.add_argument("id")
    agent_run = agent_sub.add_parser("run")
    agent_run.add_argument("--workspace", required=True)
    agent_run.add_argument("--agent", required=True)
    agent_run.add_argument("--flow", default="")
    agent_run.add_argument("--input", default='{"message":"Run the requested task."}')
    agent_run.add_argument("--token-budget", type=int, default=100000)
    agent_runs = agent_sub.add_parser("runs")
    agent_runs.add_argument("--workspace", default="")
    agent_cancel = agent_sub.add_parser("cancel")
    agent_cancel.add_argument("id")
    agent_trace = agent_sub.add_parser("trace")
    agent_trace.add_argument("id")
    agent_flows = agent_sub.add_parser("flows")
    agent_flows.add_argument("--workspace", default="")
    agent_flow_create = agent_sub.add_parser("flow-create")
    agent_flow_create.add_argument("name")
    agent_flow_create.add_argument("--workspace", required=True)
    agent_flow_create.add_argument("--file", required=True, help="Flow graph JSON file")
    agent_flow_create.add_argument("--description", default="")
    agent_approvals = agent_sub.add_parser("approvals")
    agent_approvals.add_argument("--workspace", default="")
    for decision in ("approve", "reject"):
        command = agent_sub.add_parser(decision)
        command.add_argument("id")
    agent_tools = agent_sub.add_parser("tools")
    agent_tools.add_argument("--workspace", default="")
    agent_memory = agent_sub.add_parser("memory")
    agent_memory.add_argument("--workspace", default="")


def parser():
    root = argparse.ArgumentParser(prog="openmycelium", description="Operate an OpenMycelium control plane")
    root.add_argument("--api-url", default=DEFAULT_URL)
    sub = root.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login")
    login.add_argument("email")
    login.add_argument("--password")
    sub.add_parser("logout")
    sub.add_parser("whoami")
    discover = sub.add_parser("discover")
    discover.add_argument("action", choices=["scan"])
    add_resource_commands(sub)
    add_workload_commands(sub)
    add_agent_commands(sub)
    observability = sub.add_parser("observability")
    observability.add_argument("action", choices=["summary"])
    users = sub.add_parser("user", help="Administer local accounts")
    user_sub = users.add_subparsers(dest="action", required=True)
    user_sub.add_parser("list")
    user_add = user_sub.add_parser("add")
    user_add.add_argument("email")
    user_add.add_argument("--role", default="viewer", choices=["platform_admin", "operator", "viewer"])
    user_add.add_argument("--password")
    user_delete = user_sub.add_parser("delete")
    user_delete.add_argument("id")
    audit = sub.add_parser("audit")
    audit.add_argument("action", choices=["list"])
    return root


def route(args):
    if args.command == "login":
        password = args.password or getpass.getpass("Password: ")
        return "/api/v1/auth/login", "POST", {"email": args.email, "password": password}
    if args.command == "logout":
        return "/api/v1/auth/logout", "POST", None
    if args.command == "whoami":
        return "/api/v1/auth/me", "GET", None
    if args.command == "discover":
        return "/api/v1/discovery/scan", "POST", None
    if args.command == "cluster":
        if args.action == "list":
            return "/api/v1/clusters", "GET", None
        if args.action == "delete":
            return f"/api/v1/clusters/{args.id}", "DELETE", None
        if args.action == "refresh":
            return f"/api/v1/clusters/{args.id}/refresh", "POST", None
        if args.action == "nodes":
            return f"/api/v1/clusters/{args.id}/nodes", "GET", None
        kubeconfig = pathlib.Path(args.kubeconfig).read_text(encoding="utf-8") if args.kubeconfig else ""
        return "/api/v1/clusters", "POST", {"name": args.name, "endpoint": args.endpoint, "type": args.type, "kubeconfig": kubeconfig, "namespace": args.namespace, "storageClass": args.storage_class}
    if args.command == "pool":
        if args.action == "list":
            return "/api/v1/pools", "GET", None
        if args.action == "delete":
            return f"/api/v1/pools/{args.id}", "DELETE", None
        return "/api/v1/pools", "POST", {"name": args.name, "vendor": args.vendor, "runtime": args.runtime, "policy": args.policy, "selector": args.selector, "resourceName": args.resource_name, "sharingMode": args.sharing_mode, "sliceProfile": args.slice_profile, "sharingReplicas": args.sharing_replicas}
    if args.command == "queue":
        if args.action == "list":
            return "/api/v1/queues", "GET", None
        if args.action == "delete":
            return f"/api/v1/queues/{args.id}", "DELETE", None
        return "/api/v1/queues", "POST", {"name": args.name, "priority": args.priority, "acceleratorQuota": args.accelerators, "memoryQuotaGB": args.memory_gb, "preemptionEnabled": args.preemption}
    if args.command == "fabric":
        if args.action == "list":
            return "/api/v1/fabric/plans", "GET", None
        if args.action == "delete":
            return f"/api/v1/fabric/plans/{args.id}", "DELETE", None
        if args.action == "capabilities":
            query = urllib.parse.urlencode({"clusterId": args.cluster, "defaultDeviceMemoryGiB": args.device_memory_gib, "includeHostMemory": str(args.include_host_memory).lower()})
            return f"/api/v1/fabric/capabilities?{query}", "GET", None
        return "/api/v1/fabric/plans", "POST", {"name": args.name, "clusterId": args.cluster, "tensorName": args.tensor, "tensorGiB": args.size_gib, "strategy": args.strategy, "consistency": args.consistency, "defaultDeviceMemoryGiB": args.device_memory_gib, "reservePercent": args.reserve_percent, "maxDevices": args.max_devices, "includeHostMemory": args.include_host_memory}
    if args.command == "workload":
        if args.action in ("list", "refresh"):
            return "/api/v1/workloads", "GET", None
        if args.action == "submit":
            return "/api/v1/workloads", "POST", {"name": args.name, "kind": args.kind, "runtime": "kubernetes", "clusterId": args.cluster, "namespace": args.namespace, "pool": args.pool, "fabricPlanId": args.fabric_plan, "image": args.image, "imagePullSecret": args.image_pull_secret, "model": args.model, "modelVersionId": args.model_version, "command": args.container_command, "accelerators": args.accelerators, "cpu": args.cpu, "memory": args.memory, "storageGB": args.storage_gb, "replicas": args.replicas, "schedulerBackend": args.scheduler, "queueName": args.queue, "gangMinAvailable": args.gang_min, "priorityClass": args.priority_class, "topologyMode": args.topology_mode, "topologyKey": args.topology_key, "networkMode": args.network_mode, "serviceType": args.service_type, "port": args.port}
        if args.action == "delete":
            return f"/api/v1/workloads/{args.id}", "DELETE", None
        if args.action == "ssh":
            return f"/api/v1/workloads/{args.id}/ssh", "GET", None
        if args.action == "ssh-config":
            return f"/api/v1/workloads/{args.id}/ssh", "PATCH", {"host": args.host, "port": args.port, "user": args.user}
        if args.action in ("diagnostics", "logs", "events", "service", "manifest", "storage"):
            return f"/api/v1/workloads/{args.id}/{args.action}", "GET", None
        if args.action == "exec":
            return f"/api/v1/workloads/{args.id}/exec", "POST", {"container": args.container, "command": args.command}
        if args.action == "probe":
            return f"/api/v1/workloads/{args.id}/probe", "POST", {"method": args.method, "path": args.path, "body": args.body}
        return f"/api/v1/workloads/{args.id}/action", "POST", {"action": args.action}
    if args.command == "model":
        if args.action == "list":
            return "/api/v1/models", "GET", None
        if args.action == "catalog":
            return "/api/v1/model-catalog", "GET", None
        if args.action == "sync-ollama":
            return "/api/v1/model-catalog-sync/ollama", "POST", None
        if args.action == "deploy":
            return "/api/v1/inference/deploy", "POST", {"model": args.name}
        return "/api/v1/inference/generate", "POST", {"model": args.name, "prompt": args.prompt}
    if args.command == "agent":
        query = lambda workspace: "?" + urllib.parse.urlencode({"workspaceId": workspace}) if workspace else ""
        if args.action == "list":
            return "/api/v1/agents" + query(args.workspace), "GET", None
        if args.action == "create":
            return "/api/v1/agents", "POST", {"name": args.name, "workspaceId": args.workspace, "description": args.description, "framework": args.framework, "image": args.image, "command": args.command, "port": args.port, "modelVersionId": args.model_version, "spec": {"pool": args.pool, "cpu": args.cpu, "memoryRequest": args.memory, "accelerators": args.accelerators, "storageGB": args.storage_gb, "serviceType": "ClusterIP", "policy": {"tokenBudget": args.token_budget, "timeoutSeconds": args.timeout, "maxRetries": 2, "requireApproval": args.require_approval}, "memory": {"mode": args.memory_mode, "retentionDays": 30, "checkpointing": True}}}
        if args.action == "delete":
            return f"/api/v1/agents/{args.id}", "DELETE", None
        if args.action == "run":
            try:
                run_input = json.loads(args.input)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid --input JSON: {error}") from error
            return "/api/v1/agent-runs", "POST", {"workspaceId": args.workspace, "agentId": args.agent, "flowId": args.flow, "input": run_input, "tokenBudget": args.token_budget}
        if args.action == "runs":
            return "/api/v1/agent-runs" + query(args.workspace), "GET", None
        if args.action == "cancel":
            return f"/api/v1/agent-runs/{args.id}/action", "POST", {"action": "cancel"}
        if args.action == "trace":
            return f"/api/v1/agent-runs/{args.id}/events", "GET", None
        if args.action == "flows":
            return "/api/v1/agent-flows" + query(args.workspace), "GET", None
        if args.action == "flow-create":
            graph = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
            return "/api/v1/agent-flows", "POST", {"workspaceId": args.workspace, "name": args.name, "description": args.description, "graph": graph}
        if args.action == "approvals":
            return "/api/v1/agent-approvals" + query(args.workspace), "GET", None
        if args.action in ("approve", "reject"):
            return "/api/v1/agent-approvals", "PATCH", {"id": args.id, "decision": args.action}
        if args.action == "tools":
            return "/api/v1/agent-tools" + query(args.workspace), "GET", None
        if args.action == "memory":
            return "/api/v1/agent-memory" + query(args.workspace), "GET", None
    if args.command == "manifest":
        content = sys.stdin.read() if args.file == "-" else pathlib.Path(args.file).read_text(encoding="utf-8")
        return f"/api/v1/manifests/{args.action}", "POST", {"clusterId": args.cluster, "namespace": args.namespace, "manifest": content}
    if args.command == "observability":
        return "/api/v1/observability/summary", "GET", None
    if args.command == "user":
        if args.action == "list":
            return "/api/v1/users", "GET", None
        if args.action == "delete":
            return f"/api/v1/users/{args.id}", "DELETE", None
        password = args.password or getpass.getpass("Temporary password (12+ characters): ")
        return "/api/v1/users", "POST", {"email": args.email, "password": password, "role": args.role}
    if args.command == "audit":
        return "/api/v1/audit", "GET", None
    raise RuntimeError("unsupported command")


def main():
    args = parser().parse_args()
    try:
        path, method, body = route(args)
        print(json.dumps(request(args.api_url, path, method, body), indent=2))
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
