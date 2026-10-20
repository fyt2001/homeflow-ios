"""Local iPhone layout feasibility tool. Read-only unless apply/restore is explicit.

Device communication is delegated to pymobiledevice3, not Apple's private DLLs.
The simulator exercises application logic, not iOS or its protocol implementation.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import html
import json
import os
import plistlib
import socket
import struct
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


class Blocked(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(plistlib.dumps(value, fmt=plistlib.FMT_BINARY, sort_keys=True)).hexdigest()


def save_json(path, value):
    """Persist before writing the phone. Atomic replacement on the same volume."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def envelope(state):
    return {'plist_base64': base64.b64encode(plistlib.dumps(state, fmt=plistlib.FMT_BINARY)).decode(),
            'sha256': digest(state), 'hash_format': 'binary-v1'}


def unpack(value):
    state = plistlib.loads(base64.b64decode(value['plist_base64'], validate=True))
    actual_hash = (digest(state) if value.get('hash_format') == 'binary-v1' else
                   hashlib.sha256(plistlib.dumps(state, sort_keys=True)).hexdigest())
    if actual_hash != value['sha256']:
        raise Blocked('快照或方案校验失败，拒绝写入。')
    return state


def usbmux_devices():
    """Small dependency-free bootstrap check; pairing and services use upstream."""
    def read_exact(sock, size):
        result = b''
        while len(result) < size:
            part = sock.recv(size - len(result))
            if not part:
                raise Blocked('苹果连接服务提前关闭了连接。')
            result += part
        return result

    with socket.create_connection(('127.0.0.1', 27015), timeout=5) as sock:
        body = plistlib.dumps({'MessageType': 'ListDevices',
                              'ClientVersionString': 'OrganizerProbe/0.1',
                              'ProgName': 'OrganizerProbe', 'kLibUSBMuxVersion': 3})
        sock.sendall(struct.pack('<IIII', len(body) + 16, 1, 8, 1) + body)
        length, version, kind, tag = struct.unpack('<IIII', read_exact(sock, 16))
        if not 16 <= length <= 8 * 1024 * 1024 or (version, kind, tag) != (1, 8, 1):
            raise Blocked('苹果连接服务返回了不支持的消息。')
        response = plistlib.loads(read_exact(sock, length - 16))
    if 'DeviceList' not in response:
        raise Blocked('苹果服务未返回设备列表。')
    return [item['Properties'] for item in response['DeviceList']]


def app_id(item):
    return item.get('displayIdentifier') or item.get('bundleIdentifier')


def item_kind(item):
    if not isinstance(item, dict):
        return 'unknown'
    if item.get('iconType') == 'custom' or item.get('elementType') == 'widget':
        return 'widget_stack' if 'elements' in item else 'widget'
    if item.get('listType') == 'folder' and 'iconLists' in item:
        return 'folder'
    return 'app' if app_id(item) else 'unknown'


def layout_summary(state):
    counts = Counter()
    def walk(page):
        for item in page:
            kind = item_kind(item)
            counts[kind] += 1
            if kind == 'folder':
                for child in item['iconLists']:
                    walk(child)
    for page in state:
        walk(page)
    return {'counts': dict(counts), 'per_page': [dict(Counter(item_kind(i) for i in p)) for p in state]}


