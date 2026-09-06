#!/usr/bin/env python3
"""
群聊图片自动保存插件测试脚本
（合并自 astrbot_plugin_qq_auto_save_images 的测试框架）
"""

import sys
from pathlib import Path

# 添加插件目录到路径
plugin_dir = Path(__file__).parent
sys.path.insert(0, str(plugin_dir))


def test_metadata():
    """测试 metadata.yaml"""
    metadata_path = plugin_dir / "metadata.yaml"
    with open(metadata_path, 'r', encoding='utf-8') as f:
        content = f.read()

    print("✓ metadata.yaml 读取成功")

    required_fields = ['name', 'display_name', 'desc', 'version', 'author', 'repo', 'tags']
    for field in required_fields:
        if field not in content:
            print(f"✗ 缺少必需字段: {field}")
            return False

    print("✓ 所有必需字段都存在")
    return True


def test_main_syntax():
    """测试 main.py 语法"""
    try:
        import py_compile
        py_compile.compile(plugin_dir / "main.py", doraise=True)
        print("✓ main.py 语法检查通过")
        return True
    except py_compile.PyCompileError as e:
        print(f"✗ main.py 语法错误: {e}")
        return False


def test_file_structure():
    """测试文件结构"""
    required_files = [
        "main.py",
        "image_saver.py",
        "mount_checker.py",
        "group_filter.py",
        "private_filter.py",
        "__init__.py",
        "_conf_schema.json",
        "metadata.yaml",
        "README.md",
        "requirements.txt",
        "LICENSE",
    ]

    print("\n文件结构检查:")
    all_ok = True
    for file in required_files:
        file_path = plugin_dir / file
        if file_path.exists():
            print(f"  ✓ {file}")
        else:
            print(f"  ✗ {file} (缺失)")
            all_ok = False

    return all_ok


def main():
    """运行所有测试"""
    print("=" * 50)
    print("群聊图片自动保存插件 - 测试脚本")
    print("=" * 50)

    tests = [
        ("文件结构", test_file_structure),
        ("metadata.yaml", test_metadata),
        ("main.py 语法", test_main_syntax),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"\n测试: {test_name}")
        print("-" * 50)
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"✗ 测试执行失败: {e}")
            results.append((test_name, False))

    print("\n" + "=" * 50)
    print("测试结果汇总")
    print("=" * 50)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{test_name}: {status}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n🎉 所有测试通过！插件准备就绪。")
        return 0
    else:
        print("\n⚠️  部分测试失败，请检查错误信息。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
