import os
import asyncio
import aiohttp
import aiofiles
from pathlib import Path
from typing import Dict, Any, Tuple
from datetime import datetime

from astrbot.api import logger
from .mount_checker import MountChecker
from .group_filter import GroupFilter
from .private_filter import PrivateFilter


class ImageSaver:
    """图片保存管理器"""
    
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico', '.tiff'}
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        user_base_path = config.get("base_save_path", "/AstrBot/data/saved_images")
        
        self.base_save_path, self.path_status = self._validate_and_fix_path(user_base_path)
        
        self.path_valid = self.path_status["is_valid"]
        self.path_message = self.path_status["message"]
        
        self.save_by_group = config.get("save_by_group", True)
        self.max_file_size = config.get("max_file_size_mb", 50) * 1024 * 1024
        self.save_to_log = config.get("save_to_log", True)
        
        self.supported_platforms = set(
            platform.upper() 
            for platform in config.get("supported_platforms", ["AIOCQHTTP"])
        )
        
        self.group_filter = GroupFilter(config)
        self.private_filter = PrivateFilter(config)
        
        logger.info(f"📁 图片自动保存器已初始化")
        logger.info(f"📂 用户配置路径: {user_base_path}")
        logger.info(f"📂 实际使用路径: {self.base_save_path.absolute()}")
        logger.info(f"📊 路径状态: {self.path_message}")
        logger.info(f"📊 按群号分组: {self.save_by_group}")
        logger.info(f"📏 最大文件大小: {self.max_file_size / (1024*1024)} MB")
        logger.info(f"📊 群组过滤模式: {self.group_filter.mode}")
        logger.info(f"📊 好友过滤模式: {self.private_filter.mode}")
        
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
        
        if not os.path.isabs(user_path):
            possible_roots = [
                "/AstrBot",
                "/app",
                "/data",
                os.getcwd(),
            ]
            
            for root in possible_roots:
                potential_path = os.path.join(root, user_path)
                if os.path.exists(os.path.dirname(potential_path)):
                    user_path = potential_path
                    result["warnings"].append(f"将相对路径转换为绝对路径: {potential_path}")
                    break
            else:
                user_path = os.path.abspath(user_path)
                result["warnings"].append(f"使用当前工作目录作为根目录: {user_path}")
        
        path = Path(user_path)
        result["actual_path"] = str(path.absolute())
        
        writable, writable_msg = MountChecker.check_path_writable(path)
        if not writable:
            result["is_valid"] = False
            result["errors"].append(f"❌ 路径不可写: {writable_msg}")
            result["message"] = f"❌ 路径不可写: {writable_msg}"
            return self._fallback_to_default_path(result)
        
        is_mounted, mount_msg = MountChecker.is_path_mounted(path)
        result["is_mounted"] = is_mounted
        result["mount_message"] = mount_msg
        
        if not is_mounted:
            result["warnings"].append(f"⚠️ 路径未挂载: {mount_msg}")
            result["warnings"].append("⚠️ 注意: 路径未挂载到宿主机，容器重启后数据将丢失！")
            
            logger.warning(f"⚠️ 用户配置路径未挂载: {path.absolute()}")
            logger.warning(f"⚠️ 挂载状态: {mount_msg}")
            logger.warning("⚠️ 容器重启后数据将丢失！")
            
            result["message"] = f"⚠️ 路径可用但未挂载: {path.absolute()}"
            return path, result
        
        try:
            if path.exists():
                stat = os.statvfs(path)
                free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
                if free_gb < 1:
                    result["warnings"].append(f"⚠️ 磁盘空间不足: {free_gb:.1f}GB 可用")
        except:
            pass
        
        result["message"] = f"✅ 路径可用且已挂载: {path.absolute()}"
        return path, result
    
    def _fallback_to_mounted_path(self, original_result: Dict) -> Tuple[Path, Dict]:
        """回退到已挂载的路径"""
        mounted_paths = [
            "/AstrBot/data/saved_images",
            "/AstrBot/data/images",
        ]
        
        result = original_result.copy()
        result["fallback"] = True
        result["fallback_reason"] = "路径未挂载"
        
        for mounted_path in mounted_paths:
            try:
                path = Path(mounted_path)
                
                is_mounted, mount_msg = MountChecker.is_path_mounted(path)
                if not is_mounted:
                    logger.debug(f"路径 {mounted_path} 未挂载，跳过")
                    continue
                
                writable, writable_msg = MountChecker.check_path_writable(path)
                if not writable:
                    logger.debug(f"路径 {mounted_path} 不可写，跳过")
                    continue
                
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
        
        logger.error("❌ 所有挂载路径都不可用，回退到临时目录")
        return self._fallback_to_temp_path(result)
    
    def _fallback_to_default_path(self, original_result: Dict) -> Tuple[Path, Dict]:
        """回退到默认路径（不检查挂载）"""
        default_paths = [
            "/AstrBot/data/saved_images",
            "/tmp/astrbot_images",
        ]
        
        result = original_result.copy()
        result["fallback"] = True
        result["fallback_reason"] = original_result["errors"][-1] if original_result["errors"] else "路径不可用"
        
        for default_path in default_paths:
            try:
                path = Path(default_path)
                path.mkdir(parents=True, exist_ok=True)
                
                writable, writable_msg = MountChecker.check_path_writable(path)
                if writable:
                    result["actual_path"] = str(path.absolute())
                    result["message"] = f"⚠️ 已回退到默认路径: {path.absolute()} (原因: {result['fallback_reason']})"
                    result["warnings"].append(f"已回退到默认路径: {path.absolute()}")
                    return path, result
            except Exception as e:
                continue
        
        logger.error("❌ 所有默认路径都失败，回退到临时目录")
        return self._fallback_to_temp_path(result)
    
    def _fallback_to_temp_path(self, result: Dict) -> Tuple[Path, Dict]:
        """回退到临时目录"""
        try:
            temp_dirs = [
                "/tmp/astrbot_images_fallback",
                "/tmp/astrbot_plugin_images",
            ]
            
            for temp_dir in temp_dirs:
                try:
                    path = Path(temp_dir)
                    path.mkdir(parents=True, exist_ok=True)
                    
                    writable, writable_msg = MountChecker.check_path_writable(path)
                    if writable:
                        result["actual_path"] = str(path.absolute())
                        result["message"] = f"🚨 已回退到临时路径: {path.absolute()} (数据不持久)"
                        result["warnings"].append(f"警告: 使用临时路径，容器重启后数据会丢失")
                        result["warnings"].append(f"建议: 请在AstrBot插件配置中设置已挂载的路径")
                        return path, result
                except:
                    continue
            
            current_dir = Path.cwd() / "saved_images"
            current_dir.mkdir(parents=True, exist_ok=True)
            result["actual_path"] = str(current_dir.absolute())
            result["message"] = f"🚨 已回退到当前目录: {current_dir.absolute()} (数据不持久)"
            return current_dir, result
            
        except Exception as e:
            import tempfile
            temp_dir = tempfile.mkdtemp(prefix="astrbot_images_")
            path = Path(temp_dir)
            result["actual_path"] = str(path.absolute())
            result["message"] = f"🚨 已创建临时目录: {path.absolute()} (会话结束后数据会丢失)"
            return path, result
    
    def get_save_path(self, sender_name: str = "", group_id: str = "") -> Path:
        """根据日期和群号获取保存路径"""
        if not self.path_valid and self.save_to_log:
            logger.warning(f"⚠️ 使用回退路径: {self.base_save_path.absolute()}")
            for warning in self.path_status.get("warnings", []):
                logger.warning(f"  {warning}")
            for error in self.path_status.get("errors", []):
                logger.error(f"  {error}")
        
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
                group_path = self.base_save_path
        else:
            group_path = self.base_save_path
        
        if sender_name:
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
                sender_path = group_path
        else:
            sender_path = group_path
        
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
            date_path = sender_path
        
        return date_path
    
    async def save_image(self, file_source: Any, save_dir: Path, filename: str) -> bool:
        """保存图片到本地"""
        save_path = save_dir / filename
        
        try:
            if not save_dir.exists():
                logger.error(f"❌ 保存目录不存在: {save_dir}")
                return False
            
            if save_path.exists():
                base_name = save_path.stem
                counter = 1
                while save_path.exists():
                    save_path = save_dir / f"{base_name}_{counter}{save_path.suffix}"
                    counter += 1
                logger.debug(f"📝 文件已存在，使用新名称: {save_path.name}")
            
            if isinstance(file_source, str):
                if file_source.startswith('file://'):
                    file_path = file_source[7:]
                    logger.info(f"💾 处理文件URL: {file_path}")
                    return await self._save_local_file(file_path, save_path)
                elif file_source.startswith('http://') or file_source.startswith('https://'):
                    logger.info(f"🌐 处理网络图片URL: {file_source}")
                    return await self._save_url_image(file_source, save_path)
                else:
                    logger.info(f"🔍 尝试作为文件路径处理: {file_source}")
                    try:
                        if await self._save_local_file(file_source, save_path):
                            return True
                    except:
                        pass
                    logger.info(f"🔍 文件路径处理失败，尝试使用aiocqhttp API: {file_source}")
                    return await self._save_image_from_event(file_source, save_path)
            elif isinstance(file_source, bytes):
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
            
            file_size = os.path.getsize(file_path)
            if file_size > self.max_file_size:
                logger.warning(f"⚠️ 文件过大 ({file_size/1024/1024:.2f}MB)，跳过: {file_path}")
                return False
            
            async with aiofiles.open(file_path, 'rb') as src, \
                     aiofiles.open(save_path, 'wb') as dst:
                file_data = await src.read()
                await dst.write(file_data)
            
            logger.info(f"✅ 复制本地文件: {save_path.name} ({file_size/1024:.1f}KB)")
            
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
                    
                    content_length = int(response.headers.get('Content-Length', 0))
                    if content_length > self.max_file_size:
                        logger.warning(f"⚠️ 图片过大 ({content_length/1024/1024:.2f}MB)，跳过: {url}")
                        return False
                    
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
            logger.info(f"🔍 尝试使用平台适配器API获取图片: {image_id}")
            
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
            
            logger.error(f"❌ 无法获取图片: {image_id}")
            return False
            
        except Exception as e:
            logger.error(f"❌ 通过事件对象获取图片失败 {image_id}: {e}", exc_info=True)
            return False
    
    async def _save_image_by_id(self, image_id: str, save_path: Path) -> bool:
        """通过图片ID获取并保存图片"""
        try:
            api_url = f"http://localhost:3000/get_image?image_id={image_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status != 200:
                        logger.error(f"❌ 通过API获取图片失败，HTTP状态码: {response.status}")
                        return False
                    
                    async with aiofiles.open(save_path, 'wb') as f:
                        await f.write(await response.read())
                    
                    logger.info(f"✅ 通过API获取并保存图片: {save_path.name}")
                    return True
                    
        except Exception as e:
            logger.error(f"❌ 通过ID获取图片失败 {image_id}: {e}", exc_info=True)
            return False
