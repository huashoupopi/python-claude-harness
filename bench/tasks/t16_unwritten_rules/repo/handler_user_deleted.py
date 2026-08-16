"""用户注销。"""


def handle(payload):
    return {"status": "ok", "detail": f"user_deleted:{payload.get('id', '?')}"}
