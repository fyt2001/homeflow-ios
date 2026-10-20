# HomeFlow

在电脑上预览、分组和整理 iPhone 桌面。右手优先，常用入口排序，先保存快照，写入后重新读取核对。

**实验性工具。** 使用 SpringBoard 传统布局接口，不是苹果公开支持的桌面编辑 API。现代 iOS 的自由位置不能完整表达；系统可能补回图标。快照只覆盖读取到的传统布局，不是整机备份，也不能保证恢复自由位置。

[工具页面](https://fyt2001.top/tools/homeflow/) · [FYT 博客](https://fyt2001.top/)

## 快速开始

1. Windows 安装 Python 3.12+ 及苹果官方 Apple Devices / iTunes 所提供的设备驱动。无需 AltServer、Apple ID 签名、开发者账号或越狱。
2. 下载本仓库，解压后双击 **Start-Windows.cmd**。首次运行会创建独立 Python 环境并从 PyPI 安装依赖，之后自动打开本机页面。当前提供源码启动器，**没有独立 exe 安装包**。
3. 数据线连接一台 iPhone，解锁，在手机上点击“信任此电脑”。
4. 在助手打开的本机页面直接读取；若使用博客工具页，将助手窗口显示的本次连接码粘贴进去。浏览器可能询问本地网络访问权限。公网网页连接受限时使用本机页面。
5. 点击“读取并备份桌面”，点选常用 App（按频率顺序），生成方案，逐页预览，确认影响后应用。当前保留 Dock、整个首页和所有小组件页面；后续页有小组件时拒绝生成方案。

macOS / Linux 可以尝试同一 Python 安装方式，但本项目未做跨平台真机验证，Windows 启动脚本不适用：

```sh
python -m venv .venv
# 激活虚拟环境后
python -m pip install .
homeflow
```

停止助手：在助手窗口按 Ctrl+C。每次启动生成新连接码，旧码失效。连接码不写入浏览器存储，自动连接 URL 的 fragment 在读取后移除。

## 数据与恢复

快照、方案、写入日志保存在本机用户目录 `~/.homeflow/`，不上传网站。它们包含设备标识、应用列表等隐私，请勿提交到 GitHub。演示数据完全虚构。网页不读取系统屏幕使用时间，常用顺序由使用者提供；分类时长只能作为大类参考，不能当成单 App 打开频率。

每次写入前：核对设备身份、系统版本、当前布局摘要、应用实例数量和元数据、被保护页面；先持久化日志，再写入。写入后重新读取，只有完整摘要一致才报告成功。失败不盲目重试或自动覆盖恢复。请保持连接并查看本机事务记录。

网页的“撤回本次整理”只在同一次助手会话中、成功写入后可用，且要求当前桌面仍与写入结果一致。重启后可以用本机命令行读取事务记录恢复：

```sh
python -m homeflow.core restore --file "事务记录的完整路径" --output "恢复记录目录" --accept-legacy-layout --accept-widget-roundtrip --yes
```

该命令仍会执行同样的校验。请先检查事务记录是否属于当前设备。风险确认不能替代完整设备备份。

## 网站接入

将 `homeflow/web/` 三个文件复制到现有网站的 `/tools/homeflow/`，博客增加一个该路径的入口。页面内已有返回博客入口。静态页面无需后端服务器或第三方字体。UI 使用原创 CSS 和文字占位图标，不提供 App 图标原图或 iOS 自由位置的精确重建。

助手只监听 `127.0.0.1:27841`，仅接受本次随机连接码；公网连接允许的来源明确列在 `server.py`。自托管请把自己的 HTTPS 域名加入 `origins`，不可改为通配来源。浏览器本地网络限制存在差异，本机页面是备用路径。

## 复用方案与验证范围

| 方案 | 采用理由 / 结果 |
| --- | --- |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) / [PyPI](https://pypi.org/project/pymobiledevice3/11.36.0/) | 复用配对、USB 连接和 SpringBoard 服务；固定 11.36.0。GPL-3.0，依赖较大，因此放在本机，不打入网页。此前 Windows + 一台 iOS 26.6.2 真机完成读取、最小改名恢复及布局写入核对，不能外推到全部设备。 |
| [FastAPI](https://github.com/fastapi/fastapi) / [PyPI](https://pypi.org/project/fastapi/) | MIT，复用数据验证、CORS 和本机接口服务；已通过本项目模拟设备链路及权限测试。 |
| [iconstate](https://github.com/Jubstaaa/iconstate) | 已查看 README，提供 Mac 桌面整理方案；不直接满足 Windows + 博客页面接入，未安装验证，未复制其实现。 |
| [Chrome 本地网络访问说明](https://developer.chrome.com/blog/local-network-access) | 已阅读；HTTPS 网站访问回环地址可能需要浏览器许可。不能保证所有浏览器远程页面连接成功，保留本机 UI。 |

博客现有 Vue 3 / Vite / lucide-vue-next 继续复用；独立工具页是无构建 HTML/CSS/JS，不额外引入大型前端依赖。分组与右手排序为少量自定义规则，未知应用保留原文件夹名，散落应用收进“其他应用”。不足一整页时保持紧凑排列，不伪造底部空白槽位。

## 开发

```sh
python -m pip install '.[test]'
python -m unittest discover -s tests -v
```

自动测试使用模拟设备，不会写入真实 iPhone。当前网页桥接层的真机写入尚未复测；既有命令行核心的真机结果不等于网页端跨浏览器兼容性保证。

## 许可

GPL-3.0-or-later，见 [LICENSE](LICENSE)。pymobiledevice3 的版权和许可证归其贡献者所有。项目与 Apple Inc. 无关联。公开仓库只包含工具代码和虚构示例，不包含个人博客源代码、手机快照、配对记录或连接凭据。
