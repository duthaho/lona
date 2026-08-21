#!/usr/bin/env python3
"""Fixture-driven HTTP stub for the doctor test suite (stdlib only).

Reads fixtures from $STUB_DIR:
  models.json       body for GET /api/v1/models; {"__status__": N} -> error N;
                    missing file -> 500
  completions.json  {"<model-id>": status-int | "timeout"}; default 200
  gemini.json       {"<model-id>": status-int | "timeout"}; default 200
  telegram.json     {"status": N}; default 200

Every request is appended to $STUB_DIR/requests.log as
"METHOD PATH[ model=<id>]". Binds an ephemeral port and writes it to
$STUB_DIR/port when ready.
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STUB_DIR = os.environ["STUB_DIR"]


def fixture(name, default=None):
    try:
        with open(os.path.join(STUB_DIR, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _log(self, model=None):
        line = f"{self.command} {self.path}"
        if model:
            line += f" model={model}"
        with open(os.path.join(STUB_DIR, "requests.log"), "a") as f:
            f.write(line + "\n")

    def _reply(self, status, body):
        if status == "timeout":
            time.sleep(10)
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/api/v1/models":
            self._log()
            models = fixture("models.json")
            if models is None:
                self._reply(500, {"error": "no models fixture"})
            elif "__status__" in models:
                self._reply(models["__status__"], {"error": "stubbed"})
            else:
                self._reply(200, models)
        else:
            self._log()
            self._reply(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path == "/api/v1/chat/completions":
            model = self._body_json().get("model", "")
            self._log(model)
            status = fixture("completions.json", {}).get(model, 200)
            self._reply(status, {"choices": [{"message": {"content": "ok"}}]})
        elif re.match(r"^/bot[^/]+/sendMessage$", self.path):
            self._log()
            status = fixture("telegram.json", {}).get("status", 200)
            self._reply(status, {"ok": status == 200})
        elif ":generateContent" in self.path:
            model = self.path.split("/")[-1].split(":")[0]
            self._log(model)
            status = fixture("gemini.json", {}).get(model, 200)
            self._reply(status, {"candidates": []})
        else:
            self._log()
            self._reply(404, {"error": "unknown path"})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    with open(os.path.join(STUB_DIR, "port"), "w") as f:
        f.write(str(server.server_address[1]))
    server.serve_forever()


if __name__ == "__main__":
    main()
