from typing import Dict, Any, Set

from astrbot.api import logger


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
