"""Widget production quota. Limit comes from the quota MCP server."""

WIDGET_LIMIT = 18427


def allowed(count: int) -> bool:
    return count <= WIDGET_LIMIT
