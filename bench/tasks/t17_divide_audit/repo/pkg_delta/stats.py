"""Running mean."""


def mean(xs: list[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def almost_one(xs: list[float]) -> bool:
    return mean(xs) == 1.0


def nitems(xs: list[float]) -> int:
    return len(xs)
