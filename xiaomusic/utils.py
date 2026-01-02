#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import copy
import difflib
import hashlib
import io
import json
import logging
import mimetypes
import os
import platform
import random
import re
import shutil
import string
import subprocess
import tempfile
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from time import sleep
from urllib.parse import parse_qs, urlparse

import aiohttp
import edge_tts
import mutagen
from mutagen.asf import ASF
from mutagen.flac import FLAC
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    USLT,
    Encoding,
    TextFrame,
    TimeStampTextFrame,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack
from opencc import OpenCC
from PIL import Image
from requests.utils import cookiejar_from_dict

from xiaomusic.const import SUPPORT_MUSIC_TYPE

log = logging.getLogger(__package__)

cc = OpenCC("t2s")  # convert from Traditional Chinese to Simplified Chinese


### HELP FUNCTION ###
### HELP FUNCTION ###
def parse_cookie_string(cookie_string):
    """
    解析 cookie 字符串為 cookiejar 對象，供 requests 庫使用
    """
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    cookies_dict = {k: m.value for k, m in cookie.items()}
    return cookiejar_from_dict(cookies_dict, cookiejar=None, overwrite=True)


_no_elapse_chars = re.compile(r"([「」『』《》“”'\"()（）]|(?<!-)-(?!-))", re.UNICODE)


def calculate_tts_elapse(text: str) -> float:
    """
    估算 TTS (文字轉語音) 播放所需的時間
    核心邏輯：過濾掉不發音的標點符號，按固定語速計算
    speed = 4.5 字/秒 (經驗值)
    """
    # for simplicity, we use a fixed speed
    speed = 4.5  # this value is picked by trial and error
    # Exclude quotes and brackets that do not affect the total elapsed time
    return len(_no_elapse_chars.sub("", text)) / speed


_ending_punctuations = ("。", "？", "！", "；", ".", "?", "!", ";")


async def split_sentences(text_stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """
    異步流式分句處理
    將輸入的字符流緩存並按標點符號切割成完整的句子，方便逐句進行 TTS 轉換
    """
    cur = ""
    async for text in text_stream:
        cur += text
        if cur.endswith(_ending_punctuations):
            yield cur
            cur = ""
    if cur:
        yield cur


### for edge-tts utils ###
def find_key_by_partial_string(dictionary: dict[str, str], partial_key: str) -> str:
    """
    在字典中根據部分鍵名查找值 (模糊匹配)
    """
    for key, value in dictionary.items():
        if key in partial_key:
            return value


def validate_proxy(proxy_str: str) -> bool:
    """
    驗證 HTTP 代理字符串格式是否正確
    必須包含 scheme (http/https) 以及 hostname 和 port
    """
    """Do a simple validation of the http proxy string."""

    parsed = urlparse(proxy_str)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Proxy scheme must be http or https")
    if not (parsed.hostname and parsed.port):
        raise ValueError("Proxy hostname and port must be set")

    return True


# 模糊搜索入口函數
def fuzzyfinder(user_input, collection, extra_search_index=None):
    """
    模糊查找
    :param user_input: 用戶輸入的搜索詞
    :param collection: 被搜索的集合 (通常是歌曲列表)
    :param extra_search_index: 額外的搜索索引
    """
    return find_best_match(
        user_input, collection, cutoff=0.1, n=10, extra_search_index=extra_search_index
    )


def traditional_to_simple(to_convert: str):
    """將繁體中文轉換為簡體中文"""
    return cc.convert(to_convert)


# 關鍵詞檢測與排序
def keyword_detection(user_input, str_list, n):
    """
    從字符串列表中篩選包含關鍵詞的項目，並按匹配度排序
    :param user_input: 關鍵詞
    :param str_list: 候選列表
    :param n: 返回數量限制
    """
    # 過濾包含關鍵字的字符串
    matched, remains = [], []
    for item in str_list:
        if user_input in item:
            matched.append(item)
        else:
            remains.append(item)

    matched = sorted(
        matched,
        key=lambda s: difflib.SequenceMatcher(None, s, user_input).ratio(),
        reverse=True,  # 降序排序，越相似的越靠前
    )

    # 如果 n 是 -1，如果 n 大於匹配的數量，返回所有匹配的結果
    if n == -1 or n > len(matched):
        return matched, remains

    # 選擇前 n 個匹配的結果
    remains = matched[n:] + remains
    return matched[:n], remains


def real_search(prompt, candidates, cutoff, n):
    """
    執行搜索邏輯：先嘗試精確包含匹配，若不足則嘗試模糊匹配 (difflib)
    """
    matches, remains = keyword_detection(prompt, candidates, n=n)
    if len(matches) < n:
        # 如果没有准确关键词匹配，开始模糊匹配
        matches += difflib.get_close_matches(prompt, remains, n=n, cutoff=cutoff)
    return matches


def find_best_match(user_input, collection, cutoff=0.6, n=1, extra_search_index=None):
    """
    查找最佳匹配項 (支持繁簡轉換)
    1. 將用戶輸入和集合都轉為簡體小寫
    2. 先在主集合搜索
    3. 若結果不足 n 個，且提供了 extra_search_index，則繼續搜索
    """
    lower_collection = {
        traditional_to_simple(item.lower()): item for item in collection
    }
    user_input = traditional_to_simple(user_input.lower())
    matches = real_search(user_input, lower_collection.keys(), cutoff, n)
    cur_matched_collection = [lower_collection[match] for match in matches]
    if len(matches) >= n or extra_search_index is None:
        return cur_matched_collection[:n]

    # 如果數量不滿足，繼續搜索
    lower_extra_search_index = {
        traditional_to_simple(k.lower()): v
        for k, v in extra_search_index.items()
        if v not in cur_matched_collection
    }
    matches = real_search(user_input, lower_extra_search_index.keys(), cutoff, n)
    cur_matched_collection += [lower_extra_search_index[match] for match in matches]
    return cur_matched_collection[:n]


# 歌曲排序
# 歌曲排序函數
def custom_sort_key(s):
    """
    自定義排序邏輯
    優先識別字符串中的數字部分進行數值排序，而不是純字典序
    例如： "10.mp3" 應該排在 "2.mp3" 後面
    """
    # 使用正則表達式分別提取字符串的數字前綴和數字後綴
    prefix_match = re.match(r"^(\d+)", s)
    suffix_match = re.search(r"(\d+)$", s)

    numeric_prefix = int(prefix_match.group(0)) if prefix_match else None
    numeric_suffix = int(suffix_match.group(0)) if suffix_match else None

    if numeric_prefix is not None:
        # 如果前綴是數字，先按前綴數字排序，再按整個字符串排序
        return (0, numeric_prefix, s)
    elif numeric_suffix is not None:
        # 如果後綴是數字，先按前綴字符排序，再按後綴數字排序
        return (1, s[: suffix_match.start()], numeric_suffix)
    else:
        # 如果前綴和後綴都不是數字，按字典序排序
        return (2, s)


def _get_depth_path(root, directory, depth):
    """
    根據設定的掃描深度，截取目錄路徑
    防止掃描過深的目錄結構
    """
    # 計算當前目錄的深度
    relative_path = root[len(directory) :].strip(os.sep)
    path_parts = relative_path.split(os.sep)
    if len(path_parts) >= depth:
        return os.path.join(directory, *path_parts[:depth])
    else:
        return root


def _append_files_result(result, root, joinpath, files, support_extension):
    """
    將掃描到的合法音樂文件添加到結果字典中
    :param result: 存儲結果的字典
    :param root: 作為 key 的目錄名
    :param joinpath: 文件拼接的根路徑
    :param files: 文件列表
    :param support_extension: 支持的文件後綴列表
    """
    dir_name = os.path.basename(root)
    if dir_name not in result:
        result[dir_name] = []
    for file in files:
        # 過濾隱藏文件
        if file.startswith("."):
            continue
        # 過濾文件後綴
        (name, extension) = os.path.splitext(file)
        if extension.lower() not in support_extension:
            continue

        result[dir_name].append(os.path.join(joinpath, file))


def traverse_music_directory(directory, depth, exclude_dirs, support_extension):
    """
    遍歷音樂目錄
    :param directory: 根目錄路徑
    :param depth: 掃描最大深度
    :param exclude_dirs: 忽略的目錄列表
    :param support_extension: 支持的音樂格式後綴
    :return: {目錄名: [文件路徑1, 文件路徑2...]}
    """
    result = {}
    for root, dirs, files in os.walk(directory, followlinks=True):
        # 忽略排除的目錄
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        # 計算當前目錄的深度
        current_depth = root[len(directory) :].count(os.sep) + 1
        if current_depth > depth:
            depth_path = _get_depth_path(root, directory, depth - 1)
            _append_files_result(result, depth_path, root, files, support_extension)
        else:
            _append_files_result(result, root, root, files, support_extension)
    return result


# 發送給網頁 3thplay，用於三者設備播放
async def thdplay(
    action, args="/static/3thdplay.mp3", target="HTTP://192.168.1.10:58090/thdaction"
):
    """
    調用第三方播放接口
    通常用於多設備聯動或將音頻投放給其他支持該協議的設備
    """
    # 接口地址 target,在参数文件指定
    data = {"action": action, "args": args}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                target, json=data, timeout=5
            ) as response:  # 增加超时以避免长时间挂起
                # 如果响应不是200，引发异常
                response.raise_for_status()
                # 读取响应文本
                text = await response.text()
                return "[]" not in text
    except Exception as e:
        log.error(f"Error thdplay: {e}")
    return False