def validate_layout(state, allow_widgets=False):
    """Fail closed on unknown icons, widgets, holes and richer layout schemas."""
    if not isinstance(state, list) or len(state) < 2:
        raise Blocked('不支持的布局结构：需要 Dock 和至少一页桌面。')
    if not allow_widgets and any(item_kind(i) in ('widget', 'widget_stack') for p in state if isinstance(p, list) for i in p):
        raise Blocked('检测到组件或组件堆叠，尚未验证完整保留和恢复，仅支持读取。')

    def page_check(page, folder=False):
        if not isinstance(page, list):
            raise Blocked('不支持的分页结构。')
        if folder and len(page) > 9:
            raise Blocked('文件夹页超过当前验证工具支持的 9 个图标。')
        for item in page:
            if not isinstance(item, dict) or not item:
                raise Blocked('检测到空位或未知元素，仅支持读取。')
            if item_kind(item) in ('widget', 'widget_stack'):
                if not allow_widgets or folder:
                    raise Blocked('组件只能作为受保护的桌面元素。')
                # Preserve the entire opaque payload, including stack state and children.
                continue
            positions = {'gridPosition', 'position', 'location', 'row', 'column', 'x', 'y'}
            if positions.intersection(item):
                raise Blocked('检测到显式自由位置，仅支持读取。')
            if 'iconLists' in item:
                if folder or item.get('listType') != 'folder':
                    raise Blocked('未知或嵌套文件夹，仅支持读取。')
                if set(item) - {'listType', 'displayName', 'iconLists'}:
                    raise Blocked('文件夹包含未验证的元数据，仅支持读取。')
                if not isinstance(item['iconLists'], list) or not item['iconLists']:
                    raise Blocked('文件夹缺少分页。')
                if len(item['iconLists']) > 15:
                    raise Blocked('文件夹超过设备支持的 15 页。')
                for child in item['iconLists']:
                    page_check(child, folder=True)
            else:
                if set(item) - {'displayIdentifier', 'bundleIdentifier', 'displayName', 'bundleVersion', 'iconModDate'}:
                    raise Blocked('图标包含组件或未验证的元数据，仅支持读取。')
                if not isinstance(app_id(item), str) or not app_id(item):
                    raise Blocked('图标缺少应用标识，仅支持读取。')

    for page in state:
        page_check(page)
    if len(state[0]) > 4:
        raise Blocked('非标准 iPhone Dock，仅支持读取。')
    return state


def inventory(state):
    ids = []
    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            if item_kind(value) == 'folder':
                walk(value['iconLists'])
            elif item_kind(value) == 'app':
                ids.append(app_id(value))
    walk(state)
    return Counter(ids)


def compare_layout(expected, observed):
    expected_apps, observed_apps = inventory(expected), inventory(observed)
    return {
        'exact_equal': digest(expected) == digest(observed),
        'added_icon_instances': dict(observed_apps - expected_apps),
        'missing_icon_instances': dict(expected_apps - observed_apps),
        'dock_and_homepage_equal': digest(expected[:2]) == digest(observed[:2]),
        'page_lengths_expected': [len(p) for p in expected],
        'page_lengths_observed': [len(p) for p in observed],
    }


# High-confidence examples only. Unknown apps stay outside new folders.
CATEGORIES = {
    'com.tencent.xin': '社交', 'com.tencent.mqq': '社交',
    'net.whatsapp.WhatsApp': '社交', 'com.burbn.instagram': '社交',
    'com.taobao.taobao4iphone': '购物', 'com.360buy.jdmobile': '购物',
    'com.spotify.client': '影音', 'com.google.ios.youtube': '影音',
    'com.apple.Music': '影音', 'com.apple.podcasts': '影音',
    'com.apple.calculator': '工具', 'com.apple.compass': '工具',
    'com.apple.measure': '工具', 'com.apple.VoiceMemos': '工具',
}


