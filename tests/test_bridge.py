import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from homeflow import core, server


def icon(name):
    return {'displayIdentifier': 'example.' + name, 'displayName': name}


class MockPhone:
    state = []
    writes = 0
    identity = {'UniqueDeviceID': 'simulator', 'ProductVersion': '26.6.2'}
    def __init__(self, serial): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def read(self): return copy.deepcopy(self.state)
    async def write(self, state):
        type(self).state = copy.deepcopy(state)
        type(self).writes += 1


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = TestClient(server.create_app('secret', Path(self.temp.name), ['https://fyt2001.top']))
        self.headers = {'Authorization': 'Bearer secret'}
        MockPhone.state = [[icon('phone')], [icon('home')], [icon('first'), icon('second')]]
        MockPhone.writes = 0

    def post(self, path, body):
        return self.client.post('/api/' + path, json=body, headers=self.headers)

    def test_origin_token_host_and_json_guards(self):
        self.assertEqual(self.client.get('/api/health').status_code, 401)
        self.assertEqual(self.client.get('/api/health', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/health', headers={**self.headers,'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/health', headers={**self.headers,'Host':'evil.example'}).status_code, 400)
        self.assertEqual(self.client.post('/api/capture', content='{}', headers=self.headers).status_code, 415)
        r = self.client.options('/api/health', headers={'Origin':'https://fyt2001.top','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers['access-control-allow-origin'], 'https://fyt2001.top')

    @patch('homeflow.server.core.usbmux_devices', return_value=[{'ConnectionType':'USB','SerialNumber':'simulator'}])
    @patch('homeflow.server.core.Phone', MockPhone)
    def test_capture_plan_explicit_write_restore_and_replay(self, devices):
        before = copy.deepcopy(MockPhone.state)
        captured = self.post('capture', {}).json()
        self.assertNotIn('identity', captured)
        self.assertEqual(MockPhone.writes, 0)
        self.assertTrue(list(Path(self.temp.name).rglob('snapshot.json')))
        plan = self.post('plan', {'snapshot_id':captured['snapshot_id'],'favorites':['example.second']}).json()
        self.assertEqual(MockPhone.writes, 0)
        self.assertEqual(self.post('apply', {'plan_id':plan['plan_id']}).status_code, 400)
        applied = self.post('apply', {'plan_id':plan['plan_id'],'accept_layout_risk':True})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()['status'], 'verified_legacy_layout')
        self.assertEqual(MockPhone.state[:2], before[:2])
        self.assertEqual(core.inventory(MockPhone.state), core.inventory(before))
        self.assertEqual(self.post('apply', {'plan_id':plan['plan_id'],'accept_layout_risk':True}).status_code, 404)
        restored = self.post('restore', {'transaction_id':applied.json()['transaction_id'],'accept_layout_risk':True})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(MockPhone.state, before)

    @patch('homeflow.server.core.usbmux_devices', return_value=[{'ConnectionType':'USB','SerialNumber':'simulator'}])
    @patch('homeflow.server.core.Phone', MockPhone)
    def test_reject_changed_phone_without_write(self, devices):
        sid = self.post('capture', {}).json()['snapshot_id']
        pid = self.post('plan', {'snapshot_id':sid}).json()['plan_id']
        MockPhone.state[2].append(icon('new'))
        result = self.post('apply', {'plan_id':pid,'accept_layout_risk':True})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(MockPhone.writes, 0)
