import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def env_json(name, fallback):
    try:
        return json.loads(os.getenv(name, ""))
    except json.JSONDecodeError:
        return fallback


AGENT_ID = os.getenv("OPENMYCELIUM_AGENT_ID", "standalone-agent")
RUN_ID = os.getenv("OPENMYCELIUM_AGENT_RUN_ID", "standalone-run")
WORKSPACE_ID = os.getenv("OPENMYCELIUM_WORKSPACE_ID", "standalone")
SPEC = env_json("OPENMYCELIUM_AGENT_SPEC_JSON", {})
FLOW = env_json("OPENMYCELIUM_AGENT_FLOW_JSON", {})
TOOLS = env_json("OPENMYCELIUM_AGENT_TOOLS_JSON", [])
MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "").rstrip("/")
MODEL_NAME = os.getenv("MODEL_NAME", os.getenv("OPENMYCELIUM_MODEL", ""))
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")


def model_response(message):
    if not MODEL_BASE_URL or not MODEL_NAME:
        return {
            "message": message,
            "mode": "contract-only",
            "detail": "Set MODEL_BASE_URL and MODEL_NAME to enable OpenAI-compatible model execution.",
        }
    body = json.dumps(
        {"model": MODEL_NAME, "messages": [{"role": "user", "content": message}]}
    ).encode("utf-8")
    request = urllib.request.Request(
        MODEL_BASE_URL + "/v1/chat/completions",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if MODEL_API_KEY:
        request.add_header("Authorization", "Bearer " + MODEL_API_KEY)
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return {
        "message": result.get("choices", [{}])[0].get("message", {}).get("content", ""),
        "usage": result.get("usage", {}),
        "model": result.get("model", MODEL_NAME),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenMyceliumAgentRuntime/0.1"

    def send_json(self, status, value):
        content = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ready", "agentId": AGENT_ID, "runId": RUN_ID})
            return
        if self.path in ("/v1/runtime", "/.well-known/agent.json"):
            self.send_json(
                200,
                {
                    "protocolVersion": "1.0",
                    "name": AGENT_ID,
                    "version": "0.1.0",
                    "workspaceId": WORKSPACE_ID,
                    "runId": RUN_ID,
                    "spec": SPEC,
                    "flow": FLOW,
                    "tools": TOOLS,
                    "capabilities": {"streaming": False, "stateTransitionHistory": True},
                },
            )
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/run":
            self.send_json(404, {"error": "not found"})
            return
        try:
            size = min(int(self.headers.get("Content-Length", "0")), 1024 * 1024)
            value = json.loads(self.rfile.read(size) or b"{}")
            message = value.get("message") or json.dumps(value)
            output = model_response(message)
            self.send_json(
                200,
                {"agentId": AGENT_ID, "runId": RUN_ID, "status": "completed", "output": output},
            )
        except (ValueError, urllib.error.URLError, TimeoutError) as error:
            self.send_json(502, {"status": "failed", "error": str(error)})

    def log_message(self, format_string, *args):
        print(json.dumps({"level": "info", "message": format_string % args, "runId": RUN_ID}))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
