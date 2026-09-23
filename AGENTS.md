# Agent project brief

Local BLE → Homebridge → Apple Home/Siri for the observed Laifen **LFFL01-P** protocol. Read `README.md` for the user workflow; use linked docs only when needed. No Codex runtime or vendor cloud dependency for routine control.

## Map

- `laifen.py`: standard-library CLI; setup, independent dependency install, device selection, private configuration, macOS LaunchAgent, existing-Homebridge integration.
- `tools/lamp_discovery.py`, `probe.py`: scan by broadcast name, reject ambiguity, query/validate identity. Never ask a user to type a MAC address; macOS identifiers may be host-local UUIDs.
- `tools/lamp_protocol.py`: frame encoding/decoding and known status layout.
- `tools/lamp_session.py`: BLE connection, notification routing, acknowledgements.
- `tools/lamp_worker.py`: serialized stateful controller; JSON lines over stdin/stdout to the plugin.
- `homekit/homebridge-laifen-local/index.js`: Homebridge platform, HomeKit services, Apple adaptive lighting.
- `docs/protocol.md`: observed commands; `docs/compatibility.md`: hardware evidence/limits; `docs/existing-homebridge.md`: integration; `docs/usage.md`: maintenance.

## Behavior to preserve

- Four user controls: master 台灯, upper 上灯, lower 下灯, native 自动亮度. Master means both channels; never infer master state only from the lower channel.
- Both channels have independent power/brightness. Color temperature is shared (2900–5700 K); only lower exposes ColorTemperature/Apple adaptive lighting.
- From all-off, turn master on before selecting channels. The unwanted channel can briefly flash. From partial-on, enable the missing channel directly; another master-on alone does not restore it.
- Native automatic-brightness preference persists, pauses when either channel is off, resumes only when both are on. Manual brightness clears preference; adaptive color-temperature updates must not wake an off lamp.
- ACK/state can disagree with physical output: query settled state and obtain physical observation for new hardware claims. Do not blindly retry a control command on a stale ACK.

## Boundaries

- Tested physically with one LFFL01-P on Apple Silicon macOS. Same-model firmware/another Mac unverified; Linux experimental; Windows installer unsupported. CI does not prove BLE hardware compatibility.
- Device name + protocol address are discovered and validated, never hardcoded. Only a status query is allowed before identity validation. No guessed writes, DFU, firmware updates, or seat-mode guardian.
- No application-level key/authentication observed; this does not establish unencrypted BLE links or universal compatibility.
- One BLE owner per lamp. Do not stop a user's bridge or send physical-control commands merely to run offline tests. Request observation when real-hardware testing is authorized and needed.
- Runtime is separate from source: macOS `~/Library/Application Support/LaifenHomeKitBridge`, Linux `~/.local/share/laifen-homekit`. Preserve pairing/storage/preferences. Never commit runtime data, real identifiers, PINs, raw captures or personal logs.
- Existing mode installs a Python companion and plugin, backs up/merges config, preserves bridge identity/other accessories; never launches a second bridge. Restart through the existing manager.

## First-use troubleshooting

The tested lamp is a **Laifen L1 Pro** (broadcast family `LFFL01-P-XXXX`) previously used with the vendor App. Direct control with the App closed is verified; out-of-box use without prior initialization/pairing is not. Do not claim that initialization, bonding or link encryption is absent.

Try the terminal wizard directly first. If discovery, connection or status precheck fails, check power, distance, permissions and competing BLE owners. If unresolved, guide the user to pause this lamp's bridge/debug process, personally log into the official Laifen App, follow its add/connect flow and verify physical power control. Never request passwords or verification codes. Then have the user fully quit the App and retry the wizard. This is an optional diagnostic fallback, not a proven prerequisite or guaranteed fix. If it still fails, collect sanitized errors; do not guess writes or repeatedly factory-reset the lamp. Keep vendor initialization distinct from Apple Home bridge pairing.

## Validation and packaging

Use independent Python 3.11+ and Node 24 (supported majors: 22/24/26):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tools -p 'test_lamp_*.py'
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd homekit
npm ci
npm test
```

Run checks relevant to changes. Tests cover protocol vectors, discovery, controller behavior, config preservation and HAP. No live lamp needed. When releasing, keep `laifen.py` VERSION, both package versions, lockfile root version and plugin startup log aligned; verify the npm archive includes LICENSE. Exclude venv/node_modules/runtime data from source releases.

## License

v1.1.0+ uses **PolyForm-Noncommercial-1.0.0** with Required Notice attribution to **BodenQ** and the repository URL. Keep root/plugin LICENSE identical and retain notices in distributions. Do not label this OSI open source or relicense dependencies. v1.0.0 was MIT; do not imply its prior grants were revoked. Details: `docs/license.md`.
