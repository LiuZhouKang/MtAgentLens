#!/usr/bin/env python3
"""Offline structural checker for the generated Kestrel Ledger OpenAPI document.

Usage:
    python3 check_spec.py /tmp/openapi-spec.yaml

The checker reads only the file given on the command line. It makes no network
calls and writes nothing. It validates that the document describes every
endpoint in ``docs.md`` with the documented operationId, parameters, request
bodies, response codes and component schema references, plus the ``bearerAuth``
security scheme.

Exit codes: 0 = pass, 1 = problems found, 2 = usage or environment error.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("checker error: PyYAML is not installed (pip install pyyaml)")
    sys.exit(2)

SERVER_URL = "http://127.0.0.1:8480"

# (method, path) -> expected operation shape
OPERATIONS = {
    ("get", "/v2/accounts"): {
        "operation_id": "listAccounts",
        "parameters": [
            ("limit", "query", False),
            ("cursor", "query", False),
            ("status", "query", False),
        ],
        "request_body": None,
        "responses": {"200": "AccountList", "400": "Error", "401": "Error"},
    },
    ("get", "/v2/accounts/{account_id}"): {
        "operation_id": "getAccount",
        "parameters": [("account_id", "path", True)],
        "request_body": None,
        "responses": {"200": "Account", "401": "Error", "404": "Error"},
    },
    ("post", "/v2/transfers"): {
        "operation_id": "createTransfer",
        "parameters": [("Idempotency-Key", "header", True)],
        "request_body": (True, "CreateTransferRequest"),
        "responses": {
            "201": "Transfer",
            "400": "Error",
            "401": "Error",
            "409": "Error",
            "422": "Error",
        },
    },
    ("get", "/v2/transfers/{transfer_id}"): {
        "operation_id": "getTransfer",
        "parameters": [("transfer_id", "path", True)],
        "request_body": None,
        "responses": {"200": "Transfer", "401": "Error", "404": "Error"},
    },
    ("post", "/v2/transfers/{transfer_id}/cancel"): {
        "operation_id": "cancelTransfer",
        "parameters": [("transfer_id", "path", True)],
        "request_body": (False, "CancelTransferRequest"),
        "responses": {
            "200": "Transfer",
            "401": "Error",
            "404": "Error",
            "409": "Error",
        },
    },
    ("get", "/v2/ledger/entries"): {
        "operation_id": "listLedgerEntries",
        "parameters": [
            ("account_id", "query", True),
            ("from", "query", False),
            ("to", "query", False),
            ("limit", "query", False),
            ("cursor", "query", False),
        ],
        "request_body": None,
        "responses": {"200": "LedgerEntryList", "400": "Error", "401": "Error"},
    },
    ("get", "/v2/health"): {
        "operation_id": "getHealth",
        "parameters": [],
        "request_body": None,
        "responses": {"200": "HealthStatus"},
    },
}

SCHEMAS = {
    "Account": {"id", "display_name", "currency", "status", "balance_minor", "created_at"},
    "AccountList": {"data", "has_more"},
    "Transfer": {
        "id",
        "source_account_id",
        "destination_account_id",
        "amount_minor",
        "currency",
        "status",
        "idempotency_key",
        "created_at",
        "updated_at",
    },
    "CreateTransferRequest": {
        "source_account_id",
        "destination_account_id",
        "amount_minor",
        "currency",
    },
    "CancelTransferRequest": set(),
    "LedgerEntry": {
        "id",
        "transfer_id",
        "account_id",
        "direction",
        "amount_minor",
        "currency",
        "balance_after_minor",
        "posted_at",
    },
    "LedgerEntryList": {"data", "has_more"},
    "Error": {"error"},
    "HealthStatus": {"status", "version", "uptime_seconds"},
}

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


class Checker:
    def __init__(self, document):
        self.doc = document
        self.problems: list[str] = []
        self.notes: list[str] = []

    def fail(self, message: str) -> None:
        self.problems.append(message)

    def ok(self, message: str) -> None:
        self.notes.append(message)

    # -- helpers -----------------------------------------------------------
    def resolve(self, node):
        """Resolve local ``$ref`` pointers. Returns None when unresolvable."""
        seen = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                return None
            if ref in seen:
                return None
            seen.add(ref)
            node = self.pointer(ref)
        return node

    def pointer(self, ref: str):
        target = self.doc
        for part in ref.lstrip("#/").split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                return None
            target = target[part]
        return target

    def all_refs(self, node, out: list[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    out.append(value)
                else:
                    self.all_refs(value, out)
        elif isinstance(node, list):
            for item in node:
                self.all_refs(item, out)

    # -- checks ------------------------------------------------------------
    def check(self) -> None:
        doc = self.doc
        if not isinstance(doc, dict):
            self.fail("document root is not a mapping")
            return

        version = str(doc.get("openapi", ""))
        if version.startswith("3.1") or version.startswith("3.0"):
            self.ok(f"openapi version {version}")
        else:
            self.fail(f"openapi must be 3.1.x or 3.0.x, found {version!r}")

        info = doc.get("info")
        if not isinstance(info, dict):
            self.fail("missing info object")
        else:
            for field in ("title", "version"):
                if not str(info.get(field, "")).strip():
                    self.fail(f"info.{field} is missing or empty")

        servers = doc.get("servers")
        if not isinstance(servers, list) or not servers:
            self.fail("servers must be a non-empty list")
        else:
            urls = [s.get("url") for s in servers if isinstance(s, dict)]
            if len(servers) != 1:
                self.fail(f"servers must declare exactly one entry, found {len(servers)}")
            if SERVER_URL in urls:
                self.ok(f"server url {SERVER_URL}")
            else:
                self.fail(f"servers must include url {SERVER_URL!r}, found {urls!r}")

        components = doc.get("components")
        schemes = components.get("securitySchemes", {}) if isinstance(components, dict) else {}
        bearer = self.resolve(schemes.get("bearerAuth")) if isinstance(schemes, dict) else None
        if not isinstance(bearer, dict):
            self.fail("components.securitySchemes.bearerAuth is missing")
        elif bearer.get("type") != "http" or str(bearer.get("scheme", "")).lower() != "bearer":
            self.fail("bearerAuth must be type=http scheme=bearer")
        else:
            self.ok("security scheme bearerAuth")

        global_security = doc.get("security")
        if not isinstance(global_security, list) or not any(
            isinstance(item, dict) and "bearerAuth" in item for item in global_security
        ):
            self.fail("document-level security must require bearerAuth")
        else:
            self.ok("document-level bearerAuth requirement")

        self.check_paths()
        self.check_components()
        self.check_refs()

    def check_paths(self) -> None:
        paths = self.doc.get("paths")
        if not isinstance(paths, dict):
            self.fail("missing paths object")
            return

        expected_paths = {path for _, path in OPERATIONS}
        actual_paths = set(paths)
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        if missing:
            self.fail(f"missing documented paths: {missing}")
        if extra:
            self.fail(f"undocumented paths present: {extra}")
        if not missing and not extra:
            self.ok(f"{len(expected_paths)} documented paths")

        found_operations = 0
        for (method, path), expected in OPERATIONS.items():
            item = self.resolve(paths.get(path))
            if not isinstance(item, dict):
                continue  # already reported as missing path
            unexpected = sorted(
                key for key in item if key in HTTP_METHODS and key != method
            )
            if unexpected:
                self.fail(f"{path}: unexpected methods {unexpected}")
            operation = self.resolve(item.get(method))
            if not isinstance(operation, dict):
                self.fail(f"{method.upper()} {path}: operation is missing")
                continue
            found_operations += 1
            inherited = item.get("parameters", []) if isinstance(item, dict) else []
            self.check_operation(method, path, operation, expected, inherited)

        if found_operations == len(OPERATIONS) and not missing:
            self.ok(f"{len(OPERATIONS)} operations")

    def check_operation(
        self,
        method: str,
        path: str,
        operation: dict,
        expected: dict,
        inherited_parameters=(),
    ) -> None:
        label = f"{method.upper()} {path}"
        op_id = operation.get("operationId")
        if op_id != expected["operation_id"]:
            self.fail(f"{label}: operationId must be {expected['operation_id']!r}, found {op_id!r}")
        for field in ("summary", "description"):
            if not str(operation.get(field, "")).strip():
                self.fail(f"{label}: {field} is missing or empty")

        # Parameters (operation-level plus inherited path-item parameters)
        raw_params = operation.get("parameters", [])
        if raw_params is None:
            raw_params = []
        if not isinstance(raw_params, list):
            self.fail(f"{label}: parameters must be a list")
            raw_params = []
        combined = list(raw_params)
        if isinstance(inherited_parameters, list):
            combined.extend(inherited_parameters)
        actual = {}
        for raw in combined:
            param = self.resolve(raw)
            if not isinstance(param, dict):
                self.fail(f"{label}: parameter entry is not resolvable")
                continue
            name = param.get("name")
            location = param.get("in")
            if isinstance(name, str) and isinstance(location, str):
                actual[(name, location)] = bool(param.get("required", False))
        for name, location, required in expected["parameters"]:
            key = (name, location)
            if key not in actual:
                self.fail(f"{label}: missing {location} parameter {name!r}")
            elif actual[key] != required:
                self.fail(
                    f"{label}: parameter {name!r} required must be {required}, "
                    f"found {actual[key]}"
                )
        for (name, location) in actual:
            if not any(
                name == expected_name and location == expected_location
                for expected_name, expected_location, _ in expected["parameters"]
            ):
                self.fail(f"{label}: undocumented {location} parameter {name!r}")

        # Request body
        expected_body = expected["request_body"]
        body = self.resolve(operation.get("requestBody"))
        if expected_body is None:
            if body is not None:
                self.fail(f"{label}: unexpected requestBody")
        else:
            required, schema_name = expected_body
            if not isinstance(body, dict):
                self.fail(f"{label}: requestBody is missing")
            else:
                if bool(body.get("required", False)) != required:
                    self.fail(
                        f"{label}: requestBody.required must be {required}, "
                        f"found {body.get('required')!r}"
                    )
                content = body.get("content")
                schema = content.get("application/json", {}).get("schema") if isinstance(content, dict) else None
                ref_name = schema.get("$ref") if isinstance(schema, dict) else None
                if ref_name != f"#/components/schemas/{schema_name}":
                    self.fail(
                        f"{label}: requestBody must reference "
                        f"#/components/schemas/{schema_name}, found {ref_name!r}"
                    )

        # Responses (YAML may parse bare status codes as integers)
        response_map = operation.get("responses")
        if not isinstance(response_map, dict):
            self.fail(f"{label}: responses object is missing")
            return
        responses = {str(key): value for key, value in response_map.items()}
        for status, schema_name in expected["responses"].items():
            response = self.resolve(responses.get(status))
            if not isinstance(response, dict):
                self.fail(f"{label}: missing response {status}")
                continue
            if not str(response.get("description", "")).strip():
                self.fail(f"{label}: response {status} has no description")
            content = response.get("content")
            schema = content.get("application/json", {}).get("schema") if isinstance(content, dict) else None
            ref_name = schema.get("$ref") if isinstance(schema, dict) else None
            if ref_name != f"#/components/schemas/{schema_name}":
                self.fail(
                    f"{label}: response {status} must reference "
                    f"#/components/schemas/{schema_name}, found {ref_name!r}"
                )

        # Health is explicitly unauthenticated.
        if expected["operation_id"] == "getHealth":
            if operation.get("security") != []:
                self.fail(f"{label}: security must be an explicit empty list")

    def check_components(self) -> None:
        components = self.doc.get("components")
        schemas = components.get("schemas") if isinstance(components, dict) else None
        if not isinstance(schemas, dict):
            self.fail("components.schemas is missing")
            return
        schema_problems = 0
        for name, required_fields in SCHEMAS.items():
            schema = self.resolve(schemas.get(name))
            if not isinstance(schema, dict):
                self.fail(f"components.schemas.{name} is missing")
                schema_problems += 1
                continue
            if schema.get("type") != "object":
                self.fail(f"components.schemas.{name} must have type: object")
                schema_problems += 1
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                self.fail(f"components.schemas.{name} has no properties")
                schema_problems += 1
                continue
            declared_raw = schema.get("required", [])
            if declared_raw is None:
                declared_raw = []
            if not isinstance(declared_raw, list):
                self.fail(f"components.schemas.{name}.required must be a list")
                schema_problems += 1
                declared_raw = []
            declared = set(declared_raw)
            missing = sorted(required_fields - declared)
            if missing:
                self.fail(
                    f"components.schemas.{name} required list is missing {missing}"
                )
                schema_problems += 1
            unknown = sorted(required_fields - set(properties))
            if unknown:
                self.fail(
                    f"components.schemas.{name} required fields lack properties: {unknown}"
                )
                schema_problems += 1
        if schema_problems == 0:
            self.ok(f"{len(SCHEMAS)} component schemas")

    def check_refs(self) -> None:
        refs: list[str] = []
        self.all_refs(self.doc, refs)
        unresolved = []
        external = []
        for ref in refs:
            if not ref.startswith("#/"):
                external.append(ref)
            elif self.pointer(ref) is None:
                unresolved.append(ref)
        if external:
            self.fail(f"external or non-local $ref not allowed: {external}")
        if unresolved:
            self.fail(f"unresolvable $ref: {unresolved}")
        if not external and not unresolved:
            self.ok(f"{len(refs)} local $ref pointers resolve")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_spec.py <openapi-spec.yaml>")
        return 2
    target = Path(argv[1])
    if not target.is_file():
        print(f"checker error: file not found: {target}")
        return 2
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"checker error: cannot read {target}: {exc}")
        return 2
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"[FAIL] document is not valid YAML: {exc}")
        print("CHECK RESULT: FAIL — 1 problem(s)")
        return 1

    print("Kestrel Ledger OpenAPI checker")
    print(f"Target: {target}")
    checker = Checker(document)
    checker.check()

    for note in checker.notes:
        print(f"[ok]   {note}")
    for problem in checker.problems:
        print(f"[FAIL] {problem}")

    if checker.problems:
        print(f"CHECK RESULT: FAIL — {len(checker.problems)} problem(s)")
        return 1

    paths_doc = checker.doc.get("paths", {})
    paths = len(paths_doc)
    operations = 0
    for method, path in OPERATIONS:
        item = checker.resolve(paths_doc.get(path))
        if isinstance(item, dict) and isinstance(checker.resolve(item.get(method)), dict):
            operations += 1
    schemas = len(checker.doc.get("components", {}).get("schemas", {}))
    print(
        f"CHECK RESULT: PASS — {paths} paths, {operations} operations, "
        f"{schemas} schemas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
