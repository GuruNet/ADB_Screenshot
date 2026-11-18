"""Validate the wireless addresses users copy from their Android device."""

import unittest

from app import parse_endpoint


class EndpointTests(unittest.TestCase):
    def test_separate_ipv4_address_and_port(self):
        self.assertEqual(parse_endpoint(" 192.0.2.10 ", " 40001 ", "연결"),
                         ("192.0.2.10", "40001"))

    def test_pasted_address_populates_port_for_selected_action(self):
        self.assertEqual(parse_endpoint("192.0.2.10:40002", "", "페어링"),
                         ("192.0.2.10", "40002"))
        self.assertEqual(parse_endpoint("192.0.2.10:40001", "5555", "연결"),
                         ("192.0.2.10", "40001"))

    def test_ipv6_address_and_bracketed_endpoint(self):
        self.assertEqual(parse_endpoint("2001:db8::1", "5555", "연결"),
                         ("2001:db8::1", "5555"))
        self.assertEqual(parse_endpoint("[2001:db8::1]:40001", "", "연결"),
                         ("2001:db8::1", "40001"))

    def test_specific_invalid_input_messages(self):
        for host, port, label, message in [
            ("", "12345", "페어링", "IP 주소"),
            ("192.0.2.10", "", "페어링", "페어링 포트"),
            ("192.0.2.10", "70000", "연결", "연결 포트"),
            ("not-an-ip", "5555", "연결", "IP 주소"),
        ]:
            with self.subTest(host=host, port=port):
                with self.assertRaisesRegex(ValueError, message):
                    parse_endpoint(host, port, label)


if __name__ == "__main__":
    unittest.main()
