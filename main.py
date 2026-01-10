#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AstrBot 插件：群聊图片自动保存器
版本: 0.0.1

功能：
1. 自动保存群聊中发送的图片
2. 自动保存QQ好友发送的图片
3. 按日期（年-月-日）创建文件夹
4. 可选按群号分子文件夹保存
5. 智能文件命名和格式处理
6. 支持多种平台适配器
7. 挂载检测和智能路径回退
8. 支持群组白名单/黑名单过滤
9. 路径未挂载时发送警告到指定群
"""

import os
import sys
import asyncio
import aiohttp
import aiofiles
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Dict, Any, Tuple
from urllib.parse import urlparse

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp


class MountChecker:
    """挂载点检测器"""
    
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
            # 如果路径不存在，返回False
            if not path.exists():
                return False, f"❌ 路径不存在: {path}"
            
            # 方法1: 检查/proc/mounts
            if os.path.exists('/proc/mounts'):
                try:
                    with open('/proc/mounts', 'r') as f:
                        mounts = f.read()
                    
                    # 将路径标准化，确保格式一致
                    path_str = str(path.resolve())
                    
                    # 检查路径是否在挂载列表中
                    for line in mounts.split('\n'):
                        if line.strip():
                            parts = line.split()
                            if len(parts) >= 2:
                                mount_point = parts[1]
                                # 检查是否是完全匹配或者是父目录
                                if (mount_point == path_str or 
                                    path_str.startswith(mount_point + '/')):
                                    # 排除系统临时挂载点
                                    if not mount_point.startswith(('/proc', '/sys', '/dev', '/run')):
                                        return True, f"✅ 路径已挂载: {path_str} (挂载点: {mount_point})"
                except Exception as e:
                    logger.error(f"读取/proc/mounts失败: {e}")
            
            # 方法2: 检查是否是容器内已知的挂载点
            # 我们知道/AstrBot/data是挂载点，检查是否是它的子目录
            known_mounts = [
                "/AstrBot/data",
                "/AstrBot/data/saved_images",
            ]
            
            path_str = str(path.resolve())
            for mount_point in known_mounts:
                if path_str.startswith(mount_point):
                    return True, f"✅ 路径在已知挂载点下: {mount_point}"
            
            # 方法3: 统计检查（简单方法，不总是准确）
            try:
                # 获取当前路径的statvfs
                current_stat = os.statvfs(path)
                # 获取根路径的statvfs
                root_stat = os.statvfs('/')
                
                # 如果文件系统ID不同，可能是挂载点
                if current_stat.f_fsid != root_stat.f_fsid:
                    return True, f"✅ 路径在不同文件系统上（可能是挂载点）"
            except Exception as e:
                logger.debug(f"statvfs检查失败: {e}")
            
            # 如果以上方法都检测不到挂载，假设未挂载
            return False, f"⚠️ 路径可能未挂载（容器重启后数据可能丢失）: {path}"
            
        except Exception as e:
            logger.error(f"挂载检测失败: {e}", exc_info=True)
            return False, f"❌ 挂载检测失败: {e}"
    
    @staticmethod
    def check_path_writable(path: Path) -> Tuple[bool, str]:
        """检查路径是否可写"""
        try:
            # 尝试创建目录
            path.mkdir(parents=True, exist_ok=True)
            
            # 测试写入权限
            test_file = path / ".write_test"
            test_content = f"测试时间: {datetime.now().isoformat()}"
            test_file.write_text(test_content)
            
            # 验证写入
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


class GroupFilter:
    """群组过滤器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.mode = config.get("group_filter_mode", "all").lower()
        self.whitelist = set(str(gid) for gid in config.get("group_whitelist", []))
        self.blacklist = set(str(gid) for gid in config.get("group_blacklist", []))
        
        logger.info(f"👥 群组过滤器初始化 - 模式: {self.mode}")
        if self.mode == "whitelist" and self.whitelist:
            logger.info(f"  ✅ 白名单群组: {', '.join(sorted(self.whitelist))}")
        elif self.mode == "blacklist" and self.blacklist:
            logger.info(f"  ✅ 黑名单群组: {', '.join(sorted(self.blacklist))}")
        elif self.mode == "all":
            logger.info("  ✅ 保存所有群的图片")
    
    def is_group_allowed(self, group_id: str) -> bool:
        """检查群组是否允许保存图片"""
        if self.mode == "all":
            return True
        elif self.mode == "whitelist":
            return str(group_id) in self.whitelist
        elif self.mode == "blacklist":
            return str(group_id) not in self.blacklist
        else:
            logger.warning(f"⚠️ 未知的群组过滤模式: {self.mode}，默认允许所有群")
            return True
    
    def add_to_whitelist(self, group_id: str) -> bool:
        """添加群到白名单"""
        group_id_str = str(group_id)
        if group_id_str not in self.whitelist:
            self.whitelist.add(group_id_str)
            logger.info(f"✅ 群组 {group_id_str} 已添加到白名单")
            return True
        return False
    
    def remove_from_whitelist(self, group_id: str) -> bool:
        """从白名单移除群"""
        group_id_str = str(group_id)
        if group_id_str in self.whitelist:
            self.whitelist.remove(group_id_str)
            logger.info(f"✅ 群组 {group_id_str} 已从白名单移除")
            return True
        return False
    
    def add_to_blacklist(self, group_id: str) -> bool:
        """添加群到黑名单"""
        group_id_str = str(group_id)
        if group_id_str not in self.blacklist:
            self.blacklist.add(group_id_str)
            logger.info(f"✅ 群组 {group_id_str} 已添加到黑名单")
            return True
        return False
    
    def remove_from_blacklist(self, group_id: str) -> bool:
        """从黑名单移除群"""
        group_id_str = str(group_id)
        if group_id_str in self.blacklist:
            self.blacklist.remove(group_id_str)
            logger.info(f"✅ 群组 {group_id_str} 已从黑名单移除")
            return True
        return False
    
    def get_status(self) -> str:
        """获取过滤器状态"""
        if self.mode == "all":
            return "✅ 当前模式：保存所有群的图片"
        elif self.mode == "whitelist":
            if self.whitelist:
                return f"✅ 当前模式：仅保存白名单中的群（{len(self.whitelist)}个）"
            else:
                return "⚠️ 当前模式：白名单模式，但白名单为空（将不会保存任何群的图片）"
        elif self.mode == "blacklist":
            if self.blacklist:
                return f"✅ 当前模式：不保存黑名单中的群（{len(self.blacklist)}个群被屏蔽）"
            else:
                return "✅ 当前模式：黑名单模式，但黑名单为空（将保存所有群的图片）"
        else:
            return f"❌ 未知模式：{self.mode}"


