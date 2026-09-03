"""Inbound payload parsing tests, pure stdlib.

Runs under both ``python -m unittest`` and pytest.
"""

import unittest

from hermes_plivo_sms import schemas


def _form(**fields):
    return {k: [v] for k, v in fields.items()}


class SchemaTests(unittest.TestCase):
    def test_parse_form_body_decodes_urlencoded(self):
        raw = b"From=%2B15551112222&To=%2B15553334444&Text=hello+there&MessageUUID=u-1"
        form = schemas.parse_form_body(raw)
        self.assertEqual(form["From"], ["+15551112222"])
        self.assertEqual(form["Text"], ["hello there"])

    def test_parse_form_body_keeps_blank_values(self):
        form = schemas.parse_form_body(b"From=%2B15551112222&Text=")
        self.assertEqual(form["Text"], [""])

    def test_flatten_form_takes_first_value(self):
        self.assertEqual(schemas.flatten_form({"a": ["1", "2"], "b": []}), {"a": "1"})

    def test_extract_inbound_returns_message(self):
        form = _form(
            From="+15551112222", To="+15553334444", Text=" hi ", MessageUUID="u-9"
        )
        inbound = schemas.extract_inbound(form)
        self.assertIsNotNone(inbound)
        self.assertEqual(inbound.from_number, "+15551112222")
        self.assertEqual(inbound.to_number, "+15553334444")
        self.assertEqual(inbound.text, "hi")
        self.assertEqual(inbound.message_uuid, "u-9")
        self.assertIs(inbound.form, form)

    def test_extract_inbound_none_without_sender_or_text(self):
        self.assertIsNone(schemas.extract_inbound(_form(Text="hi")))
        self.assertIsNone(schemas.extract_inbound(_form(From="+15551112222", Text="")))
        self.assertIsNone(schemas.extract_inbound({}))


if __name__ == "__main__":
    unittest.main()
