# 徕芬大路灯 · 本地 HomeKit 桥接

Local BLE → HomeKit bridge for the observed **Laifen LFFL01-P** protocol. Runs on your own computer, without Codex, the vendor cloud, or an active vendor App session. Includes a terminal setup wizard and an existing-Homebridge integration mode.

> 当前只在**一台 LFFL01-P、Mac mini M4 / macOS 27、Node 24 / Python 3.12**上完成实灯验证。设备地址已参数化，不等于已验证所有同型号固件。其他型号不受支持；Linux 仅为实验性前台运行路径，Windows 暂无安装/常驻支持。请先阅读[兼容性说明](docs/compatibility.md)。

## 提供什么

“家庭”中可展示四个控制项：

| 名称 | 控制范围 |
| --- | --- |
| 台灯 | 整灯总开关；只说“打开/关闭台灯”时控制两路 |
| 上灯 | 上灯独立开关、亮度 |
| 下灯 | 下灯独立开关、亮度，以及两路共用色温 |
| 自动亮度 | 灯内光传感器调光的持久偏好 |

- 色温 2900–5700 K，实际影响上下两路。Apple 自适应照明入口在下灯，以其亮度计算共同色温。
- 任一路关闭时，先暂停灯内自动亮度；两路均打开后才恢复。手动调亮度会退出自动亮度，需重新打开该开关。
- “自动亮度”开关表示你的选择：关灯时仍可显示开，但传感器调光已暂停，不会据此开灯。
- 入座感应、日光同行、定时和 DFU 不作为控制功能开放。本项目不提供“落座守护”。

## 开始：终端向导

准备一台常开、在灯具蓝牙范围内的 Mac；iPhone 和 Mac 能在同一局域网内发现 HomeKit 服务。安装独立的 [Node.js 24 LTS](https://nodejs.org/en/download) 和 [Python 3.12](https://www.python.org/downloads/)，也可使用自己安装的 uv/Python。确认 `node --version`、`python3 --version` 可用（Python 最低 3.11）。不要使用 Codex 内置运行时。

下载本仓库，或：

```sh
git clone https://github.com/BodenQ/laifen-homekit.git
cd laifen-homekit
python3 laifen.py setup
```

向导会：

1. 让你选择“已有 Homebridge”或“独立安装”。
2. 在私人运行目录安装 Python 依赖；独立模式再安装锁定版本的 Homebridge。
3. 提示从手机后台退出徕芬 App，然后输入 App 里的**设备名**，例如 `LFFL01-P-ABCD`。也可回车扫描候选。
4. 按完整名称搜索蓝牙广播；有多个同名候选时让你选择，不按信号强弱猜测。
5. 检查 FF01/FF02 服务、查询状态、验证名称与已知状态格式，自动读出协议地址。不要求手填地址。
6. 生成本机配置。每个独立桥接随机生成自己的 HomeKit 身份与配对码。

预检只有状态查询，没有开关、亮度、色温或固件写入。预检通过仅说明状态格式符合已知协议，新灯的物理控制仍需验证。

macOS 首次使用时，按系统提示允许终端/实际 Python 进程使用蓝牙和本地网络。没有这些权限，命令可能失败；向导不会修改系统隐私权限。

### 没有 Homebridge：独立运行

向导配置完后：

```sh
python3 laifen.py run
```

按终端二维码/配对码在“家庭”添加桥接。也可以在另一个终端执行 `python3 laifen.py pair` 查看本机配对码。首次添加后，把台灯、上下灯、自动亮度放到同一房间；上下灯若合并为一个版块，在配件设置选择“作为单独版块分开显示”。

保留总开关名“台灯”，分控名“上灯”“下灯”，自动亮度名“自动亮度”。若旧配件容器也叫“台灯”，将其改名“灯具分控”，避免 Siri 同名歧义。不要把自动亮度编入整灯开关群组。

先实测整灯关/开、单路关/开、亮度和色温。正常后按 Ctrl+C 停止前台，再开启 macOS 登录自启：

```sh
python3 laifen.py start
python3 laifen.py status
# 停止后台服务：
python3 laifen.py stop
```

之后关闭 Codex 或终端均不影响运行。macOS 登录当前账户后自启；不是登录前的系统服务。运行时防止空闲睡眠，主动睡眠、退出登录、关机仍会中断控制。

### 已有 Homebridge：复用现有桥接

```sh
python3 laifen.py setup --mode existing
```

向导准备 BLE 工作进程、插件压缩包和 `platform.json`，**不创建第二个桥接、不生成新配对码、不启动新 Homebridge**。你可以提供现有 `config.json` 和插件安装前缀，由向导安装插件并备份、合并配置；也可跳过自动合并，按[已有 Homebridge 接入说明](docs/existing-homebridge.md)手动操作。之后用原有 Homebridge 管理器重启，继续使用原配对。

**请在现有 Homebridge 的运行主机、运行账号下执行。** macOS 同账号已覆盖安装设计；Docker、不同服务用户、Linux 蓝牙转发等部署仍需自行配置权限，尚未进行实机认证。插件要求 Homebridge 2.4.x；不会擅自升级你的现有 Homebridge。

## 高级命令

不使用交互向导也可以：

```sh
python3 laifen.py install
python3 laifen.py scan
python3 laifen.py configure --name LFFL01-P-ABCD
python3 laifen.py run
```

多个同名候选时用扫描结果的本机标识：`configure --name LFFL01-P-ABCD --identifier '<扫描结果>'`。Mac 的标识通常是 UUID，不是 App 里的 MAC。换电脑后重新扫描配置，不复制旧电脑的 UUID。

全局 `--runtime` 要放在子命令前。默认运行目录：

- macOS：`~/Library/Application Support/LaifenHomeKitBridge/`
- Linux（实验）：`~/.local/share/laifen-homekit/`，仅 `run` 前台运行，未提供系统服务安装。

```sh
python3 laifen.py --runtime '/自定义/私人目录' setup
```

同一灯不要同时运行两个桥接、调试脚本或徕芬 App。安装更新前先停止桥接，再从新下载源码执行相同模式的 `install`，原运行目录中的配对数据库、配置和偏好会保留。

## 换灯、换电脑与加密

已验证命令帧没有观察到应用层加密、设备专属密钥或登录鉴权；尾部 XOR 是校验，序号不是密钥。程序不需要厂商账号。**这不等于证明蓝牙链路未加密，也不保证其他型号/固件使用相同协议。**

另一盏同系列灯需重新运行向导，通过名称、服务和状态预检后谨慎验证。另一台 Mac 需自行安装运行时和授权蓝牙，本机标识重新发现。新桥接默认需要重新加入“家庭”；迁移旧 HomeKit 配对要先停旧桥接，再私密迁移整个 storage，并重新配置本机路径/标识。参见[兼容性与迁移](docs/compatibility.md)、[协议记录](docs/protocol.md)。

## 开发与测试

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tools -p 'test_lamp_*.py'
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd homekit
npm ci
npm test
```

测试包含脱敏的真实 App 命令向量、合成的多设备状态、歧义发现拒绝、现有桥接配置保留、总开关/分控/自动亮度行为和 Apple 自适应照明。离线测试不代替另一台实体灯的兼容验证。

仓库不含实际设备地址、配对码、HomeKit 数据库、原始蓝牙抓包或个人日志。运行目录和日志包含私人信息，提交 issue 时请先脱敏。MIT License；本项目为非官方社区研究，不是徕芬或 Apple 官方产品。
