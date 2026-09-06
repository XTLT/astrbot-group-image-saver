AstrBot 群聊图片自动保存插件

自动保存群聊和好友发送的图片，并按群号和日期分类归档，支持图片备注。

✨ 功能特性

✅ 自动监听群聊中的图片消息

✅ 自动监听QQ好友发送的图片消息

✅ 按日期（年-月-日）创建文件夹

✅ 可选按群号分子文件夹保存

✅ 智能文件命名（时间戳+序号+扩展名）

✅ 支持多种图片格式（jpg、png、gif、webp等）

✅ 可配置保存路径和大小限制

✅ 静默运行，不打扰群聊

✅ 多来源取图（消息数据 / 组件文件转换 / 网络URL / 图片ID / CQ码）

✅ 图片备注功能（单图备注追加到文件名，多图备注写入txt文件）

✅ 路径挂载检测和警告

✅ 群组白名单/黑名单过滤

✅ 好友白名单/黑名单过滤

📦 安装

方式一：通过 GitHub 安装（推荐）

在 AstrBot 管理面板的插件市场中输入仓库地址 https://github.com/XTLT/astrbot-group-image-saver 安装。

方式二：手动安装

下载本仓库

将文件夹放入 AstrBot/data/plugins/ 目录

重启 AstrBot 或在插件管理页面重载插件

⚙️ 配置

在 AstrBot 的插件配置页面进行配置：

配置项 默认值 说明

base_save_path /AstrBot/data/saved_images 图片保存的根目录

save_by_group true 是否按群号分子文件夹

max_file_size_mb 50 单张图片最大大小（MB）

save_to_log true 是否记录保存日志

supported_platforms ["AIOCQHTTP"] 支持的平台（其他平台需自行验证）

group_filter_mode all 群组过滤模式：all=所有群, whitelist=仅白名单, blacklist=不保存黑名单

group_whitelist [] 白名单群号列表

group_blacklist [] 黑名单群号列表

warning_group " 接收路径未挂载警告的群号（留空则不发送警告）

save_private_images true 是否保存QQ好友发送的图片

private_filter_mode all 好友过滤模式：all=所有好友, whitelist=仅白名单, blacklist=不保存黑名单

private_whitelist [] 白名单QQ好友列表

private_blacklist [] 黑名单QQ好友列表

save_notes true 是否启用图片备注功能

📁 目录结构

图片保存的目录结构如下：

保存根目录 (base_save_path)/

├── 123456789/ # 群号文件夹（如果 save_by_group=true）

│ ├── 2025-12-30/ # 日期文件夹

│ │ ├── 发送者昵称 / # 发送者昵称文件夹

│ │ │ ├── 图片发送的年月日+图片发送在群里的具体时间.jpg

📝 备注功能

在发送图片时，如果消息携带备注信息（从消息额外信息 notes 字段获取），插件会自动处理：

单张图片备注：备注直接追加到图片文件名后

发送图片 + 备注"重要"

-> 20251230_113630_0_重要.jpg

多张图片备注：在保存目录下创建txt文件保存备注

发送3张图片 + 备注"会议记录"

-> 20251230_113630_备注.txt

内容：

备注信息: 会议记录

保存时间: 2026-12-30 11:36:30

图片数量: 3

图片列表:

1. 20251230_113630_0.jpg

2. 20251230_113630_1.jpg

3. 20251230_113630_2.jpg

可通过配置项 save_notes 关闭此功能。

🔧 平台兼容性

根据 AstrBot 官方文档，以下平台适配器支持情况如下：

平台 支持状态 备注

QQ个人号(aiocqhttp) ✅ 支持 功能完整

Telegram ⚠️ 有限支持 图片获取依赖 OneBot API，可能无法获取

飞书 ⚠️ 有限支持 图片获取依赖 OneBot API，可能无法获取

QQ官方接口 ⚠️ 有限支持 可能无法获取图片

企业微信 ⚠️ 有限支持 需测试验证

钉钉 ❌ 不支持 仅支持HTTP链接图片

使用方法

现在您可以：

在AstrBot WebUI中配置保存路径：

进入插件管理

找到"群聊图片自动保存"插件

点击配置

修改 base_save_path 为您想要的路径，例如：

/Bctp （如果您挂载了这个目录）

/AstrBot/data/saved_images （默认值）

或其他任意容器内的路径

检查当前配置：

在群里发送 /imgsave_config 命令

查看当前使用的配置

测试配置是否生效：

发送 /imgsave_test 命令

发送图片测试保存功能

查看统计信息：

发送 /imgsave_stats 命令

查看图片保存统计数据

管理群组过滤：

发送 /imgsave_groups 命令

配置群组白名单/黑名单

管理好友过滤：

发送 /imgsave_private 命令

配置好友白名单/黑名单

配置生效的原理

插件启动时：

从 context.config 获取用户配置如果没有配置，使用默认配置应用配置到 ImageSaver 实例

用户修改配置时：

在WebUI中修改配置后保存

AstrBot会自动重新加载插件

插件使用新的配置初始化

通过命令（/imgsave_groups、/imgsave_private）修改的过滤配置会自动持久化，插件重载后仍然生效

路径处理：

如果配置的是相对路径（如 data/saved_images）

插件会尝试将其转换为容器内的绝对路径

如果配置的是绝对路径（如 /Bctp），直接使用

🐛 故障排查

图片未保存

检查插件是否启用

检查平台适配器是否支持图片消息

查看日志文件是否有错误信息

权限问题

确保保存目录有写入权限

检查磁盘空间是否充足

网络图片下载失败

检查网络连接

检查图片URL是否有效

备注未生效

确认消息是否携带备注信息（notes 字段）

确认配置项 save_notes 为 true

📄 许可证

MIT License
