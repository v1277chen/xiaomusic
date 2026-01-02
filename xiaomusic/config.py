from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from typing import get_type_hints

from xiaomusic.const import (
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)
from xiaomusic.utils import validate_proxy


# 默認指令關鍵詞與動作映射字典
# key: 用戶說的關鍵詞
# value: 對應執行的內部函數名或指令代碼
def default_key_word_dict():
    return {
        "下一首": "play_next",             # 播放下一首
        "上一首": "play_prev",             # 播放上一首
        "單曲循環": "set_play_type_one",   # 設置為單曲循環模式
        "全部循環": "set_play_type_all",   # 設置為全部循環模式
        "隨機播放": "set_play_type_rnd",   # 設置為隨機播放模式
        "單曲播放": "set_play_type_sin",   # 設置為單曲播放（播完停止）
        "順序播放": "set_play_type_seq",   # 設置為順序播放模式
        "分鐘後關機": "stop_after_minute",  # X分鐘後停止播放
        "刷新列表": "gen_music_list",      # 重新掃描並生成音樂列表
        "加入收藏": "add_to_favorites",    # 將當前歌曲加入收藏
        "收藏歌曲": "add_to_favorites",    # 同上
        "取消收藏": "del_from_favorites",  # 將當前歌曲從收藏移除
        "播放列表第": "play_music_list_index", # 播放特定列表中的第 N 首
        "刪除歌曲": "cmd_del_music",       # 刪除當前播放的歌曲文件
    }


# 默認用戶自定義關鍵詞字典（示例）
# 這裡展示了如何使用 exec# 指令來執行更複雜的操作
def default_user_key_word_dict():
    return {
        "測試自定義口令": 'exec#code1("hello")',  # 執行名為 code1 的自定義代碼片段
        "測試鏈接": 'exec#httpget("https://github.com/hanxi/xiaomusic")', # 執行 HTTP GET 請求
    }


# 定義哪些指令的參數位於關鍵詞之前
# 例如 "10分鐘後關機"，"10" 是參數，位於 "分鐘後關機" 之前
KEY_WORD_ARG_BEFORE_DICT = {
    "分鐘後關機": True,
}


# 關鍵詞匹配優先級列表
# 匹配時會按照列表順序進行遍歷，長度較長的關鍵詞通常應放在前面以避免誤匹配
def default_key_match_order():
    return [
        "分鐘後關機",
        "下一首",
        "上一首",
        "單曲循環",
        "全部循環",
        "隨機播放",
        "單曲播放",
        "順序播放",
        "關機",
        "刷新列表",
        "播放列表第",
        "播放列表",
        "加入收藏",
        "收藏歌曲",
        "取消收藏",
        "刪除歌曲",
    ]


@dataclass
class Device:
    """
    設備信息數據類
    存儲單個小愛音箱的運行狀態與配置
    """
    did: str = ""           # 設備唯一標識符 (Device ID)
    device_id: str = ""     # 設備 ID (通常與 did 相同或相關聯)
    hardware: str = ""      # 硬體型號代碼 (如 L06A, LX06)
    name: str = ""          # 設備名稱 (用戶自定義的名稱)
    play_type: int = PLAY_TYPE_RND  # 當前播放模式 (默認為隨機播放)
    cur_music: str = ""     # 當前正在播放的歌曲名稱
    cur_playlist: str = ""  # 當前使用的播放列表名稱
    playlist2music: dict[str, str] = field(default_factory=dict) # 記錄各播放列表最後一次播放的歌曲，用於斷點續傳


