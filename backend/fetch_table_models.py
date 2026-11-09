"""Download the Table Transformer models into the image at build time (ADR-046).

Reads the pinned model ids and revisions from `parsers/tables.py` (parsed, not
imported, so the Docker layer needs no other code) and fetches only the two
files `from_pretrained` reads. The image then runs with `HF_HUB_OFFLINE=1`: no
conversion depends on Hugging Face being reachable.

  python fetch_table_models.py parsers/tables.py
"""
from __future__ import annotations

import ast
import sys

from huggingface_hub import hf_hub_download

FILES = ("config.json", "model.safetensors")


def pinned(path: str) -> list[tuple[str, str]]:
    """(model id, revision) for each `*_MODEL = ("id", "revision")` constant."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id.endswith("_MODEL")):
            name, revision = ast.literal_eval(node.value)
            out.append((name, revision))
    return out


def main() -> None:
    models = pinned(sys.argv[1])
    if len(models) != 2:
        raise SystemExit(f"expected two pinned models in {sys.argv[1]}, found {models}")
    for name, revision in models:
        for filename in FILES:
            print(hf_hub_download(name, filename, revision=revision))


if __name__ == "__main__":
    main()