async def downloadfile(url):
    """
    簡單的異步文件下載
    :param url: 文件地址
    :return: 文件內容 (text)
    """
    # 清理和验证URL
    # 解析URL
    parsed_url = urlparse(url)
    # 基础验证：仅允许HTTP和HTTPS协议
    if parsed_url.scheme not in ("http", "https"):
        raise Warning(
            f"Invalid URL scheme: {parsed_url.scheme}. Only HTTP and HTTPS are allowed."
        )
    # 構建目標URL
    cleaned_url = parsed_url.geturl()

    # 使用 aiohttp 創建一個客戶端會話來發起請求
    async with aiohttp.ClientSession() as session:
        async with session.get(
            cleaned_url, timeout=5
        ) as response:  # 增加超時以避免長時間掛起
            # 如果響應不是200，引發異常
            response.raise_for_status()
            # 讀取響應文本
            text = await response.text()
            return text


def is_mp3(url):
    """判斷 URL 是否指向 MP3 文件"""
    mt = mimetypes.guess_type(url)
    if mt and mt[0] == "audio/mpeg":
        return True
    return False


def is_m4a(url):
    """判斷 URL 是否指向 M4A 文件"""
    return url.endswith(".m4a")


async def _get_web_music_duration(session, url, config, start=0, end=500):
    """
    異步獲取網絡音樂文件的部分內容並估算其時長。

    通過請求 URL 的前幾個字節（默認 0-500）下載部分文件頭，
    寫入臨時文件後調用本地工具（如 ffprobe）解析元數據獲取音頻時長。
    這樣可以避免下載完整的大文件。

    :param session: aiohttp.ClientSession 實例
    :param url: 音樂文件的 URL 地址
    :param config: 包含配置信息的對象（如 ffmpeg 路徑）
    :param start: 請求的起始字節位置
    :param end: 請求的結束字節位置
    :return: 返回音頻的持續時間（秒），如果失敗則返回 0
    """
    duration = 0
    # 設置請求頭 Range，只請求部分內容（用於快速獲取元數據）
    headers = {"Range": f"bytes={start}-{end}"}

    # 使用 aiohttp 異步發起 GET 請求，獲取部分音頻內容
    async with session.get(url, headers=headers) as response:
        array_buffer = await response.read()  # 讀取響應的二進制內容

    # 創建一個命名的臨時文件，並禁用自動刪除（以便後續讀取）
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(array_buffer)  # 將下載的部分內容寫入臨時文件
        tmp_path = tmp.name  # 獲取該臨時文件的真實路徑

    try:
        # 調用 get_local_music_duration 並傳入文件路徑，而不是文件對象
        duration = await get_local_music_duration(tmp_path, config)
    except Exception as e:
        log.error(f"Error _get_web_music_duration: {e}")
    finally:
        # 手動刪除臨時文件，避免殘留
        os.unlink(tmp_path)

    return duration


