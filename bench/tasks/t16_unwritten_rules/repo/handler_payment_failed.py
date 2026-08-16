"""支付失败。"""


def handle(payload):
    return {"status": "failed", "detail": f"payment_failed:{payload.get('id', '?')}"}
