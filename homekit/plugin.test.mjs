import test from 'node:test';
import assert from 'node:assert/strict';
import { HomebridgeAPI } from './node_modules/homebridge/dist/api.js';
import plugin from './homebridge-laifen-local/index.js';

function fixture(device={name:'LFFL01-P-ABCD',protocolAddress:'aa:bb:cc:dd:ee:ff'}) {
  const api=new HomebridgeAPI(); const registered=[];
  api.registerPlatformAccessories=(p,n,items)=>registered.push(...items);
  api.updatePlatformAccessories=()=>{};
  const log={info(){},warn(){},error(){}};
  const p=new plugin.LaifenPlatform(log,{device},api);
  p.setup();
  return {api,p,registered};
}
test('HomeKit identities derive from configured lamp, not the original research device',()=>{
  const one=fixture();
  const two=fixture({name:'LFFL01-P-1234',protocolAddress:'01:02:03:04:05:06'});
  const ids=new Set([...one.registered,...two.registered].map(a=>a.UUID));
  assert.equal(ids.size,6);
});
test('independent master accessory, paired channels, shared temperature and separate mode switch',async()=>{
  const {api,p,registered}=fixture();
  assert.equal(registered.length,3);
  const C=api.hap.Characteristic;
  const lights=registered[0].services.filter(s=>s.UUID===api.hap.Service.Lightbulb.UUID && s.subtype!=='master');
  const master=registered[1].getServiceById(api.hap.Service.Lightbulb,'master');
  assert.equal(master.isPrimaryService,true);
  assert.equal(master.getCharacteristic(C.Name).value,'台灯');
  assert.equal(master.testCharacteristic(C.Brightness),false);
  assert.deepEqual(lights.map(s=>s.subtype),['upper','lower']);
  assert.deepEqual(lights.map(s=>s.getCharacteristic(C.Name).value),['上灯','下灯']);
  assert.deepEqual(lights.map(s=>s.getCharacteristic(C.Brightness).props.minValue),[1,1]);
  p.online=true; p.update({power:true,upper:66,lower:20,upper_on:true,lower_on:true,temperature:4800});
  assert.deepEqual(lights.map(s=>s.getCharacteristic(C.Brightness).value),[66,20]);
  const writes=[];
  p.request=async(action,value)=>{
    writes.push([action,value]); const state={...p.state,[action]:value}; p.update(state); return state;
  };
  await lights[1].getCharacteristic(C.On).handleSetRequest(false);
  assert.deepEqual(writes,[['lower_on',false]]);
  assert.deepEqual(lights.map(s=>s.getCharacteristic(C.On).value),[true,false]);
  await lights[0].getCharacteristic(C.Brightness).handleSetRequest(80);
  assert.deepEqual(lights.map(s=>s.getCharacteristic(C.Brightness).value),[80,20]);
  assert.equal(lights[0].testCharacteristic(C.ColorTemperature),false);
  await lights[1].getCharacteristic(C.ColorTemperature).handleSetRequest(250);
  assert.deepEqual(writes.at(-1),['temperature',4000]);
  await master.getCharacteristic(C.On).handleSetRequest(false);
  assert.deepEqual(writes.at(-1),['power',false]);
  await master.getCharacteristic(C.On).handleSetRequest(true);
  assert.deepEqual(writes.at(-1),['power',true]);
  p.unavailable(); await assert.rejects(p.current());
});
test('automatic brightness switch shows persisted intent even while lamp is off',async()=>{
  const {api,p,registered}=fixture();
  const C=api.hap.Characteristic;
  const toggle=registered[2].getServiceById(api.hap.Service.Switch,'auto-brightness');
  assert.equal(toggle.getCharacteristic(C.Name).value,'自动亮度');
  p.online=true;
  p.update({power:false,upper:100,lower:20,upper_on:false,lower_on:false,
    temperature:2907,auto_brightness:false,auto_brightness_preference:true});
  assert.equal(toggle.getCharacteristic(C.On).value,true);
  const writes=[];
  p.request=async(a,v)=>{writes.push([a,v]);p.update({...p.state,auto_brightness_preference:v});return p.state;};
  await toggle.getCharacteristic(C.On).handleSetRequest(false);
  assert.deepEqual(writes,[['auto_preference',false]]);
  assert.equal(toggle.getCharacteristic(C.On).value,false);
  assert.equal(p.state.power,false);
});
test('migrates the paired upper temperature control to lower without replacing services',()=>{
  const {api,p,registered}=fixture();
  const accessory=registered[0]; const C=api.hap.Characteristic;
  accessory.removeController(p.adaptiveController);
  const upper=accessory.getServiceById(api.hap.Service.Lightbulb,'upper');
  const lower=accessory.getServiceById(api.hap.Service.Lightbulb,'lower');
  accessory.addService(api.hap.Service.Lightbulb,'台灯','master').setPrimaryService(true);
  upper.setPrimaryService(true); lower.setPrimaryService(false);
  upper.getCharacteristic(C.ColorTemperature);
  lower.removeCharacteristic(lower.getCharacteristic(C.ColorTemperature));
  const migrated=new plugin.LaifenPlatform(p.log,p.config,api);
  for (const item of registered) migrated.configureAccessory(item);
  migrated.setup();
  assert.equal(registered.length,3);
  assert.equal(accessory.getServiceById(api.hap.Service.Lightbulb,'upper'),upper);
  assert.equal(accessory.getServiceById(api.hap.Service.Lightbulb,'lower'),lower);
  assert.equal(upper.testCharacteristic(C.ColorTemperature),false);
  assert.equal(lower.testCharacteristic(C.ColorTemperature),true);
  assert.equal(upper.isPrimaryService,false); assert.equal(lower.isPrimaryService,false);
  assert.equal(accessory.getServiceById(api.hap.Service.Lightbulb,'master'),undefined);
  assert.equal(registered[1].getServiceById(api.hap.Service.Lightbulb,'master').isPrimaryService,true);
});
test('queued slider values coalesce without crossing power command',async()=>{
  const {p}=fixture(); const writes=[]; let release;
  p.request=async(a,v)=>{
    writes.push([a,v]);
    if(writes.length===1) await new Promise(r=>release=r);
    return {};
  };
  const tasks=[p.enqueue('upper',20),p.enqueue('upper',30),p.enqueue('upper',40),
    p.enqueue('power',false),p.enqueue('upper',50)];
  release(); await Promise.all(tasks);
  assert.deepEqual(writes,[['upper',20],['upper',40],['power',false],['upper',50]]);
});

