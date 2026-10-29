"""Import PyTorch before pandas/numpy-heavy libraries.

On Windows, loading torch's c10.dll after pandas has initialised its own
OpenMP runtime fails with WinError 1114. Entry points import this module first;
if torch is not installed or cannot load, the sequence model falls back to its
Markov backend.
"""
try:  # pragma: no cover - environment dependent
    import torch  # noqa: F401
    TORCH_OK = True
except Exception:  # ImportError or OSError (DLL init)
    TORCH_OK = False
