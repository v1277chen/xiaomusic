# 支援的音樂檔案格式列表
SUPPORT_MUSIC_TYPE = [
    ".mp3",  # MP3 格式
    ".flac", # FLAC 無損格式
    ".wav",  # WAV 無損格式
    ".ape",  # APE 無損格式
    ".ogg",  # OGG 格式
    ".m4a",  # M4A 格式
    ".wma",  # WMA 格式
]

# 小米 Mina 服務的對話記錄查詢 API
# source=dialogu: 來源為對話
# hardware: 硬體型號
# timestamp: 時間戳
# limit: 限制返回的條數
LATEST_ASK_API = "https://userprofile.mina.mi.com/device_profile/v2/conversation?source=dialogu&hardware={hardware}&timestamp={timestamp}&limit=2"
# 小米帳號 Cookie 模板
# deviceId: 設備 ID
# serviceToken: 服務 Token
# userId: 用戶 ID
COOKIE_TEMPLATE = "deviceId={device_id}; serviceToken={service_token}; userId={user_id}"

# 播放模式常量定義
PLAY_TYPE_ONE = 0  # 單曲循環：重複播放同一首歌
PLAY_TYPE_ALL = 1  # 全部循環：按順序播放列表中的所有歌曲，播完後重新開始
PLAY_TYPE_RND = 2  # 隨機播放：隨機選擇列表中的歌曲播放
PLAY_TYPE_SIN = 3  # 單曲播放：播放完一首歌後停止
PLAY_TYPE_SEQ = 4  # 順序播放：按順序播放列表中的所有歌曲，播完後停止

# 需要使用 Mina 接口獲取對話記錄的設備型號列表
# 某些舊型號或特殊型號的小愛音箱無法透過標準途徑獲取，需走 Mina 通道
GET_ASK_BY_MINA = [
    "M01",
]

# 需要使用 play_music 接口進行播放的設備型號列表
# 這些設備可能不支持直接的 URL 播放指令，需要使用特定的音樂播放接口
NEED_USE_PLAY_MUSIC_API = [
    "X08C",
    "X08E",
    "X8F",
    "X4B",
    "LX05",
    "OH2",
    "OH2P",
    "X6A",
]

# 各設備型號對應的 TTS (Text-to-Speech) 指令參數
# 不同型號的音箱在發送 TTS 指令時，其 payload 中的參數標識可能不同
# key: 設備硬件型號
# value: 指令標識 (例如 "5-3", "5-1" 等)
TTS_COMMAND = {
    "OH2": "5-3",
    "OH2P": "7-3",
    "LX06": "5-1",
    "S12": "5-1",
    "L15A": "7-3",
    "LX5A": "5-1",
    "LX01": "5-1",
    "LX05": "5-1",
    "X10A": "7-3",
    "L17A": "7-3",
    "ASX4B": "5-3",
    "L06A": "5-1",
    "L05B": "5-3",
    "L05C": "5-3",
    "X6A": "7-3",
    "X08E": "7-3",
    "L09A": "3-1",
    "LX04": "5-1",
}
