#!/usr/bin/env python3
"""
End-to-end smoke test for the prediction stack: boots the internal predictor
service and the external API service as real HTTP servers on isolated ports,
seeds a small synthetic history, and exercises the full request chain for
the probabilistic predictor. Meant to be run once before
deploying changes that touch the prediction endpoints.

Does NOT touch the real deployment: uses a temp STATE_DIR and isolated ports
(so it can run safely alongside an already-running production instance).

Usage:
    cd ai/test-predictor
    .venv/bin/python3 src/tests/e2e_smoke.py
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VENV_BIN = os.path.join(PROJECT_ROOT, ".venv", "bin")
PREDICTOR_PORT = 5101
API_PORT = 5100
FAILURES = []


def check(label, condition, detail=""):
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def wait_for_port(host, port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def write_sample_ts(ts_dir, system, name, verb, scenario, pattern):
    os.makedirs(ts_dir, exist_ok=True)
    lines = ["system,name,verb,backend,scenario,success,attempt,start,job_id,run_id"]
    for i, success in enumerate(pattern):
        lines.append(
            f"{system},{name},{verb},openstack,{scenario},{success},1,"
            f"2026-09-{(i % 28) + 1:02d}T10:00:00Z,1000,{2000 + i}"
        )
    path = os.path.join(ts_dir, f"results_job_1000_run_9999_scenario_{scenario}_attempt_1.ts")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


class Service:
    def __init__(self, name, cmd, env, host, port):
        self.name = name
        self.cmd = cmd
        self.env = env
        self.host = host
        self.port = port
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            self.cmd, cwd=PROJECT_ROOT, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if not wait_for_port(self.host, self.port, timeout=60):
            self.stop()
            raise RuntimeError(f"{self.name} did not open port {self.port} in time")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def dump_output_on_failure(self):
        if self.proc and self.proc.poll() not in (None, 0):
            print(f"--- {self.name} output (exit={self.proc.returncode}) ---")
            if self.proc.stdout:
                print(self.proc.stdout.read())


def main():
    tmp_dir = tempfile.mkdtemp(prefix="test-predictor-e2e-")
    predictor_base = f"http://127.0.0.1:{PREDICTOR_PORT}"
    api_base = f"http://127.0.0.1:{API_PORT}"

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{PROJECT_ROOT}/src/common:{PROJECT_ROOT}/src"
    env["TEST_PREDICTOR_STATE_DIR"] = tmp_dir
    env["TEST_PREDICTOR_PREDICTOR_PORT"] = str(PREDICTOR_PORT)
    env["TEST_PREDICTOR_API_PORT"] = str(API_PORT)

    predictor_svc = Service(
        "predictor",
        [f"{VENV_BIN}/python3", "src/services/jobs/predictor.py"],
        env, "127.0.0.1", PREDICTOR_PORT,
    )
    api_svc = Service(
        "api",
        [f"{VENV_BIN}/python3", "-c",
         f"from src.services.api.main import app; app.run(host='127.0.0.1', port={API_PORT}, threaded=True)"],
        env, "127.0.0.1", API_PORT,
    )

    try:
        predictor_svc.start()
        api_svc.start()

        system, name, verb, scenario = "ubuntu-24.04-64", "tests/main/e2e-smoke", "executing", "generic"
        pattern = [1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 1]
        write_sample_ts(os.path.join(tmp_dir, "data", "ts"), system, name, verb, scenario, pattern)

        resp = requests.post(f"{predictor_base}/internal/rebuild-cache", timeout=30)
        check("rebuild-cache seeds the synthetic history", resp.status_code == 200, resp.text)

        # --- Internal predictor: full probabilistic context ---
        resp_prob = requests.get(
            f"{predictor_base}/internal/context",
            params={"system": system, "name": name, "verb": verb, "scenario": scenario},
            timeout=10,
        )
        prob_len = len(resp_prob.json().get("history", [])) if resp_prob.status_code == 200 else -1
        check("probabilistic context returns full seeded depth (20)", prob_len == len(pattern), f"got {prob_len}")

        # --- Internal predictor: /internal/predict ---
        predict_payload = {"system": system, "name": name, "verb": verb, "scenario": scenario}
        resp = requests.post(f"{predictor_base}/internal/predict",
                              json=predict_payload, timeout=10)
        body = resp.json() if resp.status_code == 200 else {}
        check("probabilistic predict succeeds", resp.status_code == 200, resp.text)
        check("probabilistic predict probability is in [0,1]", 0.0 <= body.get("probability", -1) <= 1.0, str(body))
        check("probabilistic predict model field is correct", body.get("model") == "probabilistic", str(body))
        check("probabilistic predict context_len is full depth (20)", body.get("context_len") == len(pattern), str(body))

        # --- Internal predictor: /internal/predict-pattern (no cache needed) ---
        pattern_str = ",".join(str(v) for v in pattern)
        resp = requests.get(
            f"{predictor_base}/internal/predict-pattern",
            params={"name": "anything", "system": "anything", "verb": "executing",
                    "scenario": "generic", "pattern": pattern_str},
            timeout=10,
        )
        body = resp.json() if resp.status_code == 200 else {}
        check("predict-pattern probabilistic works with unseen labels", resp.status_code == 200, resp.text)
        check("predict-pattern probabilistic uses full pattern (not truncated)",
              body.get("pattern_info", {}).get("truncated") is False, str(body))

        # --- External API ---
        resp = requests.get(f"{api_base}/predict", params=predict_payload, timeout=10)
        body = resp.json() if resp.status_code == 200 else {}
        check("external /predict (probabilistic) succeeds", resp.status_code == 200, resp.text)
        check("external /predict surfaces model field", body.get("model") == "probabilistic", str(body))

        resp = requests.get(f"{api_base}/predict-with-history",
                             params=predict_payload, timeout=10)
        body = resp.json() if resp.status_code == 200 else {}
        check("external /predict-with-history (probabilistic) succeeds", resp.status_code == 200, resp.text)
        check("external /predict-with-history reflects full depth (20)",
              body.get("history_length") == len(pattern), str(body))

        print("\nNOTE: /rank-risk and /worst-systems are intentionally not exercised here --"
              " they iterate every known name/system and are too slow for a smoke test.")

    except Exception as e:
        FAILURES.append(f"unhandled exception: {e}")
        print(f"[FAIL] unhandled exception: {e}")
        predictor_svc.dump_output_on_failure()
        api_svc.dump_output_on_failure()
    finally:
        api_svc.stop()
        predictor_svc.stop()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{len(FAILURES)} failure(s).")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