const baseState={power:true,upper:100,lower:30,upper_on:true,lower_on:true,temperature:4831};
const flush=async p=>{for(let i=0;i<100 && p.busy;i++) await new Promise(r=>setImmediate(r)); assert.equal(p.busy,false);};
function adaptiveFixture(t) {
  const f=fixture(); const {p,api}=f; const writes=[];
  p.online=true; p.update({...baseState});
  p.request=async(action,value,options)=>{
    writes.push({action,value,adaptive:options.adaptive});
    const state=action==='clear_adaptive_temperature'?p.state:{...p.state,[action]:value};
    p.update(state); return state;
  };
  t.after(()=>{p.online=false;p.adaptiveController.disableAdaptiveLighting();});
  const schedule={activeTransition:{iid:1,brightnessCharacteristicIID:2,
    transitionStartMillis:Date.now(),timeMillisOffset:0,
    transitionId:api.hap.uuid.generate('test-only-adaptive-schedule'),transitionStartBuffer:'0000000000000000',
    transitionCurve:[{temperature:340,brightnessAdjustmentFactor:-0.8,transitionTime:0},
      {temperature:340,brightnessAdjustmentFactor:-0.8,transitionTime:86400000}],
    brightnessAdjustmentRange:{minBrightnessValue:10,maxBrightnessValue:100},
    updateInterval:60000,notifyIntervalThreshold:600000}};
  p.adaptiveController.deserialize(schedule);
  return {...f,writes};
}
test('adaptive schedule uses lower brightness and survives its own BLE state echoes',async t=>{
  const {p,writes}=adaptiveFixture(t); await flush(p);
  assert.equal(p.adaptiveController.isAdaptiveLightingActive(),true);
  assert.equal(writes[0].value,Math.round(1000000/(340-0.8*30)));
  p.update({...p.state,upper:20}); await flush(p);
  assert.equal(writes.length,1);
  p.update({...p.state,lower:80}); await flush(p);
  assert.equal(writes.at(-1).value,Math.round(1000000/(340-0.8*80)));
  assert.equal(p.adaptiveController.isAdaptiveLightingActive(),true);
  assert.ok(p.adaptiveController.serialize().activeTransition);
});
test('manual Home colour and external lamp changes disable adaptive lighting',async t=>{
  const first=adaptiveFixture(t); await flush(first.p);
  const lower=first.registered[0].getServiceById(first.api.hap.Service.Lightbulb,'lower');
  await lower.getCharacteristic(first.api.hap.Characteristic.ColorTemperature).handleSetRequest(250);
  await flush(first.p);
  assert.equal(first.p.adaptiveController.isAdaptiveLightingActive(),false);
  assert.equal(first.p.state.temperature,4000);
  assert.ok(first.writes.some(w=>w.action==='clear_adaptive_temperature'));
  const second=adaptiveFixture(t); await flush(second.p);
  second.p.update({...second.p.state,temperature:5700}); await flush(second.p);
  assert.equal(second.p.adaptiveController.isAdaptiveLightingActive(),false);
});
test('deferred target applied on wake is not mistaken for an external override',async t=>{
  const {p}=adaptiveFixture(t); await flush(p);
  p.update({...p.state,power:false,upper_on:false,lower_on:false,target_temperature:4007});
  p.update({...p.state,power:true,lower_on:true,temperature:4007});
  assert.equal(p.adaptiveController.isAdaptiveLightingActive(),true);
  p.update({...p.state,daylight:true}); await flush(p);
  assert.equal(p.adaptiveController.isAdaptiveLightingActive(),false);
});
test('a manual override cancels queued adaptive targets before they reach BLE',async t=>{
  const {p}=adaptiveFixture(t); await flush(p);
  const writes=[]; let release;
  p.request=async(a,v)=>{writes.push([a,v]);if(a==='upper') await new Promise(r=>release=r);return p.state;};
  const blocked=p.enqueue('upper',50);
  const automatic=p.enqueue('temperature',3500,{adaptive:true});
  p.disableAdaptive('test manual override');
  const manual=p.enqueue('temperature',4000);
  release(); await Promise.all([blocked,automatic,manual]);
  assert.equal(writes.some(([a,v])=>a==='temperature'&&v===3500),false);
  assert.ok(writes.some(([a,v])=>a==='temperature'&&v===4000));
});
