"""Exact advertised-name selection; never choose the nearest/first lamp."""
from bleak import BleakScanner
from lamp_protocol import MODEL_NAME


async def candidates(timeout=10):
    devices=await BleakScanner.discover(timeout=timeout,return_adv=True)
    return [(device,adv) for device,adv in devices.values()
            if MODEL_NAME.fullmatch(adv.local_name or device.name or '')]


async def find_target(name,identifier=None,timeout=15):
    if not name or not MODEL_NAME.fullmatch(name):
        raise ValueError('Set the exact supported LFFL01-P-XXXX name first')
    found=[d for d,a in await candidates(timeout)
           if (a.local_name or d.name)==name
           and (identifier is None or d.address.lower()==identifier.lower())]
    if len(found)!=1:
        raise RuntimeError(f'Expected one exact target; found {len(found)}. '
                           'Close the vendor App, check range, or specify --identifier from scan.')
    return found[0]