class PrivateFilter:
    """好友过滤器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.mode = config.get("private_filter_mode", "all").lower()
        self.whitelist = set(str(uid) for uid in config.get("private_whitelist", []))
        self.blacklist = set(str(uid) for uid in config.get("private_blacklist", []))
        
        logger.info(f"👤 好友过滤器初始化 - 模式: {self.mode}")
        if self.mode == "whitelist" and self.whitelist:
            logger.info(f"  ✅ 白名单好友: {', '.join(sorted(self.whitelist))}")
        elif self.mode == "blacklist" and self.blacklist:
            logger.info(f"  ✅ 黑名单好友: {', '.join(sorted(self.blacklist))}")
        elif self.mode == "all":
            logger.info("  ✅ 保存所有好友的图片")
    
    def is_user_allowed(self, user_id: str) -> bool:
        """检查好友是否允许保存图片"""
        if self.mode == "all":
            return True
        elif self.mode == "whitelist":
            return str(user_id) in self.whitelist
        elif self.mode == "blacklist":
            return str(user_id) not in self.blacklist
        else:
            logger.warning(f"⚠️ 未知的好友过滤模式: {self.mode}，默认允许所有好友")
            return True
    
    def add_to_whitelist(self, user_id: str) -> bool:
        """添加好友到白名单"""
        user_id_str = str(user_id)
        if user_id_str not in self.whitelist:
            self.whitelist.add(user_id_str)
            logger.info(f"✅ 好友 {user_id_str} 已添加到白名单")
            return True
        return False
    
    def remove_from_whitelist(self, user_id: str) -> bool:
        """从白名单移除好友"""
        user_id_str = str(user_id)
        if user_id_str in self.whitelist:
            self.whitelist.remove(user_id_str)
            logger.info(f"✅ 好友 {user_id_str} 已从白名单移除")
            return True
        return False
    
    def add_to_blacklist(self, user_id: str) -> bool:
        """添加好友到黑名单"""
        user_id_str = str(user_id)
        if user_id_str not in self.blacklist:
            self.blacklist.add(user_id_str)
            logger.info(f"✅ 好友 {user_id_str} 已添加到黑名单")
            return True
        return False
    
    def remove_from_blacklist(self, user_id: str) -> bool:
        """从黑名单移除好友"""
        user_id_str = str(user_id)
        if user_id_str in self.blacklist:
            self.blacklist.remove(user_id_str)
            logger.info(f"✅ 好友 {user_id_str} 已从黑名单移除")
            return True
        return False
    
    def get_status(self) -> str:
        """获取过滤器状态"""
        if self.mode == "all":
            return "✅ 当前模式：保存所有好友的图片"
        elif self.mode == "whitelist":
            if self.whitelist:
                return f"✅ 当前模式：仅保存白名单中的好友（{len(self.whitelist)}个）"
            else:
                return "⚠️ 当前模式：白名单模式，但白名单为空（将不会保存任何好友的图片）"
        elif self.mode == "blacklist":
            if self.blacklist:
                return f"✅ 当前模式：不保存黑名单中的好友（{len(self.blacklist)}个好友被屏蔽）"
            else:
                return "✅ 当前模式：黑名单模式，但黑名单为空（将保存所有好友的图片）"
        else:
            return f"❌ 未知模式：{self.mode}"


class ImageSaver:
    """图片保存管理器"""
    
    # 常见的图片扩展名
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico', '.tiff'}
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # 从配置获取保存路径
        user_base_path = config.get("base_save_path", "/AstrBot/data/saved_images")
        
        # 路径检测和修复（包含挂载检测）
        self.base_save_path, self.path_status = self._validate_and_fix_path(user_base_path)
        
        # 路径状态信息
        self.path_valid = self.path_status["is_valid"]
        self.path_message = self.path_status["message"]
        
        self.save_by_group = config.get("save_by_group", True)
        self.max_file_size = config.get("max_file_size_mb", 50) * 1024 * 1024  # 转换为字节
        self.save_to_log = config.get("save_to_log", True)
        
        # 支持的平台
        self.supported_platforms = set(
            platform.upper() 
            for platform in config.get("supported_platforms", ["AIOCQHTTP"])
        )
        
        # 群组过滤器
        self.group_filter = GroupFilter(config)
        
        # 好友过滤器
        self.private_filter = PrivateFilter(config)
        
        # 记录初始化信息
        logger.info(f"📁 图片自动保存器已初始化")
        logger.info(f"📂 用户配置路径: {user_base_path}")
        logger.info(f"📂 实际使用路径: {self.base_save_path.absolute()}")
        logger.info(f"📊 路径状态: {self.path_message}")
        logger.info(f"📊 按群号分组: {self.save_by_group}")
        logger.info(f"📏 最大文件大小: {self.max_file_size / (1024*1024)} MB")
        logger.info(f"📊 群组过滤模式: {self.group_filter.mode}")
        logger.info(f"📊 好友过滤模式: {self.private_filter.mode}")
        
        # 添加路径挂载检查
        if not self.path_status.get("is_mounted", False):
            logger.warning(f"⚠️ 警告：路径未挂载到宿主机，容器重启后数据将丢失！")
            logger.warning(f"⚠️ 建议：在docker run命令中使用 -v 参数挂载该路径")
            logger.warning(f"⚠️ 例如：docker run -v /宿主机路径:/www/dk_project/dk_app/astrbot/astrbot_nJZP/data/saved_images ...")
    
    def _validate_and_fix_path(self, user_path: str) -> Tuple[Path, Dict]:
        """验证并修复保存路径 - 包含挂载检测"""
        result = {
            "is_valid": True,
            "message": "路径正常",
            "original_path": user_path,
            "actual_path": "",
            "is_mounted": False,
            "mount_message": "",
            "warnings": [],
            "errors": [],
            "fallback": False,
            "fallback_reason": ""
        }
        
        # 转换为绝对路径
        if not os.path.isabs(user_path):
            # 尝试多种可能的根目录
            possible_roots = [
                "/AstrBot",      # 容器内的标准根目录
                "/app",          # 可能的其他根目录
                "/data",         # 可能的数据目录
                os.getcwd(),     # 当前工作目录
            ]
            
            for root in possible_roots:
                potential_path = os.path.join(root, user_path)
                if os.path.exists(os.path.dirname(potential_path)):
                    user_path = potential_path
                    result["warnings"].append(f"将相对路径转换为绝对路径: {potential_path}")
                    break
            else:
                # 如果没找到，使用当前工作目录
                user_path = os.path.abspath(user_path)
                result["warnings"].append(f"使用当前工作目录作为根目录: {user_path}")
        
        path = Path(user_path)
        result["actual_path"] = str(path.absolute())
        
        # 第一步：检查路径是否存在且可写
        writable, writable_msg = MountChecker.check_path_writable(path)
        if not writable:
            result["is_valid"] = False
            result["errors"].append(f"❌ 路径不可写: {writable_msg}")
            result["message"] = f"❌ 路径不可写: {writable_msg}"
            return self._fallback_to_default_path(result)
        
        # 第二步：检查路径是否挂载
        is_mounted, mount_msg = MountChecker.is_path_mounted(path)
        result["is_mounted"] = is_mounted
        result["mount_message"] = mount_msg
        
        if not is_mounted:
            # 路径未挂载，记录警告但不自动回退
            result["warnings"].append(f"⚠️ 路径未挂载: {mount_msg}")
            result["warnings"].append("⚠️ 注意: 路径未挂载到宿主机，容器重启后数据将丢失！")
            
            # 不再自动回退到挂载路径，而是使用用户配置的路径
            logger.warning(f"⚠️ 用户配置路径未挂载: {path.absolute()}")
            logger.warning(f"⚠️ 挂载状态: {mount_msg}")
            logger.warning("⚠️ 容器重启后数据将丢失！")
            
            # 继续使用用户配置的路径
            result["message"] = f"⚠️ 路径可用但未挂载: {path.absolute()}"
            return path, result
        
        # 第三步：检查磁盘空间（可选）
        try:
            if path.exists():
                stat = os.statvfs(path)
                free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
                if free_gb < 1:  # 小于1GB空间
                    result["warnings"].append(f"⚠️ 磁盘空间不足: {free_gb:.1f}GB 可用")
        except:
            pass
        
        result["message"] = f"✅ 路径可用且已挂载: {path.absolute()}"
        return path, result
    
    def _fallback_to_mounted_path(self, original_result: Dict) -> Tuple[Path, Dict]:
        """回退到已挂载的路径"""
        # 尝试多个已知的已挂载路径
        mounted_paths = [
            "/AstrBot/data/saved_images",  # 默认挂载路径
            "/AstrBot/data/images",        # 备选挂载路径
        ]
        
        result = original_result.copy()
        result["fallback"] = True
        result["fallback_reason"] = "路径未挂载"
        
        for mounted_path in mounted_paths:
            try:
                path = Path(mounted_path)
                
                # 检查是否挂载
                is_mounted, mount_msg = MountChecker.is_path_mounted(path)
                if not is_mounted:
                    logger.debug(f"路径 {mounted_path} 未挂载，跳过")
                    continue
                
                # 检查是否可写
                writable, writable_msg = MountChecker.check_path_writable(path)
                if not writable:
                    logger.debug(f"路径 {mounted_path} 不可写，跳过")
                    continue
                
                # 使用这个路径
                path.mkdir(parents=True, exist_ok=True)
                result["actual_path"] = str(path.absolute())
                result["is_mounted"] = True
                result["mount_message"] = mount_msg
                result["message"] = f"⚠️ 已回退到挂载路径: {path.absolute()} (原因: {original_result['mount_message']})"
                result["warnings"].append(f"已回退到挂载路径: {path.absolute()}")
                
                logger.info(f"🔄 已回退到挂载路径: {path.absolute()}")
                logger.info(f"🔄 回退原因: {original_result['mount_message']}")
                
                return path, result
                
            except Exception as e:
                logger.debug(f"尝试挂载路径 {mounted_path} 失败: {e}")
                continue
        
        # 如果所有挂载路径都失败，回退到临时目录
        logger.error("❌ 所有挂载路径都不可用，回退到临时目录")
        return self._fallback_to_temp_path(result)
    
    def _fallback_to_default_path(self, original_result: Dict) -> Tuple[Path, Dict]:
        """回退到默认路径（不检查挂载）"""
        default_paths = [
            "/AstrBot/data/saved_images",  # 默认路径1
            "/tmp/astrbot_images",         # 默认路径2（临时目录）
        ]
        
        result = original_result.copy()
        result["fallback"] = True
        result["fallback_reason"] = original_result["errors"][-1] if original_result["errors"] else "路径不可用"
        
        for default_path in default_paths:
            try:
                path = Path(default_path)
                path.mkdir(parents=True, exist_ok=True)
                
                # 检查是否可写
                writable, writable_msg = MountChecker.check_path_writable(path)
                if writable:
                    result["actual_path"] = str(path.absolute())
                    result["message"] = f"⚠️ 已回退到默认路径: {path.absolute()} (原因: {result['fallback_reason']})"
                    result["warnings"].append(f"已回退到默认路径: {path.absolute()}")
                    return path, result
            except Exception as e:
                continue
        
        # 如果所有默认路径都失败，使用临时目录
        logger.error("❌ 所有默认路径都失败，回退到临时目录")
        return self._fallback_to_temp_path(result)
    
    def _fallback_to_temp_path(self, result: Dict) -> Tuple[Path, Dict]:
        """回退到临时目录"""
        try:
            # 尝试多个临时目录
            temp_dirs = [
                "/tmp/astrbot_images_fallback",
                "/tmp/astrbot_plugin_images",
            ]
            
            for temp_dir in temp_dirs:
                try:
                    path = Path(temp_dir)
                    path.mkdir(parents=True, exist_ok=True)
                    
                    # 检查是否可写
                    writable, writable_msg = MountChecker.check_path_writable(path)
                    if writable:
                        result["actual_path"] = str(path.absolute())
                        result["message"] = f"🚨 已回退到临时路径: {path.absolute()} (数据不持久)"
                        result["warnings"].append(f"警告: 使用临时路径，容器重启后数据会丢失")
                        result["warnings"].append(f"建议: 请在AstrBot插件配置中设置已挂载的路径")
                        return path, result
                except:
                    continue
            
            # 最后的手段：当前目录
            current_dir = Path.cwd() / "saved_images"
            current_dir.mkdir(parents=True, exist_ok=True)
            result["actual_path"] = str(current_dir.absolute())
            result["message"] = f"🚨 已回退到当前目录: {current_dir.absolute()} (数据不持久)"
            return current_dir, result
            
        except Exception as e:
            # 极端情况：创建临时文件
            import tempfile
            temp_dir = tempfile.mkdtemp(prefix="astrbot_images_")
            path = Path(temp_dir)
            result["actual_path"] = str(path.absolute())
            result["message"] = f"🚨 已创建临时目录: {path.absolute()} (会话结束后数据会丢失)"
            return path, result
    
    def get_save_path(self, sender_name: str = "", group_id: str = "") -> Path:
        """根据日期和群号获取保存路径"""
        # 如果路径无效，记录警告
        if not self.path_valid and self.save_to_log:
            logger.warning(f"⚠️ 使用回退路径: {self.base_save_path.absolute()}")
            for warning in self.path_status.get("warnings", []):
                logger.warning(f"  {warning}")
            for error in self.path_status.get("errors", []):
                logger.error(f"  {error}")
        
        # 创建群号文件夹
        if group_id:
            group_path = self.base_save_path / str(group_id)
            try:
                if not group_path.exists():
                    group_path.mkdir(parents=True, exist_ok=True)
                    if self.save_to_log:
                        logger.info(f"🏠 创建群号文件夹: {group_path}")
                        logger.info(f"📁 群号文件夹绝对路径: {group_path.absolute()}")
            except Exception as e:
                logger.error(f"❌ 创建群号文件夹失败 {group_path}: {e}")
                # 如果无法创建群号文件夹，使用基础目录
                group_path = self.base_save_path
        else:
            group_path = self.base_save_path
        
        # 创建发送者昵称文件夹
        if sender_name:
            # 处理特殊字符
            safe_sender_name = sender_name.replace('/', '_').replace('\\', '_').replace(':', '_')
            sender_path = group_path / safe_sender_name
            try:
                if not sender_path.exists():
                    sender_path.mkdir(parents=True, exist_ok=True)
                    if self.save_to_log:
                        logger.info(f"👤 创建发送者文件夹: {sender_path}")
                        logger.info(f"📁 发送者文件夹绝对路径: {sender_path.absolute()}")
            except Exception as e:
                logger.error(f"❌ 创建发送者文件夹失败 {sender_path}: {e}")
                # 如果无法创建发送者文件夹，使用群号目录
                sender_path = group_path
        else:
            sender_path = group_path
        
        # 创建日期文件夹
        today_str = datetime.now().strftime("%Y-%m-%d")
        date_path = sender_path / today_str
        
        try:
            if not date_path.exists():
                date_path.mkdir(parents=True, exist_ok=True)
                if self.save_to_log:
                    logger.info(f"📅 创建日期文件夹: {date_path}")
                    logger.info(f"📁 日期文件夹绝对路径: {date_path.absolute()}")
        except Exception as e:
            logger.error(f"❌ 创建日期文件夹失败 {date_path}: {e}")
            # 如果无法创建日期文件夹，尝试使用发送者目录
            date_path = sender_path
        
        # 不需要按群号分组，直接返回日期路径
        return date_path
    
    async def save_image(self, file_source: Any, save_dir: Path, filename: str) -> bool:
        """保存图片到本地"""
        save_path = save_dir / filename
        
        try:
            # 检查目录是否存在且可写
            if not save_dir.exists():
                logger.error(f"❌ 保存目录不存在: {save_dir}")
                return False
            
            # 检查文件是否已存在（避免覆盖）
            if save_path.exists():
                # 如果文件已存在，添加后缀
                base_name = save_path.stem
                counter = 1
                while save_path.exists():
                    save_path = save_dir / f"{base_name}_{counter}{save_path.suffix}"
                    counter += 1
                logger.debug(f"📝 文件已存在，使用新名称: {save_path.name}")
            
            # 处理不同类型的file_source
            if isinstance(file_source, str):
                if file_source.startswith('file://'):
                    # 处理文件URL
                    file_path = file_source[7:]  # 移除file://
                    logger.info(f"💾 处理文件URL: {file_path}")
                    return await self._save_local_file(file_path, save_path)
                elif file_source.startswith('http://') or file_source.startswith('https://'):
                    # 处理网络图片URL
                    logger.info(f"🌐 处理网络图片URL: {file_source}")
                    return await self._save_url_image(file_source, save_path)
                else:
                    # 尝试作为文件路径处理
                    logger.info(f"🔍 尝试作为文件路径处理: {file_source}")
                    try:
                        if await self._save_local_file(file_source, save_path):
                            return True
                    except:
                        pass
                    # 如果文件路径处理失败，尝试使用aiocqhttp API
                    logger.info(f"🔍 文件路径处理失败，尝试使用aiocqhttp API: {file_source}")
                    return await self._save_image_from_event(file_source, save_path)
            elif isinstance(file_source, bytes):
                # 直接保存二进制数据
                logger.info(f"🔢 保存二进制数据，大小: {len(file_source)} bytes")
                async with aiofiles.open(save_path, 'wb') as f:
                    await f.write(file_source)
                logger.info(f"✅ 保存二进制图片: {save_path.name} ({len(file_source)} bytes)")
                return True
            else:
                logger.warning(f"⚠️ 不支持的file_source类型: {type(file_source)}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 保存图片失败 {filename}: {e}", exc_info=True)
            return False
    
    async def _save_local_file(self, file_path: str, save_path: Path) -> bool:
        """保存本地文件"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"❌ 源文件不存在: {file_path}")
                return False
            
            # 获取文件大小
            file_size = os.path.getsize(file_path)
            if file_size > self.max_file_size:
                logger.warning(f"⚠️ 文件过大 ({file_size/1024/1024:.2f}MB)，跳过: {file_path}")
                return False
            
            # 复制文件
            async with aiofiles.open(file_path, 'rb') as src, \
                     aiofiles.open(save_path, 'wb') as dst:
                file_data = await src.read()
                await dst.write(file_data)
            
            logger.info(f"✅ 复制本地文件: {save_path.name} ({file_size/1024:.1f}KB)")
            
            # 验证文件
            if save_path.exists() and save_path.stat().st_size == file_size:
                return True
            else:
                logger.error(f"❌ 文件复制后验证失败")
                return False
                
        except Exception as e:
            logger.error(f"❌ 复制本地文件失败 {file_path}: {e}", exc_info=True)
            return False
    
    async def _save_url_image(self, url: str, save_path: Path) -> bool:
        """从URL下载并保存图片"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.error(f"❌ 下载图片失败，HTTP状态码: {response.status}")
                        return False
                    
                    # 检查文件大小
                    content_length = int(response.headers.get('Content-Length', 0))
                    if content_length > self.max_file_size:
                        logger.warning(f"⚠️ 图片过大 ({content_length/1024/1024:.2f}MB)，跳过: {url}")
                        return False
                    
                    # 读取并保存文件
                    async with aiofiles.open(save_path, 'wb') as f:
                        await f.write(await response.read())
                    
                    logger.info(f"✅ 下载并保存图片: {save_path.name} ({content_length} bytes)")
                    return True
                    
        except Exception as e:
            logger.error(f"❌ 下载图片失败 {url}: {e}", exc_info=True)
            return False
    
    async def _save_image_from_event(self, image_id: str, save_path: Path) -> bool:
        """通过事件对象获取图片（备用方法）"""
        try:
            # 尝试使用平台适配器的API获取图片
            # 这里需要根据实际平台适配器进行调整
            logger.info(f"🔍 尝试使用平台适配器API获取图片: {image_id}")
            
            # 尝试使用aiocqhttp的默认API地址
            api_url = f"http://localhost:5700/get_image?file={image_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        async with aiofiles.open(save_path, 'wb') as f:
                            await f.write(await response.read())
                        logger.info(f"✅ 通过aiocqhttp API获取并保存图片: {save_path.name}")
                        return True
                    else:
                        logger.error(f"❌ 通过aiocqhttp API获取图片失败，HTTP状态码: {response.status}")
            
            # 如果所有方法都失败，返回False
            logger.error(f"❌ 无法获取图片: {image_id}")
            return False
            
        except Exception as e:
            logger.error(f"❌ 通过事件对象获取图片失败 {image_id}: {e}", exc_info=True)
            return False
    
    async def _save_image_by_id(self, image_id: str, save_path: Path) -> bool:
        """通过图片ID获取并保存图片"""
        try:
            # 尝试使用napcat API获取图片
            # 注意：这里需要根据实际API进行调整
            api_url = f"http://localhost:3000/get_image?image_id={image_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status != 200:
                        logger.error(f"❌ 通过API获取图片失败，HTTP状态码: {response.status}")
                        return False
                    
                    # 读取并保存文件
                    async with aiofiles.open(save_path, 'wb') as f:
                        await f.write(await response.read())
                    
                    logger.info(f"✅ 通过API获取并保存图片: {save_path.name}")
                    return True
                    
        except Exception as e:
            logger.error(f"❌ 通过ID获取图片失败 {image_id}: {e}", exc_info=True)
            return False


@register("astrbot_plugin_group_image_saver", "AstrBotHelper", "群聊图片自动保存插件", "2.7.2")
class GroupImageSaverPlugin(Star):
    """AstrBot 群聊图片自动保存插件主类"""
    
    def __init__(self, context: Context, config: dict):
        """初始化插件 - 使用正确的构造函数签名"""
        super().__init__(context)
        
        # 直接从构造函数参数获取配置
        self.config = config or {}
        
        # 如果没有配置，尝试从context的其他属性获取
        if not self.config:
            # 尝试不同的属性名
            for attr_name in ['plugin_config', '_config', 'get_config']:
                if hasattr(context, attr_name):
                    attr_value = getattr(context, attr_name)
                    if callable(attr_value):
                        try:
                            self.config = attr_value() or {}
                            break
                        except:
                            continue
                    elif attr_value:
                        self.config = attr_value or {}
                        break
        
        # 如果还是没有配置，使用默认配置
        if not self.config:
            self.config = {}
            logger.warning("⚠️ 未获取到插件配置，使用空配置")
        
        # 初始化警告标记
        self.warning_sent = False
        self.mount_warning_sent = False
        
        # 确保配置中有必要的键
        default_config = {
            "base_save_path": "/AstrBot/data/saved_images",
            "save_by_group": True,
            "max_file_size_mb": 50,
            "save_to_log": True,
            "supported_platforms": ["AIOCQHTTP"],
            "group_filter_mode": "all",
            "group_whitelist": [],
            "group_blacklist": [],
            "warning_group": "",
            "save_private_images": True,
            "private_filter_mode": "all",
            "private_whitelist": [],
            "private_blacklist": []
        }
        
        for key, value in default_config.items():
            if key not in self.config:
                self.config[key] = value
        
        logger.info(f"🔧 插件配置: {self.config}")
        
        self.image_saver = ImageSaver(self.config)
        
        # 警告群号配置
        self.warning_group = str(self.config.get("warning_group", "")).strip()
        self.warning_sent = False  # 标记是否已发送警告
        
        # 统计信息
        self.stats = {
            'total_images': 0,
            'successful_saves': 0,
            'failed_saves': 0,
            'filtered_groups': 0,  # 新增：被过滤掉的群组数量
            'last_save_time': None
        }
        
        logger.info(f"🚀 群聊图片自动保存插件 v2.6.0 已加载")
        
        # 如果检测到路径回退，发送警告
        if (self.image_saver.path_status.get("fallback", False) and 
            self.warning_group and 
            not self.warning_sent):
            
            # 延迟发送警告，确保插件完全加载
            asyncio.create_task(self.send_warning_message())
    
    async def send_warning_message(self):
        """发送路径回退警告到指定群"""
        try:
            # 等待插件完全加载
            await asyncio.sleep(5)
            
            # 构建警告消息
            warning_msg = [
                "⚠️ 【图片保存插件重要警告】 ⚠️",
                "",
                "检测到您配置的保存路径未被挂载，插件已自动回退到可用路径。",
                "",
                "📊 详细状态：",
                f"原始配置路径：{self.image_saver.path_status.get('original_path', '未知')}",
                f"实际使用路径：{self.image_saver.path_status.get('actual_path', '未知')}",
                f"回退原因：{self.image_saver.path_status.get('fallback_reason', '未知')}",
                "",
                "⚠️ 重要提示：",
                "1. 当前使用的路径是容器内的临时路径",
                "2. 容器重启后，已保存的图片数据将丢失",
                "3. 请在AstrBot插件配置中设置正确的挂载路径",
                "",
                "🔧 解决方法：",
                "1. 检查Docker容器的挂载配置",
                "2. 确保配置的路径在容器内存在且已挂载",
                "3. 建议使用默认路径：/AstrBot/data/saved_images",
                "",
                "💡 提示：如需关闭此警告，请在配置中将warning_group留空。"
            ]
            
            # 发送消息到指定群
            await self.context.send_group_msg(
                group_id=int(self.warning_group),
                message="\n".join(warning_msg)
            )
            
            self.warning_sent = True
            logger.info(f"📢 已发送路径回退警告到群 {self.warning_group}")
            
        except Exception as e:
            logger.error(f"❌ 发送警告消息失败: {e}")
    
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """
        监听所有群消息，提取并保存图片
        """
        # 1. 获取群号和发送者信息
        group_id = str(event.message_obj.group_id)
        
        # 2. 检查群组是否允许保存
        if not self.image_saver.group_filter.is_group_allowed(group_id):
            # 记录被过滤的群组统计
            self.stats['filtered_groups'] += 1
            
            # 只在调试模式下记录日志
            if logger.level <= logging.DEBUG:
                logger.debug(f"⏭️ 群组 {group_id} 被过滤，跳过图片保存")
            return
        
        # 3. 如果这是警告群并且路径已回退，发送简要警告
        if (group_id == self.warning_group and 
            self.image_saver.path_status.get("fallback", False) and 
            not self.warning_sent):
            
            try:
                # 发送简要警告
                brief_warning = (
                    "⚠️ 图片保存插件提醒：当前使用回退路径保存图片，"
                    "容器重启后数据将丢失。请检查插件配置。"
                )
                yield event.plain_result(brief_warning)
                logger.info(f"📢 已向群 {group_id} 发送简要路径警告")
                self.warning_sent = True
            except Exception as e:
                logger.error(f"❌ 发送简要警告失败: {e}")
        
        # 4. 如果路径未挂载但未回退，发送挂载警告
        if (group_id == self.warning_group and 
            not self.image_saver.path_status.get("fallback", False) and 
            not self.image_saver.path_status.get("is_mounted", True) and 
            not self.mount_warning_sent):
            
            try:
                # 发送挂载警告
                mount_warning = (
                    "⚠️ 图片保存插件提醒：当前使用的路径未挂载，"
                    "容器重启后数据将丢失。请将路径挂载到宿主机。"
                )
                yield event.plain_result(mount_warning)
                logger.info(f"📢 已向群 {group_id} 发送挂载警告")
                self.mount_warning_sent = True
            except Exception as e:
                logger.error(f"❌ 发送挂载警告失败: {e}")
        
        # 4. 提取消息中的图片组件
        image_components: List[Comp.Image] = [
            seg for seg in event.message_obj.message 
            if isinstance(seg, Comp.Image)
        ]
        
        if not image_components:
            return
        
        sender_name = event.get_sender_name()
        
        # 5. 记录日志
        logger.info(f"📨 检测到群 {group_id} ({sender_name}) 中的消息包含 {len(image_components)} 张图片")
        
        # 6. 如果路径已回退，在日志中记录详细信息
        if self.image_saver.path_status.get("fallback", False):
            logger.warning(f"🚨 警告：当前使用回退路径保存图片，数据可能丢失！")
            logger.warning(f"🚨 原始路径：{self.image_saver.path_status.get('original_path', '未知')}")
            logger.warning(f"🚨 实际路径：{self.image_saver.path_status.get('actual_path', '未知')}")
        
        # 7. 获取保存路径
        save_dir = self.image_saver.get_save_path(sender_name, group_id)
        logger.info(f"📂 保存目录: {save_dir}")
        
        # 8. 保存每张图片
        saved_count = 0
        for idx, img_comp in enumerate(image_components):
            file_source = img_comp.file
            if not file_source:
                logger.warning(f"⚠️ 图片组件 {idx} 的 file 属性为空")
                continue
            
            # 生成文件名 - 使用消息发送时间戳 + 序号
            msg_time = event.message_obj.time if hasattr(event.message_obj, 'time') else datetime.now().timestamp()
            # 转换为datetime对象
            msg_datetime = datetime.fromtimestamp(msg_time)
            # 格式化为年月日+具体时间
            filename = msg_datetime.strftime("%Y%m%d_%H%M%S")
            # 添加序号，避免多张图片文件名重复
            filename += f"_{idx}"
            # 保留原始文件扩展名（如果有）
            if isinstance(file_source, str) and '.' in file_source:
                original_ext = '.' + file_source.split('.')[-1].lower()
                if original_ext in self.image_saver.IMAGE_EXTENSIONS:
                    filename += original_ext
                else:
                    filename += '.jpg'
            else:
                filename += '.jpg'
            
            logger.info(f"💾 图片 {idx} 将保存为: {filename}")
            
            # 保存图片
            try:
                # 尝试直接从消息组件获取图片数据
                success = False
                
                # 方法1: 检查img_comp是否有data属性
                if hasattr(img_comp, 'data') and img_comp.data:
                    logger.info(f"🔍 尝试直接从消息组件获取图片数据")
                    async with aiofiles.open(save_dir / filename, 'wb') as f:
                        await f.write(img_comp.data)
                    logger.info(f"✅ 直接从消息组件保存图片: {filename}")
                    success = True
                
                # 方法2: 检查是否有url属性
                elif hasattr(img_comp, 'url') and img_comp.url:
                    logger.info(f"🔍 尝试从图片URL下载: {img_comp.url}")
                    success = await self.image_saver._save_url_image(img_comp.url, save_dir / filename)
                
                # 方法3: 尝试使用平台适配器的API
                elif not success:
                    logger.info(f"🔍 尝试使用平台适配器API获取图片: {file_source}")
                    success = await self.image_saver._save_image_from_event(file_source, save_dir / filename)
                
                # 方法4: 尝试使用CQ码获取图片
                elif not success and hasattr(event, 'raw_message'):
                    logger.info(f"🔍 尝试从原始消息中提取图片URL")
                    import re
                    cq_image_match = re.search(r'\[CQ:image,file=(.*?)\]', event.raw_message)
                    if cq_image_match:
                        cq_file = cq_image_match.group(1)
                        # 尝试构造图片URL
                        if cq_file.startswith('http'):
                            success = await self.image_saver._save_url_image(cq_file, save_dir / filename)
                        else:
                            # 尝试使用默认的图片服务器
                            image_url = f"http://localhost:5700/get_image?file={cq_file}"
                            success = await self.image_saver._save_url_image(image_url, save_dir / filename)
            except Exception as e:
                logger.error(f"❌ 保存图片时发生异常: {e}")
                success = False
            
            # 更新统计
            self.stats['total_images'] += 1
            if success:
                self.stats['successful_saves'] += 1
                saved_count += 1
                
                # 确认文件确实被保存
                full_path = save_dir / filename
                if full_path.exists():
                    file_size = full_path.stat().st_size
                    logger.info(f"✅ 图片 {idx} 保存成功: {full_path} ({file_size} bytes)")
                    # 额外检查：打印绝对路径
                    logger.info(f"📁 绝对路径: {full_path.absolute()}")
                    # 额外检查：打印文件存在性
                    logger.info(f"📋 文件存在性检查: {full_path.exists()}")
                    # 额外检查：打印文件权限
                    try:
                        import stat
                        file_stat = os.stat(full_path)
                        logger.info(f"🔐 文件权限: {oct(file_stat.st_mode)[-3:]}")
                    except:
                        pass
                    # 额外检查：打印目录内容
                    try:
                        dir_content = os.listdir(save_dir)
                        logger.info(f"📂 目录内容: {dir_content}")
                    except:
                        pass
                else:
                    logger.error(f"❌ 图片保存后文件不存在: {full_path}")
                    self.stats['successful_saves'] -= 1
                    self.stats['failed_saves'] += 1
            else:
                self.stats['failed_saves'] += 1
                logger.error(f"❌ 图片 {idx} 保存失败")
        
        # 9. 更新最后保存时间
        self.stats['last_save_time'] = datetime.now().isoformat()
        
        # 10. 记录保存结果
        if saved_count > 0:
            logger.info(f"🎉 成功保存 {saved_count}/{len(image_components)} 张图片")
            
            # 如果使用回退路径，记录额外警告
            if self.image_saver.path_status.get("fallback", False):
                logger.warning(f"🚨 警告：图片已保存到回退路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议尽快在插件配置中设置正确的挂载路径。")
            
            # 如果路径未挂载，记录额外警告
            if not self.image_saver.path_status.get("is_mounted", True):
                logger.warning(f"🚨 警告：图片已保存到未挂载的路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议：在docker run命令中使用 -v 参数挂载该路径")
                logger.warning(f"🚨 例如：docker run -v /宿主机路径:/www/dk_project/dk_app/astrbot/astrbot_nJZP/data/saved_images ...")
                logger.warning(f"🚨 或者在AstrBot WebUI中配置已挂载的路径")
        else:
            logger.error(f"❌ 所有图片保存失败，共 {len(image_components)} 张")
    
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """
        监听所有好友消息，提取并保存图片
        """
        # 1. 获取好友信息
        try:
            # 尝试多种方式获取用户ID
            if hasattr(event.message_obj, 'user_id'):
                user_id = str(event.message_obj.user_id)
            elif hasattr(event.message_obj, 'from_id'):
                user_id = str(event.message_obj.from_id)
            elif hasattr(event.message_obj, 'sender_id'):
                user_id = str(event.message_obj.sender_id)
            elif hasattr(event, 'user_id'):
                user_id = str(event.user_id)
            elif hasattr(event, 'from_id'):
                user_id = str(event.from_id)
            elif hasattr(event, 'sender_id'):
                user_id = str(event.sender_id)
            else:
                # 如果无法获取用户ID，使用发送者昵称作为替代
                sender_name = event.get_sender_name()
                user_id = sender_name.replace(' ', '_')
                logger.warning(f"⚠️ 无法获取用户ID，使用发送者昵称作为替代: {user_id}")
        except Exception as e:
            logger.error(f"❌ 获取用户ID失败: {e}")
            return
        
        # 2. 检查是否启用好友图片保存
        if not self.config.get("save_private_images", True):
            logger.debug(f"⏭️ 好友图片保存功能已禁用，跳过")
            return
        
        # 3. 检查好友是否允许保存
        if not self.image_saver.private_filter.is_user_allowed(user_id):
            # 只在调试模式下记录日志
            if logger.level <= logging.DEBUG:
                logger.debug(f"⏭️ 好友 {user_id} 被过滤，跳过图片保存")
            return
        
        # 4. 提取消息中的图片组件
        image_components: List[Comp.Image] = [
            seg for seg in event.message_obj.message 
            if isinstance(seg, Comp.Image)
        ]
        
        if not image_components:
            return
        
        sender_name = event.get_sender_name()
        
        # 5. 记录日志
        logger.info(f"📨 检测到好友 {user_id} ({sender_name}) 发送的消息包含 {len(image_components)} 张图片")
        
        # 6. 如果路径已回退，在日志中记录详细信息
        if self.image_saver.path_status.get("fallback", False):
            logger.warning(f"🚨 警告：当前使用回退路径保存图片，数据可能丢失！")
            logger.warning(f"🚨 原始路径：{self.image_saver.path_status.get('original_path', '未知')}")
            logger.warning(f"🚨 实际路径：{self.image_saver.path_status.get('actual_path', '未知')}")
        
        # 7. 获取保存路径（好友消息不按群号分组）
        save_dir = self.image_saver.get_save_path(sender_name)
        logger.info(f"📂 保存目录: {save_dir}")
        
        # 8. 保存每张图片
        saved_count = 0
        for idx, img_comp in enumerate(image_components):
            file_source = img_comp.file
            if not file_source:
                logger.warning(f"⚠️ 图片组件 {idx} 的 file 属性为空")
                continue
            
            # 生成文件名 - 使用消息发送时间戳 + 序号
            msg_time = event.message_obj.time if hasattr(event.message_obj, 'time') else datetime.now().timestamp()
            # 转换为datetime对象
            msg_datetime = datetime.fromtimestamp(msg_time)
            # 格式化为年月日+具体时间
            filename = msg_datetime.strftime("%Y%m%d_%H%M%S")
            # 添加序号，避免多张图片文件名重复
            filename += f"_{idx}"
            # 保留原始文件扩展名（如果有）
            if isinstance(file_source, str) and '.' in file_source:
                original_ext = '.' + file_source.split('.')[-1].lower()
                if original_ext in self.image_saver.IMAGE_EXTENSIONS:
                    filename += original_ext
                else:
                    filename += '.jpg'
            else:
                filename += '.jpg'
            
            logger.info(f"💾 图片 {idx} 将保存为: {filename}")
            
            # 保存图片
            try:
                # 尝试直接从消息组件获取图片数据
                success = False
                
                # 方法1: 检查img_comp是否有data属性
                if hasattr(img_comp, 'data') and img_comp.data:
                    logger.info(f"🔍 尝试直接从消息组件获取图片数据")
                    async with aiofiles.open(save_dir / filename, 'wb') as f:
                        await f.write(img_comp.data)
                    logger.info(f"✅ 直接从消息组件保存图片: {filename}")
                    success = True
                
                # 方法2: 检查是否有url属性
                elif hasattr(img_comp, 'url') and img_comp.url:
                    logger.info(f"🔍 尝试从图片URL下载: {img_comp.url}")
                    success = await self.image_saver._save_url_image(img_comp.url, save_dir / filename)
                
                # 方法3: 尝试使用平台适配器的API
                elif not success:
                    logger.info(f"🔍 尝试使用平台适配器API获取图片: {file_source}")
                    success = await self.image_saver._save_image_from_event(file_source, save_dir / filename)
                
                # 方法4: 尝试使用CQ码获取图片
                elif not success and hasattr(event, 'raw_message'):
                    logger.info(f"🔍 尝试从原始消息中提取图片URL")
                    import re
                    cq_image_match = re.search(r'\[CQ:image,file=(.*?)\]', event.raw_message)
                    if cq_image_match:
                        cq_file = cq_image_match.group(1)
                        # 尝试构造图片URL
                        if cq_file.startswith('http'):
                            success = await self.image_saver._save_url_image(cq_file, save_dir / filename)
                        else:
                            # 尝试使用默认的图片服务器
                            image_url = f"http://localhost:5700/get_image?file={cq_file}"
                            success = await self.image_saver._save_url_image(image_url, save_dir / filename)
            except Exception as e:
                logger.error(f"❌ 保存图片时发生异常: {e}")
                success = False
            
            # 更新统计
            self.stats['total_images'] += 1
            if success:
                self.stats['successful_saves'] += 1
                saved_count += 1
                
                # 确认文件确实被保存
                full_path = save_dir / filename
                if full_path.exists():
                    file_size = full_path.stat().st_size
                    logger.info(f"✅ 图片 {idx} 保存成功: {full_path} ({file_size} bytes)")
                    # 额外检查：打印绝对路径
                    logger.info(f"📁 绝对路径: {full_path.absolute()}")
                else:
                    logger.error(f"❌ 图片保存后文件不存在: {full_path}")
                    self.stats['successful_saves'] -= 1
                    self.stats['failed_saves'] += 1
            else:
                self.stats['failed_saves'] += 1
                logger.error(f"❌ 图片 {idx} 保存失败")
        
        # 9. 更新最后保存时间
        self.stats['last_save_time'] = datetime.now().isoformat()
        
        # 10. 记录保存结果
        if saved_count > 0:
            logger.info(f"🎉 成功保存 {saved_count}/{len(image_components)} 张好友图片")
            
            # 如果使用回退路径，记录额外警告
            if self.image_saver.path_status.get("fallback", False):
                logger.warning(f"🚨 警告：图片已保存到回退路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议尽快在插件配置中设置正确的挂载路径。")
            
            # 如果路径未挂载，记录额外警告
            if not self.image_saver.path_status.get("is_mounted", True):
                logger.warning(f"🚨 警告：图片已保存到未挂载的路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议：在docker run命令中使用 -v 参数挂载该路径")
        else:
            logger.error(f"❌ 所有好友图片保存失败，共 {len(image_components)} 张")
    
    @filter.command("imgsave_config")
    async def show_config(self, event: AstrMessageEvent):
        """显示当前配置"""
        config_info = []
        config_info.append("⚙️ 当前插件配置:")
        config_info.append("")
        
        # 基础配置
        config_info.append("📊 基础配置:")
        for key, value in self.config.items():
            if key not in ["group_whitelist", "group_blacklist"]:
                if key == "supported_platforms" and isinstance(value, list):
                    config_info.append(f"  {key}: {', '.join(value)}")
                else:
                    config_info.append(f"  {key}: {value}")
        
        # 群组过滤配置
        config_info.append("")
        config_info.append("👥 群组过滤配置:")
        config_info.append(f"  模式: {self.config.get('group_filter_mode', 'all')}")
        
        whitelist = self.config.get("group_whitelist", [])
        blacklist = self.config.get("group_blacklist", [])
        
        if self.config.get("group_filter_mode") == "whitelist":
            if whitelist:
                config_info.append(f"  白名单群组: {', '.join(str(g) for g in whitelist)}")
            else:
                config_info.append("  白名单群组: 空（将不会保存任何图片）")
        elif self.config.get("group_filter_mode") == "blacklist":
            if blacklist:
                config_info.append(f"  黑名单群组: {', '.join(str(g) for g in blacklist)}")
            else:
                config_info.append("  黑名单群组: 空（将保存所有图片）")
        
        # 好友过滤配置
        config_info.append("")
        config_info.append("👤 好友过滤配置:")
        config_info.append(f"  模式: {self.config.get('private_filter_mode', 'all')}")
        config_info.append(f"  保存好友图片: {self.config.get('save_private_images', True)}")
        
        private_whitelist = self.config.get("private_whitelist", [])
        private_blacklist = self.config.get("private_blacklist", [])
        
        if self.config.get("private_filter_mode") == "whitelist":
            if private_whitelist:
                config_info.append(f"  白名单好友: {', '.join(str(g) for g in private_whitelist)}")
            else:
                config_info.append("  白名单好友: 空（将不会保存任何好友图片）")
        elif self.config.get("private_filter_mode") == "blacklist":
            if private_blacklist:
                config_info.append(f"  黑名单好友: {', '.join(str(g) for g in private_blacklist)}")
            else:
                config_info.append("  黑名单好友: 空（将保存所有好友图片）")
        
        # 路径状态
        config_info.append("")
        config_info.append("📁 路径状态:")
        config_info.append(f"  {self.image_saver.path_message}")
        
        # 显示挂载信息
        config_info.append("")
        config_info.append("🔗 挂载状态:")
        config_info.append(f"  {self.image_saver.path_status.get('mount_message', '未知')}")
        
        # 显示详细状态
        if not self.image_saver.path_valid:
            config_info.append("")
            config_info.append("⚠️ 路径问题详情:")
            for warning in self.image_saver.path_status.get("warnings", []):
                config_info.append(f"  {warning}")
            for error in self.image_saver.path_status.get("errors", []):
                config_info.append(f"  {error}")
        
        # 显示回退信息（如果有）
        if self.image_saver.path_status.get("fallback", False):
            config_info.append("")
            config_info.append("🔄 路径回退信息:")
            config_info.append(f"  原因: {self.image_saver.path_status.get('fallback_reason', '未知')}")
            if self.image_saver.path_status.get("original_path"):
                config_info.append(f"  原始路径: {self.image_saver.path_status['original_path']}")
            config_info.append(f"  实际路径: {self.image_saver.path_status['actual_path']}")
            
            # 显示警告信息
            config_info.append("")
            config_info.append("🚨 重要警告:")
            config_info.append("  当前使用回退路径保存图片！")
            config_info.append("  容器重启后，已保存的图片数据将丢失！")
            
            if self.warning_group:
                if self.warning_sent:
                    config_info.append(f"  ⚠️ 已发送警告到群: {self.warning_group}")
                else:
                    config_info.append(f"  ⚠️ 将发送警告到群: {self.warning_group}")
            else:
                config_info.append("  ⚠️ 未配置警告群号，不会发送警告消息")
        
        config_info.append("")
        config_info.append("💡 使用方法:")
        config_info.append("  /imgsave_config - 显示当前配置")
        config_info.append("  /imgsave_test - 测试插件功能")
        config_info.append("  /imgsave_stats - 显示统计信息")
        config_info.append("  /imgsave_groups - 管理群组白名单/黑名单")
        
        yield event.plain_result("\n".join(config_info))
    
    @filter.command("imgsave_test")
    async def test_save(self, event: AstrMessageEvent):
        """测试插件是否正常工作"""
        # 创建测试目录和文件
        test_dir = self.image_saver.get_save_path("test")
        test_file = test_dir / "test.txt"
        
        try:
            # 确保目录存在
            test_dir.mkdir(parents=True, exist_ok=True)
            
            # 写入测试文件
            test_content = f"测试时间: {datetime.now().isoformat()}\n插件版本: 2.6.0\n"
            test_file.write_text(test_content)
            
            if test_file.exists():
                file_size = test_file.stat().st_size
                
                # 获取群组过滤器状态
                filter_status = self.image_saver.group_filter.get_status()
                
                # 获取好友过滤器状态
                private_filter_status = self.image_saver.private_filter.get_status()
                
                result = f"""✅ 插件工作正常

