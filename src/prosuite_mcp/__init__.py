from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prosuite-mcp")
except PackageNotFoundError:
    __version__ = "unknown"
