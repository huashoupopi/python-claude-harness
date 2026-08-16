"""订单已发货。"""


def handle(payload):
    return {"status": "ok", "detail": f"order_shipped:{payload.get('id', '?')}"}
