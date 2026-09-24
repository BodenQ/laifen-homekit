import test from 'node:test';
import assert from 'node:assert/strict';
import {HomebridgeAPI} from './node_modules/homebridge/dist/api.js';
import adapter from './homebridge-laifen-local/adaptive-lighting.js';
const {createAdaptiveController}=adapter;
const api=new HomebridgeAPI();
const {hap}=api;
const C=hap.Characteristic;
const START=1800000000000;

function schedule(curve, overrides={}) {
  return {activeTransition:{iid:1,brightnessCharacteristicIID:2,
    transitionStartMillis:START,timeMillisOffset:150,
    transitionId:hap.uuid.generate('anonymous-curve'),transitionStartBuffer:'0000000000000000',
    transitionCurve:structuredClone(curve),brightnessAdjustmentRange:{minBrightnessValue:10,maxBrightnessValue:100},
    updateInterval:60000,notifyIntervalThreshold:180000,...overrides}};
}
const curve=[
  {temperature:344,brightnessAdjustmentFactor:0,transitionTime:0,duration:120000},
  {temperature:350.5555419921875,brightnessAdjustmentFactor:-0.6555555462837219,transitionTime:1800000,duration:30000},
  {temperature:347.6666564941406,brightnessAdjustmentFactor:-1.4666666984558105,transitionTime:1800000,duration:3600000},
  {temperature:353,brightnessAdjustmentFactor:-1.2999999523162842,transitionTime:1800000},
  {temperature:344,brightnessAdjustmentFactor:0,transitionTime:1800000,duration:90000},
  {temperature:344,brightnessAdjustmentFactor:0,transitionTime:10000},
];
function light(mode,brightness=30,reference=100) {
  const service=new hap.Service.Lightbulb('test');
  service.getCharacteristic(C.Brightness).updateValue(brightness);
  const temperature=service.getCharacteristic(C.ColorTemperature);
  temperature.setProps({minValue:176,maxValue:344,minStep:1});
  const writes=[]; temperature.onSet((v)=>{writes.push(v);});
  const controller=createAdaptiveController(hap,service,mode,reference);
  controller.constructServices();controller.configureServices();
  return {controller,service,temperature,writes};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('all curve segments match upstream at 100%, preserving original data and actual brightness',async t=>{
  let now=START+150;
  t.mock.method(Date,'now',()=>now);
  let elapsed=0;const offsets=new Set([0,1]);
  for(let i=0;i<curve.length-1;i++) {
    elapsed+=curve[i].transitionTime;
    const end=elapsed+(curve[i].duration??0)+curve[i+1].transitionTime;
    for(const value of [elapsed,elapsed+(curve[i].duration??0),end-1,end,end+1]) offsets.add(value);
    for(let j=1;j<11;j++)offsets.add(elapsed+Math.floor((end-elapsed)*j/11));
    elapsed+=curve[i].duration??0;
  }
  for (const offset of [...offsets].sort((a,b)=>a-b)) {
    now=START+150+offset;
    for (const brightness of [1,30,100]) {
      const native=light('apple',100), independent=light('independent',brightness);
      try {
        const original=schedule(curve); const snapshot=structuredClone(original);
        native.controller.deserialize(structuredClone(original));
        independent.controller.deserialize(original);
        await settle();
        assert.deepEqual(independent.writes,native.writes,`offset ${offset}, brightness ${brightness}`);
        assert.equal(independent.controller.isAdaptiveLightingActive(),native.controller.isAdaptiveLightingActive());
        assert.equal(independent.service.getCharacteristic(C.Brightness).value,brightness);
        assert.deepEqual(original,snapshot);
        if(independent.controller.isAdaptiveLightingActive())assert.deepEqual(independent.controller.serialize(),snapshot);
      } finally {
        native.controller.disableAdaptiveLighting();independent.controller.disableAdaptiveLighting();
      }
    }
  }
});

test('fixed reference honors Apple multiplier bounds and mired limits',async t=>{
  t.mock.method(Date,'now',()=>START+150);
  for(const [temperature,factor,range] of [[350,-1.5,{minBrightnessValue:10,maxBrightnessValue:80}],
    [350,-3,{minBrightnessValue:10,maxBrightnessValue:100}],
    [500,0,{minBrightnessValue:10,maxBrightnessValue:100}]]) {
    const data=schedule([{temperature,brightnessAdjustmentFactor:factor,transitionTime:0},
      {temperature,brightnessAdjustmentFactor:factor,transitionTime:600000}],{brightnessAdjustmentRange:range});
    const native=light('apple',100),independent=light('independent',12);
    try {
      native.controller.deserialize(structuredClone(data));independent.controller.deserialize(data);await settle();
      assert.deepEqual(independent.writes,native.writes);
      assert.ok(independent.writes.every(v=>v>=176&&v<=344));
    } finally {native.controller.disableAdaptiveLighting();independent.controller.disableAdaptiveLighting();}
  }
});

test('upstream timers, event throttling, renewal, restore and explicit disable are retained',async t=>{
  t.mock.timers.enable({apis:['Date','setTimeout'],now:START+150});
  const native=light('apple',100), independent=light('independent',30);
  const nativeEvents=[],independentEvents=[];
  native.temperature.on('change',v=>{if(v.reason==='event')nativeEvents.push(v.newValue);});
  independent.temperature.on('change',v=>{if(v.reason==='event')independentEvents.push(v.newValue);});
  t.after(()=>{native.controller.disableAdaptiveLighting();independent.controller.disableAdaptiveLighting();});
  const data=schedule(curve);
  native.controller.deserialize(structuredClone(data));independent.controller.deserialize(structuredClone(data));await settle();
  for(let i=0;i<8;i++){t.mock.timers.tick(60000);await settle();}
  assert.deepEqual(independent.writes,native.writes);
  assert.deepEqual(independentEvents,nativeEvents);
  assert.ok(independentEvents.length>0);assert.ok(independentEvents.length<independent.writes.length);
  const old=independent.controller.serialize();
  independent.controller.disableAdaptiveLighting();
  const restored=light('independent',5);t.after(()=>restored.controller.disableAdaptiveLighting());
  restored.controller.deserialize(structuredClone(old));await settle();
  assert.equal(restored.writes.at(-1),native.writes.at(-1));
  const fresh=schedule([{temperature:320,brightnessAdjustmentFactor:-1,transitionTime:0},
    {temperature:340,brightnessAdjustmentFactor:0,transitionTime:120000}],
    {transitionStartMillis:Date.now(),timeMillisOffset:0,updateInterval:15000,notifyIntervalThreshold:30000});
  native.controller.disableAdaptiveLighting();restored.controller.disableAdaptiveLighting();
  native.controller.deserialize(structuredClone(fresh));restored.controller.deserialize(structuredClone(fresh));await settle();
  t.mock.timers.tick(15000);await settle();
  assert.equal(restored.writes.at(-1),native.writes.at(-1));
  const before=restored.writes.length;restored.controller.disableAdaptiveLighting();
  t.mock.timers.tick(15000);await settle();assert.equal(restored.writes.length,before);
});

test('actual brightness changes do not change independent colour and manual colour disables it',async t=>{
  t.mock.method(Date,'now',()=>START+150+2000000);
  const f=light('independent',30);t.after(()=>f.controller.disableAdaptiveLighting());
  f.controller.deserialize(schedule(curve));await settle();const expected=f.writes.at(-1);
  f.service.getCharacteristic(C.Brightness).updateValue(10);await settle();
  assert.equal(f.writes.at(-1),expected);assert.equal(f.service.getCharacteristic(C.Brightness).value,10);
  f.service.getCharacteristic(C.Brightness).updateValue(100);await settle();assert.equal(f.writes.at(-1),expected);
  await f.temperature.handleSetRequest(250);assert.equal(f.controller.isAdaptiveLightingActive(),false);
});

test('invalid mode fails explicitly instead of silently changing policy',()=>{
  assert.throws(()=>createAdaptiveController(hap,new hap.Service.Lightbulb('test'),'unknown'),/adaptiveLightingMode/);
});

test('70% reference follows each Apple segment at all actual brightness levels',async t=>{
  let now=START+150;t.mock.method(Date,'now',()=>now);
  for(let offset=0;offset<11000000;offset+=77777) {
    now=START+150+offset;
    const native=light('apple',70),independent=light('independent',(offset%100)+1,70);
    try {
      native.controller.deserialize(schedule(curve));independent.controller.deserialize(schedule(curve));await settle();
      assert.deepEqual(independent.writes,native.writes,`70% at ${offset}`);
    } finally {native.controller.disableAdaptiveLighting();independent.controller.disableAdaptiveLighting();}
  }
  for(const value of [0,101,NaN,'70'])
    assert.throws(()=>createAdaptiveController(hap,new hap.Service.Lightbulb('test'),'independent',value),/adaptiveReferenceBrightness/);
});
