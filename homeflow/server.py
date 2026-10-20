"""Loopback-only bridge. A random session capability protects every device route."""
from __future__ import annotations

import argparse
import asyncio
import hmac
import os
from pathlib import Path
import secrets
import uuid
import webbrowser
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import core, planner, history

WEB = Path(__file__).with_name('web')


def public_layout(state):
    def item(value):
        kind = core.item_kind(value)
        result = {'kind': kind, 'name': value.get('displayName') or core.app_id(value) or '小组件'}
        if kind == 'app':
            result['id'] = core.app_id(value)
        if kind == 'folder':
            result['children'] = [item(a) for page in value['iconLists'] for a in page]
        return result
    return [[item(a) for a in page] for page in state]


class PlanInput(BaseModel):
    snapshot_id: uuid.UUID
    favorites: list[str] = Field(default_factory=list, max_length=24)
    hand: Literal['left', 'right', 'both'] = 'right'
    style: Literal['compact', 'mixed', 'spread'] = 'mixed'
    goal: Literal['speed', 'calm', 'familiar'] = 'speed'
    favorite_limit: Literal[4, 8, 16] = 8
    grouping: Literal['task', 'original'] = 'task'
    widgets: Literal['preserve', 'review', 'replan'] = 'preserve'
    avoid: list[str] = Field(default_factory=list, max_length=200)


class WriteInput(BaseModel):
    plan_id: uuid.UUID
    accept_layout_risk: bool = False


class RestoreInput(BaseModel):
    transaction_id: uuid.UUID
    accept_layout_risk: bool = False


def create_app(token: str, data_dir: Path, origins: list[str]):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST'],
                       allow_headers=['Authorization', 'Content-Type'], allow_private_network=True)
    lock = asyncio.Lock()
    snapshots, plans = {}, {}

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if origin and origin not in origins:
                return JSONResponse({'detail': '此网页未获得本机访问权限。'}, status_code=403)
            if request.method != 'OPTIONS' and not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
                return JSONResponse({'detail': '请粘贴本机助手本次启动显示的连接码。'}, status_code=401)
            if request.method == 'POST' and request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': '仅接受 JSON 请求。'}, status_code=415)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(core.Blocked)
    async def blocked(request, error):
        # Do not leak local paths or identifiers through internal exceptions.
        return JSONResponse({'detail': str(error).split('记录：')[0]}, status_code=409)

    async def open_phone():
        try:
            devices = await asyncio.to_thread(core.usbmux_devices)
        except OSError:
            raise core.Blocked('无法连接 Apple 服务。请安装 Apple Devices / iTunes 驱动并连接数据线。')
        devices = [d for d in devices if d.get('ConnectionType') == 'USB']
        if len(devices) != 1:
            raise core.Blocked('请连接并解锁一台 iPhone，点击“信任此电脑”；多台设备请只保留一台。')
        return core.Phone(devices[0]['SerialNumber'])

    @app.get('/api/health')
    async def health():
        return {'name': 'HomeFlow', 'version': '0.3.0', 'personalization': True, 'undo_history': True}

    @app.post('/api/capture')
    async def capture():
        async with lock:
            async with await open_phone() as phone:
                state = await phone.read()
                core.validate_layout(state, allow_widgets=True)
                sid = uuid.uuid4().hex
                snap = core.snapshot(phone, state)
                core.save_json(data_dir / sid / 'snapshot.json', snap)
                snapshots[sid] = snap
                return {'snapshot_id': sid, 'layout': public_layout(state),
                        'ios': phone.identity['ProductVersion'], 'summary': core.layout_summary(state)}

    @app.post('/api/plan')
    async def plan(body: PlanInput):
        async with lock:
            snap = snapshots.get(body.snapshot_id.hex)
            if not snap:
                raise HTTPException(404, '读取记录已过期，请重新读取。')
            before = core.unpack(snap['state'])
            after, metadata = planner.personalize(before, **body.model_dump(exclude={'snapshot_id'}))
            pid = uuid.uuid4().hex
            plans[pid] = (snap, after, metadata['review_only'])
            core.save_json(data_dir / pid / 'plan.json', {'snapshot': snap, 'target': core.envelope(after), 'metadata': metadata})
            return {'plan_id': pid, 'layout': public_layout(after), 'summary': core.layout_summary(after),
                    'review_only': metadata['review_only'], 'notes': metadata['notes']}

    @app.post('/api/apply')
    async def apply(body: WriteInput):
        if not body.accept_layout_risk:
            raise HTTPException(400, '请阅读并确认布局影响。')
        async with lock:
            stored = plans.pop(body.plan_id.hex, None)
            if not stored:
                raise HTTPException(404, '方案不存在或已使用，请重新生成。')
            snap, after, review_only = stored
            if review_only:
                raise core.Blocked('小组件评估方案不能自动写入，请选择保留原样后重新生成。')
            async with await open_phone() as phone:
                core.identity_check(snap['identity'], phone.identity)
                journal, path = await core.verified_write(phone, core.unpack(snap['state']), after,
                    data_dir / body.plan_id.hex, accept_legacy=True, accept_widgets=True,
                    context={'operation': 'apply'})
                tid = path.stem.removeprefix('transaction-')
                return {'status': journal['status'], 'transaction_id': tid, 'layout': public_layout(after)}

    @app.get('/api/history')
    async def history_list():
        async with lock:
            async with await open_phone() as phone:
                current = await phone.read()
                records = await asyncio.to_thread(history.read_records, data_dir)
                return {'records': history.list_for_phone(records, phone.identity, current)}

    @app.post('/api/restore-preview')
    async def restore_preview(body: RestoreInput):
        async with lock:
            async with await open_phone() as phone:
                current = await phone.read()
                record = history.eligible_record(data_dir, body.transaction_id.hex, phone.identity, current)
                return {'transaction_id': body.transaction_id.hex, 'created_utc': record.get('created_utc', ''),
                        'current': public_layout(current), 'restored': public_layout(core.unpack(record['before']))}

    @app.post('/api/restore')
    async def restore(body: RestoreInput):
        if not body.accept_layout_risk:
            raise HTTPException(400, '请确认恢复影响。')
        async with lock:
            async with await open_phone() as phone:
                current = await phone.read()
                journal = history.eligible_record(data_dir, body.transaction_id.hex, phone.identity, current)
                restored, _ = await core.verified_write(phone, core.unpack(journal['target']),
                    core.unpack(journal['before']), data_dir / 'restore', accept_legacy=True, accept_widgets=True,
                    context={'operation': 'undo', 'undo_of': body.transaction_id.hex})
                return {'status': restored['status'], 'layout': public_layout(core.unpack(journal['before']))}

    @app.get('/')
    async def index():
        return FileResponse(WEB / 'index.html')

    @app.get('/{asset}')
    async def asset(asset: str):
        if asset not in ('app.js', 'style.css'):
            raise HTTPException(404)
        return FileResponse(WEB / asset)
    return app


def main():
    parser = argparse.ArgumentParser(description='HomeFlow local iPhone bridge')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.homeflow')
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    url = 'http://127.0.0.1:27841/#token=' + token
    origins = ['https://fyt2001.top', 'https://fyt-blog.pages.dev', 'http://127.0.0.1:27841', 'http://localhost:27841']
    print('HomeFlow 本机助手已启动。连接码仅在本次启动有效：', token, flush=True)
    print('网页连接受限时，请使用本机页面：', url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    import uvicorn
    uvicorn.run(create_app(token, args.data_dir, origins), host='127.0.0.1', port=27841, log_level='warning')


if __name__ == '__main__':
    main()
