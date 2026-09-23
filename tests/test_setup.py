import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('setup',Path(__file__).resolve().parents[1]/'laifen.py')
setup=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(setup)
DEVICE={'name':'LFFL01-P-ABCD','protocolAddress':'aa:bb:cc:dd:ee:ff'}


class SetupTests(unittest.TestCase):
    def test_existing_bridge_identity_plugins_and_accessories_are_preserved(self):
        old={'bridge':{'username':'TEST-IDENTITY','pin':'TEST-PIN'},'accessories':[{'name':'other'}],
             'platforms':[{'platform':'OtherPlatform','setting':123}]}
        merged=setup.merge_platform(old,{'platform':'LaifenLocal','device':DEVICE})
        self.assertEqual(merged['bridge'],old['bridge'])
        self.assertEqual(merged['accessories'],old['accessories'])
        self.assertEqual(merged['platforms'][0],old['platforms'][0])
        self.assertEqual(len(old['platforms']),1)
        self.assertEqual(len(setup.merge_platform(merged,merged['platforms'][1])['platforms']),2)
        wrong={**DEVICE,'protocolAddress':'01:02:03:04:05:06'}
        with self.assertRaises(ValueError):setup.merge_platform(merged,{'platform':'LaifenLocal','device':wrong})

    def test_standalone_identity_is_fresh_and_reconfigure_preserves_pairing(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory)
            setup.save_json(r/'installation.json',{'mode':'standalone'})
            config=setup.create_config(r,DEVICE,0)
            repeated=setup.create_config(r,{**DEVICE,'identifier':'NEW-HOST-UUID'},0)
            self.assertEqual(config['bridge'],repeated['bridge'])
            self.assertEqual(repeated['platforms'][0]['device']['identifier'],'NEW-HOST-UUID')
            wrong={**DEVICE,'protocolAddress':'01:02:03:04:05:06'}
            with self.assertRaises(RuntimeError):setup.create_config(r,wrong,0)
            self.assertEqual((r/'homekit/storage/config.json').stat().st_mode&0o777,0o600)

    def test_existing_mode_does_not_generate_a_second_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory)
            setup.save_json(r/'installation.json',{'mode':'existing','complete':True})
            setup.create_config(r,DEVICE)
            self.assertTrue((r/'platform.json').exists())
            self.assertFalse((r/'homekit/storage/config.json').exists())
            with self.assertRaisesRegex(RuntimeError,'第二个'):setup.launch(r)

    def test_runtime_lock_blocks_a_second_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Path(directory)
            with setup.runtime_lock(r):
                with self.assertRaises(RuntimeError):setup.runtime_lock(r)

    def test_probe_failure_leaves_configuration_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(setup,'probe',side_effect=RuntimeError('wrong device')):
                with self.assertRaises(RuntimeError):setup.configure(Path(directory),'LFFL01-P-ABCD')
            self.assertFalse((Path(directory)/'homekit/storage/config.json').exists())

    def test_plugin_allowlist_preserved_and_explicit_disable_not_overridden(self):
        original={'bridge':{},'plugins':['homebridge-other']}
        fragment={'platform':'LaifenLocal','device':DEVICE}
        result=setup.merge_platform(original,fragment)
        self.assertEqual(result['plugins'],['homebridge-other','homebridge-laifen-local'])
        original['disabledPlugins']=['homebridge-laifen-local']
        with self.assertRaises(ValueError):setup.merge_platform(original,fragment)
