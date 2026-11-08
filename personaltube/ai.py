"""JSON-in, JSON-out calls to the AI the user picked: an HTTP API or a subscription CLI."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import store

OPENROUTER_URL = "https://openrouter.ai/api/v1"

# provider: (menu label, scoring model, profile model). Empty model = the tool's default.
PROVIDERS = {
    "openrouter": ("OpenRouter API key (pay per use, about $0.05 per update)",
                   "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"),
    "claude": ("Claude subscription, through the Claude Code CLI", "haiku", "sonnet"),
    "codex": ("ChatGPT subscription, through the Codex CLI", "", ""),
    "openai": ("Any OpenAI-compatible API (OpenAI, Ollama, LM Studio, ...)", "", ""),
}
CLI = {"claude": "claude", "codex": "codex"}


def settings(ai_cfg=None):
    ai = ai_cfg or store.config()["ai"]
    _, model, profile_model = PROVIDERS[ai["provider"]]
    model = ai["model"] or model
    return {**ai, "model": model, "profile_model": ai["profile_model"] or profile_model or model}


def parallel():
    """How many calls to run at once, and how many videos to put in one call."""
    provider = settings()["provider"]
    return (2, 100) if provider in CLI else (4, 60)


def ask(system, user, big=False, ai_cfg=None, tries=3):
    """Send one request and return the JSON object from the answer. big=True uses the profile model."""
    s = settings(ai_cfg)
    model = s["profile_model"] if big else s["model"]
    call = {"openrouter": _http, "openai": _http, "claude": _claude, "codex": _codex}[s["provider"]]
    error = None
    for attempt in range(tries):
        try:
            text = call(s, model, system, user)
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as e:
            error = e
            if attempt + 1 < tries:
                time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"AI request failed: {error}")


def _http(s, model, system, user):
    base = (s["base_url"] or OPENROUTER_URL).rstrip("/")
    env = "OPENROUTER_API_KEY" if s["provider"] == "openrouter" else "OPENAI_API_KEY"
    key = s["api_key"] or os.environ.get(env, "")
    headers = {"Content-Type": "application/json", "X-Title": "PersonalTube"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps({
        "model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e


def _claude(s, model, system, user):
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
           "--system-prompt", system]
    if model:
        cmd += ["--model", model]
    with tempfile.TemporaryDirectory() as tmp:  # empty folder: no project instructions get loaded
        out = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=600, cwd=tmp)
    if out.returncode:
        raise RuntimeError(f"claude: {(out.stderr or out.stdout).strip()[-300:]}")
    data = json.loads(out.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude: {data.get('result')}")
    return data["result"]


def _codex(s, model, system, user):
    prompt = f"{system}\n\nAnswer with one JSON object only. Do not run any commands.\n\n{user}"
    with tempfile.TemporaryDirectory() as tmp:
        answer = Path(tmp) / "answer.txt"
        cmd = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "--ephemeral",
               "--color", "never", "-o", str(answer)]
        if model:
            cmd += ["-m", model]
        out = subprocess.run(cmd + ["-"], input=prompt, capture_output=True, text=True,
                             timeout=900, cwd=tmp)
        if out.returncode:
            raise RuntimeError(f"codex: {out.stderr.strip()[-300:]}")
        return answer.read_text()


def installed(provider):
    return provider not in CLI or shutil.which(CLI[provider]) is not None


def check(ai_cfg):
    """Return an error message if this AI config does not answer, else None."""
    if not installed(ai_cfg["provider"]):
        return f"The `{CLI[ai_cfg['provider']]}` command is not installed on this computer."
    try:
        ask("Answer with JSON only.", 'Return {"ok": true}', ai_cfg=ai_cfg, tries=1)
    except RuntimeError as e:
        return str(e)
    return None
