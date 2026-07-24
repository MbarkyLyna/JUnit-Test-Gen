from __future__ import annotations

import os

import psutil

MIN_FREE_RAM_GB = float(os.environ.get("MIN_FREE_RAM_GB", "2.5"))


def check_host_memory() -> tuple[bool, str, float]:
    """Return (ok, message, available_gb)."""
    avail_gb = psutil.virtual_memory().available / (1024**3)
    if avail_gb < MIN_FREE_RAM_GB:
        return (
            False,
            f"Insufficient RAM: {avail_gb:.1f} GB free (need ≥ {MIN_FREE_RAM_GB} GB). "
            "Close other applications before multi-class or project generation.",
            avail_gb,
        )
    return True, f"RAM OK: {avail_gb:.1f} GB available", avail_gb
