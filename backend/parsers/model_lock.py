"""One lock for loading any of the backend's PyTorch models (ADR-056).

transformers 5.x builds a model's weights on PyTorch's ``meta`` device and then
fills them in. Two models loading at the same moment in two threads (the table
and layout passes, or two conversions cold-starting together) can leave one with
``meta`` tensors: run 2026-11-11 on 2 CPUs broke the layout model that way, and
every later page lost its layout pass ("Tensor on device meta is not on the
expected device cpu"). Each model's loader takes this lock, so loads never
overlap; the OCR engine also loads the enabled models before it starts any
inference beside OCR, so no inference runs while a load is in progress.
"""
from __future__ import annotations

import threading

LOAD_LOCK = threading.Lock()
