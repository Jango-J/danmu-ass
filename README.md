# danmu-ass

给自己的视频加一点弹幕。把腾讯视频、B 站或爱奇艺的单集播放链接交给 `danmu-ass`，它会生成一份可以用播放器加载的 **ASS 弹幕文件**。

默认导出整集，弹幕以小字在画面顶部三行滚动。工具会合并短时间内的重复内容，并在弹幕密集时按来源提供的点赞、权重等信号挑选，尽量少遮挡画面。

## 快速开始

需要 Python 3.9 或更新版本。使用标准库，无需安装额外依赖。

```sh
git clone https://github.com/Jango-J/danmu-ass.git
cd danmu-ass
python3 export-ass.py '粘贴单集播放链接'
```

生成的 `.ass` 在 `弹幕/` 目录。打开自己的视频，在播放器里加载这份外部字幕即可。

如果你的视频是从原片剪出的片段，可以只导出对应时间，并让弹幕从片段的零点计时：

```sh
python3 export-ass.py '粘贴单集播放链接' --start 15:00 --end 17:00 --rebase -o clip.ass
```

## 支持范围

| 平台 | 可用链接 |
| --- | --- |
| 腾讯视频 | 单集播放页 |
| B 站 | BV / av 视频页（支持分 P）、番剧 ep 单集页 |
| 爱奇艺 | `v_…html` 单集页，且该集提供公开压缩 XML 弹幕 |

实际可导出的内容取决于平台返回的弹幕池。用自己的剪辑版本播放时，请先对齐视频时间轴。

<details>
<summary>在 Codex 云端使用</summary>

把仓库加入云端环境，允许访问 `dm.video.qq.com`、`api.bilibili.com`、`pcw-api.iq.com`、`pcw-api.iqiyi.com` 和 `cmts.iqiyi.com`，然后发布环境。使用时在云端任务里发单集链接，请 Codex 导出 ASS。

</details>