@dataclass
class Config:
    """
    全局配置類
    存儲所有系統配置，支持從環境變量、命令行參數和配置文件加載
    """
    # 小米帳號配置
    account: str = os.getenv("MI_USER", "")  # 小米帳號，環境變量 MI_USER
    password: str = os.getenv("MI_PASS", "") # 小米密碼，環境變量 MI_PASS
    mi_did: str = os.getenv("MI_DID", "")    # 指定的設備 DID，逗號分割支持多設備，環境變量 MI_DID
    cookie: str = ""                         # 小米登錄 Cookie，可手動指定

    # 系統設置
    verbose: bool = os.getenv("XIAOMUSIC_VERBOSE", "").lower() == "true" # 是否開啟詳細日誌
    music_path: str = os.getenv("XIAOMUSIC_MUSIC_PATH", "music")         # 音樂文件存放目錄
    temp_path: str = os.getenv("XIAOMUSIC_TEMP_PATH", "music/tmp")       # 臨時文件目錄
    download_path: str = os.getenv("XIAOMUSIC_DOWNLOAD_PATH", "music/download") # 下載音樂存放目錄
    conf_path: str = os.getenv("XIAOMUSIC_CONF_PATH", "conf")            # 配置文件存放目錄
    cache_dir: str = os.getenv("XIAOMUSIC_CACHE_DIR", "cache")           # 緩存目錄
    hostname: str = os.getenv("XIAOMUSIC_HOSTNAME", "192.168.2.5")       # 本機 IP 或域名，用於構造回調 URL
    port: int = int(os.getenv("XIAOMUSIC_PORT", "8090"))                 # 服務監聽端口
    public_port: int = int(os.getenv("XIAOMUSIC_PUBLIC_PORT", 0))        # 公網訪問端口，如果為 0 則默認與 port 相同
    proxy: str = os.getenv("XIAOMUSIC_PROXY", None)                      # 全局 HTTP 代理設置
    loudnorm: str = os.getenv("XIAOMUSIC_LOUDNORM", None)                # ffmpeg 音量均衡參數

    # 搜索相關
    search_prefix: str = os.getenv(
        "XIAOMUSIC_SEARCH", "bilisearch:"
    )  # 默認搜索引擎前綴，支持 "bilisearch:" (B站) or "ytsearch:" (YouTube)

    # 外部工具路徑
    ffmpeg_location: str = os.getenv("XIAOMUSIC_FFMPEG_LOCATION", "./ffmpeg/bin") # ffmpeg 可執行文件路徑
    get_duration_type: str = os.getenv(
        "XIAOMUSIC_GET_DURATION_TYPE", "ffprobe"
    )  # 獲取音頻時長的方式：mutagen (python庫) or ffprobe (外部命令)

    # 允許執行的指令列表，用於安全過濾
    active_cmd: str = os.getenv(
        "XIAOMUSIC_ACTIVE_CMD",
        "play,search_play,set_play_type_rnd,playlocal,search_playlocal,play_music_list,play_music_list_index,stop_after_minute,stop",
    )
    
    # 掃描音樂時排除的目錄
    exclude_dirs: str = os.getenv("XIAOMUSIC_EXCLUDE_DIRS", "@eaDir,tmp")
    
    # 掃描音樂時忽略標籤信息的目錄列表
    ignore_tag_dirs: str = os.getenv("XIAOMUSIC_IGNORE_TAG_DIRS", "")

    # 掃描音樂目錄的最大深度
    music_path_depth: int = int(os.getenv("XIAOMUSIC_MUSIC_PATH_DEPTH", "10"))

    # HTTP Basic Auth 認證配置
    disable_httpauth: bool = (
        os.getenv("XIAOMUSIC_DISABLE_HTTPAUTH", "true").lower() == "true"
    )  # 是否禁用 HTTP 認證 (默認禁用)
    httpauth_username: str = os.getenv("XIAOMUSIC_HTTPAUTH_USERNAME", "") # HTTP 認證用戶名
    httpauth_password: str = os.getenv("XIAOMUSIC_HTTPAUTH_PASSWORD", "") # HTTP 認證密碼

    # 遠程歌單配置
    music_list_url: str = os.getenv("XIAOMUSIC_MUSIC_LIST_URL", "")     # 遠程歌單 URL
    music_list_json: str = os.getenv("XIAOMUSIC_MUSIC_LIST_JSON", "")   # 遠程歌單 JSON 內容
    custom_play_list_json: str = os.getenv("XIAOMUSIC_CUSTOM_PLAY_LIST_JSON", "") # 自定義播放列表 JSON

    # 下載控制
    disable_download: bool = (
        os.getenv("XIAOMUSIC_DISABLE_DOWNLOAD", "false").lower() == "true"
    ) # 是否禁止下載歌曲 (僅在線播放)

    # 關鍵詞配置
    key_word_dict: dict[str, str] = field(default_factory=default_key_word_dict)
    key_match_order: list[str] = field(default_factory=default_key_match_order)

    # 特殊音樂 API 配置 (針對某些設備無需 URL 播放)
    use_music_api: bool = (
        os.getenv("XIAOMUSIC_USE_MUSIC_API", "false").lower() == "true"
    )
    use_music_audio_id: str = os.getenv(
        "XIAOMUSIC_USE_MUSIC_AUDIO_ID", "1582971365183456177"
    )
    use_music_id: str = os.getenv("XIAOMUSIC_USE_MUSIC_ID", "355454500")

    # 日誌文件路徑
    log_file: str = os.getenv("XIAOMUSIC_LOG_FILE", "xiaomusic.log.txt")

    # 模糊搜索配置
    fuzzy_match_cutoff: float = float(os.getenv("XIAOMUSIC_FUZZY_MATCH_CUTOFF", "0.6")) # 模糊匹配閾值
    enable_fuzzy_match: bool = (
        os.getenv("XIAOMUSIC_ENABLE_FUZZY_MATCH", "true").lower() == "true"
    )

    stop_tts_msg: str = os.getenv("XIAOMUSIC_STOP_TTS_MSG", "收到,再見") # 停止播放時的回覆語
    enable_config_example: bool = False # 是否生成示例配置文件

    # 關鍵詞環境變量 (允許用戶自定義觸發詞)
    keywords_playlocal: str = os.getenv(
        "XIAOMUSIC_KEYWORDS_PLAYLOCAL", "播放本地歌曲,本地播放歌曲"
    )
    keywords_search_playlocal: str = os.getenv(
        "XIAOMUSIC_KEYWORDS_SEARCH_PLAYLOCAL", "本地搜索播放"
    )
    keywords_play: str = os.getenv("XIAOMUSIC_KEYWORDS_PLAY", "播放歌曲,放歌曲")
    keywords_search_play: str = os.getenv("XIAOMUSIC_KEYWORDS_SEARCH_PLAY", "搜索播放")
    keywords_online_play: str = os.getenv("XIAOMUSIC_KEYWORDS_ONLINE_PLAY", "在線播放")
    keywords_stop: str = os.getenv("XIAOMUSIC_KEYWORDS_STOP", "關機,暫停,停止,停止播放")
    keywords_playlist: str = os.getenv(
        "XIAOMUSIC_KEYWORDS_PLAYLIST", "播放列表,播放歌單"
    )
    user_key_word_dict: dict[str, str] = field(
        default_factory=default_user_key_word_dict
    )

    # 強制停止模式
    enable_force_stop: bool = (
        os.getenv("XIAOMUSIC_ENABLE_FORCE_STOP", "false").lower() == "true"
    )

    devices: dict[str, Device] = field(default_factory=dict)
    
    # 設備分組配置
    group_list: str = os.getenv(
        "XIAOMUSIC_GROUP_LIST", ""
    )  # 格式: did1:group_name,did2:group_name

    # 標籤與格式轉換
    remove_id3tag: bool = (
        os.getenv("XIAOMUSIC_REMOVE_ID3TAG", "false").lower() == "true"
    ) # 下載時是否移除 ID3 標籤
    convert_to_mp3: bool = os.getenv("CONVERT_TO_MP3", "false").lower() == "true" # 是否強制轉換為 MP3
    delay_sec: int = int(os.getenv("XIAOMUSIC_DELAY_SEC", 3))  # 下一首歌延遲播放秒數
    continue_play: bool = (
        os.getenv("XIAOMUSIC_CONTINUE_PLAY", "false").lower() == "true"
    ) # 是否開啟斷點續傳

    # 目錄監控配置
    enable_file_watch: bool = (
        os.getenv("XIAOMUSIC_ENABLE_FILE_WATCH", "false").lower() == "true"
    ) # 是否開啟文件變動監控
    file_watch_debounce: int = int(
        os.getenv("XIAOMUSIC_FILE_WATCH_DEBOUNCE", 10)
    )  # 監控刷新防抖時間(秒)

    # 輪詢配置
    pull_ask_sec: int = int(os.getenv("XIAOMUSIC_PULL_ASK_SEC", "1")) # 輪詢間隔秒數
    enable_pull_ask: bool = (
        os.getenv("XIAOMUSIC_ENABLE_PULL_ASK", "true").lower() == "true"
    ) # 是否開啟對話輪詢 (核心功能開關)

    crontab_json: str = os.getenv("XIAOMUSIC_CRONTAB_JSON", "")  # 定時任務 JSON 配置

    # yt-dlp Cookie
    enable_yt_dlp_cookies: bool = (
        os.getenv("XIAOMUSIC_ENABLE_YT_DLP_COOKIES", "false").lower() == "true"
    )

    # 標籤緩存
    enable_save_tag: bool = (
        os.getenv("XIAOMUSIC_ENABLE_SAVE_TAG", "false").lower() == "true"
    )
    
    # 用戶分析
    enable_analytics: bool = (
        os.getenv("XIAOMUSIC_ENABLE_ANALYTICS", "true").lower() == "true"
    )

    # Mina 對話獲取開關
    get_ask_by_mina: bool = (
        os.getenv("XIAOMUSIC_GET_ASK_BY_MINA", "false").lower() == "true"
    )

    # 播放模式切換時的語音提示
    play_type_one_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_ONE_TTS_MSG", "已經設置為單曲循環"
    )
    play_type_all_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_ALL_TTS_MSG", "已經設置為全部循環"
    )
    play_type_rnd_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_RND_TTS_MSG", "已經設置為隨機播放"
    )
    play_type_sin_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_SIN_TTS_MSG", "已經設置為單曲播放"
    )
    play_type_seq_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_SEQ_TTS_MSG", "已經設置為順序播放"
    )

    recently_added_playlist_len: int = int(
        os.getenv("XIAOMUSIC_RECENTLY_ADDED_PLAYLIST_LEN", "50")
    )

    # 開啟語音刪除歌曲功能
    enable_cmd_del_music: bool = (
        os.getenv("XIAOMUSIC_ENABLE_CMD_DEL_MUSIC", "false").lower() == "true"
    )

    # 搜索結果返回數量限制
    search_music_count: int = int(os.getenv("XIAOMUSIC_SEARCH_MUSIC_COUNT", "100"))
    
    # 網絡歌曲使用 proxy 轉發 (解決跨域或內網播放問題)
    web_music_proxy: bool = (
        os.getenv("XIAOMUSIC_WEB_MUSIC_PROXY", "false").lower() == "true"
    )

    def append_keyword(self, keys, action):
        """
        添加關鍵詞
        :param keys: 逗號分隔的關鍵詞字符串 (例如 "上一首,上一曲")
        :param action: 對應的動作指令
        """
        for key in keys.split(","):
            if key:
                self.key_word_dict[key] = action
                if key not in self.key_match_order:
                    self.key_match_order.append(key)

    def append_user_keyword(self):
        """
        添加用戶自定義的關鍵詞到系統
        """
        for k, v in self.user_key_word_dict.items():
            self.key_word_dict[k] = v
            if k not in self.key_match_order:
                self.key_match_order.append(k)

    def init_keyword(self):
        """
        初始化關鍵詞匹配邏輯
        1. 獲取默認關鍵詞列表
        2. 從環境變量加載自定義配置的關鍵詞覆蓋默認值
        3. 添加用戶自定義關鍵詞
        4. 過濾掉無效的匹配順序
        """
        self.key_match_order = default_key_match_order()
        self.key_word_dict = default_key_word_dict()
        self.append_keyword(self.keywords_playlocal, "playlocal")
        self.append_keyword(self.keywords_search_playlocal, "search_playlocal")
        self.append_keyword(self.keywords_play, "play")
        self.append_keyword(self.keywords_search_play, "search_play")
        self.append_keyword(self.keywords_online_play, "online_play")
        self.append_keyword(self.keywords_stop, "stop")
        self.append_keyword(self.keywords_playlist, "play_music_list")
        self.append_user_keyword()
        # 僅保留那些在字典中有定義的關鍵詞
        self.key_match_order = [
            x for x in self.key_match_order if x in self.key_word_dict
        ]

    def __post_init__(self) -> None:
        """
        初始化後自動調用的方法
        驗證配置有效性並保存示例配置
        """
        if self.proxy:
            validate_proxy(self.proxy)

        self.init_keyword()
        # 保存配置到 config-example.json 文件
        if self.enable_config_example:
            with open("config-example.json", "w") as f:
                data = asdict(self)
                json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def from_options(cls, options: argparse.Namespace) -> Config:
        """
        從命令行參數創建 Config 對象
        優先級：命令行參數 > 配置文件 > 環境變量/默認值
        """
        config = {}
        if options.config:
            config = cls.read_from_file(options.config)
        for key, value in vars(options).items():
            if value is not None and key in cls.__dataclass_fields__:
                config[key] = value
        return cls(**config)

    @classmethod
    def convert_value(cls, k, v, type_hints):
        """
        輔助方法：根據類型提示自動轉換配置值的類型
        處理 bool, dict[str, Device] 等複雜類型
        """
        if v is not None and k in type_hints:
            expected_type = type_hints[k]
            try:
                if expected_type is bool:
                    converted_value = False
                    if str(v).lower() == "true":
                        converted_value = True
                elif expected_type == dict[str, Device]:
                    converted_value = {}
                    for kk, vv in v.items():
                        converted_value[kk] = Device(**vv)
                else:
                    converted_value = expected_type(v)
                return converted_value
            except (ValueError, TypeError) as e:
                print(f"Error converting {k}:{v} to {expected_type}: {e}")
        return None

    @classmethod
    def read_from_file(cls, config_path: str) -> dict:
        """
        從 JSON 配置文件讀取配置
        """
        result = {}
        with open(config_path, "rb") as f:
            data = json.load(f)
            type_hints = get_type_hints(cls)

            for k, v in data.items():
                converted_value = cls.convert_value(k, v, type_hints)
                if converted_value is not None:
                    result[k] = converted_value
        return result

    def update_config(self, data):
        """
        運行時動態更新配置
        更新後會重新初始化關鍵詞映射
        """
        type_hints = get_type_hints(self, globals(), locals())

        for k, v in data.items():
            converted_value = self.convert_value(k, v, type_hints)
            if converted_value is not None:
                setattr(self, k, converted_value)
        self.init_keyword()

    # 獲取設置文件路徑 (setting.json)
    def getsettingfile(self):
        # 兼容舊配置空的情況
        if not self.conf_path:
            self.conf_path = "conf"
        if not os.path.exists(self.conf_path):
            os.makedirs(self.conf_path)
        filename = os.path.join(self.conf_path, "setting.json")
        return filename

    @property
    def tag_cache_path(self):
        """獲取標籤緩存文件路徑"""
        if (len(self.cache_dir) > 0) and (not os.path.exists(self.cache_dir)):
            os.makedirs(self.cache_dir)
        filename = os.path.join(self.cache_dir, "tag_cache.json")
        return filename

    @property
    def picture_cache_path(self):
        """獲取圖片緩存路徑"""
        cache_path = os.path.join(self.cache_dir, "picture_cache")
        if not os.path.exists(cache_path):
            os.makedirs(cache_path)
        return cache_path

    @property
    def yt_dlp_cookies_path(self):
        """獲取 yt-dlp 使用的 cookies 文件路徑"""
        if not os.path.exists(self.conf_path):
            os.makedirs(self.conf_path)
        cookies_path = os.path.join(self.conf_path, "yt-dlp-cookie.txt")
        return cookies_path

    @property
    def temp_dir(self):
        """獲取臨時文件目錄"""
        if not os.path.exists(self.temp_path):
            os.makedirs(self.temp_path)
        return self.temp_path

    def get_play_type_tts(self, play_type):
        """獲取切換播放模式時的語音播報文本"""
        if play_type == PLAY_TYPE_ONE:
            return self.play_type_one_tts_msg
        if play_type == PLAY_TYPE_ALL:
            return self.play_type_all_tts_msg
        if play_type == PLAY_TYPE_RND:
            return self.play_type_rnd_tts_msg
        if play_type == PLAY_TYPE_SIN:
            return self.play_type_sin_tts_msg
        if play_type == PLAY_TYPE_SEQ:
            return self.play_type_seq_tts_msg
        return ""

    def get_ignore_tag_dirs(self):
        """獲取忽略標籤掃描的絕對目錄列表"""
        ignore_tag_absolute_dirs = []
        for ignore_tag_dir in self.ignore_tag_dirs.split(","):
            if ignore_tag_dir:
                ignore_tag_absolute_path = os.path.abspath(ignore_tag_dir)
                ignore_tag_absolute_dirs.append(ignore_tag_absolute_path)
        return ignore_tag_absolute_dirs