def build_plan(state, categories=None):
    """Conservative POC: keep Dock/homepage, group loose apps within each later page.

    Widgets and their entire pages are preserved as opaque records. Exact-name
    folders can be merged within one later page; cross-page compaction is deferred.
    """
    validate_layout(state, allow_widgets=True)
    categories = CATEGORIES if categories is None else categories
    result = copy.deepcopy(state)
    changes = []
    for page_index in range(2, len(state)):
        page = state[page_index]
        if any(item_kind(i) in ('widget', 'widget_stack') for i in page):
            continue
        # Respect the user's existing categories: merge exact same names on this page.
        folder_groups = defaultdict(list)
        for index, item in enumerate(page):
            if item_kind(item) == 'folder' and item.get('displayName'):
                folder_groups[item['displayName']].append(index)
        merged, omitted = {}, set()
        for name, positions in folder_groups.items():
            if len(positions) < 2:
                continue
            members = [copy.deepcopy(app) for pos in positions for subpage in page[pos]['iconLists'] for app in subpage]
            if not members or len(members) > 135:
                continue
            folder = copy.deepcopy(page[positions[0]])
            folder['iconLists'] = [members[i:i+9] for i in range(0, len(members), 9)]
            merged[positions[0]] = folder
            omitted.update(positions[1:])
            changes.append({'action': 'merge_same_name_folders', 'page': page_index,
                            'folder': name, 'before_folders': len(positions), 'after_folders': 1,
                            'app_count': len(members)})
        page = [merged.get(i, copy.deepcopy(item)) for i, item in enumerate(page) if i not in omitted]
        groups = defaultdict(list)
        for index, item in enumerate(page):
            category = categories.get(app_id(item)) if 'iconLists' not in item else None
            if category:
                groups[category].append(index)
        replacements, removed = {}, set()
        for category, positions in groups.items():
            if len(positions) < 2:
                continue
            members = [copy.deepcopy(page[i]) for i in positions]
            replacements[positions[0]] = {
                'listType': 'folder', 'displayName': category,
                'iconLists': [members[i:i + 9] for i in range(0, len(members), 9)]}
            removed.update(positions[1:])
            changes.append({'page': page_index, 'folder': category,
                            'apps': [app_id(item) for item in members]})
        result[page_index] = [replacements.get(i, copy.deepcopy(item))
                              for i, item in enumerate(page) if i not in removed]
    validate_transition(state, result)
    return result, changes


def validate_transition(before, after, allow_dock=False):
    validate_layout(before, allow_widgets=True)
    validate_layout(after, allow_widgets=True)
    if before[1] != after[1] or (not allow_dock and before[0] != after[0]):
        raise Blocked('方案修改了受保护的 Dock 或首页。')
    if inventory(before) != inventory(after):
        raise Blocked('方案遗漏或增加了应用图标。')
    # Entire pages containing widgets remain opaque and unchanged at the same index.
    for index, page in enumerate(before):
        if any(item_kind(i) in ('widget', 'widget_stack') for i in page):
            if index >= len(after) or page != after[index]:
                raise Blocked('方案修改了含组件的受保护页面。')
    # App raw metadata must be preserved, including duplicate Home Screen icons.
    def leaves(value):
        if isinstance(value, list):
            return [leaf for child in value for leaf in leaves(child)]
        if item_kind(value) == 'folder':
            return leaves(value['iconLists'])
        return [digest(value)]
    if Counter(leaves(before)) != Counter(leaves(after)):
        raise Blocked('方案改变了原应用图标的数据。')


class Phone:
    def __init__(self, serial):
        self.serial = serial

    async def __aenter__(self):
        from pymobiledevice3.lockdown import create_using_usbmux
        self.lockdown = await create_using_usbmux(
            serial=self.serial, connection_type='USB', pair_timeout=30,
            label='OrganizerProbe')
        self.identity = {key: self.lockdown.all_values.get(key, '') for key in
                         ('UniqueDeviceID', 'ProductType', 'ProductVersion', 'BuildVersion')}
        if not self.identity['UniqueDeviceID']:
            await self.lockdown.close()
            raise Blocked('无法确认设备身份。')
        return self

    async def __aexit__(self, *args):
        await self.lockdown.close()

    async def read(self):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        async with SpringBoardServicesService(self.lockdown) as service:
            return await service.get_icon_state('2')

    async def write(self, state):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        async with SpringBoardServicesService(self.lockdown) as service:
            await service.set_icon_state(state)


def identity_check(expected, actual):
    if expected != actual:
        raise Blocked('设备或系统版本与快照不一致，拒绝写入。')


def risk_check(identity, accept_legacy):
    version = str(identity.get('ProductVersion', ''))
    if not version or not version.split('.')[0].isdigit():
        raise Blocked('无法确定系统版本，拒绝写入。')
    if int(version.split('.')[0]) >= 18 and not accept_legacy:
        raise Blocked('iOS 18+ 的自由布局可能无法读取或还原。仅在接受传统网格重排影响后，'
                      '才可使用 --accept-legacy-layout 实验写入；这不代表已验证兼容。')


