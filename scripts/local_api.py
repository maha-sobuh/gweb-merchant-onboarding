"""Local API simulation (spec deliverable: reproducible local Lambda/API simulation).

Serves the REAL Lambda handlers over plain HTTP, backed by moto (DynamoDB + S3),
and serves the web UI at http://127.0.0.1:3000. No AWS account, Docker or network
is needed. Data lives in memory and is lost when the server stops.

Run from the repository root:
    python scripts/local_api.py
    python scripts/local_api.py --port 8080

How it maps to AWS:
- Each HTTP request is turned into an API Gateway proxy event and passed to the
  same handler function that Lambda would run.
- The one difference is document upload. On AWS the client PUTs the file to the
  presigned S3 URL. Here the presign response's `uploadUrl` is rewritten to a local
  endpoint that writes the bytes into the mocked S3 bucket (the content type must
  match the presigned request, as S3 would enforce).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TABLE_NAME = "MerchantOnboarding"
BUCKET_NAME = "local-documents-bucket"
REGION = "us-east-1"

os.environ.update(
    {
        "TABLE_NAME": TABLE_NAME,
        "DOCUMENTS_BUCKET": BUCKET_NAME,
        "LOG_LEVEL": "WARNING",
        "INTERNAL_TIMEOUT_SECONDS": "20",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
    }
)

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

APP = r"^/applications/(?P<id>[^/]+)"

ROUTES = [
    ("POST", re.compile(r"^/applications$"), "applications_create"),
    ("GET", re.compile(APP + r"$"), "applications_get"),
    ("PATCH", re.compile(APP + r"/applicant$"), "applicant_update"),
    ("PATCH", re.compile(APP + r"/business$"), "business_update"),
    ("POST", re.compile(APP + r"/documents/presign$"), "documents_presign"),
    ("POST", re.compile(APP + r"/documents/(?P<documentId>[^/]+)/complete$"), "documents_complete"),
    ("GET", re.compile(r"^/mcc$"), "mcc_search"),
    ("POST", re.compile(APP + r"/classify$"), "classify"),
    ("POST", re.compile(APP + r"/evaluate$"), "evaluate"),
    ("GET", re.compile(APP + r"/evaluation$"), "get_evaluation"),
    ("POST", re.compile(APP + r"/submit$"), "submit"),
]
UPLOAD_ROUTE = re.compile(r"^/_local_upload/(?P<app>[^/]+)/(?P<doc>[^/]+)$")

LOCK = threading.Lock()  # moto and the handlers run one request at a time
STATE: dict = {}


def setup_aws() -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    dynamodb.create_table(
        TableName=TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
    )
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET_NAME)


class Ctx:
    def __init__(self) -> None:
        self.aws_request_id = str(uuid.uuid4())


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalMerchantOnboarding/1.0"

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, fmt, *args):
        # args is (request line, status code, size) for normal requests
        status = args[1] if len(args) > 1 else ""
        sys.stderr.write(f"{self.requestline} -> {status}\n")

    # --- routing ---------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if method == "GET" and path in ("/", "/index.html"):
            return self._serve_ui()
        if method == "GET" and path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        upload = UPLOAD_ROUTE.match(path)
        if method == "PUT" and upload:
            return self._local_upload(upload.group("app"), upload.group("doc"))

        for route_method, pattern, name in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(path)
            if match:
                return self._invoke(name, match.groupdict(), parsed.query)

        self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "No such route"}})

    def _invoke(self, name: str, params: dict, query_string: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8") if length else None
        query = {k: v[0] for k, v in parse_qs(query_string).items()} or None
        event = {
            "httpMethod": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": raw_body,
            "isBase64Encoded": False,
            "pathParameters": params or None,
            "queryStringParameters": query,
        }

        module = importlib.import_module(f"handlers.{name}")
        with LOCK:
            response = module.handler(event, Ctx())

        status = response["statusCode"]
        body = response.get("body") or ""

        if name == "documents_presign" and status == 201:
            payload = json.loads(body)
            host = self.headers.get("Host", "127.0.0.1:3000")
            payload["uploadUrl"] = f"http://{host}/_local_upload/{params['id']}/{payload['documentId']}"
            body = json.dumps(payload)

        self._send_raw(status, body.encode("utf-8"), "application/json")

    def _local_upload(self, application_id: str, document_id: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        sent_type = self.headers.get("Content-Type", "")
        with LOCK:
            document = STATE["repo"].get_document(application_id, document_id)
            if document is None:
                return self._send_raw(404, b"NoSuchDocument", "text/plain")
            if sent_type != document.content_type:
                return self._send_raw(
                    403,
                    b"SignatureDoesNotMatch: Content-Type differs from the presigned request",
                    "text/plain",
                )
            STATE["s3"].put_object(
                Bucket=BUCKET_NAME, Key=document.s3_key, Body=data, ContentType=sent_type
            )
        self._send_raw(200, b"", "text/plain")

    def _serve_ui(self) -> None:
        page = ROOT / "ui" / "index.html"
        if not page.exists():
            return self._send_raw(404, b"ui/index.html not found", "text/plain")
        self._send_raw(200, page.read_bytes(), "text/html; charset=utf-8")

    # --- responses -------------------------------------------------------

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Idempotency-Key")

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_raw(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _send_raw(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local API simulation for the merchant onboarding handlers")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    with mock_aws():
        setup_aws()
        from repositories.application_repo import ApplicationRepository

        STATE["repo"] = ApplicationRepository()
        STATE["s3"] = boto3.client("s3", region_name=REGION)

        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"Local API and UI running at http://127.0.0.1:{args.port}")
        print("Data is kept in memory and is lost when the server stops. Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
