# -*- coding: utf-8 -*-
"""
冒烟测试：验证 _save_local_file 修复逻辑（稳定等待 + 实际字节校验）能解决
“源文件仍在写入时，写前快照 vs 写后大小”竞态误判。

不依赖 astrbot 环境，独立可运行：python _smoke_test_fix.py
"""
import asyncio
import os
import shutil
import tempfile
import threading
import time


def wait_for_file_stable(file_path, max_wait=5.0):
    """与 image_saver._wait_for_file_stable 同逻辑的同步版"""
    try:
        prev_size = os.path.getsize(file_path)
    except OSError:
        return None
    waited = 0.0
    while waited < max_wait:
        time.sleep(0.05)  # 测试用加速版间隔
        waited += 0.05
        try:
            cur_size = os.path.getsize(file_path)
        except OSError:
            return None
        if cur_size == prev_size:
            return cur_size
        prev_size = cur_size
    return None


def old_logic_check(source, dest, snapshot_size):
    """旧逻辑：写后大小 == 写前快照（原 bug）"""
    with open(source, 'rb') as src, open(dest, 'wb') as dst:
        data = src.read()
        dst.write(data)
    actual = os.path.getsize(dest)
    return actual == snapshot_size, len(data), actual


def new_logic_check(source, dest):
    """新逻辑：等稳定 + 写后按实际字节数校验"""
    stable = wait_for_file_stable(source)
    if stable is None:
        return False, 0, -1
    with open(source, 'rb') as src, open(dest, 'wb') as dst:
        data = src.read()
        dst.write(data)
    actual = os.path.getsize(dest)
    return (actual == len(data) and len(data) > 0), len(data), actual


def simulate_growing_source(tmpdir):
    """确定性复现竞态：快照取到 200KB，随后文件继续增长到 600KB（模拟下载未完成）"""
    src = os.path.join(tmpdir, "media_image_growing.jpg")
    dest_old = os.path.join(tmpdir, "old_result.jpg")
    dest_new = os.path.join(tmpdir, "new_result.jpg")

    # ---- 旧逻辑场景 ----
    with open(src, 'wb') as f:
        f.write(b'X' * 200_000)
    snapshot_size = os.path.getsize(src)  # 旧逻辑在此取快照（文件仍在“下载中”）

    # 快照之后、读取之前，文件又被写入 400KB（下载仍在继续）
    with open(src, 'ab') as f:
        f.write(b'Y' * 400_000)

    ok_old, len_old, actual_old = old_logic_check(src, dest_old, snapshot_size)

    # ---- 新逻辑场景 ----
    with open(src, 'wb') as f:
        f.write(b'X' * 200_000)

    # 后台线程模拟“下载仍在进行”：稍后继续追加 400KB
    def grow():
        time.sleep(0.02)
        with open(src, 'ab') as f:
            f.write(b'Y' * 400_000)

    t = threading.Thread(target=grow)
    t.start()
    ok_new, len_new, actual_new = new_logic_check(src, dest_new)
    t.join()

    return (ok_old, snapshot_size, len_old, actual_old), (ok_new, len_new, actual_new)


def main():
    tmpdir = tempfile.mkdtemp(prefix="imgsaver_smoke_")
    try:
        old_r, new_r = simulate_growing_source(tmpdir)
        ok_old, snap, len_old, actual_old = old_r
        ok_new, len_new, actual_new = new_r

        print(f"旧逻辑: 快照={snap} 读取={len_old} 写后={actual_old} -> 校验结果: {'通过' if ok_old else '❌失败(误判)'}")
        print(f"新逻辑: 稳定大小={actual_new if ok_new else 'N/A'} 读取={len_new} 写后={actual_new} -> 校验结果: {'✅通过' if ok_new else '❌失败'}")

        assert ok_old is False, "旧逻辑在该竞态下应失败（复现 bug）"
        assert ok_new is True, "新逻辑应等待稳定并成功保存"
        assert actual_new == 600_000, f"最终文件应完整为 600_000 bytes, 实际 {actual_new}"
        print("\nSMOKE_TEST_PASS: 新逻辑在“边写边读”竞态下保存完整文件并校验通过；旧逻辑复现误判失败。")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