📂 测试目录: {test_dir}
📄 测试文件: {test_file}
📊 文件大小: {file_size} bytes

👥 群组过滤状态:
{filter_status}

👤 好友过滤状态:
{private_filter_status}

📁 路径状态:
{self.image_saver.path_message}

🔗 挂载状态:
{self.image_saver.path_status.get('mount_message', '未知')}

"""
                # 如果路径已回退，添加警告信息
                if self.image_saver.path_status.get("fallback", False):
                    result += f"""🚨 路径回退警告:
原始配置路径: {self.image_saver.path_status.get('original_path', '未知')}
实际使用路径: {self.image_saver.path_status.get('actual_path', '未知')}
回退原因: {self.image_saver.path_status.get('fallback_reason', '未知')}

"""
                
                result += f"""⚙️ 当前配置:
保存路径: {self.image_saver.base_save_path.absolute()}
按群分组: {self.image_saver.save_by_group}
最大文件: {self.image_saver.max_file_size / (1024*1024)} MB
过滤模式: {self.config.get('group_filter_mode', 'all')}
警告群号: {self.warning_group or '未配置'}

📈 当前统计:
检测图片: {self.stats['total_images']}
成功保存: {self.stats['successful_saves']}
保存失败: {self.stats['failed_saves']}
过滤群组: {self.stats['filtered_groups']}
"""
                
                # 清理测试文件
                if test_file.exists():
                    test_file.unlink()
                    
                yield event.plain_result(result)
            else:
                yield event.plain_result(f"❌ 测试文件创建失败: {test_file}")
                
        except Exception as e:
            yield event.plain_result(f"❌ 插件测试失败: {e}")
    
    @filter.command("imgsave_stats")
    async def show_stats(self, event: AstrMessageEvent):
        """显示插件统计信息"""
        # 获取群组过滤器状态
        filter_status = self.image_saver.group_filter.get_status()
        
        # 获取好友过滤器状态
        private_filter_status = self.image_saver.private_filter.get_status()
        
        stats_text = f"""📊 图片保存插件统计信息

