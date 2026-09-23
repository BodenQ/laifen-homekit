"""Discovery and status-only compatibility check. No lamp control writes."""
import argparse
import asyncio
import json
from pathlib import Path
from bleak import BleakClient
from lamp_discovery import candidates,find_target
from lamp_protocol import SERVICE,CHARACTERISTIC
from lamp_session import Session


class ProbeSession(Session):
    def emit(self,*args,**kwargs):
        pass


async def run(args):
    if not args.name:
        for device,adv in await candidates(args.timeout):
            print(json.dumps({'name':adv.local_name or device.name,
                              'identifier':device.address,'rssi':adv.rssi}))
        return
    device=await find_target(args.name,args.identifier,args.timeout)
    async with BleakClient(device,timeout=20) as client:
        matches=[c for service in client.services if service.uuid.lower()==SERVICE
                 for c in service.characteristics if c.uuid.lower()==CHARACTERISTIC]
        if len(matches)!=1 or not {'write','notify'}.issubset(matches[0].properties):
            raise RuntimeError('Unsupported GATT layout: expected FF01 / FF02 Write+Notify')
        session=ProbeSession(None,args.name)
        session.client=client;session.characteristic=matches[0]
        await client.start_notify(matches[0],session.notification)
        state=await session.query()
        await client.stop_notify(matches[0])
    device_config={'name':state['name'],'protocolAddress':state['address']}
    # Identifiers are host-local CoreBluetooth UUIDs on macOS. Only pin one
    # when explicitly requested; otherwise retain portable exact-name lookup.
    if args.identifier:device_config['identifier']=args.identifier
    result={'device':device_config,'status':state}
    if args.output:Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--name');p.add_argument('--identifier');p.add_argument('--output')
    p.add_argument('--timeout',type=float,default=12)
    asyncio.run(run(p.parse_args()))
