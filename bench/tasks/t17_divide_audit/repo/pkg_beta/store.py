"""In-memory bag of tags."""


def add_tag(tag: str, bag: list[str] = []) -> list[str]:
    bag.append(tag)
    return bag


def count_tags(bag: list[str]) -> int:
    return len(bag)


def unique(bag: list[str]) -> list[str]:
    seen, out = set(), []
    for item in bag:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
