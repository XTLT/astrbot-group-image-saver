#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并功能深度测试：验证从 astrbot_plugin_qq_auto_save_images 吸收的功能
1. 备注功能（_get_notes / _apply_notes：单图重命名、多图写txt）
2. convert_to_file_path 取图方式（保存链新分支）
3. 群/私聊 handler 完整流程
4. save_notes 配置项
"""
import sys
import os
import time
import types
import tempfile
import shutil
from pathlib import Path

# ---------- mock astrbot 环境 ----------
astrbot = types.ModuleType("astrbot")
astrbot.__path__ = []
api = types.ModuleType("astrbot.api")
api.__path__ = []
astrbot.api = api
sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = api


class MockLogger:
    @staticmethod
    def info(msg, *a, **k): print(f"[INFO] {msg}")
    @staticmethod
    def warning(msg, *a, **k): print(f"[WARN] {msg}")
    @staticmethod
    def error(msg, *a, **k): print(f"[ERROR] {msg}")
    @staticmethod
    def debug(msg, *a, **k): pass

api.logger = MockLogger()


class EventMessageType:
    GROUP_MESSAGE = "group_message"
    PRIVATE_MESSAGE = "private_message"


class _Filter:
    EventMessageType = EventMessageType

    @staticmethod
    def event_message_type(msg_type):
        def deco(func):
            func._event_type = msg_type
            return func
        return deco

    @staticmethod
    def command(name):
        def deco(func):
            func._command = name
            return func
        return deco

    @staticmethod
    def all():
        def deco(func):
            return func
        return deco


class AstrMessageEvent:
    pass


api.event = types.ModuleType("astrbot.api.event")
api.event.filter = _Filter()
api.event.EventMessageType = EventMessageType
api.event.AstrMessageEvent = AstrMessageEvent
sys.modules["astrbot.api.event"] = api.event


class Context:
    pass


class Star:
    def __init__(self, context):
        self.context = context


def register(*args, **kwargs):
    def deco(cls):
        return cls
    return deco


api.star = types.ModuleType("astrbot.api.star")
api.star.Context = Context
api.star.Star = Star
api.star.register = register
sys.modules["astrbot.api.star"] = api.star


class Image:
    def __init__(self, file, data=None, url=None):
        self.file = file
        self.data = data
        self.url = url

    async def convert_to_file_path(self):
        return None


api.message_components = types.ModuleType("astrbot.api.message_components")
api.message_components.Image = Image
api.message_components.Plain = type("Plain", (), {})
sys.modules["astrbot.api.message_components"] = api.message_components

# ---------- 注册插件包（目录名含连字符，需 sys.modules 注入） ----------
PLUGIN_DIR = Path(__file__).parent
pkg_name = "astrbot_group_image_saver"
pkg = types.ModuleType(pkg_name)
pkg.__path__ = [str(PLUGIN_DIR)]
sys.modules[pkg_name] = pkg

from astrbot_group_image_saver.main import GroupImageSaverPlugin


# ---------- 测试工具 ----------
PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  ✗ {name} {detail}")


class MockPlatform:
    name = "AIOCQHTTP"


class MockEvent:
    def __init__(self, group_id=None, user_id=None, notes=None, images=None, raw_message=None):
        self.platform = MockPlatform()
        self.message_obj = types.SimpleNamespace(
            group_id=group_id, user_id=user_id, time=time.time(), message=images or []
        )
        self._notes = notes
        self.raw_message = raw_message

    def get_sender_name(self):
        return "测试用户"

    def get_extra(self, key, default=None):
        return self._notes if key == "notes" else default

    def plain_result(self, text):
        return text


async def drain(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


def make_plugin(tmp: Path, extra_config=None):
    config = {
        "base_save_path": str(tmp),
        "save_by_group": True,
        "max_file_size_mb": 50,
        "save_to_log": True,
        "supported_platforms": ["AIOCQHTTP"],
        "group_filter_mode": "all",
        "warning_group": "",
        "save_private_images": True,
        "private_filter_mode": "all",
    }
    if extra_config:
        config.update(extra_config)
    ctx = Context()
    return GroupImageSaverPlugin(ctx, config)


# ---------- 测试 ----------
async def main():
    global PASS, FAIL
    tmp_root = Path(tempfile.mkdtemp(prefix="merge_test_"))
    print("=" * 60)
    print("合并功能深度测试")
    print("=" * 60)

    # 1. 插件实例化 + default_config 合并
    print("\n[1] 插件实例化与配置合并")
    plugin = make_plugin(tmp_root / "a")
    check("插件实例化成功", plugin is not None)
    check("default_config 包含 save_notes=True", plugin.config.get("save_notes") is True)

    plugin2 = make_plugin(tmp_root / "b", {"save_notes": False})
    check("用户配置可覆盖 save_notes=False", plugin2.config.get("save_notes") is False)

    # 2. _get_notes
    print("\n[2] _get_notes 备注获取")
    ev_no_extra = MockEvent(group_id="1", images=[])
    ev_no_extra.get_extra = None
    check("无 get_extra 时返回空字符串", plugin._get_notes(ev_no_extra) == "")

    ev_empty = MockEvent(group_id="1", notes="", images=[])
    check("空备注返回空字符串", plugin._get_notes(ev_empty) == "")

    ev_notes = MockEvent(group_id="1", notes="  重要文件  ", images=[])
    check("备注去空格后返回", plugin._get_notes(ev_notes) == "重要文件")

    class BrokenExtra:
        def get_extra(self, *a, **k):
            raise RuntimeError("boom")

    ev_broken = MockEvent(group_id="1", images=[])
    ev_broken.get_extra = BrokenExtra().get_extra
    check("get_extra 抛异常时返回空字符串", plugin._get_notes(ev_broken) == "")

    # 3. _apply_notes 单图重命名
    print("\n[3] _apply_notes 单图备注重命名")
    single_dir = tmp_root / "single"
    single_dir.mkdir(parents=True)
    f1 = single_dir / "20260906_120000_0.jpg"
    f1.write_bytes(b"imgdata")
    plugin._apply_notes([f1], "重要")
    renamed = single_dir / "20260906_120000_0_重要.jpg"
    check("单图被重命名为带备注", renamed.exists() and not f1.exists())

    # 重名冲突时自动去重
    f2 = single_dir / "20260906_120100_0.jpg"
    f2.write_bytes(b"imgdata")
    conflict = single_dir / "20260906_120100_0_备注x.jpg"
    conflict.write_bytes(b"imgdata")
    plugin._apply_notes([f2], "备注x")
    renamed2 = sorted(single_dir.glob("20260906_120100_0_备注x*.jpg"))
    check("备注文件名冲突自动去重", len(renamed2) >= 1 and renamed2[0].exists())

    # 4. _apply_notes 多图写txt
    print("\n[4] _apply_notes 多图写备注txt")
    multi_dir = tmp_root / "multi"
    multi_dir.mkdir(parents=True)
    paths = []
    for i in range(3):
        p = multi_dir / f"20260906_130000_{i}.jpg"
        p.write_bytes(b"imgdata")
        paths.append(p)
    plugin._apply_notes(paths, "会议记录")
    note_files = list(multi_dir.glob("*_备注.txt"))
    check("多图创建备注txt", len(note_files) == 1)
    if note_files:
        content = note_files[0].read_text(encoding="utf-8")
        check("txt 包含备注内容", "会议记录" in content)
        check("txt 包含图片数量", "图片数量: 3" in content)
        check("txt 列出图片列表", "20260906_130000_0.jpg" in content and "20260906_130000_2.jpg" in content)

    # 5. 群消息 handler：convert_to_file_path 取图 + 单图备注
    print("\n[5] 群消息 handler 完整流程（convert_to_file_path + 单图备注）")
    g_dir = tmp_root / "group"
    plugin_g = make_plugin(g_dir)
    src = tmp_root / "src_group.jpg"
    src.write_bytes(b"group-img-bytes")
    img = Image(file="image.jpg")
    img.convert_to_file_path = lambda: _async_val(str(src))
    ev = MockEvent(group_id="888888", notes="群公告图", images=[img])
    await drain(plugin_g.on_group_message(ev))

    saved_g = list(g_dir.rglob("*.jpg"))
    check("群图片已保存", len(saved_g) == 1)
    if saved_g:
        check("群图片内容正确", saved_g[0].read_bytes() == b"group-img-bytes")
        check("单图备注已追加到文件名", "群公告图" in saved_g[0].name)
        check("按群号分目录", "888888" in str(saved_g[0].relative_to(g_dir)))

    # 6. 群消息 handler：无备注时文件名不含备注
    print("\n[6] 群消息 handler（无备注）")
    g2_dir = tmp_root / "group2"
    plugin_g2 = make_plugin(g2_dir)
    src2 = tmp_root / "src_group2.jpg"
    src2.write_bytes(b"plain-img")
    img2 = Image(file="image.jpg")
    img2.convert_to_file_path = lambda: _async_val(str(src2))
    ev2 = MockEvent(group_id="999999", notes=None, images=[img2])
    await drain(plugin_g2.on_group_message(ev2))
    saved_g2 = list(g2_dir.rglob("*.jpg"))
    check("无备注图片已保存", len(saved_g2) == 1)
    if saved_g2:
        check("文件名不含备注", "备注" not in saved_g2[0].name)

    # 7. 私聊消息 handler：convert_to_file_path + 多图备注写txt
    print("\n[7] 私聊消息 handler（多图备注写txt）")
    p_dir = tmp_root / "private"
    plugin_p = make_plugin(p_dir)
    imgs = []
    for i in range(2):
        s = tmp_root / f"src_priv_{i}.jpg"
        s.write_bytes(f"priv-{i}".encode())
        im = Image(file=f"p{i}.jpg")
        im.convert_to_file_path = lambda p=s: _async_val(str(p))
        imgs.append(im)
    ev_p = MockEvent(user_id="10001", notes="重要截图", images=imgs)
    await plugin_p.on_private_message(ev_p)

    saved_p = list(p_dir.rglob("*.jpg"))
    check("私聊图片已保存", len(saved_p) == 2)
    note_p = list(p_dir.rglob("*_备注.txt"))
    check("私聊多图创建备注txt", len(note_p) == 1)
    if note_p:
        content = note_p[0].read_text(encoding="utf-8")
        check("私聊备注内容正确", "重要截图" in content and "图片数量: 2" in content)

    # 8. 过滤仍生效（群黑名单模式）
    print("\n[8] 过滤功能未被破坏")
    bl_dir = tmp_root / "blacklist"
    plugin_bl = make_plugin(bl_dir, {"group_filter_mode": "blacklist", "group_blacklist": ["123"]})
    img_bl = Image(file="image.jpg")
    img_bl.convert_to_file_path = lambda: _async_val(str(src))
    ev_bl = MockEvent(group_id="123", notes="x", images=[img_bl])
    await drain(plugin_bl.on_group_message(ev_bl))
    check("黑名单群图片未保存", len(list(bl_dir.rglob("*.jpg"))) == 0)
    check("过滤计数已记录", plugin_bl.stats['filtered_groups'] == 1)

    # 9. 平台不支持时跳过
    print("\n[9] 平台过滤")
    unsup_dir = tmp_root / "unsup"
    plugin_us = make_plugin(unsup_dir)
    ev_us = MockEvent(group_id="777", images=[Image(file="a.jpg")])
    ev_us.platform = types.SimpleNamespace(name="TELEGRAM")
    await drain(plugin_us.on_group_message(ev_us))
    check("不支持平台跳过保存", len(list(unsup_dir.rglob("*.jpg"))) == 0)

    print("\n" + "=" * 60)
    print(f"结果: {PASS} 通过 / {FAIL} 失败")
    if FAILURES:
        print("失败项: " + ", ".join(FAILURES))
    shutil.rmtree(tmp_root, ignore_errors=True)
    return 0 if FAIL == 0 else 1


async def _async_val(v):
    return v


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
