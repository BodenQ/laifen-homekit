"""Regression for ACK arriving before a physical state update."""
import io
import time
import unittest
from unittest.mock import AsyncMock
from lamp_protocol import Frame
from lamp_session import Session

class SettlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_probe_without_pinned_address_cannot_control(self):
        s=Session(io.StringIO(),device_name='LFFL01-P-ABCD')
        s.emit=lambda *a,**k:None
        s.write=AsyncMock(return_value=(time.monotonic(),1))
        s.wait_for=AsyncMock(return_value=(None,{'power':False}))
        await s.query()
        self.assertFalse(s.verified)
        with self.assertRaisesRegex(RuntimeError,'verification required'):
            await s.control('power',True)

    def session(self):
        s=Session(io.StringIO())
        s.emit=lambda *a,**k:None
        s.verified=True
        s.last_state={'power':True,'upper':66,'lower':20}
        s.write=AsyncMock(return_value=(time.monotonic(),1))
        s.wait_for=AsyncMock(return_value=(Frame(0xc1,8,1,b'\0'),None))
        return s

    async def test_stale_status_after_ack_polls_without_repeating_control(self):
        s=self.session()
        s.query=AsyncMock(side_effect=[dict(s.last_state),{'power':False,'upper':66,'lower':20}])
        await s.control('power',False)
        s.write.assert_awaited_once_with('power',False)
        self.assertEqual(s.query.await_count,2)

    async def test_persistent_mismatch_fails_without_repeating_control(self):
        s=self.session()
        s.query=AsyncMock(return_value=dict(s.last_state))
        with self.assertRaisesRegex(RuntimeError,'state did not settle'):
            await s.control('power',False,settle_timeout=0.02)
        s.write.assert_awaited_once_with('power',False)

    async def test_sensor_brightness_change_does_not_fail_power_verification(self):
        s=self.session()
        s.last_state['auto_brightness']=True
        s.query=AsyncMock(return_value={'power':False,'upper':70,'lower':25,'auto_brightness':False})
        await s.control('power',False)
        s.write.assert_awaited_once_with('power',False)

if __name__=='__main__':unittest.main()