async def get_web_music_duration(url, config):
    """
    獲取網絡音頻文件的時長
    會處理重定向，並嘗試通過部分下載的方式快速獲取信息
    如果第一次嘗試失敗（下載前500字節），會嘗試下載更多數據（前3000字節）
    """
    duration = 0
    try:
        parsed_url = urlparse(url)
        file_path = parsed_url.path
        _, extension = os.path.splitext(file_path)
        if extension.lower() not in SUPPORT_MUSIC_TYPE:
            cleaned_url = parsed_url.geturl()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    cleaned_url,
                    allow_redirects=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36"
                    },
                ) as response:
                    url = str(response.url)
        # 設置總超時時間為3秒
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            duration = await _get_web_music_duration(
                session, url, config, start=0, end=500
            )
            if duration <= 0:
                duration = await _get_web_music_duration(
                    session, url, config, start=0, end=3000
                )
    except Exception as e:
        log.error(f"Error get_web_music_duration: {e}")
    return duration, url


# 获取文件播放时长
async def get_local_music_duration(filename, config):
    """
    獲取本地音樂文件時長
    支持通過 ffprobe (ffmpeg組件) 或 mutagen (Python庫) 兩種方式獲取
    """
    duration = 0
    if config.get_duration_type == "ffprobe":
        duration = get_duration_by_ffprobe(filename, config.ffmpeg_location)
    else:
        duration = await get_duration_by_mutagen(filename)

    # 换个方式重试一次
    if duration == 0:
        if config.get_duration_type != "ffprobe":
            duration = get_duration_by_ffprobe(filename, config.ffmpeg_location)
        else:
            duration = await get_duration_by_mutagen(filename)

    return duration


async def get_duration_by_mutagen(file_path):
    """使用 mutagen 庫解析文件頭部獲取時長"""
    duration = 0
    try:
        loop = asyncio.get_event_loop()
        if is_mp3(file_path):
            m = await loop.run_in_executor(None, mutagen.mp3.MP3, file_path)
        else:
            m = await loop.run_in_executor(None, mutagen.File, file_path)
        duration = m.info.length
    except Exception as e:
        log.warning(f"Error getting local music {file_path} duration: {e}")
    return duration


def get_duration_by_ffprobe(file_path, ffmpeg_location):
    """使用系統安裝的 ffprobe 命令行工具獲取時長"""
    duration = 0
    try:
        # 構造 ffprobe 命令參數
        cmd_args = [
            os.path.join(ffmpeg_location, "ffprobe"),
            "-v",
            "error",  # 只輸出錯誤信息，避免混雜在其他輸出中
            "-show_entries",
            "format=duration",  # 僅顯示時長
            "-of",
            "json",  # 以 JSON 格式輸出
            file_path,
        ]

        # 輸出待執行的完整命令
        full_command = " ".join(cmd_args)
        log.info(f"待執行及其完整命令 ffprobe command: {full_command}")

        # 使用 ffprobe 獲取文件的元數據，並以 JSON 格式輸出
        result = subprocess.run(
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # 输出命令执行结果
        log.info(
            f"命令執行結果 command result - return code: {result.returncode}, stdout: {result.stdout}"
        )

        # 解析 JSON 输出
        ffprobe_output = json.loads(result.stdout)

        # 获取时长
        duration = float(ffprobe_output["format"]["duration"])
        log.info(
            f"Successfully extracted duration: {duration} seconds for file: {file_path}"
        )

    except Exception as e:
        log.warning(f"Error getting local music {file_path} duration: {e}")
    return duration


def get_random(length):
    """生成指定長度的隨機字母數字字符串"""
    return "".join(random.sample(string.ascii_letters + string.digits, length))


# 深拷贝把敏感数据设置为*
def deepcopy_data_no_sensitive_info(data, fields_to_anonymize=None):
    """
    深拷貝數據並對敏感字段進行脫敏處理 (例如密碼顯示為 ******)
    用於日誌記錄或數據導出
    """
    if fields_to_anonymize is None:
        fields_to_anonymize = [
            "account",
            "password",
            "httpauth_username",
            "httpauth_password",
        ]

    copy_data = copy.deepcopy(data)

    # 檢查copy_data是否是字典或具有屬性的對象
    if isinstance(copy_data, dict):
        # 對字典進行處理
        for field in fields_to_anonymize:
            if field in copy_data:
                copy_data[field] = "******"
    else:
        # 對對象進行處理
        for field in fields_to_anonymize:
            if hasattr(copy_data, field):
                setattr(copy_data, field, "******")

    return copy_data


# k1:v1,k2:v2
def parse_str_to_dict(s, d1=",", d2=":"):
    """
    解析鍵值對字符串為字典
    例如: "k1:v1,k2:v2" -> {"k1": "v1", "k2": "v2"}
    """
    # 初始化一个空字典
    result = {}
    parts = s.split(d1)

    for part in parts:
        # 根据冒号切割
        subparts = part.split(d2)
        if len(subparts) == 2:  # 防止数据不是成对出现
            k, v = subparts
            result[k] = v

    return result


# remove mp3 file id3 tag and padding to reduce delay
def no_padding(info):
    # this will remove all padding
    return 0


def remove_id3_tags(input_file: str, config) -> str:
    """
    移除 MP3 文件的 ID3 標籤
    某些小愛音箱在播放帶有特定 ID3 標籤的 MP3 時會有異常延遲，移除標籤可解決此問題
    處理後的文件會保存在臨時目錄
    """
    audio = MP3(input_file, ID3=ID3)

    # 檢查是否存在ID3 v2.3或v2.4標籤
    if not (
        audio.tags
        and (audio.tags.version == (2, 3, 0) or audio.tags.version == (2, 4, 0))
    ):
        return None

    music_path = config.music_path
    temp_dir = config.temp_dir

    # 構造新文件的路徑
    out_file_name = os.path.splitext(os.path.basename(input_file))[0]
    out_file_path = os.path.join(temp_dir, f"{out_file_name}.mp3")
    relative_path = os.path.relpath(out_file_path, music_path)

    # 路徑相同的情況
    input_absolute_path = os.path.abspath(input_file)
    output_absolute_path = os.path.abspath(out_file_path)
    if input_absolute_path == output_absolute_path:
        log.info(f"File {input_file} = {out_file_path} . Skipping remove_id3_tags.")
        return None

    # 檢查目標文件是否存在
    if os.path.exists(out_file_path):
        log.info(f"File {out_file_path} already exists. Skipping remove_id3_tags.")
        return relative_path

    # 開始去除（不再需要檢查）
    # 拷貝文件
    shutil.copy(input_file, out_file_path)
    outaudio = MP3(out_file_path, ID3=ID3)
    # 刪除ID3標籤
    outaudio.delete()
    # 保存修改後的文件
    outaudio.save(padding=no_padding)
    log.info(f"File {out_file_path} remove_id3_tags ok.")
    return relative_path


def convert_file_to_mp3(input_file: str, config) -> str:
    """
    調用 ffmpeg 將音頻文件轉換為 MP3 格式
    同時支持音量均衡 (Loudnorm)
    """
    music_path = config.music_path
    temp_dir = config.temp_dir

    out_file_name = os.path.splitext(os.path.basename(input_file))[0]
    out_file_path = os.path.join(temp_dir, f"{out_file_name}.mp3")
    relative_path = os.path.relpath(out_file_path, music_path)

    # 路徑相同的情況
    input_absolute_path = os.path.abspath(input_file)
    output_absolute_path = os.path.abspath(out_file_path)
    if input_absolute_path == output_absolute_path:
        log.info(f"File {input_file} = {out_file_path} . Skipping convert_file_to_mp3.")
        return None

    absolute_music_path = os.path.abspath(music_path)
    if not input_absolute_path.startswith(absolute_music_path):
        log.error(f"Invalid input file path: {input_file}")
        return None

    # 檢查目標文件是否存在
    if os.path.exists(out_file_path):
        log.info(f"File {out_file_path} already exists. Skipping convert_file_to_mp3.")
        return relative_path

    # 檢查是否存在 loudnorm 參數
    loudnorm_args = []
    if config.loudnorm:
        loudnorm_args = ["-af", config.loudnorm]

    command = [
        os.path.join(config.ffmpeg_location, "ffmpeg"),
        "-i",
        input_absolute_path,
        "-f",
        "mp3",
        "-vn",
        "-y",
        *loudnorm_args,
        out_file_path,
    ]

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        log.exception(f"Error during conversion: {e}")
        return None

    log.info(f"File {input_file} to {out_file_path} convert_file_to_mp3 ok.")
    return relative_path


chinese_to_arabic = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
    "亿": 100000000,
}


