import pathlib

import codegen
import service

HERE = pathlib.Path(__file__).parent


def _form(**overrides):
    form = {
        "username": "amelia",
        "email": "amelia@example.com",
        "age": 34,
        "zipcode": "941070",
    }
    form.update(overrides)
    return form


def test_generated_file_matches_the_schema():
    expected = codegen.render((HERE / "schema.def").read_text(encoding="utf-8"))
    actual = (HERE / "validators.py").read_text(encoding="utf-8")
    assert actual == expected, "validators.py 与 schema.def 对不上——生成物没有重新生成"


def test_a_complete_form_is_accepted():
    assert service.validate_signup(_form()) == []


def test_username_must_be_at_least_three_characters():
    assert "username" in service.validate_signup(_form(username="am"))
    assert "username" not in service.validate_signup(_form(username="ame"))


def test_email_must_contain_an_at_sign():
    assert "email" in service.validate_signup(_form(email="amelia.example.com"))
    assert "email" not in service.validate_signup(_form())


def test_age_has_an_upper_bound():
    assert "age" in service.validate_signup(_form(age=131))
    assert "age" not in service.validate_signup(_form(age=130))


def test_zipcode_must_be_six_digits():
    assert "zipcode" in service.validate_signup(_form(zipcode="94107"))
    assert "zipcode" not in service.validate_signup(_form(zipcode="941070"))
