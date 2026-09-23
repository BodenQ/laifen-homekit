"""Observed Laifen LFFL01 framing. Pure functions; no Bluetooth access."""
from dataclasses import dataclass
from functools import reduce
from operator import xor

import re

MODEL_NAME = re.compile(r'LFFL01-P-[0-9A-Fa-f]{4}')
SERVICE = '0000ff01-0000-1000-8000-00805f9b34fb'
CHARACTERISTIC = '0000ff02-0000-1000-8000-00805f9b34fb'

def encode(action, sequence, value=None):
    if not 0 <= sequence <= 255:
        raise ValueError('sequence outside 0..255')
    if action == 'status' and value is None:
        category, command, payload = 0x41, 3, b''
    elif action == 'power' and type(value) is bool:
        category, command, payload = 1, 8, bytes([value])
    elif action in ('upper','lower') and type(value) is int and 1 <= value <= 100:
        category, command, payload = 3, 2 if action=='upper' else 4, bytes([value])
    elif action in ('upper_on','lower_on') and type(value) is bool:
        category, command, payload = 3, 1 if action=='upper_on' else 3, bytes([value])
    elif action == 'temperature' and type(value) is int and 2900 <= value <= 5700:
        category, command, payload = 3, 5, value.to_bytes(2,'big')
    elif action == 'auto_brightness' and type(value) is bool:
        # App-captured AA030C: 01 enables, 00 disables.
        category, command, payload = 3, 12, bytes([value])
    else:
        raise ValueError('unsupported action or value')
    body = bytes([0xaa,category,command,sequence])+len(payload).to_bytes(2,'big')+payload
    return body+bytes([reduce(xor,body,0)])

@dataclass(frozen=True)
class Frame:
    category: int
    command: int
    sequence: int
    payload: bytes

def decode(data):
    data=bytes(data)
    if len(data)<7 or data[0]!=0x5a:
        raise ValueError('invalid notification prefix/size')
    if len(data)!=int.from_bytes(data[4:6],'big')+7:
        raise ValueError('notification length mismatch')
    if reduce(xor,data,0):
        raise ValueError('notification XOR mismatch')
    return Frame(data[1],data[2],data[3],data[6:-1])

def state_from_frame(frame, expected_name=None, expected_address=None):
    if (frame.category,frame.command)!=(0x81,3):
        return None
    p=frame.payload
    if len(p)!=43:
        raise ValueError('unexpected state payload length')
    name=p[:15].split(b'\0',1)[0].decode('ascii')
    address=p[15:21].hex(':')
    if not MODEL_NAME.fullmatch(name):
        raise ValueError('unsupported model/status layout')
    if expected_name is not None and name!=expected_name:
        raise ValueError('device name mismatch')
    if expected_address is not None and address.lower()!=expected_address.lower():
        raise ValueError('device protocol address mismatch')
    if any(p[i] not in (0,1) for i in (21,22,24)) or not (0<=p[23]<=100 and 0<=p[25]<=100):
        raise ValueError('state fields outside observed bounds')
    return {'name':name,'address':address,'power':bool(p[21]),
            'upper':p[23],'lower':p[25], 'channel_flags':[p[22],p[24]],
            'upper_on':bool(p[22]),'lower_on':bool(p[24]),
            'temperature':int.from_bytes(p[26:28],'big'),
            'seat_sensing':bool(p[28]),'seat_distance':p[29],
            'seat_delay_seconds':int.from_bytes(p[30:32],'big'),
            'auto_brightness':bool(p[32]),'daylight':bool(p[33]),
            'night':bool(p[34]),'night_start':[p[35],p[36]],
            'night_end':[p[37],p[38]],'delay_off':bool(p[39]),
            'delay_minutes':p[40]}
