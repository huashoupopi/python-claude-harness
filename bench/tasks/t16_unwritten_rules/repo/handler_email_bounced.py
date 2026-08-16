"""邮件退信。"""


def handle(payload):
    return {"status": "failed", "detail": f"email_bounced:{payload.get('id', '?')}"}
