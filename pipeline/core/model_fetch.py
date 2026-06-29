"""
model_fetch.py — optional helper to provision local GGUF model files from
Hugging Face for your inference server (llama.cpp / llama-swap).

The pipeline and backend never load GGUFs themselves — they call the inference
server over HTTP. This helper is a convenience for populating that server's model
folder: it checks whether the GGUF files you need are present and, with the
user's permission, downloads the missing ones from Hugging Face.

It is driven by a manifest (models.json, committed and pre-filled) because the
model names in config.py are your own llama-swap aliases, not Hugging Face repo
IDs. Edit models.json to set model_dir and each repo_id:

    {
      "model_dir": "/path/to/llama/models",  # the folder your server loads GGUFs from
      "hf_token": "",                         # optional — for gated/private repos
      "models": [
        {"name": "Embedding_Qwen3.6", "repo_id": "owner/repo", "filename": "x.gguf"}
      ]
    }

Detection is purely "does model_dir/<filename> exist". Downloads go to the same
model_dir via huggingface_hub (resumable; honours hf_token / the HF_TOKEN env).
"""

import os
import json


class ManifestError(Exception):
    """Raised for a missing/invalid manifest or a missing huggingface_hub."""


def _base_dir() -> str:
    # Parent of core/ — i.e. the pipeline/ folder, next to gui.py / tui.py.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


MANIFEST_PATH = os.path.join(_base_dir(), "models.json")


def load_manifest(path: str = MANIFEST_PATH) -> dict:
    """Load and validate the manifest. Raises ManifestError on any problem."""
    if not os.path.exists(path):
        raise ManifestError(
            "No models.json found next to the pipeline. It is normally committed; "
            "restore it and set model_dir + each repo_id (see the README)."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        raise ManifestError(f"Could not read models.json: {exc}") from exc

    model_dir = (data.get("model_dir") or "").strip()
    models = data.get("models") or []
    if not model_dir:
        raise ManifestError("models.json: 'model_dir' is empty.")
    if not models:
        raise ManifestError("models.json: 'models' list is empty.")
    for m in models:
        if not m.get("repo_id") or not m.get("filename"):
            raise ManifestError(
                f"models.json: entry missing repo_id/filename: {m!r}"
            )
        m.setdefault("name", m["filename"])

    token = (data.get("hf_token") or "").strip() or None
    return {"model_dir": model_dir, "hf_token": token, "models": models}


def check_models(manifest: dict) -> tuple[list[dict], list[dict]]:
    """Return (present, missing) model entries by testing model_dir/<filename>."""
    model_dir = manifest["model_dir"]
    present, missing = [], []
    for m in manifest["models"]:
        target = os.path.join(model_dir, m["filename"])
        (present if os.path.exists(target) else missing).append(m)
    return present, missing


def ensure_hf_available() -> None:
    """Raise ManifestError with a friendly hint if huggingface_hub is absent."""
    try:
        import huggingface_hub  # noqa: F401
    except ImportError as exc:
        raise ManifestError(
            "huggingface_hub is not installed. Run: pip install -r requirements.txt"
        ) from exc


def download_model(entry: dict, model_dir: str, token: str | None = None) -> str:
    """Download one GGUF into model_dir/<filename>; return the local path."""
    from huggingface_hub import hf_hub_download

    os.makedirs(model_dir, exist_ok=True)
    return hf_hub_download(
        repo_id=entry["repo_id"],
        filename=entry["filename"],
        local_dir=model_dir,
        token=token,
    )
