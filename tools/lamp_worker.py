"""Single BLE owner. Private JSON-lines stdin/stdout IPC for Homebridge."""
import argparse
import asyncio
import datetime
import json
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
import signal
import sys
from bleak import BleakClient, BleakScanner
from lamp_protocol import SERVICE, CHARACTERISTIC, encode
from lamp_discovery import find_target
from lamp_session import Session


def output(**event):
    print(json.dumps(event,ensure_ascii=False),flush=True)


class BridgeSession(Session):
    def __init__(self,worker):
        super().__init__(None,worker.device_name,worker.protocol_address)
        self.worker=worker

    def emit(self,event,**fields):
        self.worker.logger.info(json.dumps({
            'at':datetime.datetime.now().astimezone().isoformat(),
            'event':event,**fields},ensure_ascii=False))
        if event=='rx' and fields.get('state') and self.worker.online:
            output(event='state',state=self.worker.view_state(fields['state']))
        if event=='invalid_rx':
            self.worker.lost.set()


class Worker:
    def __init__(self,logpath,preference_path=None,device_name=None,protocol_address=None,identifier=None):
        self.device_name=device_name
        self.protocol_address=protocol_address
        self.identifier=identifier
        self.logger=logging.getLogger('lamp-worker')
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(RotatingFileHandler(logpath,maxBytes=5_000_000,backupCount=3))
        self.lock=asyncio.Lock()
        self.lost=asyncio.Event()
        self.stop=asyncio.Event()
        self.session=None
        self.online=False
        self.deferred_brightness={}
        self.deferred_temperature=None
        self.deferred_temperature_adaptive=False
        self.preference_path=Path(preference_path) if preference_path else None
        self.auto_preference=None
        self.observed=None
        if self.preference_path and self.preference_path.exists():
            saved=json.loads(self.preference_path.read_text())
            if type(saved.get('auto_brightness')) is not bool:
                raise ValueError('invalid saved auto-brightness preference')
            self.auto_preference=saved['auto_brightness']
            self.observed=saved.get('observed')

    def save_preference(self):
        if self.preference_path and self.auto_preference is not None:
            self.preference_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.preference_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'auto_brightness':self.auto_preference,
                'observed':self.observed},ensure_ascii=False))
            temporary.replace(self.preference_path)

    def prefer_auto(self,value,reason):
        if self.auto_preference!=value:
            self.auto_preference=value
            self.logger.info(json.dumps({'event':'auto_brightness_preference',
                'value':value,'reason':reason},ensure_ascii=False))
            self.save_preference()

    def remember(self,state):
        observed={k:state.get(k) for k in ('power','upper_on','lower_on','upper','lower','auto_brightness',
                                         'seat_sensing','daylight')}
        if observed!=self.observed:
            self.observed=observed
            self.save_preference()

    @staticmethod
    def both_on(state):
        return bool(state and state.get('upper_on') and state.get('lower_on'))

    async def restore_auto_on_wake(self,s):
        if self.auto_preference and self.both_on(s.last_state) and not s.last_state.get('auto_brightness'):
            if s.last_state.get('seat_sensing') or s.last_state.get('daylight'):
                self.prefer_auto(False,'lamp has a conflicting native mode')
                return
            await s.control('auto_brightness',True)

    async def reconcile(self,s):
        """Process external state at a query boundary, never during own writes."""
        state=s.last_state
        previous=self.observed
        actual=state.get('auto_brightness',False)
        if self.auto_preference is None:
            self.prefer_auto(actual,'adopt initial lamp setting')
        elif actual:
            self.prefer_auto(True,'native mode enabled outside bridge')
        elif state.get('seat_sensing') or state.get('daylight'):
            self.prefer_auto(False,'conflicting native mode selected')
        elif self.auto_preference and state['power']:
            changed_brightness=previous and any(state[k]!=previous.get(k) for k in ('upper','lower'))
            if changed_brightness or (self.both_on(state) and self.both_on(previous)
                                      and previous.get('auto_brightness')):
                self.prefer_auto(False,'external manual brightness or native mode override')
            elif previous and not self.both_on(previous):
                await self.restore_auto_on_wake(s)
        # The native sensor controls the whole fixture. Its "off" channel
        # flag can remain false even when the user sees that head fade back on.
        # Keep the user's preference, but run the mode only with both heads on.
        if not self.both_on(s.last_state) and s.last_state.get('auto_brightness'):
            await s.control('auto_brightness',False)
        self.remember(s.last_state)

    def availability(self,online,error=None):
        self.online=online
        output(event='availability',online=online,error=error)

    def view_state(self,state):
        result=dict(state)
        result['auto_brightness_preference']=bool(self.auto_preference)
        if self.deferred_temperature is not None:
            result['target_temperature']=self.deferred_temperature
        return result

    async def maintain(self):
        delay=2
        while not self.stop.is_set():
            self.lost.clear()
            try:
                device=await find_target(self.device_name,self.identifier,timeout=15)
                if not device:
                    raise RuntimeError('lamp not found; check power, range, and App connection')
                async with BleakClient(device,timeout=20,
                        disconnected_callback=lambda c:self.lost.set()) as client:
                    s=BridgeSession(self)
                    s.client=client
                    matches=[c for sv in client.services if sv.uuid.lower()==SERVICE
                             for c in sv.characteristics if c.uuid.lower()==CHARACTERISTIC]
                    if len(matches)!=1 or not {'write','notify'}.issubset(matches[0].properties):
                        raise RuntimeError('expected FF01/FF02 Write/Notify characteristic')
                    s.characteristic=matches[0]
                    await client.start_notify(s.characteristic,s.notification)
                    async with self.lock:
                        state=await s.query()
                        await self.reconcile(s)
                        self.session=s
                        self.availability(True)
                        output(event='state',state=self.view_state(s.last_state))
                    delay=2
                    while not self.lost.is_set() and not self.stop.is_set():
                        try:
                            await asyncio.wait_for(self.lost.wait(),timeout=15)
                        except TimeoutError:
                            async with self.lock:
                                await s.query()
                                await self.reconcile(s)
                                output(event='state',state=self.view_state(s.last_state))
                    self.availability(False,'disconnected')
                    async with self.lock:
                        self.session=None
                    await client.stop_notify(s.characteristic)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.availability(False,str(e))
                self.logger.warning('connection: %r',e)
            finally:
                self.online=False
                self.session=None
            if not self.stop.is_set():
                try:
                    await asyncio.wait_for(self.stop.wait(),timeout=delay)
                except TimeoutError:
                    pass
                delay=min(30,delay*2)

    async def handle(self,request):
        ident=request.get('id')
        try:
            action=request.get('action')
            value=request.get('value')
            self.logger.info(json.dumps({'at':datetime.datetime.now().astimezone().isoformat(),
                'event':'homekit_request','id':ident,'action':action,'value':value},ensure_ascii=False))
            # Encode validates the whitelist before any control takes place.
            if action=='clear_adaptive_temperature':
                # Private IPC housekeeping only; never writes to BLE or wakes
                # the lamp. Manual deferred targets must remain intact.
                async with self.lock:
                    if self.deferred_temperature_adaptive:
                        self.deferred_temperature=None
                        self.deferred_temperature_adaptive=False
                    state=self.view_state(self.session.last_state) if self.session else None
                    output(id=ident,ok=True,state=state)
                return
            if action in ('upper','lower') and type(value) is int and value==0:
                action,value=action+'_on',False
            if action=='auto_preference':
                if type(value) is not bool:
                    raise ValueError('auto preference must be boolean')
            else:
                encode(action,0,value)
            async with self.lock:
                s=self.session
                if not self.online or s is None or self.lost.is_set():
                    raise RuntimeError('lamp offline')
                async with asyncio.timeout(18):
                    await s.query()
                    await self.reconcile(s)
                    if action=='auto_preference':
                        if value and (s.last_state.get('seat_sensing') or s.last_state.get('daylight')):
                            raise ValueError('请先关闭灯内入座感应或日光同行')
                        self.prefer_auto(value,'Home automatic brightness switch')
                        # An explicit choice supersedes older queued slider targets.
                        self.deferred_brightness.clear()
                        active=value and self.both_on(s.last_state)
                        if bool(s.last_state.get('auto_brightness'))!=active:
                            await s.control('auto_brightness',active)
                        self.remember(s.last_state)
                        output(id=ident,ok=True,state=self.view_state(s.last_state))
                        return
                    if action!='status':
                        if action in ('power','upper_on','lower_on') and not value and s.last_state.get('auto_brightness'):
                            # Stop the whole-fixture sensor before enforcing off.
                            await s.control('auto_brightness',False)
                        if action in ('upper','lower'):
                            # Home's slider-bottom trailing Brightness=1 must
                            # not erase the automatic preference after On=false.
                            if value==1 and not s.last_state[action+'_on']:
                                self.remember(s.last_state)
                                output(id=ident,ok=True,state=self.view_state(s.last_state))
                                return
                            self.prefer_auto(False,'manual brightness request')
                            if s.last_state.get('auto_brightness'):
                                await s.control('auto_brightness',False)
                        # Both-off disables channel-on in the firmware. The
                        # captured master-on command enables both channels;
                        # then restore the other channel to off before applying
                        # any deferred values. Do not retry a failed raw write.
                        if action in ('upper_on','lower_on') and value and not s.last_state['power']:
                            other='lower_on' if action=='upper_on' else 'upper_on'
                            await s.control('power',True)
                            await s.control(other,False)
                        if action=='power' and value and s.last_state['power']:
                            # When already partially on, firmware ACKs master-on
                            # but preserves the head mask. Explicitly enable the
                            # missing head(s) using verified channel commands.
                            for channel in ('upper_on','lower_on'):
                                if not s.last_state[channel]:
                                    await s.control(channel,True)
                        # Home may send On=false followed by Brightness=1 at
                        # the bottom of its slider. Brightness must not undo off.
                        deferred=False
                        if action in ('upper','lower') and not s.last_state[action+'_on']:
                            self.deferred_brightness[action]=value
                            deferred=True
                        if action=='temperature':
                            if s.last_state.get('daylight'):
                                raise RuntimeError('请先在徕芬App关闭日光同行，再手动设置色温')
                            if not (s.last_state['upper_on'] or s.last_state['lower_on']):
                                self.deferred_temperature=value
                                self.deferred_temperature_adaptive=request.get('adaptive') is True
                                deferred=True
                        explicit_power=action in ('power','upper_on','lower_on') and not value
                        if not deferred and (explicit_power or s.last_state[action]!=value):
                            await s.control(action,value)
                        if not deferred:
                            if action in ('upper','lower'):
                                self.deferred_brightness.pop(action,None)
                            if action=='temperature':
                                self.deferred_temperature=None
                                self.deferred_temperature_adaptive=False
                        if action in ('upper_on','lower_on','power') and value:
                            for channel in ('upper','lower'):
                                if s.last_state[channel+'_on'] and channel in self.deferred_brightness:
                                    await s.control(channel,self.deferred_brightness[channel])
                                    del self.deferred_brightness[channel]
                            if self.deferred_temperature is not None and not s.last_state.get('daylight'):
                                await s.control('temperature',self.deferred_temperature)
                                self.deferred_temperature=None
                                self.deferred_temperature_adaptive=False
                            await self.restore_auto_on_wake(s)
                    self.remember(s.last_state)
                    output(id=ident,ok=True,state=self.view_state(s.last_state))
        except Exception as e:
            output(id=ident,ok=False,error=str(e))
            if self.session is not None:
                self.availability(False,str(e))
                self.lost.set()

    async def run(self):
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(sig,self.stop.set)
        reader=asyncio.StreamReader(limit=8192)
        transport,_=await loop.connect_read_pipe(lambda:asyncio.StreamReaderProtocol(reader),sys.stdin)
        maintain=asyncio.create_task(self.maintain())
        stop_task=asyncio.create_task(self.stop.wait())
        try:
            while not self.stop.is_set():
                read=asyncio.create_task(reader.readline())
                done,_=await asyncio.wait((read,stop_task),return_when=asyncio.FIRST_COMPLETED)
                if stop_task in done:
                    read.cancel()
                    break
                line=read.result()
                if not line:
                    break
                try:
                    req=json.loads(line)
                    if not isinstance(req,dict):
                        raise ValueError('expected request object')
                except Exception as e:
                    output(ok=False,error=str(e))
                    continue
                await self.handle(req)
        finally:
            self.stop.set()
            stop_task.cancel()
            maintain.cancel()
            await asyncio.gather(maintain,stop_task,return_exceptions=True)
            transport.close()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--log',required=True)
    p.add_argument('--device-name',required=True)
    p.add_argument('--protocol-address',required=True)
    p.add_argument('--identifier')
    args=p.parse_args()
    asyncio.run(Worker(args.log,args.log+'.preferences.json',args.device_name,
                       args.protocol_address,args.identifier).run())
