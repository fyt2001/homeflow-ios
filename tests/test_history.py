import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from homeflow import core, server
import test_bridge as bridge
from test_bridge import MockPhone, icon


@patch('homeflow.server.core.usbmux_devices', return_value=[{'ConnectionType':'USB','SerialNumber':'simulator'}])
@patch('homeflow.server.core.Phone', MockPhone)
class HistoryTests(unittest.TestCase):
    setUp = bridge.BridgeTests.setUp
    post = bridge.BridgeTests.post

    def apply(self, **options):
        sid = self.post('capture', {}).json()['snapshot_id']
        pid = self.post('plan', {'snapshot_id':sid, **options}).json()['plan_id']
        result = self.post('apply', {'plan_id':pid,'accept_layout_risk':True})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['transaction_id']

    def records(self):
        result = self.client.get('/api/history', headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['records']

    def restart(self):
        self.client = TestClient(server.create_app('new-secret', Path(self.temp.name), ['https://fyt2001.top']))
        self.headers = {'Authorization':'Bearer new-secret'}

    def test_restart_preview_confirm_restore_and_replay_guard(self, devices):
        before = copy.deepcopy(MockPhone.state)
        tid = self.apply()
        writes = MockPhone.writes
        self.restart()
        self.assertTrue(self.records()[0]['can_restore'])
        preview = self.post('restore-preview', {'transaction_id':tid})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(MockPhone.writes, writes)
        self.assertEqual(self.post('restore', {'transaction_id':tid}).status_code, 400)
        self.assertEqual(self.post('restore', {'transaction_id':tid,'accept_layout_risk':True}).status_code, 200)
        self.assertEqual(MockPhone.state, before)
        self.restart()
        self.assertEqual(self.records()[0]['status'], 'undone')
        self.assertEqual(self.post('restore', {'transaction_id':tid,'accept_layout_risk':True}).status_code, 409)

    def test_manual_change_after_preview_never_overwritten(self, devices):
        tid = self.apply()
        self.assertEqual(self.post('restore-preview', {'transaction_id':tid}).status_code, 200)
        MockPhone.state[2].append(icon('added-by-user'))
        current = copy.deepcopy(MockPhone.state)
        writes = MockPhone.writes
        self.assertFalse(self.records()[0]['can_restore'])
        self.assertEqual(self.post('restore', {'transaction_id':tid,'accept_layout_risk':True}).status_code, 409)
        self.assertEqual(MockPhone.state, current)
        self.assertEqual(MockPhone.writes, writes)

    def test_can_undo_multiple_steps_in_order(self, devices):
        before = copy.deepcopy(MockPhone.state)
        first = self.apply()
        second = self.apply(style='spread')
        by_id = {r['transaction_id']:r for r in self.records()}
        self.assertFalse(by_id[first]['can_restore'])
        self.assertTrue(by_id[second]['can_restore'])
        self.assertEqual(self.post('restore', {'transaction_id':second,'accept_layout_risk':True}).status_code, 200)
        self.assertTrue(next(r for r in self.records() if r['transaction_id']==first)['can_restore'])
        self.assertEqual(self.post('restore', {'transaction_id':first,'accept_layout_risk':True}).status_code, 200)
        self.assertEqual(MockPhone.state, before)

    def test_other_device_and_changed_system_blocked(self, devices):
        tid = self.apply()
        with patch.object(MockPhone, 'identity', {'UniqueDeviceID':'other','ProductVersion':'26.6.2'}):
            self.assertEqual(self.records(), [])
            self.assertEqual(self.post('restore-preview', {'transaction_id':tid}).status_code, 409)
        with patch.object(MockPhone, 'identity', {'UniqueDeviceID':'simulator','ProductVersion':'27.0'}):
            self.assertFalse(self.records()[0]['can_restore'])
            self.assertEqual(self.post('restore', {'transaction_id':tid,'accept_layout_risk':True}).status_code, 409)

    def test_incomplete_undo_journal_survives_restart_and_blocks_retry(self, devices):
        tid = self.apply()
        records = list(Path(self.temp.name).glob('*/transaction-*.json'))
        source = json.loads(records[0].read_text(encoding='utf-8'))
        pending = {'before':source['target'], 'target':source['before'], 'identity':source['identity'],
                   'status':'unknown_after_write', 'context':{'operation':'undo','undo_of':tid}}
        core.save_json(Path(self.temp.name)/'restore'/'transaction-11111111111111111111111111111111.json',pending)
        self.restart()
        self.assertEqual(self.records()[0]['status'], 'undo_needs_attention')
        self.assertEqual(self.post('restore', {'transaction_id':tid,'accept_layout_risk':True}).status_code, 409)

    def test_corrupt_journal_skipped_and_unknown_id_cannot_restore(self, devices):
        core.save_json(Path(self.temp.name)/'broken'/'transaction-11111111111111111111111111111111.json',{'status':'verified_legacy_layout'})
        self.assertEqual(self.records(), [])
        self.assertEqual(self.post('restore', {'transaction_id':'11111111111111111111111111111111','accept_layout_risk':True}).status_code, 409)
