"""Captured command vectors plus explicitly synthetic, anonymous status frames."""
import json
from functools import reduce
from operator import xor
from pathlib import Path
import unittest
from lamp_protocol import encode,decode,state_from_frame


def status_frame(name='LFFL01-P-ABCD',address='aabbccddeeff'):
    p=bytearray(43)
    p[:15]=name.encode().ljust(15,b'\0')
    p[15:21]=bytes.fromhex(address)
    p[21:28]=bytes([1,1,66,1,20,0x12,0xc0])
    raw=bytes.fromhex('5a810300002b')+p
    return raw+bytes([reduce(xor,raw,0)])


class ProtocolTests(unittest.TestCase):
    def test_captured_app_commands_have_no_device_specific_fields(self):
        rows=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/app-command-vectors.json').read_text())
        self.assertEqual({r['action'] for r in rows},{'status','power','upper_on','lower_on','upper','lower','temperature','auto_brightness'})
        for r in rows:self.assertEqual(encode(r['action'],r['sequence'],r['value']).hex(),r['hex'])

    def test_other_device_identity_is_read_from_payload(self):
        frame=decode(status_frame())
        state=state_from_frame(frame,'LFFL01-P-ABCD','aa:bb:cc:dd:ee:ff')
        self.assertEqual(state['upper'],66)
        self.assertEqual(state['address'],'aa:bb:cc:dd:ee:ff')
        state=state_from_frame(decode(status_frame('LFFL01-P-1234','010203040506')))
        self.assertEqual(state['name'],'LFFL01-P-1234')

    def test_wrong_target_and_unsupported_model_rejected(self):
        frame=decode(status_frame())
        with self.assertRaises(ValueError):state_from_frame(frame,'LFFL01-P-1234')
        with self.assertRaises(ValueError):state_from_frame(frame,expected_address='00:00:00:00:00:00')
        with self.assertRaises(ValueError):state_from_frame(decode(status_frame('OTHER-LAMP')))

    def test_corruption_bounds_and_arbitrary_commands_rejected(self):
        raw=bytearray(status_frame());raw[-1]^=1
        with self.assertRaises(ValueError):decode(raw)
        with self.assertRaises(ValueError):decode(status_frame()[:-1])
        for args in [('upper',0,0),('lower',0,101),('dfu',0,b'123'),('power',0,1)]:
            with self.assertRaises(ValueError):encode(*args)
