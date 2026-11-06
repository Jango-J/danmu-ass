# danmu-ass

从腾讯视频单集播放链接生成顶部三行的小字滚动 ASS。只需要 Python 3.9+，使用 Python 标准库。

```sh
python3 export-ass.py '腾讯视频单集链接'
python3 export-ass.py '腾讯视频单集链接' --start 15:00 --end 17:00 --rebase -o clip.ass
```

默认输出到仓库下的「弹幕」目录，文件名为单集 VID。用播放器加载为外部字幕即可。遇到同名文件时，可用 `-o` 指定新路径。

## Codex 云端

创建云端环境并关联本仓库，在额外允许的域名中加入 `dm.video.qq.com`，然后发布。手机 ChatGPT 的 Codex 里选择该云端环境，发腾讯视频单集链接。任务会生成可保存的 ASS 文件。
