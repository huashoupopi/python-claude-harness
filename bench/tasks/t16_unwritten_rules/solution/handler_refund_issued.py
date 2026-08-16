"""退款已发起。"""


def handle(payload):
    return {"status": "ok", "detail": f"refund_issued:{payload.get('id', '?')}"}
