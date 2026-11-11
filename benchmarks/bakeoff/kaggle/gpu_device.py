"""Run the backend's two model passes on a GPU, for the end-to-end benchmark only.

`parsers/tables.py` (Table Transformer, ADR-046) and `parsers/layout.py`
(PP-DocLayoutV3, ADR-048) load their models on the CPU, and production has no
setting to move them. Rather than add one before the benchmark says it is worth
having, this wraps each module's `_load()` so the models it returns sit on the
GPU. Each call's tensors go to the GPU and its outputs come back to the CPU, so
the code after inference (post-processing, geometry, every rule) runs exactly
as in production. Weights stay fp32: only the device changes.

    import gpu_device; gpu_device.install("cuda")   # before the first conversion
"""
from __future__ import annotations


def _to(value, device):
    import torch

    if torch.is_tensor(value):
        return value.to(device)
    if isinstance(value, tuple):
        return tuple(_to(v, device) for v in value)
    if isinstance(value, list):
        return [_to(v, device) for v in value]
    return value


class OnDevice:
    """A model moved to `device` that takes and returns CPU tensors."""

    def __init__(self, model, device: str):
        self._model = model.to(device)
        self._device = device

    def __getattr__(self, name):          # .config, .eval(), ... come from the model
        return getattr(self._model, name)

    def __call__(self, *args, **kwargs):
        out = self._model(*_to(args, self._device), **{k: _to(v, self._device) for k, v in kwargs.items()})
        for key in list(out.keys()):      # a transformers ModelOutput: dict-like, attributes in sync
            out[key] = _to(out[key], "cpu")
        return out


def install(device: str = "cuda") -> None:
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("gpu_device.install: no CUDA device visible")
    from parsers import layout, tables

    tables_load, layout_load = tables._load, layout._load

    def tables_on_device():
        models = tables_load()
        for key in ("detect", "structure"):
            if not isinstance(models[key], OnDevice):
                models[key] = OnDevice(models[key], device)
        return models

    def layout_on_device():
        model = layout_load()
        if not isinstance(model["model"], OnDevice):
            model["model"] = OnDevice(model["model"], device)
        return model

    tables._load = tables_on_device
    layout._load = layout_on_device
