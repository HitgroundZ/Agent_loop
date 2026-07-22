from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Agent Loop Day 10 live evals")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("cases.json"),
    )
    parser.add_argument("--user-id", default="day10-eval", help="eval principal prefix")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    run_suffix = f"day10-eval-{int(time.time())}-{uuid4().hex[:6]}"
    failures = 0

    print(f"Day 10 live evals: {len(cases)} cases @ {args.base_url}")
    for index, case in enumerate(cases, start=1):
        session_id = f"{run_suffix}-{index}"
        payload = _replace_templates(
            case["payload"],
            {
                "case_user_id": f"{args.user_id}-{run_suffix}-{index}",
                "memory_user_id": f"{args.user_id}-{run_suffix}-memory",
                "session_id": session_id,
            },
        )
        try:
            run = _request_json(
                f"{args.base_url.rstrip('/')}/api/agent/runs",
                method="POST",
                payload=payload,
                timeout=args.timeout,
            )
            errors = _validate(run, case.get("expect") or {})
            if not errors and case.get("decision"):
                errors.extend(
                    _apply_decision(
                        args.base_url,
                        str(payload["user_id"]),
                        run,
                        case["decision"],
                        args.timeout,
                    )
                )
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors = [f"request failed: {exc}"]

        if errors:
            failures += 1
            print(f"FAIL {case['name']}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"PASS {case['name']}")

    passed = len(cases) - failures
    print(f"Result: {passed}/{len(cases)} passed")
    return 1 if failures else 0


def _apply_decision(
    base_url: str,
    principal_id: str,
    run: dict,
    decision: dict,
    timeout: float,
) -> list[str]:
    pending = [
        action for action in run.get("tool_actions") or []
        if action.get("status") == "pending"
    ]
    if not pending:
        return ["decision requested but no pending action exists"]
    action = pending[0]
    response = _request_json(
        f"{base_url.rstrip('/')}/api/tool-actions/{action['id']}/{decision['type']}",
        method="POST",
        payload={"reason": decision.get("reason") or "Day10 eval"},
        headers={
            "X-Principal-Id": principal_id,
            "Idempotency-Key": f"day10-eval-{uuid4()}",
        },
        timeout=timeout,
    )
    resumed = response.get("run") or {}
    expected_state = decision.get("expect_state")
    if expected_state and resumed.get("current_state") != expected_state:
        return [
            f"decision expected state={expected_state}, got={resumed.get('current_state')}"
        ]
    return []


def _validate(run: dict, expect: dict) -> list[str]:
    errors: list[str] = []
    if expect.get("state") and run.get("current_state") != expect["state"]:
        errors.append(
            f"expected state={expect['state']}, got={run.get('current_state')}"
        )

    actions = run.get("tool_actions") or []
    action_names = {action.get("tool_name") for action in actions}
    for name in expect.get("required_tools") or []:
        if name not in action_names:
            errors.append(f"required tool missing: {name}")
    for name in expect.get("forbidden_tools") or []:
        if name in action_names:
            errors.append(f"forbidden tool was called: {name}")

    statuses = {action.get("tool_name"): action.get("status") for action in actions}
    for name, expected_status in (expect.get("tool_status") or {}).items():
        if statuses.get(name) != expected_status:
            errors.append(
                f"tool {name} expected status={expected_status}, got={statuses.get(name)}"
            )

    trace_states = set(run.get("state_flow") or [])
    for state in expect.get("trace_states") or []:
        if state not in trace_states:
            errors.append(f"trace state missing: {state}")

    citation_min = expect.get("citation_min")
    if citation_min is not None and len(run.get("citations") or []) < citation_min:
        errors.append(
            f"expected at least {citation_min} citations, got={len(run.get('citations') or [])}"
        )

    if "handoff" in expect:
        actual = bool((run.get("evaluation") or {}).get("need_human_handoff"))
        if actual != bool(expect["handoff"]):
            errors.append(f"expected handoff={expect['handoff']}, got={actual}")
    return errors


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"HTTP {exc.code}: {detail[:500]}") from exc


def _replace_templates(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_templates(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_templates(item, variables) for item in value]
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace(f"{{{{{key}}}}}", replacement)
    return value


if __name__ == "__main__":
    sys.exit(main())
