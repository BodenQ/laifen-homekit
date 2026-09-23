'use strict';
const { spawn } = require('node:child_process');
const readline = require('node:readline');

const PLUGIN = 'homebridge-laifen-local';
const PLATFORM = 'LaifenLocal';

class LaifenPlatform {
  constructor(log, config, api) {
    this.log=log; this.config=config; this.api=api;
    this.cached=new Map(); this.services=[]; this.pending=new Map();
    this.online=false; this.state=null; this.stateTime=0;
    this.jobs=[]; this.busy=false; this.nextId=1; this.stopped=false;
    this.temperatureInFlight=0; this.adaptiveEpoch=0;
    api.on('didFinishLaunching',()=>{this.setup(); this.startWorker();});
    api.on('shutdown',()=>{
      this.stopped=true; clearTimeout(this.restartTimer);
      this.child?.kill('SIGTERM'); this.rejectPending('bridge stopped');
    });
  }
  configureAccessory(accessory) { this.cached.set(accessory.UUID,accessory); }
  setup() {
    const {Service,Characteristic:C,uuid}=this.api.hap;
    const device=this.config.device;
    if(!device || !/^LFFL01-P-[0-9a-f]{4}$/i.test(device.name)
        || !/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(device.protocolAddress))
      throw new Error('Run laifen.py configure to select and verify a supported lamp first.');
    const serial=device.protocolAddress.replaceAll(':','').toUpperCase();
    const identity='laifen:'+serial;
    const id=uuid.generate(identity);
    const exists=this.cached.get(id);
    const accessory=exists || new this.api.platformAccessory('徕芬大路灯',id);
    this.accessory=accessory;
    accessory.getService(Service.AccessoryInformation)
      .setCharacteristic(C.Manufacturer,'Laifen (Local Bridge)')
      .setCharacteristic(C.Model,'LFFL01-P')
      .setCharacteristic(C.SerialNumber,serial);
    // A dedicated accessory gives Siri an unambiguous whole-fixture target.
    // A primary service inside the old multi-service accessory still resolved
    // to a channel on the user's existing Home, even after separating tiles.
    const oldMaster=accessory.getServiceById(Service.Lightbulb,'master');
    if(oldMaster) accessory.removeService(oldMaster);
    const masterId=uuid.generate(identity+':master');
    const masterExists=this.cached.get(masterId);
    const masterAccessory=masterExists || new this.api.platformAccessory('台灯',masterId);
    masterAccessory.getService(Service.AccessoryInformation)
      .setCharacteristic(C.Manufacturer,'Laifen (Local Bridge)')
      .setCharacteristic(C.Model,'LFFL01-P Master')
      .setCharacteristic(C.SerialNumber,serial+'-MASTER');
    this.masterService=masterAccessory.getServiceById(Service.Lightbulb,'master')
      || masterAccessory.addService(Service.Lightbulb,'台灯','master');
    this.masterService.setCharacteristic(C.Name,'台灯');
    this.masterService.displayName='台灯';
    this.masterService.setPrimaryService(true);
    this.masterService.getCharacteristic(C.On)
      .onGet(async()=>Boolean((await this.current()).power))
      .onSet(value=>this.set('power',Boolean(value)));
    for (const [channel,name] of [['upper','上灯'],['lower','下灯']]) {
      const service=accessory.getServiceById(Service.Lightbulb,channel)
        || accessory.addService(Service.Lightbulb,name,channel);
      service.setCharacteristic(C.Name,name);
      service.displayName=name;
      service.setPrimaryService(false);
      service.getCharacteristic(C.On)
        .onGet(async()=> (await this.current())[channel+'_on'])
        .onSet(value=>this.set(channel+'_on',Boolean(value)));
      const brightness=service.getCharacteristic(C.Brightness);
      brightness.updateValue(Math.max(1,Number(brightness.value)||1));
      brightness
        .setProps({minValue:1,maxValue:100,minStep:1})
        .onGet(async()=> (await this.current())[channel])
        .onSet(value=>this.set(channel,Number(value)));
      this.services.push({service,channel});
      // Both lamp heads share one physical colour temperature. Expose the
      // control on the lower light only, avoiding competing AL schedules.
      if (channel==='lower') {
        const temperature=service.getCharacteristic(C.ColorTemperature);
        temperature.updateValue(temperature.value>=176 && temperature.value<=344 ? temperature.value : 208);
        temperature.setProps({minValue:176,maxValue:344,minStep:1})
          .onGet(async()=>this.mireds(await this.current()))
          .onSet((value,context)=>this.setTemperature(value,context));
      } else if (service.testCharacteristic(C.ColorTemperature)) {
        // Migrate the already-paired upper service without changing its UUID.
        service.removeCharacteristic(service.getCharacteristic(C.ColorTemperature));
      }
    }
    const lower=accessory.getServiceById(Service.Lightbulb,'lower');
    // A separate control accessory keeps a whole-lamp Off action from also
    // clearing the remembered automatic-brightness preference.
    const autoId=uuid.generate(identity+':automatic-brightness');
    const autoExists=this.cached.get(autoId);
    const autoAccessory=autoExists || new this.api.platformAccessory('自动亮度',autoId);
    autoAccessory.getService(Service.AccessoryInformation)
      .setCharacteristic(C.Manufacturer,'Laifen (Local Bridge)')
      .setCharacteristic(C.Model,'LFFL01-P Automatic Brightness')
      .setCharacteristic(C.SerialNumber,serial+'-AUTO');
    this.autoBrightnessService=autoAccessory.getServiceById(Service.Switch,'auto-brightness')
      || autoAccessory.addService(Service.Switch,'自动亮度','auto-brightness');
    this.autoBrightnessService.setCharacteristic(C.Name,'自动亮度');
    this.autoBrightnessService.displayName='自动亮度';
    this.autoBrightnessService.getCharacteristic(C.On)
      .onGet(async()=>Boolean((await this.current()).auto_brightness_preference))
      .onSet(value=>this.set('auto_preference',Boolean(value)));
    this.adaptiveController=new this.api.hap.AdaptiveLightingController(lower,{
      controllerMode:this.api.hap.AdaptiveLightingControllerMode.AUTOMATIC,
    });
    accessory.configureController(this.adaptiveController);
    lower.getCharacteristic(C.CharacteristicValueActiveTransitionCount).on('change',change=>{
      this.adaptiveEpoch++;
      this.log.info('Apple自适应照明：'+(change.newValue ? '已启用（依据下灯亮度，控制整灯色温）' : '已停用'));
      if(!change.newValue && this.online) this.enqueue('clear_adaptive_temperature').catch(e=>this.log.warn(e.message));
    });
    if (!exists) this.api.registerPlatformAccessories(PLUGIN,PLATFORM,[accessory]);
    else this.api.updatePlatformAccessories([accessory]);
    if (!masterExists) this.api.registerPlatformAccessories(PLUGIN,PLATFORM,[masterAccessory]);
    if (!autoExists) this.api.registerPlatformAccessories(PLUGIN,PLATFORM,[autoAccessory]);
    this.log.info('版本 1.1.0；独立台灯总开关控制两路。自动亮度：两路全开恢复、单路关闭暂停、手调退出。共用色温与Apple自适应照明入口在下灯。');
  }
  disableAdaptive(reason) {
    if(this.adaptiveController?.isAdaptiveLightingActive()) {
      this.log.info('停止Apple自适应照明：'+reason);
      this.adaptiveController.disableAdaptiveLighting();
    }
  }
  async setTemperature(value,context) {
    const adaptive=Boolean(this.adaptiveController && context?.controller===this.adaptiveController);
    if(!adaptive) this.disableAdaptive('家庭手动设置色温');
    const kelvin=Math.max(2900,Math.min(5700,Math.round(1000000/Number(value))));
    if(adaptive && this.state?.daylight) {
      this.disableAdaptive('灯内日光同行已开启');
      throw this.hapError();
    }
    try {
      await this.enqueue('temperature',kelvin,{adaptive});
      if(adaptive && this.adaptiveController.isAdaptiveLightingActive())
        this.log.info(`自适应色温目标 ${kelvin}K；下灯亮度 ${this.state?.lower}%；${this.state?.power?'已应用':'关灯缓存，不开灯'}`);
    } catch(e) { this.log.warn('色温控制失败：'+e.message); throw this.hapError(); }
  }
  mireds(state) { return Math.max(176,Math.min(344,Math.round(1000000/(state.target_temperature ?? state.temperature)))); }
  hapError() { return new this.api.hap.HapStatusError(this.api.hap.HAPStatus.SERVICE_COMMUNICATION_FAILURE); }
  update(state) {
    const previous=this.state;
    const previousTemperature=previous?.temperature ?? this.accessory?.context.observedTemperature;
    const ownTemperature=this.temperatureInFlight>0 || state.temperature===previous?.target_temperature;
    if(state.daylight) this.disableAdaptive('灯内日光同行已开启');
    else if(previousTemperature!==undefined && state.temperature!==previousTemperature && !ownTemperature)
      this.disableAdaptive('检测到灯端色温改变（包括断线期间的改变）');
    this.state=state; this.stateTime=Date.now();
    if(this.accessory && this.accessory.context.observedTemperature!==state.temperature) {
      this.accessory.context.observedTemperature=state.temperature;
      this.api.updatePlatformAccessories([this.accessory]);
    }
    const C=this.api.hap.Characteristic;
    this.masterService?.updateCharacteristic(C.On,Boolean(state.power));
    this.autoBrightnessService?.updateCharacteristic(C.On,Boolean(state.auto_brightness_preference));
    for (const {service,channel} of this.services) {
      service.updateCharacteristic(C.On,state[channel+'_on']);
      service.updateCharacteristic(C.Brightness,state[channel]);
      // The adaptive controller owns its event notification interval. Updating
      // ColorTemperature here every BLE response would bypass that interval.
      if(channel==='lower' && !this.adaptiveController?.isAdaptiveLightingActive())
        service.updateCharacteristic(C.ColorTemperature,this.mireds(state));
    }
  }
  unavailable() {
    this.online=false;
    const C=this.api.hap.Characteristic;
    this.masterService?.getCharacteristic(C.On).updateValue(this.hapError());
    this.autoBrightnessService?.getCharacteristic(C.On).updateValue(this.hapError());
    for (const {service,channel} of this.services) {
      service.getCharacteristic(C.On).updateValue(this.hapError());
      service.getCharacteristic(C.Brightness).updateValue(this.hapError());
      if(channel==='lower') service.getCharacteristic(C.ColorTemperature).updateValue(this.hapError());
    }
  }
  rejectPending(message) {
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error(message)); }
    this.pending.clear();
  }
  startWorker() {
    if (this.stopped) return;
    const args=[this.config.worker,'--log',this.config.bleLog,
      '--device-name',this.config.device.name,'--protocol-address',this.config.device.protocolAddress];
    if(this.config.device.identifier) args.push('--identifier',this.config.device.identifier);
    this.child=spawn(this.config.python,args,{stdio:['pipe','pipe','pipe']});
    const child=this.child;
    readline.createInterface({input:child.stdout}).on('line',line=>{
      let msg; try { msg=JSON.parse(line); } catch { this.log.error('Invalid worker output'); return; }
      if (msg.event==='availability') {
        if (msg.online) {
          this.online=true; this.log.info('灯具蓝牙已连接');
          if(!this.adaptiveController?.isAdaptiveLightingActive())
            this.enqueue('clear_adaptive_temperature').catch(e=>this.log.warn(e.message));
        }
        else { this.unavailable(); this.log.warn('灯具离线：'+(msg.error||'')); }
      }
      if (msg.event==='state' && this.online) this.update(msg.state);
      if (msg.id!==undefined && this.pending.has(msg.id)) {
        const p=this.pending.get(msg.id); this.pending.delete(msg.id); clearTimeout(p.timer);
        if (msg.ok) { if(msg.state) this.update(msg.state); p.resolve(msg.state); }
        else p.reject(new Error(msg.error));
      }
    });
    child.stderr.on('data',data=>this.log.warn(String(data).trim()));
    child.on('error',e=>this.log.error('BLE worker: '+e.message));
    child.on('close',()=>{
      this.unavailable(); this.rejectPending('BLE worker stopped');
      if (!this.stopped) this.restartTimer=setTimeout(()=>this.startWorker(),5000);
    });
  }
  request(action,value,options={}) {
    if (!this.online || !this.child?.stdin.writable) return Promise.reject(new Error('lamp offline'));
    const id=this.nextId++;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{
        this.pending.delete(id); this.unavailable();
        reject(new Error('BLE request timed out')); this.child?.kill('SIGTERM');
      },22000);
      this.pending.set(id,{resolve,reject,timer});
      this.child.stdin.write(JSON.stringify({id,action,value,adaptive:Boolean(options.adaptive)})+'\n',error=>{
        if (error && this.pending.has(id)) {
          this.pending.delete(id); clearTimeout(timer); reject(error);
        }
      });
    });
  }
  enqueue(action,value,options={}) {
    return new Promise((resolve,reject)=>{
      const last=this.jobs.at(-1);
      if (last && last.action===action && last.adaptive===Boolean(options.adaptive)
          && last.epoch===this.adaptiveEpoch && ['upper','lower','temperature'].includes(action)) {
        last.value=value; last.waiters.push({resolve,reject});
      } else {
        if (this.jobs.length>=12) { reject(new Error('control queue busy')); return; }
        this.jobs.push({action,value,adaptive:Boolean(options.adaptive),epoch:this.adaptiveEpoch,waiters:[{resolve,reject}]});
      }
      this.drain();
    });
  }
  async drain() {
    if (this.busy) return;
    this.busy=true;
    try {
      while (this.jobs.length) {
        const job=this.jobs.shift();
        if(job.adaptive && (!this.adaptiveController?.isAdaptiveLightingActive() || job.epoch!==this.adaptiveEpoch)) {
          for(const waiter of job.waiters) waiter.resolve(this.state);
          continue;
        }
        if(job.action==='temperature') this.temperatureInFlight++;
        try {
          const state=await this.request(job.action,job.value,job);
          for (const waiter of job.waiters) waiter.resolve(state);
        } catch (error) {
          for (const waiter of job.waiters) waiter.reject(error);
        } finally {
          if(job.action==='temperature') this.temperatureInFlight--;
        }
      }
    } finally { this.busy=false; }
  }
  async current() {
    if (!this.online || !this.state) throw this.hapError();
    if (Date.now()-this.stateTime>20000) {
      try { return await this.enqueue('status'); } catch { throw this.hapError(); }
    }
    return this.state;
  }
  async set(action,value) {
    try { await this.enqueue(action,value); }
    catch (e) { this.log.warn('控制失败：'+e.message); throw this.hapError(); }
  }
}

module.exports=api=>api.registerPlatform(PLUGIN,PLATFORM,LaifenPlatform);
module.exports.LaifenPlatform=LaifenPlatform;
