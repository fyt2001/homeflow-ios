import copy
import unittest
from unittest.mock import patch
from homeflow import planner, core
import test_bridge as bridge
from test_bridge import MockPhone, icon


class PreferenceTests(unittest.TestCase):
    def state(self):
        return [[icon('phone')], [icon('home')], [icon(str(i)) for i in range(24)]]

    def test_hand_choice_changes_positions_without_changing_inventory(self):
        before = self.state()
        left, _ = planner.personalize(before, ['example.0'], hand='left', style='spread')
        right, _ = planner.personalize(before, ['example.0'], hand='right', style='spread')
        both, _ = planner.personalize(before, ['example.0'], hand='both', style='spread')
        self.assertNotEqual(left[2], right[2])
        self.assertLess(next(i for i,a in enumerate(left[2]) if core.app_id(a)=='example.0')%4, 2)
        self.assertGreaterEqual(next(i for i,a in enumerate(right[2]) if core.app_id(a)=='example.0')%4, 2)
        for after in (left,right,both):
            self.assertEqual(after[:2], before[:2])
            self.assertEqual(core.inventory(after), core.inventory(before))

    def test_styles_produce_different_folder_density(self):
        before = self.state()
        compact, _ = planner.personalize(before, ['example.'+str(i) for i in range(8)], style='compact')
        mixed, _ = planner.personalize(before, ['example.'+str(i) for i in range(8)], style='mixed')
        spread, _ = planner.personalize(before, style='spread')
        direct = lambda state: sum(core.item_kind(a)=='app' for p in state[2:] for a in p)
        self.assertEqual(direct(compact), 4)
        self.assertEqual(direct(mixed), 8)
        self.assertEqual(direct(spread), 24)
        for state in (compact,mixed,spread): self.assertEqual(core.inventory(state),core.inventory(before))

    def test_avoid_overrides_favorite_and_familiar_is_exact(self):
        before = self.state()
        after, _ = planner.personalize(before, ['example.0'], avoid=['example.0'], goal='calm')
        avoid = [a for p in after[2:] for a in p if a.get('displayName')=='少打开']
        self.assertEqual(len(avoid), 1)
        self.assertEqual(core.app_id(avoid[0]['iconLists'][0][0]), 'example.0')
        after, _ = planner.personalize(before, goal='familiar')
        self.assertEqual(after, before)

    def test_widget_intent_is_review_only_and_preserves_raw_payload(self):
        before = self.state()
        before[1].append({'elementType':'widget', 'payload':{'unknown':'opaque'}})
        for policy in ('preserve','review','replan'):
            after, meta = planner.personalize(before, widgets=policy)
            self.assertEqual(after[1], before[1])
            self.assertEqual(meta['review_only'], policy!='preserve')

    def test_large_folder_is_split_and_duplicate_icons_preserved(self):
        before = [[icon('phone')],[icon('home')]] + [[icon('same') for _ in range(24)] for _ in range(6)]
        after, _ = planner.personalize(before, style='compact')
        core.validate_layout(after, allow_widgets=True)
        self.assertEqual(core.inventory(before), core.inventory(after))


class WidgetWriteGuardTests(unittest.TestCase):
    setUp = bridge.BridgeTests.setUp
    post = bridge.BridgeTests.post
    @patch('homeflow.server.core.usbmux_devices', return_value=[{'ConnectionType':'USB','SerialNumber':'simulator'}])
    @patch('homeflow.server.core.Phone', MockPhone)
    def test_review_cannot_write_even_with_explicit_risk_acceptance(self, devices):
        MockPhone.state[1].append({'elementType':'widget','payload':{'unknown':'opaque'}})
        sid = self.post('capture', {}).json()['snapshot_id']
        result = self.post('plan', {'snapshot_id':sid,'widgets':'replan','hand':'left','style':'spread'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['review_only'])
        applied = self.post('apply', {'plan_id':result.json()['plan_id'],'accept_layout_risk':True})
        self.assertEqual(applied.status_code, 409)
        self.assertEqual(MockPhone.writes, 0)
