#!/usr/bin/env python3
"""Install, configure and run a standalone Laifen HomeKit bridge.

Only the Python standard library is needed to start the terminal wizard.
The Bluetooth backend is installed into the runtime's private venv.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

VERSION='1.0.0'
SOURCE=Path(__file__).resolve().parent
LABEL='io.github.laifen-homekit'
DEFAULT_RUNTIME=Path.home()/('Library/Application Support/LaifenHomeKitBridge'
                            if sys.platform=='darwin' else '.local/share/laifen-homekit')


def call(args,**kwargs):
    return subprocess.run([str(a) for a in args],check=True,**kwargs)


def read_json(path):
    return json.loads(Path(path).read_text())


def save_json(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    with temporary.open('w') as file:
        os.chmod(temporary,0o600)
        json.dump(data,file,ensure_ascii=False,indent=2)
        file.write('\n')
    temporary.replace(path)


def runtime_lock(runtime):
    lock=(runtime/'.bridge.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('桥接正在运行，请先执行 stop；前台运行请先 Ctrl+C')
    return lock


def install(runtime,node=None,mode='standalone'):
    if sys.version_info<(3,11):raise RuntimeError('需要独立安装的 Python 3.11 或以上版本')
    if sys.platform not in ('darwin','linux'):raise RuntimeError('安装器目前支持 macOS；Linux 仅实验性前台运行')
    node=Path(node or shutil.which('node') or '')
    if not node.is_file():raise RuntimeError('请先安装 Node.js 24 LTS，并确认 node 在 PATH 中')
    node=node.resolve()
    major=int(call([node,'--version'],capture_output=True,text=True).stdout.strip().lstrip('v').split('.')[0])
    if major not in (22,24,26):raise RuntimeError('此版本支持 Node 22/24/26，建议 Node 24 LTS')
    if any('codex' in str(p).lower() for p in (node,Path(sys.executable).resolve())):
        raise RuntimeError('请使用自己安装的 Node/Python，不要使用 Codex 内置运行时')
    npm=node.parent/'npm'
    if not npm.is_file():npm=Path(shutil.which('npm') or '')
    if not npm.is_file():raise RuntimeError('未找到 npm，请完整安装 Node.js')
    if runtime.exists() and any(runtime.iterdir()) and not (runtime/'installation.json').exists():
        raise RuntimeError('目标目录已有其他内容；请选择空的 --runtime 目录')
    if (runtime/'installation.json').exists() and read_json(runtime/'installation.json').get('mode')!=mode:
        raise RuntimeError('此目录属于另一安装模式，请使用不同 --runtime 目录，避免覆盖已有桥接')
    runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    runtime.chmod(0o700)
    with runtime_lock(runtime):
        # Checkpoints let a failed dependency download be resumed with install.
        save_json(runtime/'installation.json',{'version':VERSION,'node':str(node),'mode':mode,'complete':False})
        for name in ('tools','homekit'):
            if (SOURCE/name).resolve()==(runtime/name).resolve():continue
            shutil.copytree(SOURCE/name,runtime/name,dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('node_modules','__pycache__','storage','logs','*.test.mjs','test_*.py'))
        for name in ('requirements.txt','laifen.py'):
            if (SOURCE/name).resolve()!=(runtime/name).resolve():shutil.copy2(SOURCE/name,runtime/name)
        # Resolving the interpreter and using symlinks also supports uv's
        # relocatable Python; copying that executable can lose its stdlib path.
        call([Path(sys.executable).resolve(),'-m','venv','--clear','--symlinks',runtime/'.venv'])
        python=runtime/'.venv/bin/python'
        call([python,'-m','pip','install','-r',runtime/'requirements.txt'])
        env={**os.environ,'PATH':str(node.parent)+os.pathsep+os.environ.get('PATH','')}
        if mode=='standalone':
            call([npm,'ci','--omit=dev','--no-audit','--no-fund'],cwd=runtime/'homekit',env=env)
        else:
            call([npm,'pack',runtime/'homekit/homebridge-laifen-local','--pack-destination',runtime],env=env)
        (runtime/'homekit/logs').mkdir(exist_ok=True)
        save_json(runtime/'installation.json',{'version':VERSION,'node':str(node),'mode':mode,'complete':True})
    print('独立运行目录：',runtime)


def installed(runtime):
    metadata=read_json(runtime/'installation.json')
    if not metadata.get('complete'):raise RuntimeError('依赖安装未完成，请重新执行 install')
    return metadata


def probe(runtime,name=None,identifier=None):
    installed(runtime)
    args=[runtime/'.venv/bin/python',runtime/'tools/probe.py']
    if name:args+=['--name',name]
    if identifier:args+=['--identifier',identifier]
    result=call(args,capture_output=True,text=True)
    if name:return json.loads(result.stdout)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def platform_config(runtime,device):
    return {'platform':'LaifenLocal','name':'徕芬大路灯',
            'python':str(runtime/'.venv/bin/python'),'worker':str(runtime/'tools/lamp_worker.py'),
            'bleLog':str(runtime/'homekit/logs/ble.jsonl'),'device':device}


def create_config(runtime,device,port=51826):
    if read_json(runtime/'installation.json').get('mode')=='existing':
        fragment=platform_config(runtime,device)
        save_json(runtime/'platform.json',fragment)
        return fragment
    config_path=runtime/'homekit/storage/config.json'
    if config_path.exists():
        old=read_json(config_path)
        old_device=next(p['device'] for p in old['platforms'] if p.get('platform')=='LaifenLocal')
        if old_device['protocolAddress']!=device['protocolAddress']:
            raise RuntimeError('此运行目录已绑定另一盏灯。请用不同 --runtime 安装，避免覆盖配对。')
        for p in old['platforms']:
            if p.get('platform')=='LaifenLocal':p['device']=device
        save_json(config_path,old)
        return old
    with socket.socket() as sock:sock.bind(('',port))
    address=bytes([0x02])+secrets.token_bytes(5)
    digits=''.join(str(secrets.randbelow(10)) for _ in range(8))
    while len(set(digits))==1 or digits in ('12345678','87654321'):
        digits=''.join(str(secrets.randbelow(10)) for _ in range(8))
    config={'bridge':{'name':'徕芬蓝牙桥','username':address.hex(':').upper(),
        'port':port,'pin':digits[:3]+'-'+digits[3:5]+'-'+digits[5:]},
        'platforms':[platform_config(runtime,device)]}
    save_json(config_path,config)
    return config


def configure(runtime,name,identifier=None,port=51826):
    with runtime_lock(runtime):
        result=probe(runtime,name,identifier)
        config=create_config(runtime,result['device'],port)
    print('状态预检通过：',result['device']['name'])
    print('这证明状态格式兼容；新型号/固件的实体开关反应仍需逐项验证。')
    print('配置已保存。')
    return config


def wizard(runtime,node=None,port=51826,mode=None):
    print('徕芬 HomeKit 终端连接向导 / Laifen HomeKit setup')
    print('将使用本机 Node/Python，下载依赖，不需要 Codex 或灯具云账号。')
    if mode is None:
        answer=input('已有 Homebridge 吗？1=已有，接入现有桥接；2=没有，独立安装 [2]：').strip() or '2'
        if answer not in ('1','2'):raise ValueError('请选择 1 或 2')
        mode='existing' if answer=='1' else 'standalone'
    install(runtime,node,mode)
    print('\n请保持灯通电，并从手机后台退出徕芬 App。')
    name=input('输入 App 中的设备名（例如 LFFL01-P-ABCD），直接回车列出候选：').strip()
    with runtime_lock(runtime):rows=probe(runtime)
    if name:rows=[r for r in rows if r['name']==name]
    if not rows:raise RuntimeError('未找到目标，请关闭 App、检查蓝牙权限和距离后重新 setup')
    for i,row in enumerate(rows,1):print(f"{i}. {row['name']}  {row['identifier']}  RSSI {row['rssi']}")
    if len(rows)==1:chosen=rows[0]
    else:
        choice=int(input('输入要连接的编号（不确定时 Ctrl+C 退出）：'))
        if not 1<=choice<=len(rows):raise ValueError('编号超出范围')
        chosen=rows[choice-1]
    # Pin only if the broadcast name is ambiguous; UUID is local to this host.
    identifier=chosen['identifier'] if sum(r['name']==chosen['name'] for r in rows)>1 else None
    configure(runtime,chosen['name'],identifier,port)
    if mode=='existing':
        print('已生成插件压缩包和 platform.json；不会启动第二个桥接或生成新配对码。')
        config=input('现有 Homebridge 的 config.json 路径（回车跳过自动接入）：').strip()
        if config:
            prefix=input('现有插件安装前缀（包含 node_modules 的目录；须为当前账号可写）：').strip()
            integrate(runtime,Path(config).expanduser().resolve(),Path(prefix).expanduser().resolve())
        else:print('请参考 docs/existing-homebridge.md 接入现有 Homebridge。')
        return
    print('\n下一步：python3 laifen.py run\n然后在“家庭”中添加桥接，按终端显示的二维码/配对码操作。')
    print('前台实测正常后 Ctrl+C，再执行 python3 laifen.py start（macOS 登录自启）。')


def merge_platform(config,fragment):
    if not isinstance(config,dict) or not isinstance(config.get('bridge'),dict):
        raise ValueError('这不是 Homebridge config.json')
    result=json.loads(json.dumps(config))
    platforms=result.setdefault('platforms',[])
    if not isinstance(platforms,list):raise ValueError('platforms 必须是数组')
    existing=[p for p in platforms if p.get('platform')=='LaifenLocal']
    if len(existing)>1:raise ValueError('当前版本每个桥接只支持一个 LaifenLocal 实例')
    if existing:
        previous=existing[0]
        if previous.get('device',{}).get('protocolAddress')!=fragment['device']['protocolAddress']:
            raise ValueError('已有 LaifenLocal 配置属于不同设备，拒绝覆盖')
        platforms[platforms.index(previous)]={**previous,**fragment}
    else:platforms.append(fragment)
    if 'plugins' in result:
        if not isinstance(result['plugins'],list):raise ValueError('plugins 必须是数组')
        if 'homebridge-laifen-local' not in result['plugins']:result['plugins'].append('homebridge-laifen-local')
    if 'homebridge-laifen-local' in result.get('disabledPlugins',[]):
        raise ValueError('现有配置禁用了本插件；请先在 Homebridge 中明确启用后再接入')
    return result


def integrate(runtime,config_path,plugin_prefix):
    metadata=installed(runtime)
    if metadata.get('mode')!='existing':raise RuntimeError('请先使用 setup --mode existing')
    fragment=read_json(runtime/'platform.json')
    config_path=config_path.resolve();plugin_prefix=plugin_prefix.resolve()
    if not config_path.is_file() or not (plugin_prefix/'node_modules').is_dir():
        raise ValueError('需要现有 config.json 和包含 node_modules 的插件安装前缀')
    if config_path.stat().st_uid!=os.getuid() or not os.access(plugin_prefix,os.W_OK):
        raise RuntimeError('请以现有 Homebridge 的运行账号执行，确保该账号也能读取本桥接运行目录')
    original=config_path.read_bytes()
    merged=merge_platform(json.loads(original),fragment)
    node=Path(metadata['node']);npm=node.parent/'npm'
    package=runtime/f'homebridge-laifen-local-{VERSION}.tgz'
    env={**os.environ,'PATH':str(node.parent)+os.pathsep+os.environ.get('PATH','')}
    call([npm,'install','--prefix',plugin_prefix,'--omit=dev','--no-audit','--no-fund',package],env=env)
    if config_path.read_bytes()!=original:
        raise RuntimeError('配置在安装期间被其他程序修改；插件已安装，请重新运行 integrate 合并配置')
    backup=config_path.with_name(config_path.name+f'.laifen-backup-{time.time_ns()}')
    backup.write_bytes(original);backup.chmod(0o600)
    save_json(config_path,merged)
    print('插件已安装，配置已合并；原 bridge 身份和其他配件配置保持不变。备份：',backup)
    print('请用现有 Homebridge UI/服务管理器重启一次；原家庭配对继续使用。')


def launch(runtime):
    metadata=installed(runtime)
    if metadata.get('mode')=='existing':raise RuntimeError('已有桥接模式：请从现有 Homebridge 启动，不要启动第二个桥接')
    if not (runtime/'homekit/storage/config.json').exists():raise RuntimeError('请先运行 setup 或 configure')
    with runtime_lock(runtime):
        args=[metadata['node'],str(runtime/'homekit/node_modules/homebridge/bin/homebridge.js'),
              '-U',str(runtime/'homekit/storage'),'-P',str(runtime/'homekit/homebridge-laifen-local'),
              '--strict-plugin-resolution']
        if sys.platform=='darwin':args=['/usr/bin/caffeinate','-i',*args]
        process=subprocess.Popen(args,cwd=runtime/'homekit')
        def stop(signum,frame):
            if process.poll() is None:process.send_signal(signal.SIGTERM)
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        return process.wait()


def service(runtime,action):
    if sys.platform!='darwin':raise RuntimeError('登录自启目前仅支持 macOS；其他系统请使用 run')
    if installed(runtime).get('mode')=='existing':raise RuntimeError('请管理现有 Homebridge 服务；本工具不启动第二个桥接')
    if not (runtime/'homekit/storage/config.json').exists():raise RuntimeError('请先配置设备')
    domain=f'gui/{os.getuid()}';target=domain+'/'+LABEL
    plist=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    if plist.exists():
        old=plistlib.loads(plist.read_bytes())
        if old.get('WorkingDirectory')!=str(runtime):
            raise RuntimeError('该后台服务已属于另一运行目录；请先停止旧服务')
    if action=='status':return call(['/bin/launchctl','print',target])
    if action=='stop':return call(['/bin/launchctl','bootout',target])
    # No starting a daemon on top of an existing foreground bridge.
    with runtime_lock(runtime):pass
    spec={'Label':LABEL,'ProgramArguments':[str(runtime/'.venv/bin/python'),str(runtime/'laifen.py'),
        '--runtime',str(runtime),'run'],'WorkingDirectory':str(runtime),
        'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,
        'StandardOutPath':str(runtime/'homekit/logs/homebridge.log'),
        'StandardErrorPath':str(runtime/'homekit/logs/homebridge-error.log'),
        'EnvironmentVariables':{'PATH':str(Path(read_json(runtime/'installation.json')['node']).parent)+':/usr/bin:/bin:/usr/sbin:/sbin',
                                'LANG':'en_US.UTF-8'}}
    plist.parent.mkdir(parents=True,exist_ok=True)
    plist.write_bytes(plistlib.dumps(spec));plist.chmod(0o644)
    call(['/bin/launchctl','bootstrap',domain,plist])
    print('登录自启已开启；关闭终端不影响运行。主动睡眠、关机或退出登录会暂停。')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME)
    commands=parser.add_subparsers(dest='action',required=True)
    for name in ('setup','install'):
        cmd=commands.add_parser(name);cmd.add_argument('--node');cmd.add_argument('--port',type=int,default=51826)
        cmd.add_argument('--mode',choices=['standalone','existing'],default=None if name=='setup' else 'standalone')
    commands.add_parser('scan')
    cmd=commands.add_parser('configure');cmd.add_argument('--name',required=True)
    cmd.add_argument('--identifier');cmd.add_argument('--port',type=int,default=51826)
    cmd=commands.add_parser('integrate');cmd.add_argument('--config',type=Path,required=True)
    cmd.add_argument('--plugin-prefix',type=Path,required=True)
    for name in ('run','start','stop','status','pair'):commands.add_parser(name)
    args=parser.parse_args(argv);runtime=args.runtime.expanduser().resolve()
    os.umask(0o077)
    if args.action=='setup':wizard(runtime,args.node,args.port,args.mode)
    elif args.action=='install':install(runtime,args.node,args.mode)
    elif args.action=='scan':
        with runtime_lock(runtime):
            for row in probe(runtime):print(json.dumps(row,ensure_ascii=False))
    elif args.action=='configure':configure(runtime,args.name,args.identifier,args.port)
    elif args.action=='integrate':integrate(runtime,args.config,args.plugin_prefix)
    elif args.action=='run':return launch(runtime)
    elif args.action=='pair':print('HomeKit 配对码：',read_json(runtime/'homekit/storage/config.json')['bridge']['pin'])
    else:service(runtime,args.action)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,OSError,subprocess.CalledProcessError) as error:
        print('错误：',error,file=sys.stderr)
        if isinstance(error,subprocess.CalledProcessError) and error.stderr:print(error.stderr,file=sys.stderr)
        raise SystemExit(1)