👥 群组过滤状态:
{filter_status}

👤 好友过滤状态:
{private_filter_status}

📈 运行统计:
总计检测图片: {self.stats['total_images']}
成功保存: {self.stats['successful_saves']}
保存失败: {self.stats['failed_saves']}
过滤群组: {self.stats['filtered_groups']}
最后保存时间: {self.stats['last_save_time'] or '暂无'}

📁 路径状态:
{self.image_saver.path_message}

🔗 挂载状态:
{self.image_saver.path_status.get('mount_message', '未知')}

"""
        
        # 如果路径已回退，添加警告信息
        if self.image_saver.path_status.get("fallback", False):
            stats_text += f"""🚨 路径回退警告:
原始配置路径: {self.image_saver.path_status.get('original_path', '未知')}
实际使用路径: {self.image_saver.path_status.get('actual_path', '未知')}
回退原因: {self.image_saver.path_status.get('fallback_reason', '未知')}

"""
        
        stats_text += f"""⚙️ 配置信息:
保存路径: {self.image_saver.base_save_path.absolute()}
按群分组: {self.image_saver.save_by_group}
最大文件: {self.image_saver.max_file_size / (1024*1024)} MB
过滤模式: {self.config.get('group_filter_mode', 'all')}
警告群号: {self.warning_group or '未配置'}
好友图片保存: {self.config.get('save_private_images', True)}
好友过滤模式: {self.config.get('private_filter_mode', 'all')}

