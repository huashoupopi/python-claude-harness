"""拒付已开启。"""


def handle(payload):
    return {"status": "failed", "detail": f"chargeback_opened:{payload.get('id', '?')}"}
