"""订单已取消。"""


def handle(payload):
    return {"status": "ok", "detail": f"order_cancelled:{payload.get('id', '?')}"}
