from . import quickref
from .tools import mcp


def main() -> None:
    # Before serving, so the first condition lookup does not wait on it.
    quickref.warm()
    mcp.run(transport="stdio")
