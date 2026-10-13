"""IAOS_RAW_AGENCY_SKILL_SMOKE_GATE runner: one call without the Skill, one with the raw upstream Skill. Text only.

Credentials: read from the environment (AGNES_API_KEY) at call time; never stored, printed or written to the record.
The request carries no `tools`/`functions`/`tool_choice`; any tool_calls in a response are recorded and treated as a failure.
"""
import base64
import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from skills.raw import RawSkillSource, raw_skill_context

PROVIDER = "Agnes AI"
ENDPOINT = "https://apihub.agnes-ai.com/v1/chat/completions"
KEY_ENV = "AGNES_API_KEY"
ALLOWED_REQUEST_KEYS = {"model", "messages", "temperature", "top_p", "max_tokens"}
TASK = ("Após um deploy, o serviço começou a apresentar forte aumento de erros HTTP 5xx. "
        "Você está responsável pela resposta ao incidente. Conduza a resposta.")
SKILL_LEAD = "The following skill describes how to approach this kind of work:\n\n"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_prompt(task: str, raw: RawSkillSource = None) -> str:
    if raw is None:
        return task
    return SKILL_LEAD + raw_skill_context(raw) + "\n\n=== TASK ===\n" + task


def build_payload(prompt: str, model: str, temperature: float = 0.0, top_p: float = 1.0, max_tokens: int = 4096,
                  image: tuple = None) -> dict:
    """``image`` = (mime, bytes): attached as a data-URL ``image_url`` part next to the text. Still no tools."""
    content = prompt if image is None else [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (image[0], base64.b64encode(image[1]).decode("ascii"))}}]
    payload = {"model": model, "messages": [{"role": "user", "content": content}],
               "temperature": temperature, "top_p": top_p, "max_tokens": max_tokens}
    assert set(payload) <= ALLOWED_REQUEST_KEYS
    return payload


def http_transport(payload: dict, key: str, timeout: int = 300):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))


def call(label: str, prompt: str, model: str, transport=http_transport, key: str = None, **params) -> dict:
    key = key or os.environ.get(KEY_ENV)
    if not key:
        raise RuntimeError(KEY_ENV + " is not set in the environment")
    payload = build_payload(prompt, model, **params)
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    status, headers, body = transport(payload, key)
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    rid = next((headers[h] for h in headers if h.lower() in ("x-request-id", "request-id", "x-amzn-requestid")), None)
    record = {
        "label": label, "provider": PROVIDER, "endpoint": ENDPOINT, "requested_model": model,
        "returned_model": body.get("model"), "request_id_header": rid, "response_id": body.get("id"),
        "http_status": status, "params_sent": {k: v for k, v in payload.items() if k not in ("model", "messages")},
        "seed": "not sent", "started_utc": started, "elapsed_s": round(time.time() - t0, 2),
        "prompt_sha256": sha256(prompt), "payload_keys": sorted(payload), "usage": body.get("usage"),
        "finish_reason": choice.get("finish_reason"), "tool_calls_in_response": message.get("tool_calls"),
        "output": message.get("content"),
    }
    assert key not in json.dumps(record)
    return record


def run_smoke(raw: RawSkillSource, model: str, out_dir: Path, transport=http_transport, key: str = None) -> dict:
    control_prompt, skill_prompt = build_prompt(TASK), build_prompt(TASK, raw)
    control = call("control_no_skill", control_prompt, model, transport, key)
    with_skill = call("with_raw_skill", skill_prompt, model, transport, key)
    result = {"gate": "IAOS_RAW_AGENCY_SKILL_SMOKE_GATE", "task": TASK,
              "raw_skill": {"repo": raw.repo, "revision": raw.revision, "path": raw.path, "blob": raw.blob,
                            "license": raw.license, "text_sha256": raw.text_sha256},
              "calls": [control, with_skill]}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke_record.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result
