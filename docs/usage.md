# 维护与高级使用

所有命令在下载的源码目录执行。默认运行目录：macOS `~/Library/Application Support/LaifenHomeKitBridge`；Linux `~/.local/share/laifen-homekit`（仅实验性前台运行）。运行目录必须保留；源码目录可删除，但维护时需要重新下载同版本源码。

## 自定义目录 / 非交互连接

全局 `--runtime` 放在子命令前，并在后续命令使用同一路径：

```sh
python3 laifen.py --runtime '/自己的/私人目录' setup
python3 laifen.py --runtime '/自己的/私人目录' run
```

默认目录的非交互步骤：

```sh
python3 laifen.py install
python3 laifen.py scan
python3 laifen.py configure --name LFFL01-P-ABCD
python3 laifen.py run
```

有多个同名候选时，使用扫描结果的本机标识：`configure --name LFFL01-P-ABCD --identifier '<扫描结果>'`。不需要 App 的 MAC；换电脑应重新扫描。本版每个桥接仅支持一个 LaifenLocal 实例，macOS 自启服务也只有一个固定服务标签。

## 更新

先私密备份整个运行目录，再停止桥接；前台按 Ctrl+C，独立后台执行 `python3 laifen.py stop`，已有 Homebridge 用原管理器停止。下载新版本源码，进入新目录：

```sh
# 独立模式：
python3 laifen.py install --mode standalone
python3 laifen.py start

# 已有 Homebridge 模式：
python3 laifen.py install --mode existing
python3 laifen.py integrate --config '/原config.json路径' --plugin-prefix '/原插件前缀'
# 然后通过原管理器启动 Homebridge
```

不要切换原运行目录的模式。安装保留配置、配对数据库和自动亮度偏好；已有模式还需 integrate 安装新插件。不要重新生成配对或删除 storage。现有依赖按锁定版本安装，安装过程需联网。

## 停用与卸载

`python3 laifen.py stop` 只停止当前独立服务，**不删除登录自启文件**。彻底停用时，停止后在 Finder「前往文件夹」打开 `~/Library/LaunchAgents/`，将本项目的 `io.github.laifen-homekit.plist` 移到废纸篓。保留运行目录即可日后恢复；确需卸载时，先从家庭移除对应桥接并备份，再删除本项目运行目录。不要删除其他桥接的数据。

已有 Homebridge：停止原服务，恢复合适的 `config.json.laifen-backup-*` 或移除 LaifenLocal 平台配置，只卸载 `homebridge-laifen-local`，再启动原服务。若备份后更改过其他配件配置，应手动合并回退，避免覆盖后续修改。

## 日志与求助

独立后台日志位于运行目录 `homekit/logs/homebridge.log` 和 `homebridge-error.log`；BLE 日志为 `ble.jsonl`。已有模式同时查看原 Homebridge 日志。运行目录、`platform.json`、BLE 日志和 HomeKit storage 可能包含设备身份或密钥，请勿整包公开。

新灯实测、换电脑与配对迁移见[兼容性说明](compatibility.md)。
