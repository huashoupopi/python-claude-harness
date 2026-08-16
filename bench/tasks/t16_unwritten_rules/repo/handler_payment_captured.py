"""支付已扣款。"""


def handle(payload):
    return {"status": "ok", "detail": f"payment_captured:{payload.get('id', '?')}"}
