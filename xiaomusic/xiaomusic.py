#!/usr/bin/env python3
import asyncio
import base64
import copy
import ipaddress
import json
import logging
import math
import os
import random
import re
import socket
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import asdict
from logging.handlers import RotatingFileHandler

from aiohttp import ClientSession, ClientTimeout
from miservice import MiAccount, MiIOService, MiNAService, miio_command
from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from xiaomusic import __version__
from xiaomusic.analytics import Analytics
from xiaomusic.config import (
    KEY_WORD_ARG_BEFORE_DICT,
    Config,
    Device,
)
from xiaomusic.const import (
    COOKIE_TEMPLATE,
    GET_ASK_BY_MINA,
    LATEST_ASK_API,
    NEED_USE_PLAY_MUSIC_API,
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
    SUPPORT_MUSIC_TYPE,
    TTS_COMMAND,
)
from xiaomusic.crontab import Crontab
from xiaomusic.plugin import PluginManager
from xiaomusic.utils import (
    Metadata,
    MusicUrlCache,
    chinese_to_number,
    chmodfile,
    custom_sort_key,
    deepcopy_data_no_sensitive_info,
    downloadfile,
    extract_audio_metadata,
    find_best_match,
    fuzzyfinder,
    get_local_music_duration,
    get_web_music_duration,
    list2str,
    not_in_dirs,
    parse_cookie_string,
    parse_str_to_dict,
    save_picture_by_base64,
    set_music_tag_to_file,
    thdplay,
    traverse_music_directory,
    try_add_access_control_param,
)