💡 使用命令:
/imgsave_config - 显示详细配置和路径状态
/imgsave_test - 测试插件功能
/imgsave_stats - 显示统计信息
/imgsave_groups - 管理群组白名单/黑名单
/imgsave_private - 管理好友白名单/黑名单
"""
        yield event.plain_result(stats_text)
    
    @filter.command("imgsave_groups")
    async def manage_groups(self, event: AstrMessageEvent):
        """管理群组白名单/黑名单"""
        args = event.get_message_text().strip().split()
        
        if len(args) < 2:
            # 显示帮助信息
            help_text = """👥 群组管理命令

使用方法:
/imgsave_groups status - 显示当前群组过滤状态
/imgsave_groups mode [all|whitelist|blacklist] - 设置过滤模式
/imgsave_groups whitelist add [群号] - 添加群到白名单
/imgsave_groups whitelist remove [群号] - 从白名单移除群
/imgsave_groups whitelist list - 显示白名单群组
/imgsave_groups blacklist add [群号] - 添加群到黑名单
/imgsave_groups blacklist remove [群号] - 从黑名单移除群
/imgsave_groups blacklist list - 显示黑名单群组

示例:
/imgsave_groups mode whitelist
/imgsave_groups whitelist add 123456789
/imgsave_groups blacklist add 987654321
"""
            yield event.plain_result(help_text)
            return
        
        command = args[1].lower()
        group_filter = self.image_saver.group_filter
        
        if command == "status":
            # 显示当前状态
            status_text = group_filter.get_status()
            
            # 显示详细列表
            if group_filter.mode == "whitelist" and group_filter.whitelist:
                status_text += f"\n\n✅ 白名单群组 ({len(group_filter.whitelist)}个):"
                for gid in sorted(group_filter.whitelist):
                    status_text += f"\n  - {gid}"
            elif group_filter.mode == "blacklist" and group_filter.blacklist:
                status_text += f"\n\n⛔️ 黑名单群组 ({len(group_filter.blacklist)}个):"
                for gid in sorted(group_filter.blacklist):
                    status_text += f"\n  - {gid}"
            
            status_text += "\n\n💡 提示：使用 /imgsave_groups 查看所有可用命令"
            yield event.plain_result(status_text)
        
        elif command == "mode":
            if len(args) < 3:
                yield event.plain_result("❌ 请指定模式：all, whitelist 或 blacklist")
                return
            
            mode = args[2].lower()
            if mode not in ["all", "whitelist", "blacklist"]:
                yield event.plain_result("❌ 无效的模式，请使用：all, whitelist 或 blacklist")
                return
            
            # 更新配置文件
            self.config["group_filter_mode"] = mode
            
            # 重新初始化群组过滤器
            self.image_saver.group_filter = GroupFilter(self.config)
            
            result = f"✅ 已设置群组过滤模式为：{mode}"
            if mode == "whitelist":
                result += f"\n当前白名单群组：{len(group_filter.whitelist)}个"
            elif mode == "blacklist":
                result += f"\n当前黑名单群组：{len(group_filter.blacklist)}个"
            
            yield event.plain_result(result)
        
        elif command == "whitelist":
            if len(args) < 3:
                yield event.plain_result("❌ 请指定操作：add, remove 或 list")
                return
            
            subcmd = args[2].lower()
            
            if subcmd == "add":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要添加的群号")
                    return
                
                group_id = args[3]
                if group_filter.add_to_whitelist(group_id):
                    # 更新配置文件
                    if "group_whitelist" not in self.config:
                        self.config["group_whitelist"] = []
                    
                    # 确保群号在配置中
                    if group_id not in [str(g) for g in self.config["group_whitelist"]]:
                        self.config["group_whitelist"].append(group_id)
                    
                    yield event.plain_result(f"✅ 群组 {group_id} 已添加到白名单")
                else:
                    yield event.plain_result(f"⚠️ 群组 {group_id} 已在白名单中")
            
            elif subcmd == "remove":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要移除的群号")
                    return
                
                group_id = args[3]
                if group_filter.remove_from_whitelist(group_id):
                    # 更新配置文件
                    if "group_whitelist" in self.config:
                        # 从列表中移除
                        self.config["group_whitelist"] = [
                            g for g in self.config["group_whitelist"] 
                            if str(g) != group_id
                        ]
                    
                    yield event.plain_result(f"✅ 群组 {group_id} 已从白名单移除")
                else:
                    yield event.plain_result(f"⚠️ 群组 {group_id} 不在白名单中")
            
            elif subcmd == "list":
                if not group_filter.whitelist:
                    yield event.plain_result("📋 白名单为空")
                else:
                    result = f"📋 白名单群组 ({len(group_filter.whitelist)}个):\n"
                    for gid in sorted(group_filter.whitelist):
                        result += f"  - {gid}\n"
                    result += f"\n💡 当前过滤模式：{group_filter.mode}"
                    yield event.plain_result(result)
            
            else:
                yield event.plain_result("❌ 无效的操作，请使用：add, remove 或 list")
        
        elif command == "blacklist":
            if len(args) < 3:
                yield event.plain_result("❌ 请指定操作：add, remove 或 list")
                return
            
            subcmd = args[2].lower()
            
            if subcmd == "add":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要添加的群号")
                    return
                
                group_id = args[3]
                if group_filter.add_to_blacklist(group_id):
                    # 更新配置文件
                    if "group_blacklist" not in self.config:
                        self.config["group_blacklist"] = []
                    
                    # 确保群号在配置中
                    if group_id not in [str(g) for g in self.config["group_blacklist"]]:
                        self.config["group_blacklist"].append(group_id)
                    
                    yield event.plain_result(f"✅ 群组 {group_id} 已添加到黑名单")
                else:
                    yield event.plain_result(f"⚠️ 群组 {group_id} 已在黑名单中")
            
            elif subcmd == "remove":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要移除的群号")
                    return
                
                group_id = args[3]
                if group_filter.remove_from_blacklist(group_id):
                    # 更新配置文件
                    if "group_blacklist" in self.config:
                        # 从列表中移除
                        self.config["group_blacklist"] = [
                            g for g in self.config["group_blacklist"] 
                            if str(g) != group_id
                        ]
                    
                    yield event.plain_result(f"✅ 群组 {group_id} 已从黑名单移除")
                else:
                    yield event.plain_result(f"⚠️ 群组 {group_id} 不在黑名单中")
            
            elif subcmd == "list":
                if not group_filter.blacklist:
                    yield event.plain_result("📋 黑名单为空")
                else:
                    result = f"📋 黑名单群组 ({len(group_filter.blacklist)}个):\n"
                    for gid in sorted(group_filter.blacklist):
                        result += f"  - {gid}\n"
                    result += f"\n💡 当前过滤模式：{group_filter.mode}"
                    yield event.plain_result(result)
            
            else:
                yield event.plain_result("❌ 无效的操作，请使用：add, remove 或 list")
        
        else:
            yield event.plain_result("❌ 未知命令，使用 /imgsave_groups 查看帮助")
    
    @filter.command("imgsave_private")
    async def manage_private(self, event: AstrMessageEvent):
        """管理好友白名单/黑名单"""
        args = event.get_message_text().strip().split()
        
        if len(args) < 2:
            # 显示帮助信息
            help_text = """👤 好友管理命令

