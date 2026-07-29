from .tools import mcp


def main() -> None:
    mcp.run(transport="stdio")
