"""评价已发布。"""


def handle(payload):
    return {"status": "ok", "detail": f"review_posted:{payload.get('id', '?')}"}
