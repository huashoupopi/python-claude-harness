"""Widget production quota. The hardcoded limit is stale."""

WIDGET_LIMIT = 100


def allowed(count: int) -> bool:
    return count <= WIDGET_LIMIT
