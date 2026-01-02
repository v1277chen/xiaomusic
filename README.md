# XiaoMusic: 無限聽歌，解放小愛音箱

[![GitHub License](https://img.shields.io/github/license/hanxi/xiaomusic)](https://github.com/hanxi/xiaomusic)
[![Docker Image Version](https://img.shields.io/docker/v/hanxi/xiaomusic?sort=semver&label=docker%20image)](https://hub.docker.com/r/hanxi/xiaomusic)
[![Docker Pulls](https://img.shields.io/docker/pulls/hanxi/xiaomusic)](https://hub.docker.com/r/hanxi/xiaomusic)
[![PyPI - Version](https://img.shields.io/pypi/v/xiaomusic)](https://pypi.org/project/xiaomusic/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/xiaomusic)](https://pypi.org/project/xiaomusic/)
[![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fhanxi%2Fxiaomusic%2Fmain%2Fpyproject.toml)](https://pypi.org/project/xiaomusic/)
[![GitHub Release](https://img.shields.io/github/v/release/hanxi/xiaomusic)](https://github.com/hanxi/xiaomusic/releases)
[![Visitors](https://api.visitorbadge.io/api/daily?path=hanxi%2Fxiaomusic&label=daily%20visitor&countColor=%232ccce4&style=flat)](https://visitorbadge.io/status?path=hanxi%2Fxiaomusic)
[![Visitors](https://api.visitorbadge.io/api/visitors?path=hanxi%2Fxiaomusic&label=total%20visitor&countColor=%232ccce4&style=flat)](https://visitorbadge.io/status?path=hanxi%2Fxiaomusic)

使用小愛音箱播放音樂，音樂使用 yt-dlp 下載。

<https://github.com/hanxi/xiaomusic>

文檔: <https://xdocs.hanxi.cc/>

> [!TIP]
> 初次安裝遇到問題請查閱 [💬 FAQ問題集合](https://github.com/hanxi/xiaomusic/issues/99) ，一般遇到的問題都已經有解決辦法。

## 👋 最簡配置運行

已經支持在 web 頁面配置其他參數，docker 啟動命令如下:

```bash
docker run -p 58090:8090 -e XIAOMUSIC_PUBLIC_PORT=58090 -v /xiaomusic_music:/app/music -v /xiaomusic_conf:/app/conf hanxi/xiaomusic
```

🔥 國內：

```bash
docker run -p 58090:8090 -e XIAOMUSIC_PUBLIC_PORT=58090 -v /xiaomusic_music:/app/music -v /xiaomusic_conf:/app/conf docker.hanxi.cc/hanxi/xiaomusic
```

測試版：

```
docker run -p 58090:8090 -e XIAOMUSIC_PUBLIC_PORT=58090 -v /xiaomusic_music:/app/music -v /xiaomusic_conf:/app/conf hanxi/xiaomusic:main
```

對應的 docker compose 配置如下：

```yaml
services:
  xiaomusic:
    image: hanxi/xiaomusic
    container_name: xiaomusic
    restart: unless-stopped
    ports:
      - 58090:8090
    environment:
      XIAOMUSIC_PUBLIC_PORT: 58090
    volumes:
      - /xiaomusic_music:/app/music
      - /xiaomusic_conf:/app/conf
```

🔥 國內：

```yaml
services:
  xiaomusic:
    image: docker.hanxi.cc/hanxi/xiaomusic
    container_name: xiaomusic
    restart: unless-stopped
    ports:
      - 58090:8090
    environment:
      XIAOMUSIC_PUBLIC_PORT: 58090
    volumes:
      - /xiaomusic_music:/app/music
      - /xiaomusic_conf:/app/conf
```

測試版：

```yaml
services:
  xiaomusic:
    image: hanxi/xiaomusic:main
    container_name: xiaomusic
    restart: unless-stopped
    ports:
      - 58090:8090
    environment:
      XIAOMUSIC_PUBLIC_PORT: 58090
    volumes:
      - /xiaomusic_music:/app/music
      - /xiaomusic_conf:/app/conf
```

- 其中 conf 目錄為配置文件存放目錄，music 目錄為音樂存放目錄，建議分開配置為不同的目錄。
- /xiaomusic_music 和 /xiaomusic_conf 是 docker 所在的主機的目錄，可以修改為其他目錄。如果報錯找不到 /xiaomusic_music 目錄，可以先執行 `mkdir -p /xiaomusic_{music,conf}` 命令新建目錄。
- /app/music 和 /app/conf 是 docker 容器裡的目錄，不要去修改。
- XIAOMUSIC_PUBLIC_PORT 是用來配置 NAS 本地端口的。8090 是容器端口，不要去修改。
- 後台訪問地址為： http://NAS_IP:58090

> [!NOTE]
> docker 和 docker compose 二選一即可，啟動成功後，在 web 頁面可以配置其他參數，帶有 `*` 號的配置是必須要配置的，其他的用不上時不用修改。初次配置時需要在頁面上輸入小米帳號和密碼保存後才能獲取到設備列表。

> [!TIP]
> 目前安裝步驟已經是最簡化了，如果還是嫌安裝麻煩，可以微信或者 QQ 約我遠程安裝，我一般週末和晚上才有時間，需要贊助個辛苦費 :moneybag: 50 元一次。

遇到問題可以去 web 設置頁面底部點擊【下載日誌文件】按鈕，然後搜索一下日誌文件內容確保裡面沒有帳號密碼信息後(有就刪除這些敏感信息)，然後在提 issues 反饋問題時把下載的日誌文件帶上。


> [!TIP]
> 作者寫的一個遊戲服務器開發實戰課程 <https://www.lanqiao.cn/courses/2770> ，購買時記得使用優惠碼: `2CZ2UA5u` 。

> [!TIP]
> - 適用於 NAS 上安裝的開源工具： <https://github.com/hanxi/tiny-nav>
> - 適用於 NAS 上安裝的網頁打印機： <https://github.com/hanxi/cups-web>
> - PVE 移動端 UI 界面：<https://github.com/hanxi/pve-touch>
> - 喜歡聽書的可以配合這個工具使用 <https://github.com/hanxi/epub2mp3>

> [!TIP]
>
> - 🔥【廣告:可用於安裝 frp 實現內網穿透】
> - 🔥 海外 RackNerd VPS 機器推薦，可支付寶付款。
> - <a href="https://my.racknerd.com/aff.php?aff=11177"><img src="https://racknerd.com/banners/320x50.gif" alt="RackNerd Mobile Leaderboard Banner" width="320" height="50"></a>
> - 不知道選哪個套餐可以直接買這個最便宜的 <https://my.racknerd.com/aff.php?aff=11177&pid=923>
> - 也可以用來部署代理，docker 部署方法見 <https://github.com/hanxi/blog/issues/96>

> [!TIP]
>
> - 🔥【廣告: 搭建您的專屬大模型主頁
> 告別繁瑣配置難題，一鍵即可暢享穩定流暢的AI體驗！】<https://university.aliyun.com/mobile?userCode=szqvatm6>

> [!TIP]
> - 免費主機
> - <a href="https://dartnode.com?aff=SnappyPigeon570"><img src="https://dartnode.com/branding/DN-Open-Source-sm.png" alt="Powered by DartNode - Free VPS for Open Source" width="320"></a>


### 🤐 支持語音口令

- 【播放歌曲】，播放本地的歌曲
- 【播放歌曲+歌名】，比如：播放歌曲周杰倫晴天
- 【上一首】
- 【下一首】
- 【單曲循環】
- 【全部循環】
- 【隨機播放】
- 【關機】，【停止播放】，兩個效果是一樣的。
- 【刷新列表】，當複製了歌曲進 music 目錄後，可以用這個口令刷新歌單。
- 【播放列表+列表名】，比如：播放列表其他。
- 【加入收藏】，把當前播放的歌曲加入收藏歌單。
- 【取消收藏】，把當前播放的歌曲從收藏歌單裡移除。
- 【播放列表收藏】，這個用於播放收藏歌單。
- ~【播放本地歌曲+歌名】，這個口令和播放歌曲的區別是本地找不到也不會去下載。~
- 【播放列表第幾個+列表名】，具體見： <https://github.com/hanxi/xiaomusic/issues/158>
- 【搜索播放+關鍵詞】，會搜索關鍵詞作為臨時搜索列表播放，比如說【搜索播放林俊傑】，會播放所有林俊傑的歌。
- 【本地搜索播放+關鍵詞】，跟搜索播放的區別是本地找不到也不會去下載。

> [!TIP]
> 隱藏玩法: 對小愛同學說播放歌曲小豬佩奇的故事，會先下載小豬佩奇的故事，然後再播放小豬佩奇的故事。

## 🛠️ pip 方式安裝運行

```shell
> pip install -U xiaomusic
> xiaomusic --help
 __  __  _                   __  __                 _
 \ \/ / (_)   __ _    ___   |  \/  |  _   _   ___  (_)   ___
  \  /  | |  / _` |  / _ \  | |\/| | | | | | / __| | |  / __|
  /  \  | | | (_| | | (_) | | |  | | | |_| | \__ \ | | | (__
 /_/\_\ |_|  \__,_|  \___/  |_|  |_|  \__,_| |___/ |_|  \___|
          XiaoMusic v0.3.69 by: github.com/hanxi

usage: xiaomusic [-h] [--port PORT] [--hardware HARDWARE] [--account ACCOUNT]
                 [--password PASSWORD] [--cookie COOKIE] [--verbose]
                 [--config CONFIG] [--ffmpeg_location FFMPEG_LOCATION]

options:
  -h, --help            show this help message and exit
  --port PORT           監聽端口
  --hardware HARDWARE   小愛音箱型號
  --account ACCOUNT     xiaomi account
  --password PASSWORD   xiaomi password
  --cookie COOKIE       xiaomi cookie
  --verbose             show info
  --config CONFIG       config file path
  --ffmpeg_location FFMPEG_LOCATION
                        ffmpeg bin path
> xiaomusic --config config.json
```

其中 `config.json` 文件可以參考 `config-example.json` 文件配置。見 <https://github.com/hanxi/xiaomusic/issues/94>

不修改默認端口 8090 的情況下，只需要執行 `xiaomusic` 即可啟動。

## 🔩 開發環境運行

- 使用 install_dependencies.sh 下載依賴
- 使用 pdm 安裝環境
- 默認監聽了端口 8090 , 使用其他端口自行修改。

```shell
pdm run xiaomusic.py
````

如果是開發前端界面，可以通過 <http://localhost:8090/docs>
查看有什麼接口。目前的 web 控制台非常簡陋，歡迎有興趣的朋友幫忙實現一個漂亮的前端，需要什麼接口可以隨時提需求。

### 🚦 代碼提交規範

提交前請執行

```
pdm lintfmt
```

用於檢查代碼和格式化代碼。

### 本地編譯 Docker Image

```shell
docker build -t xiaomusic .
```

### 技術棧

- 後端代碼使用 Python 語言編寫。
- HTTP 服務使用的是 FastAPI 框架，~~早期版本使用的是 Flask~~。
- 使用了 Docker ，在 NAS 上安裝更方便。
- 默認的前端主題使用了 jQuery 。

## 已測試支持的設備

| 型號   | 名稱                                                                                             |
| ---- | ---------------------------------------------------------------------------------------------- |
| L06A | [小愛音箱](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.l06a)             |
| L07A | [Redmi小愛音箱 Play](https://home.mi.com/webapp/content/baike/product/index.html?model=xiaomi.wifispeaker.l7a)                     |
| S12/S12A/MDZ-25-DA | [小米AI音箱](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.s12)            |
| LX5A | [小愛音箱 萬能遙控版](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.lx5a)       |
| LX05 | [小愛音箱Play（2019款）](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.lx05)  |
| L15A | [小米AI音箱（第二代）](https://home.mi.com/webapp/content/baike/product/index.html?model=xiaomi.wifispeaker.l15a#/) |
| L16A | [Xiaomi Sound](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.l16a)     |
| L17A | [Xiaomi Sound Pro](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.l17a) |
| LX06 | [小愛音箱Pro](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.lx06)          |
| LX01 | [小愛音箱mini](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.lx01)         |
| L05B | [小愛音箱Play](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.l05b)         |
| L05C | [小米小愛音箱Play 增強版](https://home.mi.com/baike/index.html#/detail?model=xiaomi.wifispeaker.l05c)   |
| L09A | [小米音箱Art](https://home.mi.com/webapp/content/baike/product/index.html?model=xiaomi.wifispeaker.l09a) |
| LX04 X10A X08A | 已經支持的觸屏版 |
| X08C X08E X8F | 已經不需要設置了. ~需要設置【型號兼容模式】選項為 true~ |
| M01/XMYX01JY | 小米小愛音箱HD 需要設置【特殊型號獲取對話記錄】選項為 true 才能語音播放|
| OH2P | XIAOMI 智能音箱 Pro |
| OH2 | XIAOMI 智能音箱 |

型號與產品名稱對照可以在這裡查詢 <https://home.miot-spec.com/s/xiaomi.wifispeaker>

> [!NOTE]
> 如果你的設備支持播放，請反饋給我添加到支持列表裡，謝謝。
> 目前應該所有設備類型都已經支持播放，有問題隨時反饋。
> 其他觸屏版不能播放可以設置【型號兼容模式】選項為 true 試試。見 <https://github.com/hanxi/xiaomusic/issues/30>

## 🎵 支持音樂格式

- mp3
- flac
- wav
- ape
- ogg
- m4a

> [!NOTE]
> 本地音樂會搜索目錄下上面格式的文件，下載的歌曲是 mp3 格式的。
> 已知 L05B L05C LX06 L16A 不支持 flac 格式。
> 如果格式不能播放可以打開【轉換為MP3】和【型號兼容模式】選項。具體見 <https://github.com/hanxi/xiaomusic/issues/153#issuecomment-2328168689>

## 🌏 網絡歌單功能

可以配置一個 json 格式的歌單，支持電台和歌曲，也可以直接用別人分享的鏈接，同時配備了 m3u 文件格式轉換工具，可以很方便的把 m3u 電台文件轉換成網絡歌單格式的 json 文件，具體用法見  <https://github.com/hanxi/xiaomusic/issues/78>

> [!NOTE]
> 歡迎有想法的朋友們製作更多的歌單轉換工具。

## 🍺 更多其他可選配置

見 <https://github.com/hanxi/xiaomusic/issues/333>

## ⚠️ 安全提醒

> [!IMPORTANT]
>
> 1. 如果配置了公網訪問 xiaomusic ，請一定要開啟密碼登陸，並設置複雜的密碼。且不要在公共場所的 WiFi 環境下使用，否則可能造成小米帳號密碼洩露。
> 2. 強烈不建議將小愛音箱的小米帳號綁定攝像頭，代碼難免會有 bug ，一旦小米帳號密碼洩露，可能監控錄像也會洩露。

## 🤔 高級篇

- 自定義口令功能 <https://github.com/hanxi/xiaomusic/issues/105>
- <https://github.com/hanxi/xiaomusic/issues/312>
- <https://github.com/hanxi/xiaomusic/issues/269>
- <https://github.com/hanxi/xiaomusic/issues/159>

## 📢 討論區

- [點擊鏈接加入QQ頻道【xiaomusic】](https://pd.qq.com/s/e2jybz0ss)
- [點擊鏈接加入群聊【xiaomusic官方交流群3】 1072151477](https://qm.qq.com/q/lxIhquqbza)
- <https://github.com/hanxi/xiaomusic/issues>
- [微信群二維碼](https://github.com/hanxi/xiaomusic/issues/86)

## ❤️ 感謝

- [xiaomi](https://www.mi.com/)
- [PDM](https://pdm.fming.dev/latest/)
- [xiaogpt](https://github.com/yihong0618/xiaogpt)
- [MiService](https://github.com/yihong0618/MiService)
- [實現原理](https://github.com/yihong0618/gitblog/issues/258)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [awesome-xiaoai](https://github.com/zzz6519003/awesome-xiaoai)
- [微信小程序: 卯卯音樂](https://github.com/F-loat/xiaoplayer)
- [pure 主題 xiaomusicUI](https://github.com/52fisher/xiaomusicUI)
- [移動端的播放器主題](https://github.com/52fisher/XMusicPlayer)
- [Tailwind主題](https://github.com/clarencejh/xiaomusic)
- [SoundScape主題](https://github.com/jhao0413/SoundScape)
- [一個第三方的主題](https://github.com/DarrenWen/xiaomusicui)
- [Umami 統計](https://github.com/umami-software/umami)
- [Sentry 報錯監控](https://github.com/getsentry/sentry)
- [JS在線播放插件](https://github.com/boluofan/xiaomusic-online)
- 所有幫忙調試和測試的朋友
- 所有反饋問題和建議的朋友

### 👉 其他教程

更多功能見 [📝 文檔匯總](https://github.com/hanxi/xiaomusic/issues/211)

## 🚨 免責聲明

本項目僅供學習和研究目的，不得用於任何商業活動。用戶在使用本項目時應遵守所在地區的法律法規，對於違法使用所導致的後果，本項目及作者不承擔任何責任。
本項目可能存在未知的缺陷和風險（包括但不限於設備損壞和帳號封禁等），使用者應自行承擔使用本項目所產生的所有風險及責任。
作者不保證本項目的準確性、完整性、及時性、可靠性，也不承擔任何因使用本項目而產生的任何損失或損害責任。
使用本項目即表示您已閱讀並同意本免責聲明的全部內容。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=hanxi/xiaomusic&type=Date)](https://star-history.com/#hanxi/xiaomusic&Date)

## 贊賞

- :moneybag: 愛發電 <https://afdian.com/a/imhanxi>
- 點個 Star :star:
- 謝謝 :heart:
- ![喝杯奶茶](https://i.v2ex.co/7Q03axO5l.png)

## License

[MIT](https://github.com/hanxi/xiaomusic/blob/main/LICENSE) License © 2024 涵曦

## 文檔部署 (Documentation Deployment)

本專案包含自動部署文檔到 GitHub Pages 的 GitHub Actions Workflow。

### 啟用步驟
1.  Fork 本專案到你的 GitHub 帳號。
2.  進入倉庫的 **Settings** -> **Pages**。
3.  在 **Build and deployment** 下的 **Source** 選擇 **Deploy from a branch**。
4.  **Branch** 選擇 `gh-pages`，文件夾選擇 `/ (root)`。
    *   注意：`gh-pages` 分支會在第一次 Action 運行成功後自動創建。如果還沒看到該分支，請先進行第 5 步觸發一次構建。
5.  確保 **Settings** -> **Actions** -> **General** 中的 **Workflow permissions** 設置為 **Read and write permissions**。
6.  修改 `docs/` 目錄下的任意文件並推送到 `master` 或 `main` 分支，即可觸發自動部署。

### 本地預覽文檔
如果你想在本地預覽文檔：
```bash
cd docs
npm install
npm run docs:dev
```
服務將啟動在 `http://localhost:3030`。
