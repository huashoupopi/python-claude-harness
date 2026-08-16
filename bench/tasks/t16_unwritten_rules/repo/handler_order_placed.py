"""订单已下单。"""


def handle(payload):
    return {"status": "ok", "detail": f"order_placed:{payload.get('id', '?')}"}