def chinese_to_number(chinese):
    """
    將中文數字字符串轉換為阿拉伯數字
    例如： "一千二百三十四" -> 1234
    """
    result = 0
    unit = 1
    num = 0
    # 處理特殊情況：以"十"開頭時，在前面加"一"
    if chinese.startswith("十"):
        chinese = "一" + chinese

    # 如果只有一個字符且是單位，直接返回其值
    if len(chinese) == 1 and chinese_to_arabic[chinese] >= 10:
        return chinese_to_arabic[chinese]
    for char in reversed(chinese):
        if char in chinese_to_arabic:
            val = chinese_to_arabic[char]
            if val >= 10:
                if val > unit:
                    unit = val
                else:
                    unit *= val
            else:
                num += val * unit
    result += num

    return result


def list2str(li, verbose=False):
    """
    將列表轉換為字符串顯示
    如果 verbose 為 False 且列表過長，則只顯示首尾部分，避免日誌刷屏
    """
    if len(li) > 5 and not verbose:
        return f"{li[:2]} ... {li[-2:]} with len: {len(li)}"
    else:
        return f"{li}"


async def get_latest_version(package_name: str) -> str:
    """
    從 PyPI 檢查指定包的最新版本號
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data["info"]["version"]
            else:
                return None


@dataclass
class Metadata:
    """
    音樂元數據數據類
    """
    title: str = ""   # 標題
    artist: str = ""  # 藝術家
    album: str = ""   # 專輯
    year: str = ""    # 年份
    genre: str = ""   # 流派
    picture: str = "" # 封面圖片路徑
    lyrics: str = ""  # 歌詞

    def __init__(self, info=None):
        if info:
            self.title = info.get("title", "")
            self.artist = info.get("artist", "")
            self.album = info.get("album", "")
            self.year = info.get("year", "")
            self.genre = info.get("genre", "")
            self.picture = info.get("picture", "")
            self.lyrics = info.get("lyrics", "")


def _get_alltag_value(tags, k):
    """獲取 mutagen 對象中所有標籤的值，並處理編碼"""
    v = tags.getall(k)
    if len(v) > 0:
        return _to_utf8(v[0])
    return ""


def _get_tag_value(tags, k):
    """獲取 mutagen 對象中指定標籤的值，并處理編碼"""
    if k not in tags:
        return ""
    v = tags[k]
    return _to_utf8(v)


def _to_utf8(v):
    """
    嘗試將各種編碼的字符串轉換為 UTF-8
    特別處理 Latin1 (ISO-8859-1) 被錯誤識別為 GBK 的情況（常見於中文 MP3 ID3v1 標籤）
    """
    if isinstance(v, TextFrame) and not isinstance(v, TimeStampTextFrame):
        old_ts = "".join(v.text)
        if v.encoding == Encoding.LATIN1:
            try:
                bs = old_ts.encode("latin1")
                ts = bs.decode("GBK", errors="ignore")
                return ts
            except Exception:
                return old_ts
        return old_ts
    elif isinstance(v, list):
        return "".join(str(item) for item in v)
    return str(v)


def save_picture_by_base64(picture_base64_data, save_root, file_path):
    """
    保存 Base64 編碼的圖片數據到文件
    :param picture_base64_data: Base64 字符串
    :param save_root: 保存根目錄
    :param file_path: 原始音樂文件路徑（用於生成 Hash 以命名圖片目錄）
    """
    try:
        picture_data = base64.b64decode(picture_base64_data)
    except (TypeError, ValueError) as e:
        log.exception(f"Error decoding base64 data: {e}")
        return None
    return _save_picture(picture_data, save_root, file_path)


def _save_picture(picture_data, save_root, file_path):
    """
    保存二進制圖片數據
    會根據 file_path 的哈希值創建子目錄，避免單個目錄文件過多
    並調用 _resize_save_image 進行縮放保存
    """
    # 計算文件名的哈希值
    file_hash = hashlib.md5(file_path.encode("utf-8")).hexdigest()
    # 創建目錄結構
    dir_path = os.path.join(save_root, file_hash[-6:])
    os.makedirs(dir_path, exist_ok=True)

    # 保存圖片
    filename = os.path.basename(file_path)
    (name, _) = os.path.splitext(filename)
    picture_path = os.path.join(dir_path, f"{name}.jpg")

    try:
        _resize_save_image(picture_data, picture_path)
    except Exception as e:
        log.warning(f"Error _resize_save_image: {e}")
    return picture_path


def _resize_save_image(image_bytes, save_path, max_size=300):
    """
    縮放並保存圖片
    將圖片限制在 max_size * max_size 範圍內，轉為 JPEG 格式以節省空間
    """
    # 將 bytes 轉換為 PIL Image 對象
    image = None
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = image.convert("RGB")
    except Exception as e:
        log.warning(f"Error _resize_save_image: {e}")
        return

    # 獲取原始尺寸
    original_width, original_height = image.size

    # 如果圖片的寬度和高度都小於 max_size，則直接保存原始圖片
    if original_width <= max_size and original_height <= max_size:
        image.save(save_path, format="JPEG")
        return

    # 計算縮放比例，保持等比縮放
    scaling_factor = min(max_size / original_width, max_size / original_height)

    # 計算新的尺寸
    new_width = int(original_width * scaling_factor)
    new_height = int(original_height * scaling_factor)

    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    resized_image.save(save_path, format="JPEG")
    return save_path


def extract_audio_metadata(file_path, save_root):
    """
    提取音頻文件的元數據 (標題, 藝術家, 封面圖等)
    支持 MP3, FLAC, MP4, OggVorbis, ASF, WavPack, WAVE 格式
    """
    metadata = Metadata()

    audio = None
    try:
        audio = mutagen.File(file_path)
    except Exception as e:
        log.warning(f"Error extract_audio_metadata file: {file_path} {e}")
    if audio is None:
        return asdict(metadata)

    tags = audio.tags
    if tags is None:
        return asdict(metadata)

    if isinstance(audio, MP3):
        # 讀取 MP3 ID3 標籤
        metadata.title = _get_tag_value(tags, "TIT2")
        metadata.artist = _get_tag_value(tags, "TPE1")
        metadata.album = _get_tag_value(tags, "TALB")
        metadata.year = _get_tag_value(tags, "TDRC")
        metadata.genre = _get_tag_value(tags, "TCON")
        metadata.lyrics = _get_alltag_value(tags, "USLT")
        for tag in tags.values():
            if isinstance(tag, APIC):
                metadata.picture = _save_picture(tag.data, save_root, file_path)
                break

    elif isinstance(audio, FLAC):
        # 讀取 FLAC 標籤
        metadata.title = _get_tag_value(tags, "TITLE")
        metadata.artist = _get_tag_value(tags, "ARTIST")
        metadata.album = _get_tag_value(tags, "ALBUM")
        metadata.year = _get_tag_value(tags, "DATE")
        metadata.genre = _get_tag_value(tags, "GENRE")
        if audio.pictures:
            metadata.picture = _save_picture(
                audio.pictures[0].data, save_root, file_path
            )
        if "lyrics" in audio:
            metadata.lyrics = audio["lyrics"][0]

    elif isinstance(audio, MP4):
        metadata.title = _get_tag_value(tags, "\xa9nam")
        metadata.artist = _get_tag_value(tags, "\xa9ART")
        metadata.album = _get_tag_value(tags, "\xa9alb")
        metadata.year = _get_tag_value(tags, "\xa9day")
        metadata.genre = _get_tag_value(tags, "\xa9gen")
        if "covr" in tags:
            metadata.picture = _save_picture(tags["covr"][0], save_root, file_path)

    elif isinstance(audio, OggVorbis):
        metadata.title = _get_tag_value(tags, "TITLE")
        metadata.artist = _get_tag_value(tags, "ARTIST")
        metadata.album = _get_tag_value(tags, "ALBUM")
        metadata.year = _get_tag_value(tags, "DATE")
        metadata.genre = _get_tag_value(tags, "GENRE")
        if "metadata_block_picture" in tags:
            picture = json.loads(base64.b64decode(tags["metadata_block_picture"][0]))
            metadata.picture = _save_picture(
                base64.b64decode(picture["data"]), save_root, file_path
            )

    elif isinstance(audio, ASF):
        metadata.title = _get_tag_value(tags, "Title")
        metadata.artist = _get_tag_value(tags, "Author")
        metadata.album = _get_tag_value(tags, "WM/AlbumTitle")
        metadata.year = _get_tag_value(tags, "WM/Year")
        metadata.genre = _get_tag_value(tags, "WM/Genre")
        if "WM/Picture" in tags:
            metadata.picture = _save_picture(
                tags["WM/Picture"][0].value, save_root, file_path
            )

    elif isinstance(audio, WavPack):
        metadata.title = _get_tag_value(tags, "Title")
        metadata.artist = _get_tag_value(tags, "Artist")
        metadata.album = _get_tag_value(tags, "Album")
        metadata.year = _get_tag_value(tags, "Year")
        metadata.genre = _get_tag_value(tags, "Genre")
        if audio.pictures:
            metadata.picture = _save_picture(
                audio.pictures[0].data, save_root, file_path
            )

    elif isinstance(audio, WAVE):
        metadata.title = _get_tag_value(tags, "Title")
        metadata.artist = _get_tag_value(tags, "Artist")

    return asdict(metadata)


def set_music_tag_to_file(file_path, info):
    audio = mutagen.File(file_path, easy=True)
    if audio is None:
        log.error(f"Unable to open file {file_path}")
        return "Unable to open file"

    if isinstance(audio, MP3):
        _set_mp3_tags(audio, info)
    elif isinstance(audio, FLAC):
        _set_flac_tags(audio, info)
    elif isinstance(audio, MP4):
        _set_mp4_tags(audio, info)
    elif isinstance(audio, OggVorbis):
        _set_ogg_tags(audio, info)
    elif isinstance(audio, ASF):
        _set_asf_tags(audio, info)
    elif isinstance(audio, WAVE):
        _set_wave_tags(audio, info)
    else:
        log.error(f"Unsupported file type for {file_path}")
        return "Unsupported file type"

    try:
        audio.save()
        log.info(f"Tags saved successfully to {file_path}")
        return "OK"
    except Exception as e:
        log.exception(f"Error saving tags: {e}")
        return "Error saving tags"


def _set_mp3_tags(audio, info):
    audio.tags = ID3()
    audio["TIT2"] = TIT2(encoding=3, text=info.title)
    audio["TPE1"] = TPE1(encoding=3, text=info.artist)
    audio["TALB"] = TALB(encoding=3, text=info.album)
    audio["TDRC"] = TDRC(encoding=3, text=info.year)
    audio["TCON"] = TCON(encoding=3, text=info.genre)

    # 使用 USLT 存儲歌詞
    if info.lyrics:
        audio["USLT"] = USLT(encoding=3, lang="eng", text=info.lyrics)

    # 添加封面圖片
    if info.picture:
        with open(info.picture, "rb") as img_file:
            image_data = img_file.read()
        audio["APIC"] = APIC(
            encoding=3, mime="image/jpeg", type=3, desc="Cover", data=image_data
        )
    audio.save()  # 保存修改


def _set_flac_tags(audio, info):
    audio["TITLE"] = info.title
    audio["ARTIST"] = info.artist
    audio["ALBUM"] = info.album
    audio["DATE"] = info.year
    audio["GENRE"] = info.genre
    if info.lyrics:
        audio["LYRICS"] = info.lyrics
    if info.picture:
        with open(info.picture, "rb") as img_file:
            image_data = img_file.read()
        audio.add_picture(image_data)


def _set_mp4_tags(audio, info):
    audio["nam"] = info.title
    audio["ART"] = info.artist
    audio["alb"] = info.album
    audio["day"] = info.year
    audio["gen"] = info.genre
    if info.picture:
        with open(info.picture, "rb") as img_file:
            image_data = img_file.read()
        audio["covr"] = [image_data]


def _set_ogg_tags(audio, info):
    audio["TITLE"] = info.title
    audio["ARTIST"] = info.artist
    audio["ALBUM"] = info.album
    audio["DATE"] = info.year
    audio["GENRE"] = info.genre
    if info.lyrics:
        audio["LYRICS"] = info.lyrics
    if info.picture:
        with open(info.picture, "rb") as img_file:
            image_data = img_file.read()
        audio["metadata_block_picture"] = base64.b64encode(image_data).decode()


def _set_asf_tags(audio, info):
    audio["Title"] = info.title
    audio["Author"] = info.artist
    audio["WM/AlbumTitle"] = info.album
    audio["WM/Year"] = info.year
    audio["WM/Genre"] = info.genre
    if info.picture:
        with open(info.picture, "rb") as img_file:
            image_data = img_file.read()
        audio["WM/Picture"] = image_data


def _set_wave_tags(audio, info):
    audio["Title"] = info.title
    audio["Artist"] = info.artist


async def check_bili_fav_list(url):
    bvid_info = {}
    parsed_url = urlparse(url)
    path = parsed_url.path
    # 提取查詢參數
    query_params = parse_qs(parsed_url.query)
    if parsed_url.hostname == "space.bilibili.com":
        if "/favlist" in path:
            lid = query_params.get("fid", [None])[0]
            type = query_params.get("ctype", [None])[0]
            if type == "11":
                type = "create"
            elif type == "21":
                type = "collect"
            else:
                raise ValueError("當前只支持合集和收藏夾")
        elif "/lists/" in path:
            parts = path.split("/")
            if len(parts) >= 4 and "?" in url:
                lid = parts[3]  # 提取 lid
                type = query_params.get("type", [None])[0]
        # https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?season_id={lid}&page_size=30&page_num=1
        page_size = 100
        page_num = 1
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": url,
            "Origin": "https://space.bilibili.com",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            if type == "season" or type == "collect":
                while True:
                    list_url = f"https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?season_id={lid}&page_size={page_size}&page_num={page_num}"
                    async with session.get(list_url) as response:
                        if response.status != 200:
                            raise Exception(f"Failed to fetch data from {list_url}")
                        data = await response.json()
                        archives = data.get("data", {}).get("archives", [])
                        if not archives:
                            break
                        for archive in archives:
                            bvid = archive.get("bvid", None)
                            title = archive.get("title", None)
                            bvid_info[bvid] = title

                        if len(archives) < page_size:
                            break
                        page_num += 1
                        sleep(1)
            elif type == "create":
                while True:
                    list_url = f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={lid}&pn={page_num}&ps={page_size}&order=mtime"
                    async with session.get(list_url) as response:
                        if response.status != 200:
                            raise Exception(f"Failed to fetch data from {list_url}")
                        data = await response.json()
                        medias = data.get("data", {}).get("medias", [])
                        if not medias:
                            break
                        for media in medias:
                            bvid = media.get("bvid", None)
                            title = media.get("title", None)
                            bvurl = f"https://www.bilibili.com/video/{bvid}"
                            bvid_info[bvurl] = title

                        if len(medias) < page_size:
                            break
                        page_num += 1
            else:
                raise ValueError("當前只支持合集和收藏夾")
    return bvid_info


# 下載播放列表
async def download_playlist(config, url, dirname):
    title = f"{dirname}/%(title)s.%(ext)s"
    sbp_args = (
        "yt-dlp",
        "--yes-playlist",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--paths",
        config.download_path,
        "-o",
        title,
        "--ffmpeg-location",
        f"{config.ffmpeg_location}",
    )

    if config.proxy:
        sbp_args += ("--proxy", f"{config.proxy}")

    if config.enable_yt_dlp_cookies:
        sbp_args += ("--cookies", f"{config.yt_dlp_cookies_path}")

    if config.loudnorm:
        sbp_args += ("--postprocessor-args", f"-af {config.loudnorm}")

    sbp_args += (url,)

    cmd = " ".join(sbp_args)
    log.info(f"download_playlist: {cmd}")
    download_proc = await asyncio.create_subprocess_exec(*sbp_args)
    return download_proc


# 下載一首歌曲
async def download_one_music(config, url, name=""):
    title = "%(title)s.%(ext)s"
    if name:
        title = f"{name}.%(ext)s"
    sbp_args = (
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--paths",
        config.download_path,
        "-o",
        title,
        "--ffmpeg-location",
        f"{config.ffmpeg_location}",
    )

    if config.proxy:
        sbp_args += ("--proxy", f"{config.proxy}")

    if config.enable_yt_dlp_cookies:
        sbp_args += ("--cookies", f"{config.yt_dlp_cookies_path}")

    if config.loudnorm:
        sbp_args += ("--postprocessor-args", f"-af {config.loudnorm}")

    sbp_args += (url,)

    cmd = " ".join(sbp_args)
    log.info(f"download_one_music: {cmd}")
    download_proc = await asyncio.create_subprocess_exec(*sbp_args)
    return download_proc


def _longest_common_prefix(file_names):
    if not file_names:
        return ""

    # 將第一個文件名作為初始前綴
    prefix = file_names[0]

    for file_name in file_names[1:]:
        while not file_name.startswith(prefix):
            # 如果當前文件名不以prefix開頭，則縮短prefix
            prefix = prefix[:-1]
            if not prefix:
                return ""

    return prefix


def safe_join_path(safe_root, directory):
    directory = os.path.join(safe_root, directory)
    # Normalize the directory path
    normalized_directory = os.path.normpath(directory)
    # Ensure the directory is within the safe root
    if not normalized_directory.startswith(os.path.normpath(safe_root)):
        raise ValueError(f"Access to directory '{directory}' is not allowed.")
    return normalized_directory


# 移除目錄下文件名前綴相同的
def remove_common_prefix(directory):
    files = os.listdir(directory)

    # 獲取所有文件的前綴
    common_prefix = _longest_common_prefix(files)

    log.info(f'Common prefix identified: "{common_prefix}"')

    pattern = re.compile(r"^[pP]?(\d+)\s+\d*(.+?)\.(.*$)")
    for filename in files:
        if filename == common_prefix:
            continue
        # 檢查文件名是否以共同前綴開頭
        if filename.startswith(common_prefix):
            # 構造新的文件名
            new_filename = filename[len(common_prefix) :]
            match = pattern.search(new_filename.strip())
            if match:
                num = match.group(1)
                name = match.group(2).replace(".", " ").strip()
                suffix = match.group(3)
                new_filename = f"{num}.{name}.{suffix}"
            # 生成完整的文件路徑
            old_file_path = os.path.join(directory, filename)
            new_file_path = os.path.join(directory, new_filename)

            # 重命名文件
            os.rename(old_file_path, new_file_path)
            log.debug(f'Renamed: "{filename}" to "{new_filename}"')


def try_add_access_control_param(config, url):
    if config.disable_httpauth:
        return url

    url_parts = urllib.parse.urlparse(url)
    file_path = urllib.parse.unquote(url_parts.path)
    correct_code = hashlib.sha256(
        (file_path + config.httpauth_username + config.httpauth_password).encode(
            "utf-8"
        )
    ).hexdigest()
    log.debug(f"rewrite url: [{file_path}, {correct_code}]")

    # make new url
    parsed_get_args = dict(urllib.parse.parse_qsl(url_parts.query))
    parsed_get_args.update({"code": correct_code})
    encoded_get_args = urllib.parse.urlencode(parsed_get_args, doseq=True)
    new_url = urllib.parse.ParseResult(
        url_parts.scheme,
        url_parts.netloc,
        url_parts.path,
        url_parts.params,
        encoded_get_args,
        url_parts.fragment,
    ).geturl()

    return new_url


# 判斷文件在不在排除目錄列表
def not_in_dirs(filename, ignore_absolute_dirs):
    file_absolute_path = os.path.abspath(filename)
    file_dir = os.path.dirname(file_absolute_path)
    for ignore_dir in ignore_absolute_dirs:
        if file_dir.startswith(ignore_dir):
            log.info(f"{file_dir} in {ignore_dir}")
            return False  # 文件在排除目錄中

    return True  # 文件不在排除目錄中


def is_docker():
    return os.path.exists("/app/.dockerenv")


async def restart_xiaomusic():
    # 重啟 xiaomusic 程序
    sbp_args = (
        "supervisorctl",
        "restart",
        "xiaomusic",
    )

    cmd = " ".join(sbp_args)
    log.info(f"restart_xiaomusic: {cmd}")
    await asyncio.sleep(2)
    proc = await asyncio.create_subprocess_exec(*sbp_args)
    exit_code = await proc.wait()  # 等待子進程完成
    log.info(f"restart_xiaomusic completed with exit code {exit_code}")
    return exit_code


async def update_version(version: str, lite: bool = True):
    if not is_docker():
        ret = "xiaomusic 更新只能在 docker 中進行"
        log.info(ret)
        return ret
    lite_tag = ""
    if lite:
        lite_tag = "-lite"
    arch = get_os_architecture()
    if "unknown" in arch:
        log.warning(f"update_version failed: {arch}")
        return arch
    # https://github.com/hanxi/xiaomusic/releases/download/main/app-amd64-lite.tar.gz
    url = f"https://gproxy.hanxi.cc/proxy/hanxi/xiaomusic/releases/download/{version}/app-{arch}{lite_tag}.tar.gz"
    target_directory = "/app"
    return await download_and_extract(url, target_directory)


def get_os_architecture():
    """
    獲取操作系統架構類型：amd64、arm64、arm-v7。

    Returns:
        str: 架构类型
    """
    arch = platform.machine().lower()

    if arch in ("x86_64", "amd64"):
        return "amd64"
    elif arch in ("aarch64", "arm64"):
        return "arm64"
    elif "arm" in arch or "armv7" in arch:
        return "arm-v7"
    else:
        return f"unknown architecture: {arch}"


async def download_and_extract(url: str, target_directory: str):
    ret = "OK"
    # 創建目標目錄
    os.makedirs(target_directory, exist_ok=True)

    # 使用 aiohttp 異步下載文件
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                file_name = os.path.join(target_directory, url.split("/")[-1])
                file_name = os.path.normpath(file_name)
                if not file_name.startswith(target_directory):
                    log.warning(f"Invalid file path: {file_name}")
                    return
                with open(file_name, "wb") as f:
                    # 以塊的方式下載文件，防止內存佔用過大
                    async for chunk in response.content.iter_any():
                        f.write(chunk)
                log.info(f"文件下載完成: {file_name}")

                # 解压下载的文件
                if file_name.endswith(".tar.gz"):
                    await extract_tar_gz(file_name, target_directory)
                else:
                    ret = f"下載失敗, 包有問題: {file_name}"
                log.warning(ret)

            else:
                ret = f"下載失敗, 狀態碼: {response.status}"
                log.warning(ret)
    return ret


async def extract_tar_gz(file_name: str, target_directory: str):
    # 使用 asyncio.create_subprocess_exec 執行 tar 解壓命令
    command = ["tar", "-xzvf", file_name, "-C", target_directory]
    # 啟動子進程執行解壓命令
    await asyncio.create_subprocess_exec(*command)
    # 不等待子進程完成
    log.info(f"extract_tar_gz ing {file_name}")


def chmodfile(file_path: str):
    try:
        os.chmod(file_path, 0o775)
    except Exception as e:
        log.info(f"chmodfile failed: {e}")


def chmoddir(dir_path: str):
    # 獲取指定目錄下的所有文件和子目錄
    for item in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item)
        # 確保是文件，且不是目錄
        if os.path.isfile(item_path):
            try:
                os.chmod(item_path, 0o775)
                log.info(f"Changed permissions of file: {item_path}")
            except Exception as e:
                log.info(f"chmoddir failed: {e}")


async def fetch_json_get(url, headers, config):
    connector = None
    proxy = None
    if config and config.proxy:
        connector = aiohttp.TCPConnector(
            ssl=False,  # 如需驗證SSL證書，可改為True（需確保代理支持）
            limit=10,
        )
        proxy = config.proxy
    try:
        # 2. 傳入代理配置創建ClientSession
        async with aiohttp.ClientSession(connector=connector) as session:
            # 3. 發起帶代理的GET請求
            async with session.get(
                url,
                headers=headers,
                proxy=proxy,  # 傳入格式化後的代理參數
                timeout=10,  # 超時時間（秒），避免無限等待
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    log.info(f"fetch_json_get: {url} success {data}")

                    # 確保返回結果為dict
                    if isinstance(data, dict):
                        return data
                    else:
                        log.warning(f"Expected dict, but got {type(data)}: {data}")
                        return {}
                else:
                    log.error(f"HTTP Error: {response.status} {url}")
                    return {}
    except aiohttp.ClientError as e:
        log.error(f"ClientError fetching {url} (proxy: {proxy}): {e}")
        return {}
    except asyncio.TimeoutError:
        log.error(f"Timeout fetching {url} (proxy: {proxy})")
        return {}
    except Exception as e:
        log.error(f"Unexpected error fetching {url} (proxy: {proxy}): {e}")
        return {}
    finally:
        # 4. 關閉連接器（避免資源洩漏）
        if connector and not connector.closed:
            await connector.close()


class LRUCache(OrderedDict):
    def __init__(self, max_size=1000):
        super().__init__()
        self.max_size = max_size

    def __setitem__(self, key, value):
        if key in self:
            # 移動到末尾(最近使用)
            self.move_to_end(key)
        super().__setitem__(key, value)
        # 如果超出大小限制,刪除最早使用的項
        if len(self) > self.max_size:
            self.popitem(last=False)

    def __getitem__(self, key):
        # 訪問時移動到末尾(最近使用)
        if key in self:
            self.move_to_end(key)
        return super().__getitem__(key)


class MusicUrlCache:
    def __init__(self, default_expire_days=1, max_size=1000):
        self.cache = LRUCache(max_size)
        self.default_expire_days = default_expire_days
        self.log = logging.getLogger(__name__)

    async def get(self, url: str, headers: dict = None, config=None) -> str:
        """獲取URL(優先從緩存獲取,沒有則請求API)

        Args:
            url: 原始URL
            headers: API請求需要的headers
        Returns:
            str: 真實播放URL
        """
        # 先查詢緩存
        cached_url = self._get_from_cache(url)
        if cached_url:
            self.log.info(f"Using cached url: {cached_url}")
            return cached_url

        # 緩存未命中,請求API
        return await self._fetch_from_api(url, headers, config)

    def _get_from_cache(self, url: str) -> str:
        """從緩存中獲取URL"""
        try:
            cached_url, expire_time = self.cache[url]
            if time.time() > expire_time:
                # 緩存過期,刪除
                del self.cache[url]
                return ""
            return cached_url
        except KeyError:
            return ""

    async def _fetch_from_api(self, url: str, headers: dict = None, config=None) -> str:
        """從API獲取真實URL"""
        data = await fetch_json_get(url, headers or {}, config)

        if not isinstance(data, dict):
            self.log.error(f"Invalid API response format: {data}")
            return ""

        real_url = data.get("url")
        if not real_url:
            self.log.error(f"No url in API response: {data}")
            return ""

        # 獲取過期時間
        expire_time = self._parse_expire_time(data)

        # 緩存結果
        self._set_cache(url, real_url, expire_time)
        self.log.info(
            f"Cached url, expire_time: {expire_time}, cache size: {len(self.cache)}"
        )
        return real_url

    def _parse_expire_time(self, data: dict) -> float | None:
        """解析API返回的過期時間"""
        try:
            extra = data.get("extra", {})
            expire_info = extra.get("expire", {})
            if expire_info and expire_info.get("canExpire"):
                expire_time = expire_info.get("time")
                if expire_time:
                    return float(expire_time)
        except Exception as e:
            self.log.warning(f"Failed to parse expire time: {e}")
        return None

    def _set_cache(self, url: str, real_url: str, expire_time: float = None):
        """設置緩存"""
        if expire_time is None:
            expire_time = time.time() + (self.default_expire_days * 24 * 3600)
        self.cache[url] = (real_url, expire_time)

    def clear(self):
        """清空緩存"""
        self.cache.clear()

    @property
    def size(self) -> int:
        """當前緩存大小"""
        return len(self.cache)


async def text_to_mp3(
    text: str, save_dir: str, voice: str = "zh-CN-XiaoxiaoNeural"
) -> str:
    """
    使用edge-tts將文本轉換為MP3語音文件

    参数:
        text: 需要轉換的文本內容
        save_dir: 保存MP3文件的目錄路徑
        voice: 語音模型（默認中文曉曉）

    返回:
        str: 生成的MP3文件完整路徑
    """
    # 確保保存目錄存在
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    # 基於文本和語音模型生成唯一文件名（避免相同文本不同語音重複）
    content = f"{text}_{voice}".encode()
    file_hash = hashlib.md5(content).hexdigest()
    mp3_filename = f"{file_hash}.mp3"
    mp3_path = os.path.join(save_dir, mp3_filename)

    # 文件已存在直接返回路徑
    if os.path.exists(mp3_path):
        return mp3_path

    # 調用edge-tts生成語音
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3_path)
        log.info(f"語音文件生成成功: {mp3_path}")
    except Exception as e:
        raise RuntimeError(f"生成語音文件失敗: {e}") from e

    return mp3_path
