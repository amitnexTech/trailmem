"""Console output safety for legacy Windows (cp1252) terminals.

Two failure layers: encoding (cp1252 cannot encode ✓/✗/emoji →
UnicodeEncodeError crash) and rendering (old console fonts show boxes).
configure() removes the crash layer; sym() picks emoji vs readable ASCII so
old consoles never get mojibake. Defined once, imported everywhere — per-file
copies drift.
"""

import sys

_unicode_ok: bool | None = None


def configure() -> None:
    """Call once at every entry point (CLI, MCP server) before any output."""
    global _unicode_ok
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    _unicode_ok = "utf" in enc
    if sys.platform == "win32" and not _unicode_ok:
        try:  # switch the console to UTF-8 so unicode genuinely renders
            import ctypes

            if ctypes.windll.kernel32.SetConsoleOutputCP(65001):
                _unicode_ok = True
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:  # crash guard: every character always encodes (worst case '?')
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def supports_unicode() -> bool:
    if _unicode_ok is None:  # library use without configure() — probe live
        return "utf" in ((getattr(sys.stdout, "encoding", "") or "").lower())
    return _unicode_ok


def sym(unicode_s: str, ascii_s: str) -> str:
    """The unicode form when the console can take it, else the ASCII form."""
    return unicode_s if supports_unicode() else ascii_s
