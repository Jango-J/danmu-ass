# danmu-ass

把腾讯视频、B 站或爱奇艺的单集链接导出为顶部三行小字 ASS。需要 Python 3.9+，使用 Python 标准库。

```sh
python3 export-ass.py '单集播放链接'
python3 export-ass.py '单集播放链接' --start 15:00 --end 17:00 --rebase -o clip.ass
```

默认输出到项目的「弹幕」目录。B 站支持 BV/av 播放页（含分 P）和番剧 ep 单集链接，读取公开 XML 弹幕池。爱奇艺支持 v_…html 播放页和公开的压缩 XML 分段，其他格式的剧集可能无法导出。平台可获取的弹幕池可能与历史总量不同。

Codex 云端的额外允许域名为 `dm.video.qq.com`（腾讯）、`api.bilibili.com`（B 站）、`pcw-api.iq.com`、`pcw-api.iqiyi.com`、`cmts.iqiyi.com`（爱奇艺）。修改后需重新发布并用新任务验证。
