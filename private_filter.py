from typing import Dict, Any

from astrbot.api import logger


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
