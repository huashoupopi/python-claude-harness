"""购物车遗弃。"""


def handle(payload):
    return {"status": "ok", "detail": f"cart_abandoned:{payload.get('id', '?')}"}
