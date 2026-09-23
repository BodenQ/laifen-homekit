"""Identity-checked BLE session; ACK and fresh state verification."""
import asyncio
import datetime
import json
import time
from lamp_protocol import encode, decode, state_from_frame

class Session:
    def __init__(self,log,device_name=None,protocol_address=None):
        self.device_name=device_name
        self.protocol_address=protocol_address
        self.log=log
        self.sequence=0
        self.queue=asyncio.Queue()
        self.last_state=None
        self.baseline=None
        self.verified=False
        self.fault=None

    def emit(self,event,**fields):
        item={'at':datetime.datetime.now().astimezone().isoformat(),'event':event,**fields}
        line=json.dumps(item,ensure_ascii=False)
        self.log.write(line+'\n'); self.log.flush()
        print(line,flush=True)

    def notification(self,char,data):
        raw=bytes(data)
        try:
            frame=decode(raw)
            state=state_from_frame(frame,self.device_name,self.protocol_address)
            self.emit('rx',hex=raw.hex(),category=frame.category,command=frame.command,
                      sequence=frame.sequence,payload=frame.payload.hex(),state=state)
            if state is not None:
                self.last_state=state
            self.queue.put_nowait((time.monotonic(),frame,state))
        except Exception as e:
            self.fault=str(e)
            self.verified=False
            self.emit('invalid_rx',hex=raw.hex(),error=str(e))

    async def wait_for(self,after,predicate,timeout=8):
        async with asyncio.timeout(timeout):
            while True:
                stamp,frame,state=await self.queue.get()
                if stamp>=after and predicate(frame,state):
                    return frame,state

    async def write(self,action,value=None):
        if self.fault:
            raise RuntimeError('session fault: '+self.fault)
        seq=self.sequence
        raw=encode(action,seq,value)
        self.sequence=(seq+1)%256
        stamp=time.monotonic()
        self.emit('tx',action=action,value=value,sequence=seq,hex=raw.hex(),
                  characteristic=self.characteristic.uuid,handle=self.characteristic.handle)
        await self.client.write_gatt_char(self.characteristic,raw,response=True)
        return stamp,seq

    async def query(self):
        stamp,seq=await self.write('status')
        _,state=await self.wait_for(stamp,lambda f,s:s is not None)
        if self.fault:
            raise RuntimeError(self.fault)
        self.verified=bool(self.device_name and self.protocol_address)
        self.emit('status_verified',state=state,query_sequence=seq)
        return state

    async def control(self,action,value,settle_timeout=6):
        if not self.verified or self.fault:
            raise RuntimeError('identity/status verification required before control')
        before=dict(self.last_state)
        stamp,seq=await self.write(action,value)
        raw=encode(action,seq,value)
        kind=(raw[1]|0xc0,raw[2])
        ack,_=await self.wait_for(stamp,lambda f,s:(f.category,f.command)==kind and f.sequence==seq)
        if ack.payload!=b'\0':
            raise RuntimeError('nonzero/unrecognized control ACK: '+ack.payload.hex())
        # Query after ACK and require a fresh, identity-checked state. The lamp
        # also sends unsolicited states using an independent sequence, so a
        # response cannot be uniquely correlated with an individual query.
        expected={k:before[k] for k in ('power','upper','lower')}
        for key in ('upper_on','lower_on','temperature'):
            if key in before:
                expected[key]=before[key]
        expected[action]=value
        if action=='power':
            for key in ('upper_on','lower_on'):
                if key in expected:expected[key]=value
        elif action in ('upper_on','lower_on'):
            expected['power']=expected['upper_on'] or expected['lower_on']
        elif action=='auto_brightness':
            # The lamp may immediately adjust brightness autonomously.
            expected={'auto_brightness':value}
        if before.get('auto_brightness') and action not in ('upper','lower'):
            expected.pop('upper',None)
            expected.pop('lower',None)
        try:
            async with asyncio.timeout(settle_timeout):
                while True:
                    state=await self.query()
                    if all(state[k]==v for k,v in expected.items()):
                        break
                    self.emit('state_pending',expected=expected,state=state)
                    # ACK precedes the physical transition/status update. Poll
                    # status only; never retransmit the control on stale state.
                    await asyncio.sleep(0.4)
        except TimeoutError as e:
            raise RuntimeError(f'state did not settle: expected {expected}, got {self.last_state}') from e
        self.emit('control_verified',action=action,value=value,sequence=seq,state=state)

