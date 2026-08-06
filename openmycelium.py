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
    cluster_add.add_argument("endpoint")
    cluster_add.add_argument("--type", default="kubernetes", choices=["kubernetes", "docker", "bare-metal"])
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


def add_workload_commands(sub):
    workload = sub.add_parser("workload", help="Submit and operate workloads")
    workload_sub = workload.add_subparsers(dest="action", required=True)
    workload_sub.add_parser("list")
    workload_sub.add_parser("refresh")
    submit = workload_sub.add_parser("submit")
    submit.add_argument("name")
    submit.add_argument("--kind", default="batch", choices=["training", "batch", "interactive", "inference"])
    submit.add_argument("--pool", default="cpu-local")
    submit.add_argument("--image", default="")
    submit.add_argument("--accelerators", type=int, default=0)
    submit.add_argument("--ssh-host", default="")
    submit.add_argument("--ssh-port", type=int, default=22)
    submit.add_argument("--ssh-user", default="")
    for action in ("start", "stop", "redeploy", "checkpoint"):
        command = workload_sub.add_parser(action)
        command.add_argument("id")
    workload_delete = workload_sub.add_parser("delete")
    workload_delete.add_argument("id")
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
    deploy = model_sub.add_parser("deploy")
    deploy.add_argument("name")
    generate = model_sub.add_parser("generate")
    generate.add_argument("name")
    generate.add_argument("prompt")


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
        return "/api/v1/clusters", "POST", {"name": args.name, "endpoint": args.endpoint, "type": args.type}
    if args.command == "pool":
        if args.action == "list":
            return "/api/v1/pools", "GET", None
        if args.action == "delete":
            return f"/api/v1/pools/{args.id}", "DELETE", None
        return "/api/v1/pools", "POST", {"name": args.name, "vendor": args.vendor, "runtime": args.runtime, "policy": args.policy, "selector": args.selector}
    if args.command == "queue":
        if args.action == "list":
            return "/api/v1/queues", "GET", None
        if args.action == "delete":
            return f"/api/v1/queues/{args.id}", "DELETE", None
        return "/api/v1/queues", "POST", {"name": args.name, "priority": args.priority, "acceleratorQuota": args.accelerators, "memoryQuotaGB": args.memory_gb, "preemptionEnabled": args.preemption}
    if args.command == "workload":
        if args.action in ("list", "refresh"):
            return "/api/v1/workloads", "GET", None
        if args.action == "submit":
            ssh_configured = bool(args.ssh_host or args.ssh_user)
            return "/api/v1/workloads", "POST", {"name": args.name, "kind": args.kind, "pool": args.pool, "image": args.image, "accelerators": args.accelerators, "sshHost": args.ssh_host, "sshPort": args.ssh_port if ssh_configured else 0, "sshUser": args.ssh_user}
        if args.action == "delete":
            return f"/api/v1/workloads/{args.id}", "DELETE", None
        if args.action == "ssh":
            return f"/api/v1/workloads/{args.id}/ssh", "GET", None
        if args.action == "ssh-config":
            return f"/api/v1/workloads/{args.id}/ssh", "PATCH", {"host": args.host, "port": args.port, "user": args.user}
        return f"/api/v1/workloads/{args.id}/action", "POST", {"action": args.action}
    if args.command == "model":
        if args.action == "list":
            return "/api/v1/models", "GET", None
        if args.action == "deploy":
            return "/api/v1/inference/deploy", "POST", {"model": args.name}
        return "/api/v1/inference/generate", "POST", {"model": args.name, "prompt": args.prompt}
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