使用方法:
/imgsave_private status - 显示当前好友过滤状态
/imgsave_private mode [all|whitelist|blacklist] - 设置好友过滤模式
/imgsave_private whitelist add [QQ号] - 添加好友到白名单
/imgsave_private whitelist remove [QQ号] - 从白名单移除好友
/imgsave_private whitelist list - 显示白名单好友
/imgsave_private blacklist add [QQ号] - 添加好友到黑名单
/imgsave_private blacklist remove [QQ号] - 从黑名单移除好友
/imgsave_private blacklist list - 显示黑名单好友

示例:
/imgsave_private mode whitelist
/imgsave_private whitelist add 123456789
/imgsave_private blacklist add 987654321
"""
            yield event.plain_result(help_text)
            return
        
        command = args[1].lower()
        private_filter = self.image_saver.private_filter
        
        if command == "status":
            # 显示当前状态
            status_text = private_filter.get_status()
            
            # 显示详细列表
            if private_filter.mode == "whitelist" and private_filter.whitelist:
                status_text += f"\n\n✅ 白名单好友 ({len(private_filter.whitelist)}个):"
                for uid in sorted(private_filter.whitelist):
                    status_text += f"\n  - {uid}"
            elif private_filter.mode == "blacklist" and private_filter.blacklist:
                status_text += f"\n\n⛔️ 黑名单好友 ({len(private_filter.blacklist)}个):"
                for uid in sorted(private_filter.blacklist):
                    status_text += f"\n  - {uid}"
            
            status_text += "\n\n💡 提示：使用 /imgsave_private 查看所有可用命令"
            yield event.plain_result(status_text)
        
        elif command == "mode":
            if len(args) < 3:
                yield event.plain_result("❌ 请指定模式：all, whitelist 或 blacklist")
                return
            
            mode = args[2].lower()
            if mode not in ["all", "whitelist", "blacklist"]:
                yield event.plain_result("❌ 无效的模式，请使用：all, whitelist 或 blacklist")
                return
            
            # 更新配置文件
            self.config["private_filter_mode"] = mode
            
            # 重新初始化好友过滤器
            self.image_saver.private_filter = PrivateFilter(self.config)
            
            result = f"✅ 已设置好友过滤模式为：{mode}"
            if mode == "whitelist":
                result += f"\n当前白名单好友：{len(private_filter.whitelist)}个"
            elif mode == "blacklist":
                result += f"\n当前黑名单好友：{len(private_filter.blacklist)}个"
            
            yield event.plain_result(result)
        
        elif command == "whitelist":
            if len(args) < 3:
                yield event.plain_result("❌ 请指定操作：add, remove 或 list")
                return
            
            subcmd = args[2].lower()
            
            if subcmd == "add":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要添加的QQ号")
                    return
                
                user_id = args[3]
                if private_filter.add_to_whitelist(user_id):
                    # 更新配置文件
                    if "private_whitelist" not in self.config:
                        self.config["private_whitelist"] = []
                    
                    # 确保QQ号在配置中
                    if user_id not in [str(u) for u in self.config["private_whitelist"]]:
                        self.config["private_whitelist"].append(user_id)
                    
                    yield event.plain_result(f"✅ 好友 {user_id} 已添加到白名单")
                else:
                    yield event.plain_result(f"⚠️ 好友 {user_id} 已在白名单中")
            
            elif subcmd == "remove":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要移除的QQ号")
                    return
                
                user_id = args[3]
                if private_filter.remove_from_whitelist(user_id):
                    # 更新配置文件
                    if "private_whitelist" in self.config:
                        # 从列表中移除
                        self.config["private_whitelist"] = [
                            u for u in self.config["private_whitelist"] 
                            if str(u) != user_id
                        ]
                    
                    yield event.plain_result(f"✅ 好友 {user_id} 已从白名单移除")
                else:
                    yield event.plain_result(f"⚠️ 好友 {user_id} 不在白名单中")
            
            elif subcmd == "list":
                if not private_filter.whitelist:
                    yield event.plain_result("📋 白名单为空")
                else:
                    result = f"📋 白名单好友 ({len(private_filter.whitelist)}个):\n"
                    for uid in sorted(private_filter.whitelist):
                        result += f"  - {uid}\n"
                    result += f"\n💡 当前过滤模式：{private_filter.mode}"
                    yield event.plain_result(result)
            
            else:
                yield event.plain_result("❌ 无效的操作，请使用：add, remove 或 list")
        
        elif command == "blacklist":
            if len(args) < 3:
                yield event.plain_result("❌ 请指定操作：add, remove 或 list")
                return
            
            subcmd = args[2].lower()
            
            if subcmd == "add":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要添加的QQ号")
                    return
                
                user_id = args[3]
                if private_filter.add_to_blacklist(user_id):
                    # 更新配置文件
                    if "private_blacklist" not in self.config:
                        self.config["private_blacklist"] = []
                    
                    # 确保QQ号在配置中
                    if user_id not in [str(u) for u in self.config["private_blacklist"]]:
                        self.config["private_blacklist"].append(user_id)
                    
                    yield event.plain_result(f"✅ 好友 {user_id} 已添加到黑名单")
                else:
                    yield event.plain_result(f"⚠️ 好友 {user_id} 已在黑名单中")
            
            elif subcmd == "remove":
                if len(args) < 4:
                    yield event.plain_result("❌ 请指定要移除的QQ号")
                    return
                
                user_id = args[3]
                if private_filter.remove_from_blacklist(user_id):
                    # 更新配置文件
                    if "private_blacklist" in self.config:
                        # 从列表中移除
                        self.config["private_blacklist"] = [
                            u for u in self.config["private_blacklist"] 
                            if str(u) != user_id
                        ]
                    
                    yield event.plain_result(f"✅ 好友 {user_id} 已从黑名单移除")
                else:
                    yield event.plain_result(f"⚠️ 好友 {user_id} 不在黑名单中")
            
            elif subcmd == "list":
                if not private_filter.blacklist:
                    yield event.plain_result("📋 黑名单为空")
                else:
                    result = f"📋 黑名单好友 ({len(private_filter.blacklist)}个):\n"
                    for uid in sorted(private_filter.blacklist):
                        result += f"  - {uid}\n"
                    result += f"\n💡 当前过滤模式：{private_filter.mode}"
                    yield event.plain_result(result)
            
            else:
                yield event.plain_result("❌ 无效的操作，请使用：add, remove 或 list")
        
        else:
            yield event.plain_result("❌ 未知命令，使用 /imgsave_private 查看帮助")
    
    async def terminate(self):
        """插件卸载时调用"""
        logger.info("👋 群聊图片自动保存插件已卸载")
        logger.info(f"📈 最终统计: {self.stats}")