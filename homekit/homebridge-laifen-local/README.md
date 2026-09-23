# homebridge-laifen-local

Local BLE support for the observed Laifen LFFL01-P floor-lamp protocol.

This plugin needs the standalone Python BLE companion. Run the terminal wizard
with `python3 laifen.py setup --mode existing` from the full project first.
It generates the device configuration and private companion paths; installing
this plugin tarball alone does not install the Python runtime.

Full installation, compatibility limits, protocol notes and MIT license:
https://github.com/BodenQ/laifen-homekit
