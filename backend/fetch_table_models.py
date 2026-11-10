"""Download the pinned models into the image at build time (ADR-046/047, ADR-048).

Reads each module's pinned model ids and revisions (`*_MODEL = ("id",
"revision")` constants), parsed rather than imported so the Docker layer needs
no other code, and fetches only the files `from_pretrained` reads. The image
then runs with `HF_HUB_OFFLINE=1`: no conversion depends on Hugging Face
being reachable.

  python fetch_table_models.py pinned_tables.py:2 pinned_layout.py:1

Each argument is a module path and how many pinned models it must hold, so
that a renamed constant fails the build instead of shipping without its model.
"""
from __future__ import annotations

import ast
import sys

from huggingface_hub import hf_hub_download
from huggingface_hub.errors import EntryNotFoundError

FILES = ("config.json", "model.safetensors")
# Read by `AutoImageProcessor` (the layout model); Table Transformer has none.
OPTIONAL = ("preprocessor_config.json",)


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
    for arg in sys.argv[1:]:
        path, _, count = arg.partition(":")
        models = pinned(path)
        if count and len(models) != int(count):
            raise SystemExit(f"expected {count} pinned models in {path}, found {models}")
        for name, revision in models:
            for filename in FILES:
                print(hf_hub_download(name, filename, revision=revision))
            for filename in OPTIONAL:
                try:
                    print(hf_hub_download(name, filename, revision=revision))
                except EntryNotFoundError:
                    pass


if __name__ == "__main__":
    main()
