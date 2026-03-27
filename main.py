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
import asyncio
import logging
import re
from datetime import datetime
from typing import List

import aiofiles

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from .mount_checker import MountChecker
from .group_filter import GroupFilter
from .private_filter import PrivateFilter
from .image_saver import ImageSaver


@register("astrbot_plugin_group_image_saver", "AstrBotHelper", "群聊图片自动保存插件", "2.7.2")
class GroupImageSaverPlugin(Star):
    """AstrBot 群聊图片自动保存插件主类"""
    
    def __init__(self, context: Context, config: dict):
        """初始化插件 - 使用正确的构造函数签名"""
        super().__init__(context)
        
        self.config = config or {}
        
        if not self.config:
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
        
        if not self.config:
            self.config = {}
            logger.warning("⚠️ 未获取到插件配置，使用空配置")
        
        self.warning_sent = False
        self.mount_warning_sent = False
        
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
        
        self.warning_group = str(self.config.get("warning_group", "")).strip()
        self.warning_sent = False
        
        self.stats = {
            'total_images': 0,
            'successful_saves': 0,
            'failed_saves': 0,
            'filtered_groups': 0,
            'last_save_time': None
        }
        
        logger.info(f"🚀 群聊图片自动保存插件 v2.6.0 已加载")
        
        if (self.image_saver.path_status.get("fallback", False) and 
            self.warning_group and 
            not self.warning_sent):
            asyncio.create_task(self.send_warning_message())
    
    async def send_warning_message(self):
        """发送路径回退警告到指定群"""
        try:
            await asyncio.sleep(5)
            
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
        """监听所有群消息，提取并保存图片"""
        group_id = str(event.message_obj.group_id)
        
        if not self.image_saver.group_filter.is_group_allowed(group_id):
            self.stats['filtered_groups'] += 1
            
            if logger.level <= logging.DEBUG:
                logger.debug(f"⏭️ 群组 {group_id} 被过滤，跳过图片保存")
            return
        
        if (group_id == self.warning_group and 
            self.image_saver.path_status.get("fallback", False) and 
            not self.warning_sent):
            
            try:
                brief_warning = (
                    "⚠️ 图片保存插件提醒：当前使用回退路径保存图片，"
                    "容器重启后数据将丢失。请检查插件配置。"
                )
                yield event.plain_result(brief_warning)
                logger.info(f"📢 已向群 {group_id} 发送简要路径警告")
                self.warning_sent = True
            except Exception as e:
                logger.error(f"❌ 发送简要警告失败: {e}")
        
        if (group_id == self.warning_group and 
            not self.image_saver.path_status.get("fallback", False) and 
            not self.image_saver.path_status.get("is_mounted", True) and 
            not self.mount_warning_sent):
            
            try:
                mount_warning = (
                    "⚠️ 图片保存插件提醒：当前使用的路径未挂载，"
                    "容器重启后数据将丢失。请将路径挂载到宿主机。"
                )
                yield event.plain_result(mount_warning)
                logger.info(f"📢 已向群 {group_id} 发送挂载警告")
                self.mount_warning_sent = True
            except Exception as e:
                logger.error(f"❌ 发送挂载警告失败: {e}")
        
        image_components: List[Comp.Image] = [
            seg for seg in event.message_obj.message 
            if isinstance(seg, Comp.Image)
        ]
        
        if not image_components:
            return
        
        sender_name = event.get_sender_name()
        
        logger.info(f"📨 检测到群 {group_id} ({sender_name}) 中的消息包含 {len(image_components)} 张图片")
        
        if self.image_saver.path_status.get("fallback", False):
            logger.warning(f"🚨 警告：当前使用回退路径保存图片，数据可能丢失！")
            logger.warning(f"🚨 原始路径：{self.image_saver.path_status.get('original_path', '未知')}")
            logger.warning(f"🚨 实际路径：{self.image_saver.path_status.get('actual_path', '未知')}")
        
        save_dir = self.image_saver.get_save_path(sender_name, group_id)
        logger.info(f"📂 保存目录: {save_dir}")
        
        saved_count = 0
        for idx, img_comp in enumerate(image_components):
            file_source = img_comp.file
            if not file_source:
                logger.warning(f"⚠️ 图片组件 {idx} 的 file 属性为空")
                continue
            
            msg_time = event.message_obj.time if hasattr(event.message_obj, 'time') else datetime.now().timestamp()
            msg_datetime = datetime.fromtimestamp(msg_time)
            filename = msg_datetime.strftime("%Y%m%d_%H%M%S")
            filename += f"_{idx}"
            if isinstance(file_source, str) and '.' in file_source:
                original_ext = '.' + file_source.split('.')[-1].lower()
                if original_ext in self.image_saver.IMAGE_EXTENSIONS:
                    filename += original_ext
                else:
                    filename += '.jpg'
            else:
                filename += '.jpg'
            
            logger.info(f"💾 图片 {idx} 将保存为: {filename}")
            
            try:
                success = False
                
                if hasattr(img_comp, 'data') and img_comp.data:
                    logger.info(f"🔍 尝试直接从消息组件获取图片数据")
                    async with aiofiles.open(save_dir / filename, 'wb') as f:
                        await f.write(img_comp.data)
                    logger.info(f"✅ 直接从消息组件保存图片: {filename}")
                    success = True
                
                elif hasattr(img_comp, 'url') and img_comp.url:
                    logger.info(f"🔍 尝试从图片URL下载: {img_comp.url}")
                    success = await self.image_saver._save_url_image(img_comp.url, save_dir / filename)
                
                elif not success:
                    logger.info(f"🔍 尝试使用平台适配器API获取图片: {file_source}")
                    success = await self.image_saver._save_image_from_event(file_source, save_dir / filename)
                
                elif not success and hasattr(event, 'raw_message'):
                    logger.info(f"🔍 尝试从原始消息中提取图片URL")
                    cq_image_match = re.search(r'\[CQ:image,file=(.*?)\]', event.raw_message)
                    if cq_image_match:
                        cq_file = cq_image_match.group(1)
                        if cq_file.startswith('http'):
                            success = await self.image_saver._save_url_image(cq_file, save_dir / filename)
                        else:
                            image_url = f"http://localhost:5700/get_image?file={cq_file}"
                            success = await self.image_saver._save_url_image(image_url, save_dir / filename)
            except Exception as e:
                logger.error(f"❌ 保存图片时发生异常: {e}")
                success = False
            
            self.stats['total_images'] += 1
            if success:
                self.stats['successful_saves'] += 1
                saved_count += 1
                
                full_path = save_dir / filename
                if full_path.exists():
                    file_size = full_path.stat().st_size
                    logger.info(f"✅ 图片 {idx} 保存成功: {full_path} ({file_size} bytes)")
                    logger.info(f"📁 绝对路径: {full_path.absolute()}")
                    logger.info(f"📋 文件存在性检查: {full_path.exists()}")
                    try:
                        import stat
                        file_stat = os.stat(full_path)
                        logger.info(f"🔐 文件权限: {oct(file_stat.st_mode)[-3:]}")
                    except:
                        pass
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
        
        self.stats['last_save_time'] = datetime.now().isoformat()
        
        if saved_count > 0:
            logger.info(f"🎉 成功保存 {saved_count}/{len(image_components)} 张图片")
            
            if self.image_saver.path_status.get("fallback", False):
                logger.warning(f"🚨 警告：图片已保存到回退路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议尽快在插件配置中设置正确的挂载路径。")
            
            if not self.image_saver.path_status.get("is_mounted", True):
                logger.warning(f"🚨 警告：图片已保存到未挂载的路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议：在docker run命令中使用 -v 参数挂载该路径")
                logger.warning(f"🚨 例如：docker run -v /宿主机路径:/www/dk_project/dk_app/astrbot/astrbot_nJZP/data/saved_images ...")
                logger.warning(f"🚨 或者在AstrBot WebUI中配置已挂载的路径")
        else:
            logger.error(f"❌ 所有图片保存失败，共 {len(image_components)} 张")
    
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """监听所有好友消息，提取并保存图片"""
        try:
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
                sender_name = event.get_sender_name()
                user_id = sender_name.replace(' ', '_')
                logger.warning(f"⚠️ 无法获取用户ID，使用发送者昵称作为替代: {user_id}")
        except Exception as e:
            logger.error(f"❌ 获取用户ID失败: {e}")
            return
        
        if not self.config.get("save_private_images", True):
            logger.debug(f"⏭️ 好友图片保存功能已禁用，跳过")
            return
        
        if not self.image_saver.private_filter.is_user_allowed(user_id):
            if logger.level <= logging.DEBUG:
                logger.debug(f"⏭️ 好友 {user_id} 被过滤，跳过图片保存")
            return
        
        image_components: List[Comp.Image] = [
            seg for seg in event.message_obj.message 
            if isinstance(seg, Comp.Image)
        ]
        
        if not image_components:
            return
        
        sender_name = event.get_sender_name()
        
        logger.info(f"📨 检测到好友 {user_id} ({sender_name}) 发送的消息包含 {len(image_components)} 张图片")
        
        if self.image_saver.path_status.get("fallback", False):
            logger.warning(f"🚨 警告：当前使用回退路径保存图片，数据可能丢失！")
            logger.warning(f"🚨 原始路径：{self.image_saver.path_status.get('original_path', '未知')}")
            logger.warning(f"🚨 实际路径：{self.image_saver.path_status.get('actual_path', '未知')}")
        
        save_dir = self.image_saver.get_save_path(sender_name)
        logger.info(f"📂 保存目录: {save_dir}")
        
        saved_count = 0
        for idx, img_comp in enumerate(image_components):
            file_source = img_comp.file
            if not file_source:
                logger.warning(f"⚠️ 图片组件 {idx} 的 file 属性为空")
                continue
            
            msg_time = event.message_obj.time if hasattr(event.message_obj, 'time') else datetime.now().timestamp()
            msg_datetime = datetime.fromtimestamp(msg_time)
            filename = msg_datetime.strftime("%Y%m%d_%H%M%S")
            filename += f"_{idx}"
            if isinstance(file_source, str) and '.' in file_source:
                original_ext = '.' + file_source.split('.')[-1].lower()
                if original_ext in self.image_saver.IMAGE_EXTENSIONS:
                    filename += original_ext
                else:
                    filename += '.jpg'
            else:
                filename += '.jpg'
            
            logger.info(f"💾 图片 {idx} 将保存为: {filename}")
            
            try:
                success = False
                
                if hasattr(img_comp, 'data') and img_comp.data:
                    logger.info(f"🔍 尝试直接从消息组件获取图片数据")
                    async with aiofiles.open(save_dir / filename, 'wb') as f:
                        await f.write(img_comp.data)
                    logger.info(f"✅ 直接从消息组件保存图片: {filename}")
                    success = True
                
                elif hasattr(img_comp, 'url') and img_comp.url:
                    logger.info(f"🔍 尝试从图片URL下载: {img_comp.url}")
                    success = await self.image_saver._save_url_image(img_comp.url, save_dir / filename)
                
                elif not success:
                    logger.info(f"🔍 尝试使用平台适配器API获取图片: {file_source}")
                    success = await self.image_saver._save_image_from_event(file_source, save_dir / filename)
                
                elif not success and hasattr(event, 'raw_message'):
                    logger.info(f"🔍 尝试从原始消息中提取图片URL")
                    cq_image_match = re.search(r'\[CQ:image,file=(.*?)\]', event.raw_message)
                    if cq_image_match:
                        cq_file = cq_image_match.group(1)
                        if cq_file.startswith('http'):
                            success = await self.image_saver._save_url_image(cq_file, save_dir / filename)
                        else:
                            image_url = f"http://localhost:5700/get_image?file={cq_file}"
                            success = await self.image_saver._save_url_image(image_url, save_dir / filename)
            except Exception as e:
                logger.error(f"❌ 保存图片时发生异常: {e}")
                success = False
            
            self.stats['total_images'] += 1
            if success:
                self.stats['successful_saves'] += 1
                saved_count += 1
                
                full_path = save_dir / filename
                if full_path.exists():
                    file_size = full_path.stat().st_size
                    logger.info(f"✅ 图片 {idx} 保存成功: {full_path} ({file_size} bytes)")
                    logger.info(f"📁 绝对路径: {full_path.absolute()}")
                else:
                    logger.error(f"❌ 图片保存后文件不存在: {full_path}")
                    self.stats['successful_saves'] -= 1
                    self.stats['failed_saves'] += 1
            else:
                self.stats['failed_saves'] += 1
                logger.error(f"❌ 图片 {idx} 保存失败")
        
        self.stats['last_save_time'] = datetime.now().isoformat()
        
        if saved_count > 0:
            logger.info(f"🎉 成功保存 {saved_count}/{len(image_components)} 张好友图片")
            
            if self.image_saver.path_status.get("fallback", False):
                logger.warning(f"🚨 警告：图片已保存到回退路径，容器重启后数据将丢失！")
                logger.warning(f"🚨 建议尽快在插件配置中设置正确的挂载路径。")
            
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
        
        config_info.append("📊 基础配置:")
        for key, value in self.config.items():
            if key not in ["group_whitelist", "group_blacklist"]:
                if key == "supported_platforms" and isinstance(value, list):
                    config_info.append(f"  {key}: {', '.join(value)}")
                else:
                    config_info.append(f"  {key}: {value}")
        
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
        
        config_info.append("")
        config_info.append("📁 路径状态:")
        config_info.append(f"  {self.image_saver.path_message}")
        
        config_info.append("")
        config_info.append("🔗 挂载状态:")
        config_info.append(f"  {self.image_saver.path_status.get('mount_message', '未知')}")
        
        if not self.image_saver.path_valid:
            config_info.append("")
            config_info.append("⚠️ 路径问题详情:")
            for warning in self.image_saver.path_status.get("warnings", []):
                config_info.append(f"  {warning}")
            for error in self.image_saver.path_status.get("errors", []):
                config_info.append(f"  {error}")
        
        if self.image_saver.path_status.get("fallback", False):
            config_info.append("")
            config_info.append("🔄 路径回退信息:")
            config_info.append(f"  原因: {self.image_saver.path_status.get('fallback_reason', '未知')}")
            if self.image_saver.path_status.get("original_path"):
                config_info.append(f"  原始路径: {self.image_saver.path_status['original_path']}")
            config_info.append(f"  实际路径: {self.image_saver.path_status['actual_path']}")
            
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
        test_dir = self.image_saver.get_save_path("test")
        test_file = test_dir / "test.txt"
        
        try:
            test_dir.mkdir(parents=True, exist_ok=True)
            
            test_content = f"测试时间: {datetime.now().isoformat()}\n插件版本: 2.6.0\n"
            test_file.write_text(test_content)
            
            if test_file.exists():
                file_size = test_file.stat().st_size
                
                filter_status = self.image_saver.group_filter.get_status()
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
        filter_status = self.image_saver.group_filter.get_status()
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
            status_text = group_filter.get_status()
            
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
            
            self.config["group_filter_mode"] = mode
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
                    if "group_whitelist" not in self.config:
                        self.config["group_whitelist"] = []
                    
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
                    if "group_whitelist" in self.config:
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
                    if "group_blacklist" not in self.config:
                        self.config["group_blacklist"] = []
                    
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
                    if "group_blacklist" in self.config:
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
            status_text = private_filter.get_status()
            
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
            
            self.config["private_filter_mode"] = mode
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
                    if "private_whitelist" not in self.config:
                        self.config["private_whitelist"] = []
                    
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
                    if "private_whitelist" in self.config:
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
                    if "private_blacklist" not in self.config:
                        self.config["private_blacklist"] = []
                    
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
                    if "private_blacklist" in self.config:
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
