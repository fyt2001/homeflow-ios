"""Right-hand-oriented draft using user ranks, not fabricated usage telemetry.

The planner preserves Dock and widget page. It reuses original icon records and
the existing snapshot, validation and preview pipeline. No device writes occur.
"""
import argparse
import copy
import json
import html
from collections import defaultdict
from pathlib import Path

from . import core as o


# Editable starter taxonomy, not authoritative App Store metadata.
OVERRIDES = {}


def assign(category, identifiers):
    for identifier in identifiers.split():
        OVERRIDES[identifier] = category


assign('沟通邮件', 'com.tencent.xin com.tencent.mqq com.apple.facetime com.apple.mobilemail com.tencent.qqmail com.apple.MobileAddressBook ph.telegra.Telegraph')
assign('影音直播', 'tv.danmaku.bilianime tv.douyu.live com.yy.kiwi com.ss.iphone.ugc.Aweme tv.twitch com.google.ios.youtube com.zhiliaoapp.musically')
assign('财务支付', 'com.memecoin.market com.apple.stocks com.apple.Passbook com.alipay.iphoneclient')
assign('生活服务', 'com.ziroom.ZiroomProject com.chinaunicom.mobilebusiness com.meituan.imeituan')
assign('出行导航', 'com.adb.aimaTravel')
assign('系统设备', 'com.apple.Passwords com.apple.Preferences com.apple.Bridge com.apple.Home com.apple.findmy com.benchu.ARMCPUZ com.apple.shortcuts com.apple.store.Jolly com.tencent.wetype net.qihoo.360mobilesafe com.microsoft.LinkToWindows com.edifier.connect com.apple.AppStore com.apple.tips')
assign('学习日用', 'com.duolingo.DuolingoMobile com.apple.calculator com.apple.compass com.apple.Magnifier com.apple.measure com.apple.mobiletimer com.apple.Translate com.apple.weather com.easy.translate.tool com.apple.iBooks com.apple.reminders com.apple.mobilecal')
assign('文档笔记', 'com.apple.VoiceMemos com.apple.journal com.apple.Pages com.apple.Numbers com.apple.Keynote com.apple.mobilenotes com.kingsoft.www.office.wpsoffice net.xmind.brownieapp')
assign('网盘文件', 'com.apple.DocumentsApp com.apple.Preview com.baidu.netdisk com.alicloud.smartdrive com.xunlei.mvideo')
assign('AI助手', 'com.deepseek.chat ai.x.GrokApp com.bot.doubao co.anysphere.sand com.tencent.workbuddy.app')
assign('浏览网络', 'com.liguangming.Shadowrocket com.apple.mobilesafari com.google.GoogleMobile com.quark.browser com.x1vpn.xiashijsq com.aolafu.leishen')
assign('游戏', 'com.apple.games')
assign('沟通邮件', 'com.apple.MobileSMS')
assign('影像创作', 'com.apple.camera')

FALLBACK = {'医疗保健与健身': '健康运动', '社交网络': '沟通邮件', '文件夹': '购物比价',
            'Creativity': '影像创作', '摄影': '影像创作', '财务': '财务支付',
            '游戏': '游戏', '乐活': '居住找房', '旅行': '出行导航', '参考': '浏览网络', '音乐': '音乐声音'}

# Deliberately labelled provisional examples, never reported as measured frequency.
EXAMPLE_FAVORITES = ['com.tencent.xin', 'com.alipay.iphoneclient',
                     'tv.danmaku.bilianime', 'com.netease.cloudmusic']
SCENARIO_PRIORITY = ['沟通邮件', '生活服务', '出行导航', '购物比价', 'AI助手',
                     '影音直播', '财务支付', '文档笔记', '工作协作', '内容社区',
                     '浏览网络', '学习日用', '网盘文件', '健康运动', '影像创作',
                     '游戏', '游戏社区', '系统设备', '求职职场', '居住找房']

SCREEN_TIME_PROXY = {
    '影音直播': '娱乐', '音乐声音': '娱乐', '游戏': '游戏', '游戏社区': '游戏',
    '内容社区': '社交', '沟通邮件': '社交', 'AI助手': '效率与财务',
    '文档笔记': '效率与财务', '网盘文件': '效率与财务', '工作协作': '效率与财务',
    '财务支付': '效率与财务', '影像创作': '创意', '购物比价': '购物与美食',
    '生活服务': '购物与美食', '出行导航': '旅游', '学习日用': '教育', '系统设备': '工具',
}


def category_for(app, source_folder):
    identifier = o.app_id(app)
    if identifier in OVERRIDES:
        return OVERRIDES[identifier], 'explicit_task_mapping'
    if source_folder in SCENARIO_PRIORITY or source_folder == '音乐声音':
        return source_folder, 'existing_task_group'
    if source_folder == '娱乐':
        if identifier in {'com.valvesoftware.Steam', 'com.tencent.ied.app.lolbible', 'com.max.xiaoheihe',
                          'com.easyplay.taptap.now', 'com.digitalextremes.warframenexus', 'com.unity3d.unityconnect'}:
            return '游戏社区', 'task_mapping'
        return '内容社区', 'original_folder_and_task_mapping'
    if source_folder == '商业':
        if identifier in {'com.tencent.ww', 'com.laiwang.DingTalk', 'com.larksuite.feishu.ka.fssw', 'com.tencent.meeting'}:
            return '工作协作', 'task_mapping'
        return '求职职场', 'original_folder_and_task_mapping'
    if source_folder in FALLBACK:
        return FALLBACK[source_folder], 'original_folder_mapping'
    return (source_folder, 'preserved_original_group') if source_folder else ('其他应用', 'unknown_loose_app')


