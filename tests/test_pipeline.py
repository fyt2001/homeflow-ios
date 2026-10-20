import asyncio
import argparse
import copy
import json
import tempfile
from datetime import datetime
import unittest
from pathlib import Path
from unittest.mock import patch

from homeflow import core as o


def icon(identifier):
    return {'displayIdentifier': identifier}


def layout():
    return [[icon('phone')], [icon('browser')],
            [icon('com.tencent.xin'), icon('com.tencent.mqq'), icon('unknown')]]


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    async def test_apply_and_restore_round_trip(self):
        before = layout()
        phone = o.Simulator(before)
        target, changes = o.build_plan(before)
        self.assertEqual(len(changes), 1)
        applied, path = await o.verified_write(phone, before, target, self.directory)
        self.assertEqual(applied['status'], 'verified_legacy_layout')
        stored = json.loads(path.read_text(encoding='utf-8'))
        restored, _ = await o.verified_write(phone, o.unpack(stored['target']),
                                             o.unpack(stored['before']), self.directory)
        self.assertEqual(restored['status'], 'verified_legacy_layout')
        self.assertEqual(await phone.read(), before)

    async def test_changed_phone_is_not_overwritten(self):
        before = layout()
        target, _ = o.build_plan(before)
        changed = copy.deepcopy(before)
        changed[2].append(icon('newly.installed'))
        phone = o.Simulator(changed)
        with self.assertRaises(o.Blocked):
            await o.verified_write(phone, before, target, self.directory)
        self.assertEqual(await phone.read(), changed)
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_snapshot_disk_failure_prevents_write(self):
        phone = o.Simulator(layout())
        target, _ = o.build_plan(layout())
        with patch.object(o, 'save_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                await o.verified_write(phone, layout(), target, self.directory)
        self.assertEqual(await phone.read(), layout())

    async def test_ambiguous_disconnect_records_unknown_without_retry(self):
        class Disconnect(o.Simulator):
            async def write(self, state):
                self.state = copy.deepcopy(state)
                raise ConnectionError('USB disconnected after send')
        phone = Disconnect(layout())
        target, _ = o.build_plan(layout())
        with self.assertRaises(ConnectionError):
            await o.verified_write(phone, layout(), target, self.directory)
        records = list(self.directory.glob('transaction-*.json'))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())['status'], 'unknown_after_write')

    async def test_successful_send_is_not_successful_apply(self):
        class IgnoresWrite(o.Simulator):
            async def write(self, state):
                pass
        phone = IgnoresWrite(layout())
        target, _ = o.build_plan(layout())
        with self.assertRaises(o.Blocked):
            await o.verified_write(phone, layout(), target, self.directory)
        journal = json.loads(next(self.directory.glob('transaction-*')).read_text())
        self.assertEqual(journal['status'], 'mismatch_no_auto_restore')

    async def test_cancelled_write_has_unknown_status(self):
        class Cancels(o.Simulator):
            async def write(self, state):
                raise asyncio.CancelledError()
        target, _ = o.build_plan(layout())
        with self.assertRaises(asyncio.CancelledError):
            await o.verified_write(Cancels(layout()), layout(), target, self.directory)
        journal = json.loads(next(self.directory.glob('transaction-*')).read_text())
        self.assertEqual(journal['status'], 'unknown_after_write')

    async def test_modern_ios_requires_specific_risk_acknowledgment(self):
        phone = o.Simulator(layout())
        phone.identity['ProductVersion'] = '26.0'
        target, _ = o.build_plan(layout())
        with self.assertRaises(o.Blocked):
            await o.verified_write(phone, layout(), target, self.directory)
        self.assertEqual(await phone.read(), layout())

    async def test_prepare_entrypoint_creates_snapshot_plan_and_preview_without_writes(self):
        class ReadOnlyPhone(o.Simulator):
            def __init__(self, serial):
                super().__init__(layout())
                self.lockdown = object()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def write(self, state):
                raise AssertionError('prepare must never write')
        args = argparse.Namespace(command='prepare', output=str(self.directory),
                                  serial=None, open_preview=False)
        with patch.object(o, 'Phone', ReadOnlyPhone), patch.object(
                o, 'usbmux_devices', return_value=[{'ConnectionType': 'USB', 'SerialNumber': 'test'}]):
            self.assertEqual(await o.main(args), 0)
        self.assertEqual(len(list(self.directory.glob('snapshot-*.json'))), 1)
        self.assertEqual(len(list(self.directory.glob('prepared-*/plan.json'))), 1)
        self.assertEqual(len(list(self.directory.glob('prepared-*/preview.html'))), 1)