async def verified_write(phone, expected, target, directory, accept_legacy=False, accept_widgets=False, allow_dock=False):
    """Always journal first. Never blindly retry an ambiguous device write."""
    validate_layout(target, allow_widgets=accept_widgets)
    risk_check(phone.identity, accept_legacy)
    current = await phone.read()
    if digest(current) != digest(expected):
        raise Blocked('手机布局已改变，请重新读取并生成方案。')
    validate_transition(current, target, allow_dock=allow_dock)
    if inventory(current) != inventory(target):
        raise Blocked('应用清单已改变，拒绝覆盖。')
    path = Path(directory) / ('transaction-' + uuid.uuid4().hex + '.json')
    journal = {'created_utc': datetime.now(timezone.utc).isoformat(),
               'identity': phone.identity, 'before': envelope(current),
               'target': envelope(target), 'status': 'prepared',
               'verification_scope': 'legacy getIconState formatVersion=2 only'}
    save_json(path, journal)
    if current == target:
        journal['status'] = 'unchanged'
        save_json(path, journal)
        return journal, path
    journal['status'] = 'write_started'
    save_json(path, journal)
    try:
        await phone.write(target)
        # Upstream setIconState has no semantic success response: re-read is required.
        after = None
        for attempt in range(6):
            after = await phone.read()
            if digest(after) == digest(target):
                journal['status'] = 'verified_legacy_layout'
                journal['after'] = envelope(after)
                save_json(path, journal)
                return journal, path
            await asyncio.sleep(0.5)
        journal['status'] = 'mismatch_no_auto_restore'
        journal['after'] = envelope(after)
        journal['comparison'] = compare_layout(target, after)
        save_json(path, journal)
        raise Blocked(f'写入后布局不符，已保存实际状态。未盲目覆盖恢复。记录：{path}')
    except BaseException as error:
        if journal['status'] == 'write_started':
            journal['status'] = 'unknown_after_write'
            journal['error_type'] = type(error).__name__
            save_json(path, journal)
        raise


def preview(path, before, after, changes, labels=None, simulated=False):
    labels = labels or {}
    def render(state):
        pages = []
        for index, page in enumerate(state):
            title = 'Dock' if index == 0 else f'第 {index} 页'
            icons = []
            for item in page:
                kind = item_kind(item)
                if kind in ('widget', 'widget_stack'):
                    identifier = item.get('containerBundleIdentifier') or item.get('bundleIdentifier')
                    name = ('组件堆叠' if kind == 'widget_stack' else '组件')
                    if identifier:
                        name += ' · ' + labels.get(identifier, identifier)
                    name += ' · ' + str(item.get('gridSize', '未知尺寸'))
                elif kind == 'folder':
                    name = item.get('displayName', '文件夹')
                    count = sum(inventory(item['iconLists']).values())
                    name = f'📁 {name} · {count}'
                elif isinstance(item, dict):
                    name = item.get('displayName') or labels.get(app_id(item)) or app_id(item) or '未知元素'
                else:
                    name = '空位或未知元素'
                if kind == 'folder':
                    contents = []
                    for folder_page, entries in enumerate(item['iconLists'], 1):
                        names = [entry.get('displayName') or labels.get(app_id(entry)) or app_id(entry)
                                 for entry in entries]
                        contents.append('<p>文件夹第 ' + str(folder_page) + ' 页</p><ol>' +
                                        ''.join('<li>' + html.escape(str(n)) + '</li>' for n in names) + '</ol>')
                    icons.append('<li><details><summary>' + html.escape(str(name)) +
                                 '</summary>' + ''.join(contents) + '</details></li>')
                else:
                    icons.append('<li>' + html.escape(str(name)) + '</li>')
            pages.append('<h3>' + title + '</h3><ul class="icons">' + ''.join(icons) + '</ul>')
        return ''.join(pages)
    summary = html.escape(json.dumps(changes, ensure_ascii=False, indent=2))
    output = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>桌面整理实验预览</title><style>'
              'body{font:16px system-ui;background:#f3f5f8;color:#172033;margin:32px}'
              'main{display:grid;grid-template-columns:1fr 1fr;gap:24px}'
              'section{background:white;border-radius:20px;padding:24px}'
              'ul.icons{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:0}'
              'ul.icons>li{list-style:none;background:#edf1f8;padding:14px;border-radius:12px;overflow-wrap:anywhere}'
              'summary{cursor:pointer}ol{padding-left:20px}ol li{margin:6px 0;font-size:14px}'
              'pre{white-space:pre-wrap}@media(max-width:800px){main{grid-template-columns:1fr}}'
              '</style><h1>桌面整理实验预览</h1>'
              + ('<p><strong>模拟数据：未连接手机，不代表 iOS 真机验证通过。</strong></p>' if simulated else '') +
              '<p>此页面只展示方案，不会修改手机。点击文件夹可查看应用。此为接口结构预览，非真实屏幕位置还原。</p>'
              '<p>传统布局快照不保证覆盖自由位置和组件；应用前必须重新核对手机状态。</p>'
              '<main><section><h2>整理前</h2>' + render(before) + '</section>'
              '<section><h2>建议布局</h2>' + render(after) + '</section></main>'
              '<h2>变更明细</h2><pre>' + summary + '</pre></html>')
    Path(path).write_text(output, encoding='utf-8')


