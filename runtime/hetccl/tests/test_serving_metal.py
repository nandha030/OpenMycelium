import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hetccl.metal import MetalAdapter
from hetccl.serving import VLLMServingAdapter


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"data": [{"id": "tiny"}]})
        elif self.path == "/v1/hetccl/capabilities":
            self._json({"speculative_decoding": True})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/hetccl/propose":
            self._json({"tokens": [1], "distributions": [{"1": 1.0}], "context": payload.get("context")})
        elif self.path == "/v1/hetccl/verify":
            self._json({"distributions": [{"1": 1.0}, {"2": 1.0}]})
        else:
            self.send_error(404)

    def _json(self, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_):
        return


class ServingMetalTests(unittest.TestCase):
    def test_vllm_capability_and_token_extensions(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = VLLMServingAdapter(f"http://127.0.0.1:{server.server_port}", "tiny", runtime="cuda")
            capabilities = adapter.capabilities()
            self.assertTrue(capabilities.openai_compatible)
            self.assertTrue(capabilities.speculative_extension)
            self.assertEqual(capabilities.models, ("tiny",))
            self.assertEqual(adapter.propose([7], 1)["tokens"], [1])
            self.assertEqual(len(adapter.verify([7], [1])["distributions"]), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_metal_bytes_fallback_is_lossless(self):
        adapter = MetalAdapter(torch_module=False)
        payload, dtype, shape = adapter.to_host_bytes(b"metal-cache")
        self.assertEqual((payload, dtype, shape), (b"metal-cache", "u8", (11,)))
        self.assertEqual(adapter.from_host_bytes(payload, dtype, shape, device="cpu"), payload)


if __name__ == "__main__":
    unittest.main()
