"""trailmem — local-first, graph-linked persistent memory for AI coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("trailmem")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"

