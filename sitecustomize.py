from __future__ import annotations

from pathlib import Path


def _patch_turbojpeg() -> None:
    try:
        import turbojpeg
    except Exception:
        return

    if not hasattr(turbojpeg, "TurboJPEG"):
        return

    candidates = [
        Path("/usr/lib/x86_64-linux-gnu/libturbojpeg.so.0"),
        Path("/usr/lib/x86_64-linux-gnu/libturbojpeg.so"),
        Path("/usr/lib/libturbojpeg.so"),
        Path("/usr/local/lib/libturbojpeg.so"),
    ]
    lib_path = next((str(path) for path in candidates if path.exists()), None)
    if lib_path is None:
        return

    original = turbojpeg.TurboJPEG

    def _wrapped(*args, **kwargs):
        if not args and "lib_path" not in kwargs:
            kwargs["lib_path"] = lib_path
        return original(*args, **kwargs)

    turbojpeg.TurboJPEG = _wrapped


_patch_turbojpeg()
