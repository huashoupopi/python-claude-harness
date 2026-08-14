import formatting
import units


def test_to_celsius():
    assert units.to_celsius(212) == 100


def test_to_fahrenheit_added():
    assert units.to_fahrenheit(100) == 212


def test_format_temp_rounds_to_one_decimal():
    assert formatting.format_temp(21.456) == "21.5C"


def test_format_temp_handles_negative():
    assert formatting.format_temp(-3.14) == "-3.1C"


def test_legacy_delegates_to_units():
    import legacy

    assert legacy.old_convert(212) == units.to_celsius(212)
    assert legacy.old_convert(-40) == units.to_celsius(-40)
