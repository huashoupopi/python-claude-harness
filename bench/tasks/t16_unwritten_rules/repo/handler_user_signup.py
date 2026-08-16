"""用户注册。"""


def handle(payload):
    return {"status": "ok", "detail": f"user_signup:{payload.get('id', '?')}"}
