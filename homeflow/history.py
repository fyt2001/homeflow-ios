"""Recover undo history from the same durable journals used for device writes."""
import json
from pathlib import Path
import uuid

from . import core

VERIFIED = {'verified_legacy_layout', 'unchanged'}


def read_records(data_dir):
    root = Path(data_dir).resolve()
    records = {}
    for path in root.glob('*/transaction-*.json'):
        if not path.resolve().is_relative_to(root):
            continue
        try:
            identifier = uuid.UUID(path.stem.removeprefix('transaction-')).hex
            value = json.loads(path.read_text(encoding='utf-8'))
            core.unpack(value['before'])
            core.unpack(value['target'])
            if (not isinstance(value['identity'], dict) or not isinstance(value['status'], str)
                    or not isinstance(value.get('context', {}), dict)
                    or not isinstance(value.get('created_utc', ''), str)):
                continue
            if path.parent.name == 'restore' and value.get('context', {}).get('operation') != 'undo':
                continue  # Pre-0.3 restoration journals have no undo provenance.
            records[identifier] = value
        except (ValueError, KeyError, TypeError, OSError, core.Blocked):
            continue
    return records


def list_for_phone(records, identity, current):
    undo_attempts = {}
    for record in records.values():
        source = record.get('context', {}).get('undo_of')
        if source:
            undo_attempts.setdefault(source, []).append(record)
    results = []
    for identifier, record in records.items():
        if record.get('context', {}).get('operation') == 'undo':
            continue
        if record['identity'].get('UniqueDeviceID') != identity.get('UniqueDeviceID'):
            continue
        status = record['status']
        attempts = undo_attempts.get(identifier, [])
        reason = ''
        if attempts:
            if any(r['status'] in VERIFIED for r in attempts):
                reason = '这次整理已撤回。'
                status = 'undone'
            else:
                reason = '撤回曾中断或未通过核对，请先检查本机事务日志。'
                status = 'undo_needs_attention'
        elif status not in VERIFIED:
            reason = '这次写入未通过完整核对，需要人工检查，不能自动撤回。'
        elif record['identity'] != identity:
            reason = '设备系统版本与记录不一致，不能自动撤回。'
        elif core.digest(current) != core.digest(core.unpack(record['target'])):
            reason = '当前桌面与这次整理结果不同；若有后续整理，请先撤回较新的记录。手动改动不会被覆盖。'
        if not reason:
            try:
                core.validate_transition(current, core.unpack(record['before']))
            except core.Blocked:
                reason = '这条记录涉及当前保护范围之外的改动，不能自动撤回。'
        results.append({'transaction_id': identifier, 'created_utc': record.get('created_utc', ''),
                        'status': status, 'can_restore': not reason, 'reason': reason,
                        'before_summary': core.layout_summary(core.unpack(record['before'])),
                        'after_summary': core.layout_summary(core.unpack(record['target']))})
    return sorted(results, key=lambda r: r['created_utc'], reverse=True)


def eligible_record(data_dir, identifier, identity, current):
    records = read_records(data_dir)
    record = records.get(identifier)
    if record is None:
        raise core.Blocked('找不到这条本机整理记录，可能已移动或损坏。')
    core.identity_check(record['identity'], identity)
    item = next((r for r in list_for_phone(records, identity, current)
                 if r['transaction_id'] == identifier), None)
    if not item or not item['can_restore']:
        raise core.Blocked(item['reason'] if item else '这条记录不能自动撤回。')
    return record
