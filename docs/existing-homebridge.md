# 接入已有 Homebridge

目标是复用原 HomeKit 桥接身份和配对，不创建另一台服务器。原桥接负责发布配件，本插件通过子进程使用自己的 Python BLE 环境。

## 前提

- 在 Homebridge 实际运行的电脑和账号下安装；BLE 适配器和灯具必须可达。
- 已有 Node 22/24/26（推荐 24）、Homebridge 2.4.x、Python 3.11+。
- 同一个灯只由一个 BLE 进程管理。请停止原有同灯插件/桥接，退出厂商 App。
- 默认运行目录权限为 0700。如果现有 Homebridge 是专用服务账号，使用那个账号执行安装，而不是扩大个人目录权限。自定义 `--runtime` 可放在服务账号自己的数据目录。
- Docker/虚拟机还需正确配置蓝牙、D-Bus 和 mDNS 网络；本项目没有验证这些环境，不能保证仅装插件即可工作。

## 终端向导

```sh
python3 laifen.py setup --mode existing
```

向导只让你输入灯的名称，协议地址自动读取。在配置接入阶段，可以提供：

1. 当前 Homebridge 实际使用的 `config.json`。
2. 插件安装前缀，即其 `node_modules` 的父目录。确认 Homebridge 启动日志/安装配置实际搜索这个路径，不要猜。常见服务可能使用其用户数据目录；全局 npm 安装可查看 `npm root -g`，再去掉末尾 `/node_modules`。

两处路径都必须由 Homebridge 的运行账号访问。向导用 `npm install --prefix` 安装本地插件包；不会安装或升级第二份 Homebridge。配置先校验和备份，再添加/更新唯一一个 `LaifenLocal` 平台；桥接名、username、PIN、其他平台及配件保留。显式 `plugins` 白名单会追加本插件；若 `disabledPlugins` 禁用了本插件，则拒绝悄悄解除禁用。

可以稍后接入：

```sh
python3 laifen.py integrate \
  --config '/Homebridge实际数据目录/config.json' \
  --plugin-prefix '/实际插件前缀'
```

安装后，通过你原来的 Homebridge UI 或服务管理器重启一次。本工具不会自动重启现有桥接，以免影响其他配件的正在执行的操作。

## 手动接入

如果不希望工具修改既有配置，向导中跳过自动接入。运行目录会生成：

- `homebridge-laifen-local-1.1.0.tgz`：可安装的 Homebridge 插件包。
- `platform.json`：这一台电脑、这一盏灯的实际配置片段，含 Python、worker、日志绝对路径和自动获取的灯身份。不要公开。

以你的 Homebridge 运行账号，在其实际插件前缀安装：

```sh
npm install --prefix '/实际插件前缀' '/运行目录/homebridge-laifen-local-1.1.0.tgz'
```

备份 `config.json` 后，将 `platform.json` 的对象追加到 `platforms` 数组，不要用它替换整个 config.json。若启用了插件白名单，将 `homebridge-laifen-local` 加入 `plugins`；然后重启现有 Homebridge。通常无需重新配对桥接，新配件会加入现有家庭。

该插件包不包含 Python 解释器；终端向导创建的独立运行目录必须保留。无需保留下载的源码目录，也不需要 Codex。

## 回退

停止现有 Homebridge，恢复 `config.json.laifen-backup-*` 中对应备份；只卸载本插件，再重启。不要删除现有 `persist`、`accessories` 或其他桥接数据库。新添加配件上的自动化可能需要整理；原桥接身份不会由安装器改变。