class LayoutTests(unittest.TestCase):
    def test_explicit_dock_policy_never_relaxes_widget_homepage_protection(self):
        before = layout()
        before[1].append({'iconType': 'custom', 'gridSize': 'small'})
        after = copy.deepcopy(before)
        after[0][0], after[2][0] = after[2][0], after[0][0]
        with self.assertRaises(o.Blocked):
            o.validate_transition(before, after)
        o.validate_transition(before, after, allow_dock=True)
        after[1][1]['gridSize'] = 'large'
        with self.assertRaises(o.Blocked):
            o.validate_transition(before, after, allow_dock=True)

    def test_ios_appended_icons_are_reported_as_mismatch_not_success(self):
        expected = layout()
        observed = copy.deepcopy(expected)
        observed[2].append(icon('system.appended'))
        difference = o.compare_layout(expected, observed)
        self.assertFalse(difference['exact_equal'])
        self.assertEqual(difference['added_icon_instances'], {'system.appended': 1})
        self.assertEqual(difference['missing_icon_instances'], {})
        self.assertTrue(difference['dock_and_homepage_equal'])

    def test_snapshot_preserves_fractional_icon_timestamps(self):
        state = layout()
        state[0][0]['iconModDate'] = datetime(2026, 9, 13, 22, 10, 15, 386067)
        restored = o.unpack(o.envelope(state))
        self.assertEqual(state, restored)
        changed = copy.deepcopy(state)
        changed[0][0]['iconModDate'] = changed[0][0]['iconModDate'].replace(microsecond=0)
        self.assertNotEqual(o.digest(state), o.digest(changed))

    def test_widget_stack_is_not_a_folder_or_app_inventory(self):
        state = layout()
        state[1].append({'iconType': 'custom', 'elements': [], 'iconLists': [[
            {'elementType': 'widget', 'displayIdentifier': 'widget-instance'}]], 'gridSize': 'small'})
        self.assertEqual(o.layout_summary(state)['counts']['widget_stack'], 1)
        self.assertNotIn('widget-instance', o.inventory(state))
        planned, _ = o.build_plan(state)
        self.assertEqual(state[1], planned[1])
        with self.assertRaisesRegex(o.Blocked, '组件'):
            o.validate_layout(planned)

    def test_merge_folders_preserves_widget_page_and_app_metadata(self):
        state = layout()
        state[1].append({'iconType': 'custom', 'elements': [], 'gridSize': 'small'})
        a = icon('a'); a['bundleVersion'] = '1'; a['iconModDate'] = 'raw-value'
        state[2] = [
            {'listType': 'folder', 'displayName': '效率', 'iconLists': [[a]]},
            icon('outside'),
            {'listType': 'folder', 'displayName': '效率', 'iconLists': [[icon('b')]]}]
        target, changes = o.build_plan(state)
        self.assertEqual(target[:2], state[:2])
        self.assertEqual(o.inventory(target), o.inventory(state))
        self.assertEqual(target[2][0]['iconLists'], [[a, icon('b')]])
        self.assertEqual(changes[0]['before_folders'], 2)
        self.assertEqual(len(target[2]), 2)
        again, changes = o.build_plan(target)
        self.assertEqual(again, target)
        self.assertEqual(changes, [])
        changed = copy.deepcopy(target)
        changed[1][-1]['gridSize'] = 'large'
        with self.assertRaises(o.Blocked):
            o.validate_transition(state, changed)

    def test_duplicate_icons_and_raw_metadata_preserved(self):
        before = layout()
        before[2].append(icon('com.tencent.xin'))
        target, _ = o.build_plan(before)
        self.assertEqual(o.inventory(before), o.inventory(target))
        self.assertEqual(target[:2], before[:2])
        self.assertEqual(before[2][-1], icon('com.tencent.xin'))

    def test_unknown_apps_and_existing_folders_survive(self):
        before = layout()
        folder = {'listType': 'folder', 'displayName': '自定义', 'iconLists': [[icon('custom.app')]]}
        before[2].append(folder)
        target, _ = o.build_plan(before)
        self.assertIn(folder, target[2])
        self.assertIn(icon('unknown'), target[2])

    def test_widget_hole_and_position_block_planning(self):
        for item in [None, {}, {'widget': 'clock'},
                     {'displayIdentifier': 'app', 'gridPosition': {'row': 3, 'column': 2}}]:
            before = layout()
            before[2].append(item)
            with self.subTest(item=item), self.assertRaises(o.Blocked):
                o.build_plan(before)

    def test_tampered_snapshot_is_rejected(self):
        snap = o.envelope(layout())
        snap['sha256'] = '0' * 64
        with self.assertRaises(o.Blocked):
            o.unpack(snap)

    def test_wrong_device_or_changed_os_is_rejected(self):
        expected = o.Simulator(layout()).identity
        for key in ['UniqueDeviceID', 'ProductVersion', 'BuildVersion']:
            actual = dict(expected)
            actual[key] += '-changed'
            with self.subTest(key=key), self.assertRaises(o.Blocked):
                o.identity_check(expected, actual)

    def test_modified_dock_or_dropped_app_is_rejected(self):
        before = layout()
        target, _ = o.build_plan(before)
        target[0] = []
        with self.assertRaises(o.Blocked):
            o.validate_transition(before, target)
        target, _ = o.build_plan(before)
        target[2].pop()
        with self.assertRaises(o.Blocked):
            o.validate_transition(before, target)


if __name__ == '__main__':
    unittest.main()