class Simulator:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.identity = {'UniqueDeviceID': 'SIMULATOR-NOT-A-PHONE', 'ProductType': 'SIMULATED',
                         'ProductVersion': '17.0', 'BuildVersion': 'SIMULATED'}
    async def read(self):
        return copy.deepcopy(self.state)
    async def write(self, state):
        self.state = copy.deepcopy(state)


def snapshot(phone, state):
    return {'schema': 1, 'identity': phone.identity,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'legacy getIconState formatVersion=2, not a device backup',
            'state': envelope(state)}


async def main(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.command == 'doctor':
        try:
            devices = usbmux_devices()
            report = {'transport_reachable': True, 'device_count': len(devices),
                      'usb_device_count': sum(x.get('ConnectionType') == 'USB' for x in devices),
                      'connection_types': [x.get('ConnectionType') for x in devices],
                      'checked_utc': datetime.now(timezone.utc).isoformat()}
        except (OSError, Blocked) as error:
            report = {'transport_reachable': False, 'error': str(error)}
        save_json(output / 'connection-report.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get('usb_device_count', 0) else 2
    if args.command == 'demo':
        def icon(bundle):
            return {'displayIdentifier': bundle}
        initial = [[icon('com.apple.mobilephone')], [icon('com.apple.mobilesafari')],
                   [icon('com.tencent.xin'), icon('com.tencent.mqq'),
                    icon('com.apple.calculator'), icon('com.apple.compass'), icon('unknown.example')]]
        phone = Simulator(initial)
        save_json(output / 'demo-snapshot.json', snapshot(phone, initial))
        target, changes = build_plan(initial)
        preview(output / 'demo-preview.html', initial, target, changes, simulated=True)
        applied, _ = await verified_write(phone, initial, target, output)
        restored, _ = await verified_write(phone, target, initial, output)
        report = {'mode': 'SIMULATED_NOT_DEVICE_VERIFIED',
                  'apply': applied['status'], 'restore': restored['status'],
                  'original_restored': initial == await phone.read(), 'changes': changes}
        save_json(output / 'demo-report.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'plan':
        snap = json.loads(Path(args.file).read_text(encoding='utf-8'))
        before = unpack(snap['state'])
        target, changes = build_plan(before)
        plan = {'schema': 1, 'identity': snap['identity'], 'before': snap['state'],
                'target': envelope(target), 'changes': changes}
        save_json(output / 'plan.json', plan)
        preview(output / 'preview.html', before, target, changes, snap.get('app_labels'))
        print(json.dumps({'plan': str(output / 'plan.json'), 'groups': len(changes)}, ensure_ascii=False))
        return 0

    devices = [d for d in usbmux_devices() if d.get('ConnectionType') == 'USB']
    if args.serial:
        devices = [d for d in devices if d.get('SerialNumber') == args.serial]
    if len(devices) != 1:
        raise Blocked(f'检测到 {len(devices)} 台匹配的 USB iPhone；请连接并信任一台设备，'
                      '多台时用 --serial 指定。未执行任何桌面写入。')
    async with Phone(devices[0]['SerialNumber']) as phone:
        if args.command in ('capture', 'prepare'):
            state = await phone.read()
            snap = snapshot(phone, state)
            # Persist raw state before asking any other device service.
            capture_path = output / ('snapshot-' + uuid.uuid4().hex + '.json')
            save_json(capture_path, snap)
            try:
                from pymobiledevice3.services.installation_proxy import InstallationProxyService
                async with InstallationProxyService(phone.lockdown) as service:
                    apps = await service.get_apps()
                snap['app_labels'] = {key: (value.get('CFBundleDisplayName') or value.get('CFBundleName') or key)
                                      for key, value in apps.items()}
                save_json(capture_path, snap)
            except Exception as error:
                snap['app_inventory_error_type'] = type(error).__name__
                save_json(capture_path, snap)
            try:
                validate_layout(state)
                layout_status = 'legacy_schema_supported_not_write_verified'
            except Blocked as error:
                layout_status = str(error)
            if args.command == 'prepare':
                run_directory = output / ('prepared-' + uuid.uuid4().hex)
                run_directory.mkdir()
                try:
                    target, changes = build_plan(state)
                    plan = {'schema': 1, 'identity': phone.identity, 'before': snap['state'],
                            'target': envelope(target), 'changes': changes}
                    save_json(run_directory / 'plan.json', plan)
                except Blocked as error:
                    target, changes = state, [{'read_only_reason': str(error)}]
                preview(run_directory / 'preview.html', state, target, changes, snap.get('app_labels'))
                if args.open_preview and sys.platform == 'win32':
                    os.startfile(str((run_directory / 'preview.html').resolve()))
            print(json.dumps({'snapshot': str(capture_path), 'layout_status': layout_status,
                              'version': phone.identity['ProductVersion'],
                              'writes_performed': 0}, ensure_ascii=False, indent=2))
            return 0
        document = json.loads(Path(args.file).read_text(encoding='utf-8'))
        identity_check(document['identity'], phone.identity)
        if args.command == 'apply':
            if document.get('review_required'):
                raise Blocked('该方案使用暂定频率，尚未确认个人偏好；请先补充常用 App 后生成正式方案。')
            before, target = unpack(document['before']), unpack(document['target'])
            validate_transition(before, target, allow_dock=args.allow_dock_relayout)
        else:
            if document.get('status') != 'verified_legacy_layout':
                raise Blocked('仅允许从已核对成功的事务恢复；不确定状态需先读取检查。')
            before, target = unpack(document['target']), unpack(document['before'])
            validate_layout(target, allow_widgets=args.accept_widget_roundtrip)
        if not args.yes:
            print('即将修改真实桌面；传统布局接口不能保证完整保留自由摆放。')
            if input('输入 APPLY 确认：').strip() != 'APPLY':
                raise Blocked('用户取消，未修改桌面。')
        journal, path = await verified_write(phone, before, target, output, args.accept_legacy_layout,
                                            args.accept_widget_roundtrip, args.allow_dock_relayout)
        print(json.dumps({'status': journal['status'], 'transaction': str(path)}, ensure_ascii=False))
        return 0


def cli():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['doctor', 'capture', 'prepare', 'plan', 'apply', 'restore', 'demo'])
    parser.add_argument('--file', help='snapshot, plan or transaction JSON')
    parser.add_argument('--serial', help='explicit USB device identifier; never auto-switch devices')
    parser.add_argument('--output', default='artifacts')
    parser.add_argument('--accept-legacy-layout', action='store_true')
    parser.add_argument('--allow-dock-relayout', action='store_true',
                        help='explicitly permit Dock changes while keeping the widget homepage protected')
    parser.add_argument('--accept-widget-roundtrip', action='store_true',
                        help='experimental: acknowledge opaque widgets may not retain visual positions')
    parser.add_argument('--yes', action='store_true', help='explicit noninteractive write confirmation')
    parser.add_argument('--open-preview', action='store_true')
    args = parser.parse_args()
    if args.command in ('plan', 'apply', 'restore') and not args.file:
        parser.error('--file is required for this command')
    try:
        return asyncio.run(asyncio.wait_for(main(args), timeout=120))
    except Exception as error:
        print(json.dumps({'status': 'blocked_or_failed', 'error_type': type(error).__name__,
                          'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(cli())
