import os
from pathlib import Path
from typing import Tuple
from datetime import datetime

from astrbot.api import logger


class MountChecker:
    """挂载点检测器"""

    # Windows 平台没有 Linux 容器挂载概念，跳过挂载检测
    _IS_WINDOWS = os.name == 'nt'

    @staticmethod
    def is_path_mounted(path: Path) -> Tuple[bool, str]:
        """
        检测路径是否被挂载
        
        Args:
            path: 要检测的路径
            
        Returns:
            (is_mounted, message) - 是否已挂载和状态信息
        """
        try:
            if not path.exists():
                return False, f"❌ 路径不存在: {path}"

            if MountChecker._IS_WINDOWS:
                return True, f"✅ Windows 平台，跳过挂载检测: {path}"

            if os.path.exists('/proc/mounts'):
                try:
                    with open('/proc/mounts', 'r') as f:
                        mounts = f.read()
                    
                    path_str = str(path.resolve())
                    
                    for line in mounts.split('\n'):
                        if line.strip():
                            parts = line.split()
                            if len(parts) >= 2:
                                mount_point = parts[1]
                                if (mount_point == path_str or 
                                    path_str.startswith(mount_point + '/')):
                                    if not mount_point.startswith(('/proc', '/sys', '/dev', '/run')):
                                        return True, f"✅ 路径已挂载: {path_str} (挂载点: {mount_point})"
                except Exception as e:
                    logger.error(f"读取/proc/mounts失败: {e}")
            
            known_mounts = [
                "/AstrBot/data",
                "/AstrBot/data/saved_images",
            ]
            
            path_str = str(path.resolve())
            for mount_point in known_mounts:
                # 使用带边界的前缀匹配，避免 /AstrBot/dataxxx 之类路径被误判
                if path_str == mount_point or path_str.startswith(mount_point + '/'):
                    return True, f"✅ 路径在已知挂载点下: {mount_point}"
            
            try:
                current_stat = os.statvfs(path)
                root_stat = os.statvfs('/')
                
                if current_stat.f_fsid != root_stat.f_fsid:
                    return True, f"✅ 路径在不同文件系统上（可能是挂载点）"
            except Exception as e:
                logger.debug(f"statvfs检查失败: {e}")
            
            return False, f"⚠️ 路径可能未挂载（容器重启后数据可能丢失）: {path}"
            
        except Exception as e:
            logger.error(f"挂载检测失败: {e}", exc_info=True)
            return False, f"❌ 挂载检测失败: {e}"
    
    @staticmethod
    def check_path_writable(path: Path) -> Tuple[bool, str]:
        """检查路径是否可写"""
        try:
            path.mkdir(parents=True, exist_ok=True)
            
            test_file = path / ".write_test"
            test_content = f"测试时间: {datetime.now().isoformat()}"
            test_file.write_text(test_content)
            
            if test_file.exists() and test_file.read_text() == test_content:
                test_file.unlink()
                return True, f"✅ 路径可写: {path}"
            else:
                if test_file.exists():
                    test_file.unlink()
                return False, f"❌ 写入测试失败"
                
        except PermissionError:
            return False, f"❌ 权限不足，无法写入: {path}"
        except Exception as e:
            return False, f"❌ 写入测试异常: {e}"