class XiaoMusic:
    """
    XiaoMusic 核心類
    負責協調和管理整個應用程序的運行，包括：
    1. 配置加載與管理 (config)
    2. 設備發現與通信 (MiService, MiIOService)
    3. 音樂播放控制 (本地播放、網絡播放)
    4. 插件系統管理 (PluginManager, JSPluginManager)
    5. 計畫任務調度 (Crontab)
    6. 數據統計 (Analytics)
    """

    def __init__(self, config: Config):
        self.config = config

        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")
        self.session = None
        self.last_timestamp = {}  # key为 did. 记录每个设备最后一次询问的时间戳 (用于轮询)
        self.last_record = None
        self.last_cmd = ""
        self.cookie_jar = None
        self.mina_service = None  # 小米 AI 服務接口 (用於獲取對話記錄)
        self.miio_service = None  # 小米 IoT 服務接口 (用於設備控制)
        self.login_acount = None
        self.login_password = None
        self.polling_event = asyncio.Event()  # 用於控制輪詢的異步事件
        self.new_record_event = asyncio.Event()  # 新對話記錄事件
        self.url_cache = MusicUrlCache()  # 緩存音樂真實 URL 避免頻繁解析

        self.all_music = {}  # 所有音樂索引 {name: path/url}
        self._all_radio = {}  # 電台列表
        self._web_music_api = {}  # 需要通過 API 獲取播放鏈接的列表
        self.music_list = {}  # 播放列表 key 為目錄名, value 為 play_list
        self.default_music_list_names = []  # 非自定義歌單 (如默認目錄)
        self.devices = {}  # 設備對象字典 key 為 did
        self._cur_did = None  # 當前操作的設備 did
        self.running_task = []  # 當前正在運行的異步任務列表
        self.all_music_tags = {}  # 歌曲額外信息 (元數據、標籤)
        self._tag_generation_task = False  # 標記是否正在生成標籤
        self._extra_index_search = {}
        self.custom_play_list = None  # 自定義播放列表

        # 初始化配置
        self.init_config()

        # 初始化日誌
        self.setup_logger()

        # 初始化計畫任務管理器
        self.crontab = Crontab(self.log)

        # 初始化 JS 插件管理器
        try:
            from xiaomusic.js_plugin_manager import JSPluginManager

            self.js_plugin_manager = JSPluginManager(self)
            self.log.info("JS Plugin Manager initialized successfully")
        except Exception as e:
            self.log.error(f"Failed to initialize JS Plugin Manager: {e}")
            self.js_plugin_manager = None

        # 初始化 JS 插件適配器 (用於格式轉換)
        try:
            from xiaomusic.js_adapter import JSAdapter

            self.js_adapter = JSAdapter(self)
            self.log.info("JS Adapter initialized successfully")
        except Exception as e:
            self.log.error(f"Failed to initialize JS Adapter: {e}")

        # 嘗試從 setting.json 加載配置
        self.try_init_setting()

        # 啟動時重新生成一次播放列表
        self._gen_all_music_list()

        # 初始化 Python 插件管理器
        self.plugin_manager = PluginManager(self)

        # 更新設備列表
        self.update_devices()

        # 啟動統計模組
        self.analytics = Analytics(self.log, self.config)

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"Startup OK. {debug_config}")

        if self.config.conf_path == self.music_path:
            self.log.warning("配置文件目錄和音樂目錄建議設置為不同的目錄")

    # 私有方法：调用插件方法的通用封装
    async def __call_plugin_method(
        self,
        plugin_name: str,
        method_name: str,
        music_item: dict,
        result_key: str,
        required_field: str = None,
        **kwargs,
    ):
        """
        通用方法：調用 JS 插件的方法並返回結果
        封裝了插件檢查、調用、錯誤處理和結果校驗邏輯

        Args:
            plugin_name: 插件名稱
            method_name: 插件方法名（如 get_media_source 或 get_lyric）
            music_item: 音樂項數據
            result_key: 返回結果中的主要字段名（如 'url' 或 'rawLrc'），用於初步校驗成功與否
            required_field: 必須存在的字段（用於進一步校驗，如必須包含 'url'）
            **kwargs: 傳遞給插件方法的額外參數

        Returns:
            dict: 包含 success 和對應字段的字典，如果失敗則包含 error 信息
        """
        if not music_item:
            return {"success": False, "error": "Music item required"}

        # 检查插件管理器是否可用
        if not self.js_plugin_manager:
            return {"success": False, "error": "JS Plugin Manager not available"}

        enabled_plugins = self.js_plugin_manager.get_enabled_plugins()
        if plugin_name not in enabled_plugins:
            return {"success": False, "error": f"Plugin {plugin_name} not enabled"}

        try:
            # 调用插件方法，传递额外参数
            result = getattr(self.js_plugin_manager, method_name)(
                plugin_name, music_item, **kwargs
            )
            # 兼容性檢查：確保返回了預期的字段
            if (
                not result
                or not result.get(result_key)
                or result.get(result_key) == "None"
            ):
                return {"success": False, "error": f"Failed to get {result_key}"}

            # 如果指定了必填字段，则额外校验
            if required_field and not result.get(required_field):
                return {
                    "success": False,
                    "error": f"Missing required field: {required_field}",
                }
            # 追加属性后返回
            result["success"] = True
            return result

        except Exception as e:
            self.log.error(f"Plugin {plugin_name} {method_name} failed: {e}")
            return {"success": False, "error": str(e)}

    def init_config(self):
        self.music_path = self.config.music_path
        self.download_path = self.config.download_path
        if not self.download_path:
            self.download_path = self.music_path

        if not os.path.exists(self.download_path):
            os.makedirs(self.download_path)

        self.hostname = self.config.hostname
        if not self.hostname.startswith(("http://", "https://")):
            self.hostname = f"http://{self.hostname}"  # 默认 http
        self.port = self.config.port
        self.public_port = self.config.public_port
        if self.public_port == 0:
            self.public_port = self.port
        # 自动3thplay生成播放 post url
        self.thdtarget = f"{self.hostname}:{self.public_port}/thdaction"  # "HTTP://192.168.1.10:58090/thdaction"

        self.active_cmd = self.config.active_cmd.split(",")
        self.exclude_dirs = set(self.config.exclude_dirs.split(","))
        self.music_path_depth = self.config.music_path_depth
        self.continue_play = self.config.continue_play

    def update_devices(self):
        self.device_id_did = {}  # key 为 device_id
        self.groups = {}  # key 为 group_name, value 为 device_id_list
        XiaoMusicDevice.dict_clear(self.devices)  # 需要清理旧的定时器
        did2group = parse_str_to_dict(self.config.group_list, d1=",", d2=":")
        for did, device in self.config.devices.items():
            group_name = did2group.get(did)
            if not group_name:
                group_name = device.name
            if group_name not in self.groups:
                self.groups[group_name] = []
            self.groups[group_name].append(device.device_id)
            self.device_id_did[device.device_id] = did
            self.devices[did] = XiaoMusicDevice(self, device, group_name)

    def setup_logger(self):
        log_format = f"%(asctime)s [{__version__}] [%(levelname)s] %(filename)s:%(lineno)d: %(message)s"
        date_format = "[%Y-%m-%d %H:%M:%S]"
        formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
        logging.basicConfig(
            format=log_format,
            datefmt=date_format,
        )

        log_file = self.config.log_file
        log_path = os.path.dirname(log_file)
        if log_path and not os.path.exists(log_path):
            os.makedirs(log_path)
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                self.log.warning(f"無法刪除舊日誌文件: {log_file} {e}")
        handler = RotatingFileHandler(
            self.config.log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=1,
            encoding="utf-8",
        )
        handler.stream.flush()
        handler.setFormatter(formatter)
        self.log = logging.getLogger("xiaomusic")
        self.log.addHandler(handler)
        self.log.setLevel(logging.DEBUG if self.config.verbose else logging.INFO)

    async def poll_latest_ask(self):
        """
        輪詢最新對話記錄
        定時向小米服務器查詢最新的語音指令，用於觸發音樂播放等功能
        支持 Mina 接口和 XiaoAi 接口兩種方式
        """
        async with ClientSession() as session:
            while True:
                if not self.config.enable_pull_ask:
                    self.log.debug("Listening new message disabled")
                    await asyncio.sleep(5)
                    continue

                self.log.debug(
                    f"Listening new message, timestamp: {self.last_timestamp}"
                )
                session._cookie_jar = self.cookie_jar

                # 拉取所有音箱的对话记录
                tasks = []
                for device_id in self.device_id_did:
                    # 首次用当前时间初始化
                    did = self.get_did(device_id)
                    if did not in self.last_timestamp:
                        self.last_timestamp[did] = int(time.time() * 1000)

                    hardware = self.get_hardward(device_id)
                    # 判斷是否強制使用 Mina 接口
                    if (hardware in GET_ASK_BY_MINA) or self.config.get_ask_by_mina:
                        tasks.append(self.get_latest_ask_by_mina(device_id))
                    else:
                        tasks.append(
                            self.get_latest_ask_from_xiaoai(session, device_id)
                        )
                await asyncio.gather(*tasks)

                # 控制輪詢頻率
                start = time.perf_counter()
                await self.polling_event.wait()
                if self.config.pull_ask_sec <= 1:
                    if (d := time.perf_counter() - start) < 1:
                        await asyncio.sleep(1 - d)
                else:
                    sleep_sec = 0
                    while True:
                        await asyncio.sleep(1)
                        sleep_sec = sleep_sec + 1
                        if sleep_sec >= self.config.pull_ask_sec:
                            break

    async def init_all_data(self, session):
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")
        is_need_login = await self.need_login()
        if is_need_login:
            self.log.info("try login")
            await self.login_miboy(session)
        else:
            self.log.info("already logined")
        await self.try_update_device_id()
        cookie_jar = self.get_cookie()
        if cookie_jar:
            session.cookie_jar.update_cookies(cookie_jar)
        self.cookie_jar = session.cookie_jar

    async def need_login(self):
        if self.mina_service is None:
            return True
        if self.mina_service is None:
            return True
        if self.login_acount != self.config.account:
            return True
        if self.login_password != self.config.password:
            return True

        try:
            await self.mina_service.device_list()
        except Exception as e:
            self.log.warning(f"可能登录失败. {e}")
            return True
        return False

    async def login_miboy(self, session):
        try:
            account = MiAccount(
                session,
                self.config.account,
                self.config.password,
                str(self.mi_token_home),
            )
            # Forced login to refresh to refresh token
            await account.login("micoapi")
            self.mina_service = MiNAService(account)
            self.miio_service = MiIOService(account)
            self.login_acount = self.config.account
            self.login_password = self.config.password
            self.log.info(f"登錄完成. {self.login_acount}")
        except Exception as e:
            self.mina_service = None
            self.miio_service = None
            self.log.warning(f"可能登錄失敗. {e}")

    async def try_update_device_id(self):
        try:
            mi_dids = self.config.mi_did.split(",")
            hardware_data = await self.mina_service.device_list()
            devices = {}
            for h in hardware_data:
                device_id = h.get("deviceID", "")
                hardware = h.get("hardware", "")
                did = h.get("miotDID", "")
                name = h.get("alias", "")
                if not name:
                    name = h.get("name", "未知名字")
                if device_id and hardware and did and (did in mi_dids):
                    device = self.config.devices.get(did, Device())
                    device.did = did
                    # 将did存一下 方便其他地方调用
                    self._cur_did = did
                    device.device_id = device_id
                    device.hardware = hardware
                    device.name = name
                    devices[did] = device
            self.config.devices = devices
            self.log.info(f"選中的設備: {devices}")
        except Exception as e:
            self.log.warning(f"可能登录失败. {e}")

    def get_cookie(self):
        if self.config.cookie:
            cookie_jar = parse_cookie_string(self.config.cookie)
            return cookie_jar

        if not os.path.exists(self.mi_token_home):
            self.log.warning(f"{self.mi_token_home} file not exist")
            return None

        with open(self.mi_token_home, encoding="utf-8") as f:
            user_data = json.loads(f.read())
        user_id = user_data.get("userId")
        service_token = user_data.get("micoapi")[1]
        device_id = self.get_one_device_id()
        cookie_string = COOKIE_TEMPLATE.format(
            device_id=device_id, service_token=service_token, user_id=user_id
        )
        return parse_cookie_string(cookie_string)

    def get_one_device_id(self):
        device_id = next(iter(self.device_id_did), "")
        return device_id

    def get_did(self, device_id):
        return self.device_id_did.get(device_id, "")

    def get_hardward(self, device_id):
        device = self.get_device_by_device_id(device_id)
        if not device:
            return ""
        return device.hardware

    def get_group_device_id_list(self, group_name):
        return self.groups[group_name]

    def get_group_devices(self, group_name):
        device_id_list = self.groups[group_name]
        devices = {}
        for device_id in device_id_list:
            did = self.device_id_did.get(device_id, "")
            if did:
                devices[did] = self.devices[did]
        return devices

    def get_device_by_device_id(self, device_id):
        did = self.device_id_did.get(device_id)
        if not did:
            return None
        return self.config.devices.get(did)

    async def get_latest_ask_from_xiaoai(self, session, device_id):
        cookies = {"deviceId": device_id}
        retries = 3
        for i in range(retries):
            try:
                timeout = ClientTimeout(total=15)
                hardware = self.get_hardward(device_id)
                url = LATEST_ASK_API.format(
                    hardware=hardware,
                    timestamp=str(int(time.time() * 1000)),
                )
                # self.log.debug(f"url:{url} device_id:{device_id} hardware:{hardware}")
                r = await session.get(url, timeout=timeout, cookies=cookies)

                # 检查响应状态码
                if r.status != 200:
                    self.log.warning(f"Request failed with status {r.status}")
                    # fix #362
                    if i == 2 and r.status == 401:
                        await self.init_all_data(self.session)
                    continue

            except asyncio.CancelledError:
                self.log.warning("Task was cancelled.")
                return None

            except Exception as e:
                self.log.warning(f"Execption {e}")
                continue

            try:
                data = await r.json()
            except Exception as e:
                self.log.warning(f"Execption {e}")
                if i == 2:
                    # tricky way to fix #282 #272 # if it is the third time we re init all data
                    self.log.info("Maybe outof date trying to re init it")
                    await self.init_all_data(self.session)
            else:
                return self._get_last_query(device_id, data)
        self.log.warning("get_latest_ask_from_xiaoai. All retries failed.")

    async def get_latest_ask_by_mina(self, device_id):
        try:
            did = self.get_did(device_id)
            messages = await self.mina_service.get_latest_ask(device_id)
            self.log.debug(
                f"get_latest_ask_by_mina device_id:{device_id} did:{did} messages:{messages}"
            )
            for message in messages:
                query = message.response.answer[0].question
                answer = message.response.answer[0].content
                last_record = {
                    "time": message.timestamp_ms,
                    "did": did,
                    "query": query,
                    "answer": answer,
                }
                self._check_last_query(last_record)
        except Exception as e:
            self.log.warning(f"get_latest_ask_by_mina {e}")
        return

    def _get_last_query(self, device_id, data):
        did = self.get_did(device_id)
        self.log.debug(f"_get_last_query device_id:{device_id} did:{did} data:{data}")
        if d := data.get("data"):
            records = json.loads(d).get("records")
            if not records:
                return
            last_record = records[0]
            last_record["did"] = did
            answers = last_record.get("answers", [{}])
            if answers:
                answer = answers[0].get("tts", {}).get("text", "").strip()
                last_record["answer"] = answer
            self._check_last_query(last_record)

    def _check_last_query(self, last_record):
        did = last_record["did"]
        timestamp = last_record.get("time")
        query = last_record.get("query", "").strip()
        self.log.debug(f"{did} 獲取到最後一條對話記錄：{query} {timestamp}")

        if timestamp > self.last_timestamp[did]:
            self.last_timestamp[did] = timestamp
            self.last_record = last_record
            self.new_record_event.set()

    def get_filename(self, name):
        if name not in self.all_music:
            self.log.info(f"get_filename not in. name:{name}")
            return ""
        filename = self.all_music[name]
        self.log.info(f"try get_filename. filename:{filename}")
        if os.path.exists(filename):
            return filename
        return ""

    # 判斷本地音樂是否存在，網絡歌曲不判斷
    def is_music_exist(self, name):
        if name not in self.all_music:
            return False
        if self.is_web_music(name):
            return True
        filename = self.get_filename(name)
        if filename:
            return True
        return False

    # 是否是網絡電台
    def is_web_radio_music(self, name):
        return name in self._all_radio

    # 是否是網絡歌曲
    def is_web_music(self, name):
        if name not in self.all_music:
            return False
        url = self.all_music[name]
        return url.startswith(("http://", "https://"))

    # 是否是需要通過api獲取播放鏈接的網絡歌曲
    def is_need_use_play_music_api(self, name):
        return name in self._web_music_api

    def get_music_tags(self, name):
        tags = copy.copy(self.all_music_tags.get(name, asdict(Metadata())))
        picture = tags["picture"]
        if picture:
            if picture.startswith(self.config.picture_cache_path):
                picture = picture[len(self.config.picture_cache_path) :]
            picture = picture.replace("\\", "/")
            if picture.startswith("/"):
                picture = picture[1:]
            encoded_name = urllib.parse.quote(picture)
            tags["picture"] = try_add_access_control_param(
                self.config,
                f"{self.hostname}:{self.public_port}/picture/{encoded_name}",
            )
        return tags

    # 修改標籤信息
    def set_music_tag(self, name, info):
        if self._tag_generation_task:
            self.log.info("tag 更新中，請等待")
            return "Tag generation task running"
        tags = copy.copy(self.all_music_tags.get(name, asdict(Metadata())))
        tags["title"] = info.title
        tags["artist"] = info.artist
        tags["album"] = info.album
        tags["year"] = info.year
        tags["genre"] = info.genre
        tags["lyrics"] = info.lyrics
        file_path = self.all_music[name]
        if info.picture:
            tags["picture"] = save_picture_by_base64(
                info.picture, self.config.picture_cache_path, file_path
            )
        if self.config.enable_save_tag and (not self.is_web_music(name)):
            set_music_tag_to_file(file_path, Metadata(tags))
        self.all_music_tags[name] = tags
        self.try_save_tag_cache()
        return "OK"

    async def get_music_sec_url(self, name, true_url):
        """獲取歌曲播放時長和播放地址

        Args:
            name: 歌曲名稱
            true_url: 真實播放URL
        Returns:
            tuple: (播放時長(秒), 播放地址)
        """

        # 獲取播放時長
        if true_url is not None:
            url = true_url
            sec = await self._get_online_music_duration(name, true_url)
            self.log.info(f"在線歌曲時長獲取：：{name} ；sec：：{sec}")
        else:
            url, origin_url = await self.get_music_url(name)
            self.log.info(
                f"get_music_sec_url. name:{name} url:{url} origin_url:{origin_url}"
            )

            # 电台直接返回
            if self.is_web_radio_music(name):
                self.log.info("電台不會有播放時長")
                return 0, url
            if self.is_web_music(name):
                sec = await self._get_web_music_duration(name, url, origin_url)
            else:
                sec = await self._get_local_music_duration(name, url)

        if sec <= 0:
            self.log.warning(f"獲取歌曲時長失敗 {name} {url}")
        return sec, url

    async def _get_web_music_duration(self, name, url, origin_url):
        """獲取網絡音樂時長"""
        if not origin_url:
            origin_url = url if url else self.all_music[name]

        if self.config.web_music_proxy:
            # 代理模式使用原始地址獲取時長
            duration, _ = await get_web_music_duration(origin_url, self.config)
        else:
            duration, url = await get_web_music_duration(origin_url, self.config)

        sec = math.ceil(duration)
        self.log.info(f"網絡歌曲 {name} : {origin_url} {url} 的時長 {sec} 秒")
        return sec

    async def _get_local_music_duration(self, name, url):
        """獲取本地音樂時長"""
        filename = self.get_filename(name)
        self.log.info(f"get_music_sec_url. name:{name} filename:{filename}")
        duration = await get_local_music_duration(filename, self.config)
        sec = math.ceil(duration)
        self.log.info(f"本地歌曲 {name} : {filename} {url} 的時長 {sec} 秒")
        return sec

    async def _get_online_music_duration(self, name, url):
        """獲取在線音樂時長"""
        self.log.info(f"get_music_sec_url. name:{name}")
        duration = await get_local_music_duration(url, self.config)
        sec = math.ceil(duration)
        self.log.info(f"在線歌曲 {name} : {url} 的時長 {sec} 秒")
        return sec

    async def get_music_url(self, name):
        """
        獲取音樂播放地址
        封裝了本地音樂、網絡音樂、代理模式等多種情況的處理邏輯

        Args:
            name: 歌曲名稱 (index 索引中的鍵)
        Returns:
            tuple: (播放地址, 原始地址)
                   - 播放地址：確實可用的 URL (可能是本地服務 URL、網絡 URL 或 代理 URL)
                   - 原始地址：網絡音樂的原始鏈接 (用於計算時長等)，本地音樂則為 None
        """
        if self.is_web_music(name):
            return await self._get_web_music_url(name)
        return self._get_local_music_url(name), None

    async def _get_web_music_url(self, name):
        """獲取網絡音樂播放地址"""
        url = self.all_music[name]
        self.log.info(f"get_music_url web music. name:{name}, url:{url}")

        # 需要通過 API 獲取真實播放地址 (例如某些網站鏈接過期或需簽名)
        if self.is_need_use_play_music_api(name):
            url = await self._get_url_from_api(name, url)
            if not url:
                return "", None

        # 是否需要通過本機代理轉發 (解決 CORS 或 內網穿透問題)
        if self.config.web_music_proxy:
            proxy_url = self._get_proxy_url(url)
            return proxy_url, url

        return url, None

    async def _get_url_from_api(self, name, url):
        """通過API獲取真實播放地址"""
        headers = self._web_music_api[name].get("headers", {})
        # 使用 url_cache 緩存短時間內的解析結果，減輕服務器壓力
        url = await self.url_cache.get(url, headers, self.config)
        if not url:
            self.log.error(f"get_music_url use api fail. name:{name}, url:{url}")
        return url

    def _get_proxy_url(self, origin_url):
        """
        獲取代理 URL
        將原始 URL base64 編碼後作為參數傳遞給 /proxy 接口
        """
        urlb64 = base64.b64encode(origin_url.encode("utf-8")).decode("utf-8")
        proxy_url = f"{self.hostname}:{self.public_port}/proxy?urlb64={urlb64}"
        self.log.info(f"Using proxy url: {proxy_url}")
        return proxy_url

    def _get_local_music_url(self, name):
        """
        獲取本地音樂播放地址
        將本地文件路徑轉換為 HTTP 服務的訪問路徑 (/music/...)
        """
        filename = self.get_filename(name)

        # 處理文件路徑，移除 music_path 前綴，並統一分隔符
        if filename.startswith(self.config.music_path):
            filename = filename[len(self.config.music_path) :]
        filename = filename.replace("\\", "/")
        if filename.startswith("/"):
            filename = filename[1:]

        self.log.info(
            f"_get_local_music_url local music. name:{name}, filename:{filename}"
        )

        # 構造 URL，對文件名進行 URL 編碼
        encoded_name = urllib.parse.quote(filename)
        url = f"{self.hostname}:{self.public_port}/music/{encoded_name}"
        # 嘗試添加訪問控制參數 (如 token)
        return try_add_access_control_param(self.config, url)

    # 给前端调用
    def refresh_music_tag(self):
        if not self.ensure_single_thread_for_tag():
            return
        filename = self.config.tag_cache_path
        if filename is not None:
            # 清空 cache
            with open(filename, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            self.log.info("刷新：已清空 tag cache")
        else:
            self.log.info("刷新：tag cache 未啟用")
        # TODO: 优化性能？
        # TODO 如何安全的清空 picture_cache_path
        self.all_music_tags = {}  # 需要清空内存残留
        self.try_gen_all_music_tag()
        self.log.info("刷新：已啟動重建 tag cache")

    def try_load_from_tag_cache(self) -> dict:
        filename = self.config.tag_cache_path
        tag_cache = {}
        try:
            if filename is not None:
                if os.path.exists(filename):
                    with open(filename, encoding="utf-8") as f:
                        tag_cache = json.load(f)
                    self.log.info(f"已從【{filename}】加載 tag cache")
                else:
                    self.log.info(f"【{filename}】tag cache 已啟用，但文件不存在")
            else:
                self.log.info("加載：tag cache 未啟用")
        except Exception as e:
            self.log.exception(f"Execption {e}")
        return tag_cache

    def try_save_tag_cache(self):
        filename = self.config.tag_cache_path
        if filename is not None:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.all_music_tags, f, ensure_ascii=False, indent=2)
            self.log.info(f"保存：tag cache 已保存到【{filename}】")
        else:
            self.log.info("保存：tag cache 未啟用")

    def ensure_single_thread_for_tag(self):
        if self._tag_generation_task:
            self.log.info("tag 更新中，請等待")
        return not self._tag_generation_task

    def try_gen_all_music_tag(self, only_items: dict = None):
        if self.ensure_single_thread_for_tag():
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._gen_all_music_tag(only_items))
                self.log.info("啟動後台構建 tag cache")
            else:
                self.log.info("協程時間循環未啟動")

    async def _gen_all_music_tag(self, only_items: dict = None):
        self._tag_generation_task = True
        if only_items is None:
            only_items = self.all_music  # 默認更新全部

        all_music_tags = self.try_load_from_tag_cache()
        all_music_tags.update(self.all_music_tags)  # 保證最新

        ignore_tag_absolute_dirs = self.config.get_ignore_tag_dirs()
        self.log.info(f"ignore_tag_absolute_dirs: {ignore_tag_absolute_dirs}")
        for name, file_or_url in only_items.items():
            start = time.perf_counter()
            if name not in all_music_tags:
                try:
                    if self.is_web_music(name):
                        # TODO: 網絡歌曲獲取歌曲額外信息
                        pass
                    elif os.path.exists(file_or_url) and not_in_dirs(
                        file_or_url, ignore_tag_absolute_dirs
                    ):
                        all_music_tags[name] = extract_audio_metadata(
                            file_or_url, self.config.picture_cache_path
                        )
                    else:
                        self.log.info(f"{name}/{file_or_url} 無法更新 tag")
                except BaseException as e:
                    self.log.exception(f"{e} {file_or_url} error {type(file_or_url)}!")
            if (time.perf_counter() - start) < 1:
                await asyncio.sleep(0.001)
            else:
                # 处理一首歌超过1秒，则等1秒，解决挂载网盘卡死的问题
                await asyncio.sleep(1)
        # 全部更新结束后，一次性赋值
        self.all_music_tags = all_music_tags
        # 刷新 tag cache
        self.try_save_tag_cache()
        self._tag_generation_task = False
        self.log.info("tag 更新完成")

    # 獲取目錄下所有歌曲,生成隨機播放列表
    def _gen_all_music_list(self):
        self.all_music = {}
        all_music_by_dir = {}
        local_musics = traverse_music_directory(
            self.music_path,
            depth=self.music_path_depth,
            exclude_dirs=self.exclude_dirs,
            support_extension=SUPPORT_MUSIC_TYPE,
        )
        for dir_name, files in local_musics.items():
            if len(files) == 0:
                continue
            if dir_name == os.path.basename(self.music_path):
                dir_name = "其他"
            if self.music_path != self.download_path and dir_name == os.path.basename(
                self.download_path
            ):
                dir_name = "下載"
            if dir_name not in all_music_by_dir:
                all_music_by_dir[dir_name] = {}
            for file in files:
                # 歌曲名字相同會覆蓋
                filename = os.path.basename(file)
                (name, _) = os.path.splitext(filename)
                self.all_music[name] = file
                all_music_by_dir[dir_name][name] = True
                self.log.debug(f"_gen_all_music_list {name}:{dir_name}:{file}")

        # self.log.debug(self.all_music)

        self.music_list = OrderedDict(
            {
                "臨時搜索列表": [],
                "所有歌曲": [],
                "所有電台": [],
                "收藏": [],
                "全部": [],  # 包含所有歌曲和所有電台
                "下載": [],  # 下載目錄下的
                "其他": [],  # 主目錄下的
                "最近新增": [],  # 按文件時間排序
            }
        )
        # 最近新增(不包含網絡歌單)
        self.music_list["最近新增"] = sorted(
            self.all_music.keys(),
            key=lambda x: os.path.getmtime(self.all_music[x]),
            reverse=True,
        )[: self.config.recently_added_playlist_len]

        # 網絡歌單
        try:
            # NOTE: 函数内会更新 self.all_music, self._music_list；重建 self._all_radio
            self._append_music_list()
        except Exception as e:
            self.log.exception(f"Execption {e}")

        # 全部，所有，自定義歌單（收藏）
        self.music_list["全部"] = list(self.all_music.keys())
        self.music_list["所有歌曲"] = [
            name for name in self.all_music.keys() if name not in self._all_radio
        ]

        # 文件夾歌單
        for dir_name, musics in all_music_by_dir.items():
            self.music_list[dir_name] = list(musics.keys())
            # self.log.debug("dir_name:%s, list:%s", dir_name, self.music_list[dir_name])

        # 歌單排序
        for _, play_list in self.music_list.items():
            play_list.sort(key=custom_sort_key)

        # 非自定義歌單
        self.default_music_list_names = list(self.music_list.keys())

        # 刷新自定義歌單
        self.refresh_custom_play_list()

        # 更新每個設備的歌單
        self.update_all_playlist()

        # 重建索引
        self._extra_index_search = {}
        for k, v in self.all_music.items():
            # 如果不是 url，則增加索引
            if not (v.startswith("http") or v.startswith("https")):
                self._extra_index_search[v] = k

        # all_music 更新，重建 tag
        self.try_gen_all_music_tag()

    def refresh_custom_play_list(self):
        try:
            # 刪除舊的自定義個歌單
            for k in list(self.music_list.keys()):
                if k not in self.default_music_list_names:
                    del self.music_list[k]
            # 合併新的自定義個歌單
            custom_play_list = self.get_custom_play_list()
            for k, v in custom_play_list.items():
                self.music_list[k] = list(v)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    # 給歌單裡補充網絡歌單
    def _append_music_list(self):
        if not self.config.music_list_json:
            return

        self._all_radio = {}
        self._web_music_api = {}
        music_list = json.loads(self.config.music_list_json)
        try:
            for item in music_list:
                list_name = item.get("name")
                musics = item.get("musics")
                if (not list_name) or (not musics):
                    continue
                one_music_list = []
                for music in musics:
                    name = music.get("name")
                    url = music.get("url")
                    music_type = music.get("type")
                    if (not name) or (not url):
                        continue
                    self.all_music[name] = url
                    one_music_list.append(name)

                    # 處理電台列表
                    if music_type == "radio":
                        self._all_radio[name] = url
                    if music.get("api"):
                        self._web_music_api[name] = music
                self.log.debug(one_music_list)
                # 歌曲名字相同會覆蓋
                self.music_list[list_name] = one_music_list
            if self._all_radio:
                self.music_list["所有電台"] = list(self._all_radio.keys())
            # self.log.debug(self.all_music)
            # self.log.debug(self.music_list)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    async def analytics_task_daily(self):
        while True:
            await self.analytics.send_daily_event()
            await asyncio.sleep(3600)

    def start_file_watch(self):
        if not self.config.enable_file_watch:
            self.log.info("目錄監控功能已關閉")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        # 延時配置項 file_watch_debounce
        self._file_watch_handler = XiaoMusicPathWatch(
            callback=self._on_file_change,
            debounce_delay=self.config.file_watch_debounce,
            loop=loop,
        )
        # 創建監控 music_path 目錄對象
        self._observer = Observer()
        self._observer.schedule(
            self._file_watch_handler, self.music_path, recursive=True
        )
        self._observer.start()
        self.log.info(f"已啟動對 {self.music_path} 的目錄監控。")

    def _on_file_change(self):
        self.log.info("檢測到目錄音樂文件變化，正在刷新歌曲列表。")
        self._gen_all_music_list()

    def stop_file_watch(self):
        if hasattr(self, "_observer"):
            self._observer.stop()
            self._observer.join()
            self.log.info("已停止目錄監控。")

    async def run_forever(self):
        self.log.info("run_forever start")
        self.try_gen_all_music_tag()  # 事件循環開始後調用一次
        self.crontab.start()
        asyncio.create_task(self.analytics.send_startup_event())
        # 取配置 enable_file_watch 循環開始時調用一次，控制目錄監控開關
        if self.config.enable_file_watch:
            self.start_file_watch()
        analytics_task = asyncio.create_task(self.analytics_task_daily())
        assert (
            analytics_task is not None
        )  # to keep the reference to task, do not remove this
        async with ClientSession() as session:
            self.session = session
            self.log.info(f"run_forever session:{self.session}")
            await self.init_all_data(session)
            task = asyncio.create_task(self.poll_latest_ask())
            assert task is not None  # to keep the reference to task, do not remove this
            while True:
                self.polling_event.set()
                await self.new_record_event.wait()
                self.new_record_event.clear()
                new_record = self.last_record
                self.polling_event.clear()  # stop polling when processing the question
                query = new_record.get("query", "").strip()
                did = new_record.get("did", "").strip()
                await self.do_check_cmd(did, query, False)
                answer = new_record.get("answer")
                answers = new_record.get("answers", [{}])
                if answers:
                    answer = answers[0].get("tts", {}).get("text", "").strip()
                    await self.reset_timer_when_answer(len(answer), did)
                    self.log.debug(f"query:{query} did:{did} answer:{answer}")

    # 匹配命令
    async def do_check_cmd(self, did="", query="", ctrl_panel=True, **kwargs):
        self.log.info(f"收到消息:{query} 控制面板:{ctrl_panel} did:{did}")
        self.last_cmd = query  # <--- 【新增這行】無論來自Web還是語音，先存下來
        try:
            opvalue, oparg = self.match_cmd(did, query, ctrl_panel)
            if not opvalue:
                await asyncio.sleep(1)
                await self.check_replay(did)
                return

            func = getattr(self, opvalue)
            await func(did=did, arg1=oparg)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    # 重置計時器
    async def reset_timer_when_answer(self, answer_length, did):
        await self.devices[did].reset_timer_when_answer(answer_length)

    def append_running_task(self, task):
        self.running_task.append(task)

    async def cancel_all_tasks(self):
        if len(self.running_task) == 0:
            self.log.info("cancel_all_tasks no task")
            return
        for task in self.running_task:
            self.log.info(f"cancel_all_tasks {task}")
            task.cancel()
        await asyncio.gather(*self.running_task, return_exceptions=True)
        self.running_task = []

    async def is_task_finish(self):
        if len(self.running_task) == 0:
            return True
        task = self.running_task[0]
        if task and task.done():
            return True
        return False

    async def check_replay(self, did):
        return await self.devices[did].check_replay()

    # 檢查是否匹配到完全一樣的指令
    def check_full_match_cmd(self, did, query, ctrl_panel):
        if query in self.config.key_match_order:
            opkey = query
            opvalue = self.config.key_word_dict.get(opkey)
            if ctrl_panel or self.isplaying(did):
                return opvalue
            else:
                if not self.active_cmd or opvalue in self.active_cmd:
                    return opvalue
        return None

    # 匹配命令
    def match_cmd(self, did, query, ctrl_panel):
        # 優先處理完全匹配
        opvalue = self.check_full_match_cmd(did, query, ctrl_panel)
        if opvalue:
            self.log.info(f"完全匹配指令. query:{query} opvalue:{opvalue}")
            # 自定義口令
            if opvalue.startswith("exec#"):
                code = opvalue.split("#", 1)[1]
                return ("exec", code)
            return (opvalue, "")

        for opkey in self.config.key_match_order:
            patternarg = rf"(.*){opkey}(.*)"
            # 匹配參數
            matcharg = re.match(patternarg, query)
            if not matcharg:
                # self.log.debug(patternarg)
                continue

            argpre = matcharg.groups()[0]
            argafter = matcharg.groups()[1]
            self.log.debug(
                "matcharg. opkey:%s, argpre:%s, argafter:%s",
                opkey,
                argpre,
                argafter,
            )
            oparg = argafter
            if opkey in KEY_WORD_ARG_BEFORE_DICT:
                oparg = argpre
            opvalue = self.config.key_word_dict.get(opkey)

            if (
                (not ctrl_panel)
                and (not self.isplaying(did))
                and self.active_cmd
                and (opvalue not in self.active_cmd)
                and (opkey not in self.active_cmd)
            ):
                self.log.info(f"不在激活命令中 {opvalue}")
                continue

            self.log.info(f"匹配到指令. opkey:{opkey} opvalue:{opvalue} oparg:{oparg}")
            # 自定義口令
            if opvalue.startswith("exec#"):
                code = opvalue.split("#", 1)[1]
                return ("exec", code)
            return (opvalue, oparg)
        self.log.info(f"未匹配到指令 {query} {ctrl_panel}")
        return (None, None)

    def find_real_music_name(self, name, n):
        if not self.config.enable_fuzzy_match:
            self.log.debug("沒開啟模糊匹配")
            return []

        all_music_list = list(self.all_music.keys())
        real_names = find_best_match(
            name,
            all_music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=n,
            extra_search_index=self._extra_index_search,
        )
        if real_names:
            if n > 1 and name not in real_names:
                # 模糊匹配模式，擴大範圍再找，最後保留隨機 n 個
                real_names = find_best_match(
                    name,
                    all_music_list,
                    cutoff=self.config.fuzzy_match_cutoff,
                    n=n * 2,
                    extra_search_index=self._extra_index_search,
                )
                random.shuffle(real_names)
                real_names = real_names[:n]
            elif name in real_names:
                # 可以精確匹配，限制只返回一個（保證網頁端播放可用）
                real_names = [name]
            self.log.info(f"根據【{name}】找到歌曲【{real_names}】")
            return real_names
        self.log.info(f"沒找到歌曲【{name}】")
        return []

    def did_exist(self, did):
        return did in self.devices

    # 播放一個 url
    async def play_url(self, did="", arg1="", **kwargs):
        self.log.info(f"手動播放鏈接：{arg1}")
        url = arg1
        return await self.devices[did].group_player_play(url)

    # 設置為單曲循環
    async def set_play_type_one(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_ONE)

    # 設置為全部循環
    async def set_play_type_all(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_ALL)

    # 設置為隨機播放
    async def set_play_type_rnd(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_RND)

    # 設置為單曲播放
    async def set_play_type_sin(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_SIN)

    # 設置為順序播放
    async def set_play_type_seq(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_SEQ)

    async def set_play_type(self, did="", play_type=PLAY_TYPE_RND, dotts=True):
        await self.devices[did].set_play_type(play_type, dotts)

    # 設置為刷新列表
    async def gen_music_list(self, **kwargs):
        self._gen_all_music_list()
        self.log.info("gen_music_list ok")

    # 更新網絡歌單
    async def refresh_web_music_list(self, **kwargs):
        url = self.config.music_list_url
        if url:
            self.log.debug(f"refresh_web_music_list begin url:{url}")
            content = await downloadfile(url)
            self.config.music_list_json = content
            # 配置文檔落地
            self.save_cur_config()
            self.log.debug(f"refresh_web_music_list url:{url} content:{content}")
        self.log.info(f"refresh_web_music_list ok {url}")

    # 刪除歌曲
    async def cmd_del_music(self, did="", arg1="", **kwargs):
        if not self.config.enable_cmd_del_music:
            await self.do_tts(did, "語音刪除歌曲功能未開啟")
            return
        self.log.info(f"cmd_del_music {arg1}")
        name = arg1
        if len(name) == 0:
            name = self.playingmusic(did)
        await self.del_music(name)

    async def del_music(self, name):
        filename = self.get_filename(name)
        if filename == "":
            self.log.info(f"${name} not exist")
            return
        try:
            os.remove(filename)
            self.log.info(f"del ${filename} success")
        except OSError:
            self.log.error(f"del ${filename} failed")
        # TODO: 这里可以优化性能
        self._gen_all_music_list()

    # ===========================MusicFree插件函數================================

    # 在線獲取歌曲列表
    async def get_music_list_online(
        self, plugin="all", keyword="", page=1, limit=20, **kwargs
    ):
        self.log.info("在線獲取歌曲列表!")
        """
        在線獲取歌曲列表

        Args:
            plugin: 插件名稱，"OpenAPI"表示 通過開放接口獲取，其他為插件在線搜索
            keyword: 搜索關鍵詞
            page: 頁碼
            limit: 每頁數量
            **kwargs: 其他參數
        Returns:
            dict: 搜索結果
        """
        openapi_info = self.js_plugin_manager.get_openapi_info()
        if (
            openapi_info.get("enabled", False)
            and openapi_info.get("search_url", "") != ""
        ):
            # 開放接口獲取
            return await self.js_plugin_manager.openapi_search(
                openapi_info.get("search_url"), keyword
            )
        else:
            if not self.js_plugin_manager:
                return {"success": False, "error": "JS Plugin Manager not available"}
            # 插件在線搜索
            return await self.get_music_list_mf(plugin, keyword, page, limit)

    @staticmethod
    async def get_real_url_of_openapi(url: str, timeout: int = 10) -> dict:
        """
        通過服務端代理獲取開放接口真實的音樂播放URL，避免CORS問題
        Args:
            url (str): 原始音樂URL
            timeout (int): 請求超時時間(秒)

        Returns:
            dict: 包含success、realUrl、statusCode等信息的字典
        """
        from urllib.parse import urlparse

        import aiohttp

        # 內部輔助函數：檢查主機解析到的IP是否安全，防止訪問內網/本地地址
        def _is_safe_hostname(parsed) -> bool:
            hostname = parsed.hostname
            if not hostname:
                return False
            try:
                # 解析主機名對應的所有地址
                addrinfo_list = socket.getaddrinfo(hostname, None)
            except Exception:
                return False
            for family, _, _, _, sockaddr in addrinfo_list:
                ip_str = (
                    sockaddr[0] if family in (socket.AF_INET, socket.AF_INET6) else None
                )
                if not ip_str:
                    continue
                try:
                    ip_obj = ipaddress.ip_address(ip_str)
                except ValueError:
                    return False
                # 拒绝内网、回环、链路本地、多播和保留地址
                if (
                    ip_obj.is_private
                    or ip_obj.is_loopback
                    or ip_obj.is_link_local
                    or ip_obj.is_multicast
                    or ip_obj.is_reserved
                ):
                    return False
            return True

        try:
            # 驗證URL格式
            parsed_url = urlparse(url)
            if not parsed_url.scheme or not parsed_url.netloc:
                return {"success": False, "url": url, "error": "Invalid URL format"}
            # 僅允許 http/https
            if parsed_url.scheme not in ("http", "https"):
                return {
                    "success": False,
                    "url": url,
                    "error": "Unsupported URL scheme",
                }
            # 檢查主機是否安全，防止SSRF到內網
            if not _is_safe_hostname(parsed_url):
                return {
                    "success": False,
                    "url": url,
                    "error": "Unsafe target host",
                }

            # 創建aiohttp客戶端會話
            async with aiohttp.ClientSession() as session:
                # 發送HEAD請求跟隨重定向
                async with session.head(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    # 獲取最終重定向後的URL
                    final_url = str(response.url)

                    return {
                        "success": True,
                        "url": final_url,
                        "statusCode": response.status,
                    }
        except Exception as e:
            return {"success": False, "url": url, "error": f"Error occurred: {str(e)}"}

    # 調用MusicFree插件獲取歌曲列表
    async def get_music_list_mf(
        self, plugin="all", keyword="", page=1, limit=20, **kwargs
    ):
        self.log.info("通過MusicFree插件搜索音樂列表!")
        """
        通過MusicFree插件搜索音樂列表

        Args:
            plugin: 插件名稱，"all"表示所有插件
            keyword: 搜索關鍵詞
            page: 頁碼
            limit: 每頁數量
            **kwargs: 其他參數

        Returns:
            dict: 搜索結果
        """
        # 檢查JS插件管理器是否可用
        if not self.js_plugin_manager:
            return {"success": False, "error": "JS插件管理器不可用"}
        # 如果關鍵詞包含 '-'，則提取歌手名、歌名
        if "-" in keyword:
            parts = keyword.split("-")
            keyword = parts[0]
            artist = parts[1]
        else:
            artist = ""
        try:
            if plugin == "all":
                # 搜索所有啟用的插件
                return await self._search_all_plugins(keyword, artist, page, limit)
            else:
                # 搜索指定插件
                return await self._search_specific_plugin(
                    plugin, keyword, artist, page, limit
                )
        except Exception as e:
            self.log.error(f"搜索音樂時發生錯誤: {e}")
            return {"success": False, "error": str(e)}

    async def _search_all_plugins(self, keyword, artist, page, limit):
        """搜索所有啟用的插件"""
        enabled_plugins = self.js_plugin_manager.get_enabled_plugins()
        if not enabled_plugins:
            return {"success": False, "error": "沒有可用的接口和插件，請先進行配置！"}

        results = []
        sources = {}

        # 計算每個插件的限制數量
        plugin_count = len(enabled_plugins)
        item_limit = max(1, limit // plugin_count) if plugin_count > 0 else limit

        # 並行搜索所有插件
        search_tasks = [
            self._search_plugin_task(plugin_name, keyword, page, item_limit)
            for plugin_name in enabled_plugins
        ]

        plugin_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # 處理搜索結果
        for i, result in enumerate(plugin_results):
            plugin_name = list(enabled_plugins)[i]

            # 檢查是否為異常對象
            if isinstance(result, Exception):
                self.log.error(f"插件 {plugin_name} 搜索失敗: {result}")
                continue

            # 檢查是否為有效的搜索結果（修改這裡的判斷邏輯）
            if result and isinstance(result, dict):
                # 檢查是否有錯誤信息
                if "error" in result:
                    self.log.error(
                        f"插件 {plugin_name} 搜索失敗: {result.get('error', '未知錯誤')}"
                    )
                    continue

                # 處理成功的搜索結果
                data_list = result.get("data", [])
                if data_list:
                    results.extend(data_list)
                    sources[plugin_name] = len(data_list)
                # 如果沒有data字段但有其他數據，也認為是成功的結果
                elif result:  # 非空字典
                    results.append(result)
                    sources[plugin_name] = 1

        # 統一排序並提取前limit條數據
        if results:
            unified_result = {"data": results}
            optimized_result = self.js_plugin_manager.optimize_search_results(
                unified_result,
                search_keyword=keyword,
                limit=limit,
                search_artist=artist,
            )
            results = optimized_result.get("data", [])

        return {
            "success": True,
            "data": results,
            "total": len(results),
            "sources": sources,
            "page": page,
            "limit": limit,
        }

    async def _search_specific_plugin(self, plugin, keyword, artist, page, limit):
        """搜索指定插件"""
        try:
            results = self.js_plugin_manager.search(plugin, keyword, page, limit)

            # 額外檢查 resources 字段
            data_list = results.get("data", [])
            if data_list:
                # 優化搜索結果排序
                results = self.js_plugin_manager.optimize_search_results(
                    results, search_keyword=keyword, limit=limit, search_artist=artist
                )

            return {
                "success": True,
                "data": results.get("data", []),
                "total": results.get("total", 0),
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            self.log.error(f"插件 {plugin} 搜索失敗: {e}")
            return {"success": False, "error": str(e)}

    async def _search_plugin_task(self, plugin_name, keyword, page, limit):
        """單個插件搜索任務"""
        try:
            return self.js_plugin_manager.search(plugin_name, keyword, page, limit)
        except Exception as e:
            # 直接拋出異常，讓 asyncio.gather 處理
            raise e

    # 調用MusicFree插件獲取真實播放url
    async def get_media_source_url(self, music_item, quality: str = "standard"):
        """獲取音樂項的媒體源URL
        Args:
            music_item : MusicFree插件定義的 IMusicItem
            quality: 音質參數
        Returns:
            dict: 包含成功狀態和URL信息的字典
        """
        # kwargs可追加
        kwargs = {"quality": quality}
        return await self.__call_plugin_method(
            plugin_name=music_item.get("platform"),
            method_name="get_media_source",
            music_item=music_item,
            result_key="url",
            required_field="url",
            **kwargs,
        )

    # 調用MusicFree插件獲取歌詞
    async def get_media_lyric(self, music_item):
        """獲取音樂項的歌詞 Lyric
        Args:
            music_item : MusicFree插件定義的 IMusicItem
        Returns:
            dict: 包含成功狀態和URL信息的字典
        """
        return await self.__call_plugin_method(
            plugin_name=music_item.get("platform"),
            method_name="get_lyric",
            music_item=music_item,
            result_key="rawLrc",
            required_field="rawLrc",
        )

    # 調用在線搜索歌曲，並優化返回
    async def search_music_online(self, search_key, name):
        """調用MusicFree插件搜索歌曲

        Args:
            search_key (str): 搜索關鍵詞
            name (str): 歌曲名
        Returns:
            dict: 包含成功狀態和URL信息的字典
        """

        try:
            # 獲取歌曲列表
            result = await self.get_music_list_online(keyword=name, limit=10)
            self.log.info(f"在線搜索歌曲列表: {result}")

            if result.get("success") and result.get("total") > 0:
                # 打印輸出 result.data
                self.log.info(f"歌曲列表: {result.get('data')}")
                # 根據搜素關鍵字，智能搜索出最符合的一條music_item
                music_item = await self._search_top_one(
                    result.get("data"), search_key, name
                )
                # 驗證 music_item 是否為字典類型
                if not isinstance(music_item, dict):
                    self.log.error(
                        f"music_item should be a dict, but got {type(music_item)}: {music_item}"
                    )
                    return {"success": False, "error": "Invalid music item format"}

                # 如果是OpenAPI，則需要轉換播放鏈接
                openapi_info = self.js_plugin_manager.get_openapi_info()
                if openapi_info.get("enabled", False):
                    return await self.get_real_url_of_openapi(music_item.get("url"))
                else:
                    media_source = await self.get_media_source_url(music_item)
                    if media_source.get("success"):
                        return {"success": True, "url": media_source.get("url")}
                    else:
                        return {"success": False, "error": media_source.get("error")}
            else:
                return {"success": False, "error": "未找到歌曲"}

        except Exception as e:
            # 記錄錯誤日誌
            self.log.error(f"searchKey {search_key} get media source failed: {e}")
            return {"success": False, "error": str(e)}

    async def _search_top_one(self, music_items, search_key, name):
        """智能搜索出最符合的一條music_item"""
        try:
            # 如果沒有音樂項目，返回None
            if not music_items:
                return None

            self.log.info(f"搜索關鍵字: {search_key}；歌名：{name}")
            # 如果只有一個項目，直接返回
            if len(music_items) == 1:
                return music_items[0]

            # 計算每個項目的匹配分數
            def calculate_match_score(item):
                """計算匹配分數"""
                title = item.get("title", "").lower() if item.get("title") else ""
                artist = item.get("artist", "").lower() if item.get("artist") else ""
                keyword = search_key.lower()

                if not keyword:
                    return 0

                score = 0
                # 歌曲名匹配權重
                if keyword in title:
                    # 完全匹配得最高分
                    if title == keyword:
                        score += 90
                    # 開頭匹配
                    elif title.startswith(keyword):
                        score += 70
                    # 結尾匹配
                    elif title.endswith(keyword):
                        score += 50
                    # 包含匹配
                    else:
                        score += 30
                # 部分字符匹配
                elif any(char in title for char in keyword.split()):
                    score += 10
                # 藝術家名匹配權重
                if keyword in artist:
                    # 完全匹配
                    if artist == keyword:
                        score += 9
                    # 開頭匹配
                    elif artist.startswith(keyword):
                        score += 7
                    # 結尾匹配
                    elif artist.endswith(keyword):
                        score += 5
                    # 包含匹配
                    else:
                        score += 3
                # 部分字符匹配
                elif any(char in artist for char in keyword.split()):
                    score += 1
                return score

            # 按匹配分數排序，返回分數最高的項目
            sorted_items = sorted(music_items, key=calculate_match_score, reverse=True)
            return sorted_items[0]

        except Exception as e:
            self.log.error(f"_search_top_one error: {e}")
            # 出現異常時返回第一個項目
            return music_items[0] if music_items else None

    # ===========================================================

    def _find_real_music_list_name(self, list_name):
        if not self.config.enable_fuzzy_match:
            self.log.debug("沒開啟模糊匹配")
            return list_name

        # 模糊搜一個播放列表（只需要一個，不需要 extra index）
        real_name = find_best_match(
            list_name,
            self.music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=1,
        )[0]
        if real_name:
            self.log.info(f"根據【{list_name}】找到播放列表【{real_name}】")
            list_name = real_name
        else:
            self.log.info(f"沒找到播放列表【{list_name}】")
        return list_name

    # 播放一個播放列表
    async def play_music_list(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        list_name = parts[0]

        music_name = ""
        if len(parts) > 1:
            music_name = parts[1]
        return await self.do_play_music_list(did, list_name, music_name)

    async def do_play_music_list(self, did, list_name, music_name=""):
        # 查找並獲取真實的音樂列表名稱
        list_name = self._find_real_music_list_name(list_name)
        # 檢查音樂列表是否存在，如果不存在則進行語音提示並返回
        if list_name not in self.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return

        # 調用設備播放音樂列表的方法
        await self.devices[did].play_music_list(list_name, music_name)

    # 播放一個播放列表裡第幾個
    async def play_music_list_index(self, did="", arg1="", **kwargs):
        patternarg = r"^([零一二三四五六七八九十百千万亿]+)个(.*)"
        # 匹配參數
        matcharg = re.match(patternarg, arg1)
        if not matcharg:
            return await self.play_music_list(did, arg1)

        chinese_index = matcharg.groups()[0]
        list_name = matcharg.groups()[1]
        list_name = self._find_real_music_list_name(list_name)
        if list_name not in self.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return

        index = chinese_to_number(chinese_index)
        play_list = self.music_list[list_name]
        if 0 <= index - 1 < len(play_list):
            music_name = play_list[index - 1]
            self.log.info(f"即將播放 ${arg1} 裡的第 ${index} 個: ${music_name}")
            await self.devices[did].play_music_list(list_name, music_name)
            return
        await self.do_tts(did, f"播放列表{list_name}中找不到第${index}個")

    # 播放
    async def play(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        search_key = parts[0]
        name = parts[1] if len(parts) > 1 else search_key
        if not name:
            name = search_key

        # 語音播放會根據歌曲匹配更新當前播放列表
        return await self.do_play(
            did, name, search_key, exact=True, update_cur_list=True
        )

    # 搜索播放：會產生臨時播放列表
    async def search_play(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        search_key = parts[0]
        name = parts[1] if len(parts) > 1 else search_key
        if not name:
            name = search_key

        # 語音搜索播放會更新當前播放列表為臨時播放列表
        return await self.do_play(
            did, name, search_key, exact=False, update_cur_list=False
        )

    # 在線播放：在線搜索、播放
    async def online_play(self, did="", arg1="", **kwargs):
        # 先推送默認【搜索中】音頻，搜索到播放url後推送給小愛
        config = self.config
        if config and hasattr(config, "hostname") and hasattr(config, "public_port"):
            proxy_base = f"{config.hostname}:{config.public_port}"
        else:
            proxy_base = "http://192.168.31.241:8090"
        search_audio = proxy_base + "/static/search.mp3"
        proxy_base + "/static/silence.mp3"
        await self.play_url(self.get_cur_did(), search_audio)

        # TODO 添加一個定時器，4秒後觸發

        # 獲取搜索關鍵詞
        parts = arg1.split("|")
        search_key = parts[0]
        name = parts[1] if len(parts) > 1 else search_key
        if not name:
            name = search_key
        self.log.info(f"搜索關鍵字{search_key},搜索歌名{name}")
        result = await self.search_music_online(search_key, name)
        # 搜索成功，則直接推送url播放
        if result.get("success", False):
            url = result.get("url", "")
            # 播放歌曲
            await self.devices[did].play_music(name, true_url=url)

    # 後台搜索播放
    async def do_play(
        self, did, name, search_key="", exact=False, update_cur_list=False
    ):
        return await self.devices[did].play(name, search_key, exact, update_cur_list)

    # 本地播放
    async def playlocal(self, did="", arg1="", **kwargs):
        return await self.devices[did].playlocal(arg1, update_cur_list=True)

    # 本地搜索播放
    async def search_playlocal(self, did="", arg1="", **kwargs):
        return await self.devices[did].playlocal(
            arg1, exact=False, update_cur_list=False
        )

    async def play_next(self, did="", **kwargs):
        return await self.devices[did].play_next()

    async def play_prev(self, did="", **kwargs):
        return await self.devices[did].play_prev()

    # 停止
    async def stop(self, did="", arg1="", **kwargs):
        return await self.devices[did].stop(arg1=arg1)

    # 定時關機
    async def stop_after_minute(self, did="", arg1=0, **kwargs):
        try:
            # 嘗試阿拉伯數字轉換中文數字
            minute = int(arg1)
        except (KeyError, ValueError):
            # 如果阿拉伯數字轉換失敗，嘗試中文數字
            minute = chinese_to_number(str(arg1))
        return await self.devices[did].stop_after_minute(minute)

    # 添加歌曲到收藏列表
    async def add_to_favorites(self, did="", arg1="", **kwargs):
        name = arg1 if arg1 else self.playingmusic(did)
        self.log.info(f"add_to_favorites {name}")
        if not name:
            self.log.warning("當前沒有在播放歌曲，添加歌曲到收藏列表失敗")
            return

        self.play_list_add_music("收藏", [name])

    # 從收藏列表中移除
    async def del_from_favorites(self, did="", arg1="", **kwargs):
        name = arg1 if arg1 else self.playingmusic(did)
        self.log.info(f"del_from_favorites {name}")
        if not name:
            self.log.warning("當前沒有在播放歌曲，從收藏列表中移除失敗")
            return

        self.play_list_del_music("收藏", [name])

    # 更新每個設備的歌單
    def update_all_playlist(self):
        for device in self.devices.values():
            device.update_playlist()

    def get_custom_play_list(self):
        if self.custom_play_list is None:
            self.custom_play_list = {}
            if self.config.custom_play_list_json:
                self.custom_play_list = json.loads(self.config.custom_play_list_json)
        return self.custom_play_list

    def save_custom_play_list(self):
        custom_play_list = self.get_custom_play_list()
        self.refresh_custom_play_list()
        self.config.custom_play_list_json = json.dumps(
            custom_play_list, ensure_ascii=False
        )
        self.save_cur_config()

    # 新增歌單
    def play_list_add(self, name):
        custom_play_list = self.get_custom_play_list()
        if name in custom_play_list:
            return False
        custom_play_list[name] = []
        self.save_custom_play_list()
        return True

    # 移除歌單
    def play_list_del(self, name):
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False
        custom_play_list.pop(name)
        self.save_custom_play_list()
        return True

    # 修改歌單名字
    def play_list_update_name(self, oldname, newname):
        custom_play_list = self.get_custom_play_list()
        if oldname not in custom_play_list:
            self.log.info(f"舊歌單名字不存在 {oldname}")
            return False
        if newname in custom_play_list:
            self.log.info(f"新歌單名字已存在 {newname}")
            return False
        play_list = custom_play_list[oldname]
        custom_play_list.pop(oldname)
        custom_play_list[newname] = play_list
        self.save_custom_play_list()
        return True

    # 獲取所有自定義歌單
    def get_play_list_names(self):
        custom_play_list = self.get_custom_play_list()
        return list(custom_play_list.keys())

    # 獲取歌單中所有歌曲
    def play_list_musics(self, name):
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return "歌單不存在", []
        play_list = custom_play_list[name]
        return "OK", play_list

    # 歌單更新歌曲
    def play_list_update_music(self, name, music_list):
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌單不存在則新建
            if not self.play_list_add(name):
                return False
        play_list = []
        for music_name in music_list:
            if (music_name in self.all_music) and (music_name not in play_list):
                play_list.append(music_name)
        # 直接覆蓋
        custom_play_list[name] = play_list
        self.save_custom_play_list()
        return True

    # 歌單新增歌曲
    def play_list_add_music(self, name, music_list):
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌單不存在則新建
            if not self.play_list_add(name):
                return False
        play_list = custom_play_list[name]
        for music_name in music_list:
            if (music_name in self.all_music) and (music_name not in play_list):
                play_list.append(music_name)
        self.save_custom_play_list()
        return True

    # 歌單移除歌曲
    def play_list_del_music(self, name, music_list):
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False
        play_list = custom_play_list[name]
        for music_name in music_list:
            if music_name in play_list:
                play_list.remove(music_name)
        self.save_custom_play_list()
        return True

    # 獲取音量
    async def get_volume(self, did="", **kwargs):
        return await self.devices[did].get_volume()

    # 3thdplay.html 的音量設置消息發送 需要配置文檔加入自定義指令
    #  "user_key_word_dict": {
    # "音量": "set_myvolume",
    # "繼續": "stop",
    # "大點音": "exec#setmyvolume(\"up\")",
    # "小點音": "exec#setmyvolume(\"down\")",

    async def set_myvolume(self, did="", arg1=0, **kwargs):
        if did not in self.devices:
            self.log.info(f"設備 did:{did} 不存在, 不能設置音量")
            return
        if arg1 == "up":
            await thdplay("up", "", self.thdtarget)

        elif arg1 == "down":
            await thdplay("down", "", self.thdtarget)
        else:
            volume = chinese_to_number(arg1)
            await thdplay("volume", str(volume), self.thdtarget)

    # 設置音量
    async def set_volume(self, did="", arg1=0, **kwargs):
        if did not in self.devices:
            self.log.info(f"設備 did:{did} 不存在, 不能設置音量")
            return
        volume = int(arg1)
        await thdplay("volume", str(volume), self.thdtarget)
        return await self.devices[did].set_volume(volume)

    # 搜索音樂
    def searchmusic(self, name):
        all_music_list = list(self.all_music.keys())
        search_list = fuzzyfinder(name, all_music_list, self._extra_index_search)
        self.log.debug(f"searchmusic. name:{name} search_list:{search_list}")
        return search_list

    # 獲取播放列表
    def get_music_list(self):
        return self.music_list

    # 獲取當前的播放列表
    def get_cur_play_list(self, did):
        return self.devices[did].get_cur_play_list()

    # 正在播放中的音樂
    def playingmusic(self, did):
        cur_music = self.devices[did].get_cur_music()
        self.log.debug(f"playingmusic. cur_music:{cur_music}")
        return cur_music

    def get_offset_duration(self, did):
        return self.devices[did].get_offset_duration()

    # 當前是否正在播放歌曲
    def isplaying(self, did):
        return self.devices[did].isplaying()

    # 獲取當前配置
    def getconfig(self):
        return self.config

    def try_init_setting(self):
        try:
            filename = self.config.getsettingfile()
            with open(filename, encoding="utf-8") as f:
                data = json.loads(f.read())
                self.update_config_from_setting(data)
        except FileNotFoundError:
            self.log.info(f"The file {filename} does not exist.")
        except json.JSONDecodeError:
            self.log.warning(f"The file {filename} contains invalid JSON.")
        except Exception as e:
            self.log.exception(f"Execption {e}")

    # 保存配置並重新啟動
    async def saveconfig(self, data):
        # 更新配置
        self.update_config_from_setting(data)
        # 配置文檔落地
        self.save_cur_config()
        # 重新初始化
        await self.reinit()

    # 配置文檔落地
    def do_saveconfig(self, data):
        filename = self.config.getsettingfile()
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 把當前配置落地
    def save_cur_config(self):
        for did in self.config.devices.keys():
            deviceobj = self.devices.get(did)
            if deviceobj is not None:
                self.config.devices[did] = deviceobj.device
        data = asdict(self.config)
        self.do_saveconfig(data)
        self.log.info("save_cur_config ok")

    def update_config_from_setting(self, data):
        # 保存之前的 enable_file_watch 配置
        pre_efw = self.config.enable_file_watch
        # 自動賦值相同字段的配置
        self.config.update_config(data)

        self.init_config()
        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"update_config_from_setting ok. data:{debug_config}")

        joined_keywords = "/".join(self.config.key_match_order)
        self.log.info(f"語音控制已啟動, 用【{joined_keywords}】開頭來控制")
        self.log.debug(f"key_word_dict: {self.config.key_word_dict}")

        # 檢查 enable_file_watch 配置是否發生變化
        now_efw = self.config.enable_file_watch
        if pre_efw != now_efw:
            self.log.info("配置更新：{}目錄監控".format("開啟" if now_efw else "關閉"))
            if now_efw:
                self.start_file_watch()
            else:
                self.stop_file_watch()

        # 重新加載計劃任務
        self.crontab.reload_config(self)

    # 重新初始化
    async def reinit(self):
        for handler in self.log.handlers:
            handler.close()
        self.setup_logger()
        if self.session:
            await self.init_all_data(self.session)
        self._gen_all_music_list()
        self.update_devices()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"reinit success. data:{debug_config}")

    # 獲取所有設備
    async def getalldevices(self, **kwargs):
        device_list = []
        try:
            device_list = await self.mina_service.device_list()
        except Exception as e:
            self.log.warning(f"Execption {e}")
            # 重新初始化
            await self.xiaomusic.reinit()
        return device_list

    async def debug_play_by_music_url(self, arg1=None):
        if arg1 is None:
            arg1 = {}
        data = arg1
        device_id = self.get_one_device_id()
        self.log.info(f"debug_play_by_music_url: {data} {device_id}")
        return await self.mina_service.ubus_request(
            device_id,
            "player_play_music",
            "mediaplayer",
            data,
        )

    async def exec(self, did="", arg1=None, **kwargs):
        self._cur_did = did
        code = arg1 if arg1 else 'code1("hello")'
        await self.plugin_manager.execute_plugin(code)

    # 此接口用於插件中獲取當前設備
    def get_cur_did(self):
        return self._cur_did

    async def do_tts(self, did, value):
        return await self.devices[did].do_tts(value)


class XiaoMusicDevice:
    def __init__(self, xiaomusic: XiaoMusic, device: Device, group_name: str):
        self.group_name = group_name
        self.device = device
        self.config = xiaomusic.config
        self.device_id = device.device_id
        self.log = xiaomusic.log
        self.xiaomusic = xiaomusic
        self.download_path = xiaomusic.download_path
        self.ffmpeg_location = self.config.ffmpeg_location

        self._download_proc = None  # 下載對象
        self._next_timer = None
        self._playing = False
        # 播放進度
        self._start_time = 0
        self._duration = 0
        self._paused_time = 0
        self._play_failed_cnt = 0

        self._play_list = []

        # 關機定時器
        self._stop_timer = None
        self._last_cmd = None
        self.update_playlist()

    @property
    def did(self):
        return self.device.did

    @property
    def hardware(self):
        return self.device.hardware

    def get_cur_music(self):
        return self.device.cur_music

    def get_offset_duration(self):
        duration = self._duration
        if not self.isplaying():
            return 0, duration
        offset = time.time() - self._start_time - self._paused_time
        return offset, duration

    async def play_music(self, name, true_url=None):
        return await self._playmusic(name, true_url=true_url)

    # 初始化播放列表
    def update_playlist(self, reorder=True):
        # 沒有重置 list 且非初始化
        if self.device.cur_playlist == "臨時搜索列表" and len(self._play_list) > 0:
            # 更新總播放列表，為了UI顯示
            self.xiaomusic.music_list["臨時搜索列表"] = copy.copy(self._play_list)
        elif (
            self.device.cur_playlist == "臨時搜索列表" and len(self._play_list) == 0
        ) or (self.device.cur_playlist not in self.xiaomusic.music_list):
            self.device.cur_playlist = "全部"
        else:
            pass  # 指定了已知的播放列表名稱

        list_name = self.device.cur_playlist
        self._play_list = copy.copy(self.xiaomusic.music_list[list_name])

        if reorder:
            if self.device.play_type == PLAY_TYPE_RND:
                random.shuffle(self._play_list)
                self.log.info(
                    f"隨機打亂 {list_name} {list2str(self._play_list, self.config.verbose)}"
                )
            else:
                self._play_list.sort(key=custom_sort_key)
                self.log.info(
                    f"沒打亂 {list_name} {list2str(self._play_list, self.config.verbose)}"
                )
        else:
            self.log.info(
                f"更新 {list_name} {list2str(self._play_list, self.config.verbose)}"
            )

    # 播放歌曲
    async def play(self, name="", search_key="", exact=True, update_cur_list=False):
        self._last_cmd = "play"
        return await self._play(
            name=name,
            search_key=search_key,
            exact=exact,
            update_cur_list=update_cur_list,
        )

    async def _play(self, name="", search_key="", exact=True, update_cur_list=False):
        if search_key == "" and name == "":
            if self.check_play_next():
                await self._play_next()
                return
            else:
                name = self.get_cur_music()
        self.log.info(f"play. search_key:{search_key} name:{name}: exact:{exact}")

        # 本地歌曲不存在時下載
        if exact:
            names = self.xiaomusic.find_real_music_name(name, n=1)
        else:
            names = self.xiaomusic.find_real_music_name(
                name, n=self.config.search_music_count
            )
        self.log.info(f"play. names:{names} {len(names)}")
        if len(names) > 0:
            if not exact:
                if len(names) > 1:  # 大於一首歌才更新
                    self._play_list = names
                    self.device.cur_playlist = "臨時搜索列表"
                    self.update_playlist()
                else:  # 只有一首歌，append
                    self._play_list = self._play_list + names
                    self.device.cur_playlist = "臨時搜索列表"
                    self.update_playlist(reorder=False)
            name = names[0]
            if update_cur_list and (name not in self._play_list):
                # 根據當前歌曲匹配歌曲列表
                self.device.cur_playlist = self.find_cur_playlist(name)
                self.update_playlist()
            self.log.debug(
                f"當前播放列表為：{list2str(self._play_list, self.config.verbose)}"
            )
            # 本地存在歌曲，直接播放
            await self._playmusic(name)
        elif not self.xiaomusic.is_music_exist(name):
            self.log.inf(f"本地不存在歌曲{name}")
            if self.config.disable_download:
                await self.do_tts(f"本地不存在歌曲{name}")
                return
            else:
                # 如果插件播放失敗，則執行下載流程
                await self.download(search_key, name)
                # 把文件插入到播放列表裡
                await self.add_download_music(name)
                await self._playmusic(name)

    # 下一首
    async def play_next(self):
        return await self._play_next()

    async def _play_next(self):
        self.log.info("開始播放下一首")
        name = self.get_cur_music()
        if (
            self.device.play_type == PLAY_TYPE_ALL
            or self.device.play_type == PLAY_TYPE_RND
            or self.device.play_type == PLAY_TYPE_SEQ
            or name == ""
            or (
                (name not in self._play_list) and self.device.play_type != PLAY_TYPE_ONE
            )
        ):
            name = self.get_next_music()
        self.log.info(f"_play_next. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            # await self.do_tts("本地沒有歌曲")
            return
        await self._play(name, exact=True)

    # 上一首
    async def play_prev(self):
        return await self._play_prev()

    async def _play_prev(self):
        self.log.info("開始播放上一首")
        name = self.get_cur_music()
        if (
            self.device.play_type == PLAY_TYPE_ALL
            or self.device.play_type == PLAY_TYPE_RND
            or name == ""
            or (name not in self._play_list)
        ):
            name = self.get_prev_music()
        self.log.info(f"_play_prev. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            await self.do_tts("本地沒有歌曲")
            return
        await self._play(name, exact=True)

    # 播放本地歌曲
    async def playlocal(self, name, exact=True, update_cur_list=False):
        self._last_cmd = "playlocal"
        if name == "":
            if self.check_play_next():
                await self._play_next()
                return
            else:
                name = self.get_cur_music()

        self.log.info(f"playlocal. name:{name}")

        # 本地歌曲不存在時下載
        if exact:
            names = self.xiaomusic.find_real_music_name(name, n=1)
        else:
            names = self.xiaomusic.find_real_music_name(
                name, n=self.config.search_music_count
            )
        if len(names) > 0:
            if not exact:
                if len(names) > 1:  # 大於一首歌才更新
                    self._play_list = names
                    self.device.cur_playlist = "臨時搜索列表"
                    self.update_playlist()
                else:  # 只有一首歌，append
                    self._play_list = self._play_list + names
                    self.device.cur_playlist = "臨時搜索列表"
                    self.update_playlist(reorder=False)
            name = names[0]
            if update_cur_list:
                # 根據當前歌曲匹配歌曲列表
                self.device.cur_playlist = self.find_cur_playlist(name)
                self.update_playlist()
            self.log.debug(
                f"當前播放列表為：{list2str(self._play_list, self.config.verbose)}"
            )
        elif not self.xiaomusic.is_music_exist(name):
            self.log.info(f"本地不存在歌曲{name}")
            await self.do_tts(f"本地不存在歌曲{name}")
            return
        await self._playmusic(name)

    async def _playmusic(self, name, true_url=None):
        self.log.info(f"_playmusic. name:{name} true_url:{true_url}")
        # 取消組內所有的下一首歌曲的定時器
        self.cancel_group_next_timer()

        self._playing = True
        self.device.cur_music = name
        self.device.playlist2music[self.device.cur_playlist] = name

        self.log.info(f"cur_music {self.get_cur_music()}")
        sec, url = await self.xiaomusic.get_music_sec_url(name, true_url)
        await self.group_force_stop_xiaoai()
        self.log.info(f"播放 {url}")
        # 有3方設備打開 /static/3thplay.html 通過socketio連接返回true 忽略小愛音箱的播放
        online = await thdplay("play", url, self.xiaomusic.thdtarget)
        self.log.info(f"IS online {online}")

        if not online:
            results = await self.group_player_play(url, name)
            if all(ele is None for ele in results):
                self.log.info(f"播放 {name} 失敗. 失敗次數: {self._play_failed_cnt}")
                await asyncio.sleep(1)
                if (
                    self.isplaying()
                    and self._last_cmd != "stop"
                    and self._play_failed_cnt < 10
                ):
                    self._play_failed_cnt = self._play_failed_cnt + 1
                    await self._play_next()
                return
        # 重置播放失敗次數
        self._play_failed_cnt = 0

        self.log.info(f"【{name}】已經開始播放了")
        await self.xiaomusic.analytics.send_play_event(name, sec, self.hardware)

        # 設置下一首歌曲的播放定時器
        if sec <= 1:
            self.log.info(f"【{name}】不會設置下一首歌的定時器")
            return
        sec = sec + self.config.delay_sec
        self._start_time = time.time()
        self._duration = sec
        self._paused_time = 0
        await self.set_next_music_timeout(sec)
        self.xiaomusic.save_cur_config()

    async def do_tts(self, value):
        self.log.info(f"try do_tts value:{value}")
        if not value:
            self.log.info("do_tts no value")
            return

        # await self.group_force_stop_xiaoai()
        await self.text_to_speech(value)

        # 最大等8秒
        sec = min(8, int(len(value) / 3))
        await asyncio.sleep(sec)
        self.log.info(f"do_tts ok. cur_music:{self.get_cur_music()}")
        await self.check_replay()

    async def force_stop_xiaoai(self, device_id):
        try:
            ret = await self.xiaomusic.mina_service.player_pause(device_id)
            self.log.info(
                f"force_stop_xiaoai player_pause device_id:{device_id} ret:{ret}"
            )
            await self.stop_if_xiaoai_is_playing(device_id)
        except Exception as e:
            self.log.warning(f"Execption {e}")

    async def get_if_xiaoai_is_playing(self):
        playing_info = await self.xiaomusic.mina_service.player_get_status(
            self.device_id
        )
        self.log.info(playing_info)
        # WTF xiaomi api
        is_playing = (
            json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1)
            == 1
        )
        return is_playing

    async def stop_if_xiaoai_is_playing(self, device_id):
        is_playing = await self.get_if_xiaoai_is_playing()
        if is_playing or self.config.enable_force_stop:
            # stop it
            ret = await self.xiaomusic.mina_service.player_stop(device_id)
            self.log.info(
                f"stop_if_xiaoai_is_playing player_stop device_id:{device_id} enable_force_stop:{self.config.enable_force_stop} ret:{ret}"
            )

    # 是否在下載中
    def isdownloading(self):
        if not self._download_proc:
            return False

        if self._download_proc.returncode is not None:
            self.log.info(
                f"Process exited with returncode:{self._download_proc.returncode}"
            )
            return False

        self.log.info("Download Process is still running.")
        return True

    # 下載歌曲
    async def download(self, search_key, name):
        if self._download_proc:
            try:
                self._download_proc.kill()
            except ProcessLookupError:
                pass

        sbp_args = (
            "yt-dlp",
            f"{self.config.search_prefix}{search_key}",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--paths",
            self.download_path,
            "-o",
            f"{name}.mp3",
            "--ffmpeg-location",
            f"{self.ffmpeg_location}",
            "--no-playlist",
        )

        if self.config.proxy:
            sbp_args += ("--proxy", f"{self.config.proxy}")

        if self.config.enable_yt_dlp_cookies:
            sbp_args += ("--cookies", f"{self.config.yt_dlp_cookies_path}")

        if self.config.loudnorm:
            sbp_args += ("--postprocessor-args", f"-af {self.config.loudnorm}")

        cmd = " ".join(sbp_args)
        self.log.info(f"download cmd: {cmd}")
        self._download_proc = await asyncio.create_subprocess_exec(*sbp_args)
        await self.do_tts(f"正在下載歌曲{search_key}")
        self.log.info(f"正在下載中 {search_key} {name}")
        await self._download_proc.wait()
        # 下載完成後，修改文件權限
        file_path = os.path.join(self.download_path, f"{name}.mp3")
        chmodfile(file_path)

    # 繼續播放被打斷的歌曲
    async def check_replay(self):
        if self.isplaying() and not self.isdownloading():
            if not self.config.continue_play:
                # 重新播放歌曲
                self.log.info("現在重新播放歌曲")
                await self._play()
            else:
                self.log.info(
                    f"繼續播放歌曲. self.config.continue_play:{self.config.continue_play}"
                )
        else:
            self.log.info(
                f"不會繼續播放歌曲. isplaying:{self.isplaying()} isdownloading:{self.isdownloading()}"
            )

    # 當前是否正在播放歌曲
    def isplaying(self):
        return self._playing

    # 把下載的音樂加入播放列表
    async def add_download_music(self, name):
        filepath = os.path.join(self.download_path, f"{name}.mp3")
        self.xiaomusic.all_music[name] = filepath
        # 應該很快，阻塞運行
        await self.xiaomusic._gen_all_music_tag({name: filepath})
        if name not in self._play_list:
            self._play_list.append(name)
            self.log.info(f"add_download_music add_music {name}")
            self.log.debug(self._play_list)

    def get_music(self, direction="next"):
        play_list_len = len(self._play_list)
        if play_list_len == 0:
            self.log.warning("當前播放列表沒有歌曲")
            return ""
        index = 0
        try:
            index = self._play_list.index(self.get_cur_music())
        except ValueError:
            pass

        if play_list_len == 1:
            new_index = index  # 當只有一首歌曲時保持當前索引不變
        else:
            if direction == "next":
                new_index = index + 1
                if (
                    self.device.play_type == PLAY_TYPE_SEQ
                    and new_index >= play_list_len
                ):
                    self.log.info("順序播放結束")
                    return ""
                if new_index >= play_list_len:
                    new_index = 0
            elif direction == "prev":
                new_index = index - 1
                if new_index < 0:
                    new_index = play_list_len - 1
            else:
                self.log.error("無效的方向參數")
                return ""

        name = self._play_list[new_index]
        if not self.xiaomusic.is_music_exist(name):
            self._play_list.pop(new_index)
            self.log.info(f"pop not exist music: {name}")
            return self.get_music(direction)
        return name

    # 獲取下一首
    def get_next_music(self):
        return self.get_music(direction="next")

    # 獲取上一首
    def get_prev_music(self):
        return self.get_music(direction="prev")

    # 判斷是否播放下一首歌曲
    def check_play_next(self):
        # 當前歌曲不在當前播放列表
        if self.get_cur_music() not in self._play_list:
            self.log.info(f"當前歌曲 {self.get_cur_music()} 不在當前播放列表")
            return True

        # 當前沒有在播放的歌曲
        if self.get_cur_music() == "":
            self.log.info("當前沒有在播放的歌曲")
            return True
        else:
            # 當前播放的歌曲不存在了
            if not self.xiaomusic.is_music_exist(self.get_cur_music()):
                self.log.info(f"當前播放的歌曲 {self.get_cur_music()} 不存在了")
                return True
        return False

    async def text_to_speech(self, value):
        try:
            # 有 tts command 優先使用 tts command 說話
            if self.hardware in TTS_COMMAND:
                tts_cmd = TTS_COMMAND[self.hardware]
                self.log.info("Call MiIOService tts.")
                value = value.replace(" ", ",")  # 不能有空格
                await miio_command(
                    self.xiaomusic.miio_service,
                    self.did,
                    f"{tts_cmd} {value}",
                )
            else:
                self.log.debug("Call MiNAService tts.")
                await self.xiaomusic.mina_service.text_to_speech(self.device_id, value)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    # 同一組設備播放
    async def group_player_play(self, url, name=""):
        device_id_list = self.xiaomusic.get_group_device_id_list(self.group_name)
        tasks = [
            self.play_one_url(device_id, url, name) for device_id in device_id_list
        ]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_player_play {url} {device_id_list} {results}")
        return results

    async def play_one_url(self, device_id, url, name):
        ret = None
        try:
            audio_id = await self._get_audio_id(name)
            if self.config.continue_play:
                ret = await self.xiaomusic.mina_service.play_by_music_url(
                    device_id, url, _type=1, audio_id=audio_id
                )
                self.log.info(
                    f"play_one_url continue_play device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            elif self.config.use_music_api or (
                self.hardware in NEED_USE_PLAY_MUSIC_API
            ):
                ret = await self.xiaomusic.mina_service.play_by_music_url(
                    device_id, url, audio_id=audio_id
                )
                self.log.info(
                    f"play_one_url play_by_music_url device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            else:
                ret = await self.xiaomusic.mina_service.play_by_url(device_id, url)
                self.log.info(
                    f"play_one_url play_by_url device_id:{device_id} ret:{ret} url:{url}"
                )
        except Exception as e:
            self.log.exception(f"Execption {e}")
        return ret

    async def _get_audio_id(self, name):
        audio_id = self.config.use_music_audio_id or "1582971365183456177"
        if not (self.config.use_music_api or self.config.continue_play):
            return str(audio_id)
        try:
            params = {
                "query": name,
                "queryType": 1,
                "offset": 0,
                "count": 6,
                "timestamp": int(time.time_ns() / 1000),
            }
            response = await self.xiaomusic.mina_service.mina_request(
                "/music/search", params
            )
            for song in response["data"]["songList"]:
                if song["originName"] == "QQ音樂":
                    audio_id = song["audioID"]
                    break
            # 沒找到QQ音樂的歌曲，取第一個
            if audio_id == 1582971365183456177:
                audio_id = response["data"]["songList"][0]["audioID"]
            self.log.debug(f"_get_audio_id. name: {name} songId:{audio_id}")
        except Exception as e:
            self.log.error(f"_get_audio_id {e}")
        return str(audio_id)

    # 重置計時器
    async def reset_timer_when_answer(self, answer_length):
        if not (self.isplaying() and self.config.continue_play):
            return
        pause_time = answer_length / 5 + 1
        offset, duration = self.get_offset_duration()
        self._paused_time += pause_time
        new_time = duration - offset + pause_time
        await self.set_next_music_timeout(new_time)
        self.log.info(
            f"reset_timer 延長定時器. answer_length:{answer_length} pause_time:{pause_time}"
        )

    # 設置下一首歌曲的播放定時器
    async def set_next_music_timeout(self, sec):
        self.cancel_next_timer()

        async def _do_next():
            await asyncio.sleep(sec)
            try:
                self.log.info("定時器時間到了")
                if self._next_timer:
                    self._next_timer = None
                    if self.device.play_type == PLAY_TYPE_SIN:
                        self.log.info("單曲播放不繼續播放下一首")
                        await self.stop(arg1="notts")
                    else:
                        await self._play_next()
                else:
                    self.log.info("定時器時間到了但是不見了")

            except Exception as e:
                self.log.error(f"Execption {e}")

        self._next_timer = asyncio.create_task(_do_next())
        self.log.info(f"{sec} 秒後將會播放下一首歌曲")

    async def set_volume(self, volume: int):
        self.log.info("set_volume. volume:%d", volume)
        try:
            await self.xiaomusic.mina_service.player_set_volume(self.device_id, volume)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    async def get_volume(self):
        volume = 0
        try:
            playing_info = await self.xiaomusic.mina_service.player_get_status(
                self.device_id
            )
            self.log.info(f"get_volume. playing_info:{playing_info}")
            volume = json.loads(playing_info.get("data", {}).get("info", "{}")).get(
                "volume", 0
            )
        except Exception as e:
            self.log.warning(f"Execption {e}")
        volume = int(volume)
        self.log.info("get_volume. volume:%d", volume)
        return volume

    async def set_play_type(self, play_type, dotts=True):
        self.device.play_type = play_type
        self.xiaomusic.save_cur_config()
        if dotts:
            tts = self.config.get_play_type_tts(play_type)
            await self.do_tts(tts)
        self.update_playlist()

    async def play_music_list(self, list_name, music_name):
        self._last_cmd = "play_music_list"
        self.device.cur_playlist = list_name
        self.update_playlist()
        if not music_name:
            music_name = self.device.playlist2music[list_name]
        self.log.info(f"开始播放列表{list_name} {music_name}")
        await self._play(music_name, exact=True)

    async def stop(self, arg1=""):
        self._last_cmd = "stop"
        self._playing = False
        if arg1 != "notts":
            await self.do_tts(self.config.stop_tts_msg)
        await asyncio.sleep(3)  # 等它说完
        # 取消组内所有的下一首歌曲的定时器
        await thdplay("stop", "", self.xiaomusic.thdtarget)
        self.cancel_group_next_timer()
        await self.group_force_stop_xiaoai()
        self.log.info("stop now")

    async def group_force_stop_xiaoai(self):
        device_id_list = self.xiaomusic.get_group_device_id_list(self.group_name)
        self.log.info(f"group_force_stop_xiaoai {device_id_list}")
        tasks = [self.force_stop_xiaoai(device_id) for device_id in device_id_list]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_force_stop_xiaoai {device_id_list} {results}")
        return results

    async def stop_after_minute(self, minute: int):
        if self._stop_timer:
            self._stop_timer.cancel()
            self._stop_timer = None
            self.log.info("關機定時器已取消")

        async def _do_stop():
            await asyncio.sleep(minute * 60)
            try:
                await self.stop(arg1="notts")
            except Exception as e:
                self.log.exception(f"Execption {e}")

        self._stop_timer = asyncio.create_task(_do_stop())
        await self.do_tts(f"收到,{minute}分鐘後將關機")

    def cancel_next_timer(self):
        self.log.info("cancel_next_timer")
        if self._next_timer:
            self._next_timer.cancel()
            self.log.info(f"下一曲定時器已取消 {self.device_id}")
            self._next_timer = None
        else:
            self.log.info("下一曲定時器不見了")

    def cancel_group_next_timer(self):
        devices = self.xiaomusic.get_group_devices(self.group_name)
        self.log.info(f"cancel_group_next_timer {devices}")
        for device in devices.values():
            device.cancel_next_timer()

    def get_cur_play_list(self):
        return self.device.cur_playlist

    # 清空所有定時器
    def cancel_all_timer(self):
        self.log.info("in cancel_all_timer")
        if self._next_timer:
            self._next_timer.cancel()
            self._next_timer = None
            self.log.info("cancel_all_timer _next_timer.cancel")

        if self._stop_timer:
            self._stop_timer.cancel()
            self._stop_timer = None
            self.log.info("cancel_all_timer _stop_timer.cancel")

    @classmethod
    def dict_clear(cls, d):
        for key in list(d):
            val = d.pop(key)
            val.cancel_all_timer()

    # 根據當前歌曲匹配歌曲列表
    def find_cur_playlist(self, name):
        # 匹配順序：
        # 1. 收藏
        # 2. 最近新增
        # 3. 排除（全部,所有歌曲,所有電台,臨時搜索列表）
        # 4. 所有歌曲
        # 5. 所有電台
        # 6. 全部
        if name in self.xiaomusic.music_list.get("收藏", []):
            return "收藏"
        if name in self.xiaomusic.music_list.get("最近新增", []):
            return "最近新增"
        for list_name, play_list in self.xiaomusic.music_list.items():
            if (list_name not in ["全部", "所有歌曲", "所有電台", "臨時搜索列表"]) and (
                name in play_list
            ):
                return list_name
        if name in self.xiaomusic.music_list.get("所有歌曲", []):
            return "所有歌曲"
        if name in self.xiaomusic.music_list.get("所有電台", []):
            return "所有電台"
        return "全部"


# 目錄監控類，使用延遲防抖，僅監控音樂文件
class XiaoMusicPathWatch(FileSystemEventHandler):
    def __init__(self, callback, debounce_delay, loop):
        self.callback = callback
        self.debounce_delay = debounce_delay
        self.loop = loop
        self._debounce_handle = None

    def on_any_event(self, event):
        # 只處理文件的創建、刪除和移動事件
        if not isinstance(event, FileCreatedEvent | FileDeletedEvent | FileMovedEvent):
            return

        if event.is_directory:
            return  # 忽略目錄事件

        # 處理文件事件
        src_ext = os.path.splitext(event.src_path)[1].lower()
        # 處理移動事件的目標路徑
        if hasattr(event, "dest_path"):
            dest_ext = os.path.splitext(event.dest_path)[1].lower()
            if dest_ext in SUPPORT_MUSIC_TYPE:
                self.schedule_callback()
                return

        if src_ext in SUPPORT_MUSIC_TYPE:
            self.schedule_callback()

    def schedule_callback(self):
        def _execute_callback():
            self._debounce_handle = None
            self.callback()

        if self._debounce_handle:
            self._debounce_handle.cancel()
        self._debounce_handle = self.loop.call_later(
            self.debounce_delay, _execute_callback
        )

    # ===================================================================
