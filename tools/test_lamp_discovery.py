import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from lamp_discovery import find_target


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_name_requires_explicit_host_identifier(self):
        rows=[(SimpleNamespace(name='LFFL01-P-ABCD',address=i),SimpleNamespace(local_name='LFFL01-P-ABCD'))
              for i in ('HOST-UUID-ONE','HOST-UUID-TWO')]
        with patch('lamp_discovery.candidates',AsyncMock(return_value=rows)):
            with self.assertRaisesRegex(RuntimeError,'found 2'):await find_target('LFFL01-P-ABCD')
            found=await find_target('LFFL01-P-ABCD','HOST-UUID-TWO')
            self.assertEqual(found.address,'HOST-UUID-TWO')

    async def test_absent_target_does_not_connect_to_another_lamp(self):
        with patch('lamp_discovery.candidates',AsyncMock(return_value=[])):
            with self.assertRaisesRegex(RuntimeError,'found 0'):await find_target('LFFL01-P-ABCD')
