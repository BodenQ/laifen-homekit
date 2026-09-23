import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import AsyncMock
from lamp_worker import Worker
from pathlib import Path

class NativeLamp:
    def __init__(self):
        self.last_state=dict(power=True,upper=100,lower=20,upper_on=True,lower_on=True,
            temperature=2907,auto_brightness=True,seat_sensing=False,daylight=False)
        self.query=AsyncMock(side_effect=lambda:dict(self.last_state))
        self.control=AsyncMock(side_effect=self.change)

    async def change(self,action,value):
        s=self.last_state
        if action=='power' and value and s['power']:
            return  # Real firmware preserves the current head mask.
        s[action]=value
        if action=='power':s.update(upper_on=value,lower_on=value)
        if action.endswith('_on'):s['power']=s['upper_on'] or s['lower_on']
        if not s['power'] or action in ('upper','lower'):s['auto_brightness']=False


class AutomaticBrightnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pref=Path(self.directory.name)/'preference.json'
        self.worker=Worker(str(Path(self.directory.name)/'ble.jsonl'),self.pref)
        self.lamp=NativeLamp()
        self.worker.session=self.lamp
        self.worker.online=True
        await self.worker.reconcile(self.lamp)

    async def request(self,action,value):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            await self.worker.handle(dict(id=1,action=action,value=value))
        result=json.loads(out.getvalue().splitlines()[-1])
        self.assertTrue(result['ok'],result)
        return result['state']

    async def test_off_retains_preference_and_auto_resumes_only_when_both_heads_are_on(self):
        await self.request('power',False)
        self.assertTrue(self.worker.auto_preference)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        self.lamp.control.reset_mock()
        await self.request('lower',1)  # Home slider-bottom companion value.
        self.lamp.control.assert_not_awaited()
        await self.request('lower_on',True)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        self.assertFalse(self.lamp.last_state['upper_on'])
        await self.request('upper_on',True)
        self.assertTrue(self.lamp.last_state['auto_brightness'])
        self.assertEqual(self.lamp.control.await_args_list[-1].args,('auto_brightness',True))

    async def test_same_value_manual_slider_disables_auto_and_does_not_return_on_wake(self):
        await self.request('lower',20)
        self.assertFalse(self.worker.auto_preference)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        await self.request('power',False)
        await self.request('lower_on',True)
        self.assertFalse(self.lamp.last_state['auto_brightness'])

    async def test_explicit_switch_off_keeps_brightness_and_enable_while_off_does_not_wake(self):
        await self.request('auto_preference',False)
        self.assertEqual((self.lamp.last_state['upper'],self.lamp.last_state['lower']),(100,20))
        await self.request('power',False)
        self.lamp.control.reset_mock()
        await self.request('auto_preference',True)
        self.lamp.control.assert_not_awaited()
        await self.request('lower_on',True)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        await self.request('power',True)
        self.assertTrue(self.lamp.last_state['upper_on'])
        self.assertTrue(self.lamp.last_state['auto_brightness'])

    async def test_preference_survives_worker_restart_while_off(self):
        await self.request('power',False)
        restarted=Worker(str(Path(self.directory.name)/'restart.log'),self.pref)
        restarted.session=self.lamp;restarted.online=True
        self.worker=restarted
        await restarted.reconcile(self.lamp)
        self.assertTrue(restarted.auto_preference)
        await self.request('upper_on',True)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        await self.request('lower_on',True)
        self.assertTrue(self.lamp.last_state['auto_brightness'])

    async def test_channel_off_stops_sensor_first_and_survives_poll_and_adaptive_ticks(self):
        self.lamp.control.reset_mock()
        await self.request('lower_on',False)
        self.assertEqual([c.args for c in self.lamp.control.await_args_list],
                         [('auto_brightness',False),('lower_on',False)])
        self.assertTrue(self.worker.auto_preference)
        await self.request('auto_preference',True)
        await self.request('temperature',4000)
        await self.worker.reconcile(self.lamp)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        self.assertFalse(self.lamp.last_state['lower_on'])
        self.assertTrue(self.lamp.last_state['upper_on'])
        self.assertTrue(self.worker.auto_preference)

    async def test_master_on_from_partial_lighting_always_enables_both(self):
        await self.request('upper_on',False)
        self.lamp.control.reset_mock()
        await self.request('power',True)
        self.assertEqual([c.args for c in self.lamp.control.await_args_list],
                         [('upper_on',True),('auto_brightness',True)])
        self.assertTrue(self.lamp.last_state['upper_on'])
        self.assertTrue(self.lamp.last_state['lower_on'])

    async def test_external_channel_off_pauses_sensor_and_external_on_resumes(self):
        await self.lamp.change('lower_on',False)
        await self.worker.reconcile(self.lamp)
        self.assertFalse(self.lamp.last_state['auto_brightness'])
        self.assertTrue(self.worker.auto_preference)
        await self.lamp.change('lower_on',True)
        await self.worker.reconcile(self.lamp)
        self.assertTrue(self.lamp.last_state['auto_brightness'])

    async def test_explicit_off_is_sent_even_if_firmware_already_reports_off(self):
        await self.request('lower_on',False)
        self.lamp.control.reset_mock()
        await self.request('lower_on',False)
        self.lamp.control.assert_awaited_once_with('lower_on',False)

    async def test_temperature_and_native_automatic_brightness_updates_preserve_preference(self):
        await self.request('temperature',4000)
        self.lamp.last_state['lower']=50  # Autonomous sensor adjustment.
        await self.worker.reconcile(self.lamp)
        self.assertTrue(self.worker.auto_preference)
        self.lamp.last_state.update(lower=60,auto_brightness=False)
        await self.worker.reconcile(self.lamp)
        self.assertFalse(self.worker.auto_preference)

    async def test_external_power_cycle_restores_but_native_conflict_does_not(self):
        await self.lamp.change('power',False)
        await self.worker.reconcile(self.lamp)
        await self.lamp.change('power',True)
        await self.worker.reconcile(self.lamp)
        self.assertTrue(self.lamp.last_state['auto_brightness'])
        self.lamp.last_state.update(seat_sensing=True,auto_brightness=False)
        await self.worker.reconcile(self.lamp)
        self.assertFalse(self.worker.auto_preference)

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_slider_zero_switches_only_its_channel(self):
        with tempfile.NamedTemporaryFile() as f:
            w=Worker(f.name)
            class Fake:
                last_state={'power':True,'upper':66,'lower':20,'upper_on':True,'lower_on':True}
                query=AsyncMock()
                control=AsyncMock()
            w.session=Fake();w.online=True
            with contextlib.redirect_stdout(io.StringIO()) as out:
                await w.handle({'id':1,'action':'lower','value':0})
            w.session.control.assert_awaited_once_with('lower_on',False)
            self.assertTrue(json.loads(out.getvalue())['ok'])

    async def test_offline_request_never_controls_or_replays(self):
        with tempfile.NamedTemporaryFile() as f:
            w=Worker(f.name)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                await w.handle({'id':2,'action':'power','value':True})
            self.assertFalse(json.loads(out.getvalue())['ok'])

    async def test_positive_brightness_after_off_does_not_wake_either_channel(self):
        with tempfile.NamedTemporaryFile() as f:
            w=Worker(f.name)
            class Fake:
                last_state={'power':False,'upper':66,'lower':20,'upper_on':False,'lower_on':False}
                query=AsyncMock()
                control=AsyncMock()
            w.session=Fake();w.online=True
            with contextlib.redirect_stdout(io.StringIO()):
                await w.handle({'id':3,'action':'upper','value':80})
            w.session.control.assert_not_awaited()
            self.assertEqual(w.deferred_brightness,{'upper':80})

    async def test_deferred_values_apply_only_after_explicit_channel_on(self):
        with tempfile.NamedTemporaryFile() as f:
            w=Worker(f.name)
            class Fake:
                last_state={'power':False,'upper':66,'lower':20,'upper_on':False,'lower_on':False,'temperature':4800}
                query=AsyncMock()
                async def change(self,a,v):
                    if a.endswith('_on') and v and not self.last_state['power']:
                        raise RuntimeError('firmware ignores channel-on while master off')
                    self.last_state[a]=v
                    if a=='power':self.last_state.update(upper_on=v,lower_on=v)
                    if a.endswith('_on'):self.last_state['power']=self.last_state['upper_on'] or self.last_state['lower_on']
            s=Fake();s.control=AsyncMock(side_effect=s.change);w.session=s;w.online=True
            with contextlib.redirect_stdout(io.StringIO()):
                await w.handle({'id':1,'action':'upper','value':80})
                await w.handle({'id':2,'action':'temperature','value':4007})
                s.control.assert_not_awaited()
                await w.handle({'id':3,'action':'upper_on','value':True})
            self.assertEqual([c.args for c in s.control.await_args_list],[('power',True),('lower_on',False),('upper',80),('temperature',4007)])
            self.assertFalse(s.last_state['lower_on'])

    async def test_lower_from_all_off_uses_master_then_restores_upper_off(self):
        with tempfile.NamedTemporaryFile() as f:
            w=Worker(f.name)
            class Fake:
                last_state={'power':False,'upper':100,'lower':30,'upper_on':False,'lower_on':False}
                query=AsyncMock()
                async def change(self,a,v):
                    if a.endswith('_on') and v and not self.last_state['power']:
                        raise RuntimeError('firmware ignores channel-on while master off')
                    self.last_state[a]=v
                    if a=='power':self.last_state.update(upper_on=v,lower_on=v)
                    if a.endswith('_on'):self.last_state['power']=self.last_state['upper_on'] or self.last_state['lower_on']
            s=Fake();s.control=AsyncMock(side_effect=s.change);w.session=s;w.online=True
            with contextlib.redirect_stdout(io.StringIO()) as out:
                await w.handle({'id':1,'action':'lower_on','value':True})
            self.assertTrue(json.loads(out.getvalue())['ok'])
            self.assertEqual([c.args for c in s.control.await_args_list],[('power',True),('upper_on',False)])
            self.assertFalse(s.last_state['upper_on'])
            self.assertTrue(s.last_state['lower_on'])

    async def test_adaptive_off_updates_cache_without_waking_and_cancel_preserves_manual(self):
        with tempfile.NamedTemporaryFile() as f:
            w=Worker(f.name)
            class Fake:
                last_state={'power':False,'upper':100,'lower':30,'upper_on':False,'lower_on':False,'temperature':4831}
                query=AsyncMock()
                control=AsyncMock()
            s=Fake();w.session=s;w.online=True
            with contextlib.redirect_stdout(io.StringIO()):
                await w.handle({'id':1,'action':'temperature','value':3200,'adaptive':True})
                self.assertEqual(w.deferred_temperature,3200)
                await w.handle({'id':2,'action':'clear_adaptive_temperature'})
                self.assertIsNone(w.deferred_temperature)
                await w.handle({'id':3,'action':'temperature','value':4007})
                await w.handle({'id':4,'action':'clear_adaptive_temperature'})
            self.assertEqual(w.deferred_temperature,4007)
            s.control.assert_not_awaited()