def reachable_slots(rows=6, columns=4, hand='right'):
    """Design heuristic, not a calibrated physiological measurement.

    Anchor is in the lower middle near the holding hand, not the extreme corner.
    Full rows are kept: no unrepresentable gaps or imaginary placeholders.
    """
    anchor_column = 3.5 if hand == 'right' else 1.5
    return sorted(range(rows * columns), key=lambda i:
                  (((i // columns + 1) - 5.4) / 1.7) ** 2 +
                  (((i % columns + 1) - anchor_column) / 1.4) ** 2)


def build(state, favorites=None, category_minutes=None):
    o.validate_layout(state, allow_widgets=True)
    if any(o.item_kind(i) in ('widget', 'widget_stack') for page in state[2:] for i in page):
        raise o.Blocked('后续页包含组件，当前右手方案只支持保留组件首页。')
    groups, apps, decisions, original_standalones = defaultdict(list), {}, [], []
    def gather(app, source):
        if o.item_kind(app) != 'app':
            raise o.Blocked('分组内出现非应用元素。')
        category, evidence = category_for(app, source)
        groups[category].append(copy.deepcopy(app))
        apps.setdefault(o.app_id(app), app)
        decisions.append({'app': o.app_id(app), 'name': app.get('displayName'),
                          'category': category, 'basis': evidence})
    for page in state[2:]:
        for item in page:
            if o.item_kind(item) == 'folder':
                for subpage in item['iconLists']:
                    for app in subpage:
                        gather(app, item.get('displayName', ''))
            else:
                gather(item, '')
                original_standalones.append(o.app_id(item))
    supplied = favorites is not None
    category_only = category_minutes is not None and not supplied
    requested = list(dict.fromkeys(favorites if supplied else
                                  original_standalones if category_only else EXAMPLE_FAVORITES))
    favorites = [identifier for identifier in requested if identifier in apps]
    promoted = []
    for identifier in favorites:
        # Move exactly one instance; repeated Home Screen icons remain intact.
        for members in groups.values():
            pos = next((i for i, app in enumerate(members) if o.app_id(app) == identifier), None)
            if pos is not None:
                promoted.append(members.pop(pos))
                break
    groups = {k: v for k, v in groups.items() if v}
    priority = {name: len(SCENARIO_PRIORITY) - i for i, name in enumerate(SCENARIO_PRIORITY)}
    items = [((-100 - i) if category_only else (1000 - i), app,
              'retained_existing_standalone' if category_only else
              'user_frequency_rank' if supplied else 'provisional_favorite')
             for i, app in enumerate(promoted)]
    for name, members in groups.items():
        rank = {identifier: i for i, identifier in enumerate(requested)}
        members.sort(key=lambda a: rank.get(o.app_id(a), len(rank)))
        folder = {'listType': 'folder', 'displayName': name,
                  'iconLists': [members[i:i+9] for i in range(0, len(members), 9)]}
        score = priority.get(name, -1)
        basis = 'provisional_scenario_priority'
        if category_minutes is not None:
            # One broad category may guide multiple folders. Do not divide its
            # time or report it as any individual folder/app's actual duration.
            category = SCREEN_TIME_PROXY.get(name)
            score = category_minutes.get(category, -1)
            basis = 'broad_category_duration_proxy' if category in category_minutes else 'category_not_available'
        items.append((score, folder, basis))
    items.sort(key=lambda item: -item[0])
    target = copy.deepcopy(state[:2])
    positions = []
    # A full 24-item page supports lower-right placement through a flat legacy list.
    # A final incomplete page stays compact; it is not falsely rendered at the bottom.
    for start in range(0, len(items), 24):
        chunk = items[start:start+24]
        slots = reachable_slots() if len(chunk) == 24 else list(range(len(chunk)))
        page = [None] * len(chunk)
        for (_, item, basis), slot in zip(chunk, slots):
            page[slot] = item
            positions.append({'page': len(target), 'row': slot // 4 + 1, 'column': slot % 4 + 1,
                              'name': item.get('displayName') or o.app_id(item),
                              'kind': o.item_kind(item), 'basis': basis})
        target.append(page)
    o.validate_transition(state, target)
    metadata = {'hand': 'right', 'frequency_source': 'user_rank' if supplied else
                'user_screenshot_category_duration' if category_only else 'provisional_examples_not_measured',
                'requested_favorites': requested, 'favorites_not_on_editable_pages': [i for i in requested if i not in apps],
                'grouping': decisions, 'positions': positions, 'folder_sizes': {k: len(v) for k,v in groups.items()},
                'thumb_model': 'heuristic lower-middle right zone, varies with grip; not measured',
                'protected': 'Dock and entire widget homepage unchanged',
                'unresolved': [d for d in decisions if d['category'] == '待确认' and d['app'] not in favorites],
                'applied': False}
    if category_minutes is not None:
        metadata['category_minutes'] = category_minutes
        metadata['category_mapping'] = SCREEN_TIME_PROXY
        metadata['individual_app_frequency_available'] = False
        metadata['category_mapping_is_inference'] = True
    return target, metadata

