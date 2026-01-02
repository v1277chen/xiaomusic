#!/usr/bin/env python3
"""
JS 插件管理器
负责加载、管理和运行 MusicFree JS 插件
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any


class JSPluginManager:
    """
    JS 插件管理器
    負責加載、管理和運行 MusicFree JS 插件
    通過啟動一個 Node.js 子進程 (js_plugin_runner.js) 在沙箱環境中執行 JS 代碼
    並通過標準輸入輸出 (Stdin/Stdout) 進行 JSON 消息通訊
    """

    def __init__(self, xiaomusic):
        self.xiaomusic = xiaomusic
        base_path = self.xiaomusic.config.conf_path
        self.log = logging.getLogger(__name__)
        # JS插件文件夾：
        self.plugins_dir = os.path.join(base_path, "js_plugins")
        # 插件配置Json：
        self.plugins_config_path = os.path.join(base_path, "plugins-config.json")
        self.plugins = {}  # 插件狀態信息緩存
        self.node_process = None
        self.message_queue = []
        self.response_handlers = {}
        self._lock = threading.Lock()
        self.request_id = 0
        self.pending_requests = {}

        # 啟動 Node.js 子進程
        self._start_node_process()

        # 啟動消息處理線程
        self._start_message_handler()

        # 加載插件
        self._load_plugins()

    def _start_node_process(self):
        """
        啟動 Node.js 子進程
        使用 subprocess.Popen 啟動 js_plugin_runner.js
        並設置標準輸入輸出管道
        """
        runner_path = os.path.join(os.path.dirname(__file__), "js_plugin_runner.js")

        try:
            self.node_process = subprocess.Popen(
                ["node", "--max-old-space-size=128", runner_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,  # 行缓冲
            )

            self.log.info("Node.js process started successfully")

            # 啟動進程監控線程
            threading.Thread(target=self._monitor_node_process, daemon=True).start()

        except Exception as e:
            self.log.error(f"Failed to start Node.js process: {e}")
            raise

    def _monitor_node_process(self):
        """監控 Node.js 進程狀態，若意外退出則自動重啟"""
        while True:
            if self.node_process and self.node_process.poll() is not None:
                self.log.warning("Node.js process died, restarting...")
                self._start_node_process()
            time.sleep(5)

    def _start_message_handler(self):
        """
        啟動消息處理執行緒
        分別監聽子進程的 Stdout (正常響應) 和 Stderr (錯誤日誌)
        """

        def stdout_handler():
            while True:
                if self.node_process and self.node_process.stdout:
                    try:
                        line = self.node_process.stdout.readline()
                        if line:
                            # 解析子進程發來的 JSON 響應
                            response = json.loads(line.strip())
                            self._handle_response(response)
                    except json.JSONDecodeError as e:
                        # 捕获非 JSON 输出（可能是插件的调试信息或错误信息）
                        self.log.warning(
                            f"Non-JSON output from Node.js process: {line.strip()}, error: {e}"
                        )
                    except Exception as e:
                        self.log.error(f"Message handler error: {e}")
                time.sleep(0.1)

        def stderr_handler():
            """處理 Node.js 進程的錯誤輸出"""
            while True:
                if self.node_process and self.node_process.stderr:
                    try:
                        error_line = self.node_process.stderr.readline()
                        if error_line:
                            self.log.error(
                                f"Node.js process error output: {error_line.strip()}"
                            )
                    except Exception as e:
                        self.log.error(f"Error handler error: {e}")
                time.sleep(0.1)

        threading.Thread(target=stdout_handler, daemon=True).start()
        threading.Thread(target=stderr_handler, daemon=True).start()

    def _send_message(
        self, message: dict[str, Any], timeout: int = 30
    ) -> dict[str, Any]:
        """
        發送消息到 Node.js 子進程
        :param message: 消息字典，包含 action 和數據
        :return: 子進程的響應結果
        """
        with self._lock:
            if not self.node_process or self.node_process.poll() is not None:
                raise Exception("Node.js process not available")

            # 生成唯一消息 ID
            message_id = f"msg_{int(time.time() * 1000)}"
            message["id"] = message_id

            # 记录发送的消息
            self.log.info(
                f"JS Plugin Manager sending message: {message.get('action', 'unknown')} for plugin: {message.get('pluginName', 'unknown')}"
            )
            # 簡化日誌輸出
            if "params" in message:
                self.log.info(f"JS Plugin Manager search params: {message['params']}")
            elif "musicItem" in message:
                self.log.info(f"JS Plugin Manager music item: {message['musicItem']}")

            # 發送消息 (JSON字符串 + 換行符)
            self.node_process.stdin.write(json.dumps(message) + "\n")
            self.node_process.stdin.flush()

            # 等待響應
            response = self._wait_for_response(message_id, timeout)
            self.log.info(
                f"JS Plugin Manager received response for message {message_id}: {response.get('success', 'unknown')}"
            )
            return response

    def _wait_for_response(self, message_id: str, timeout: int) -> dict[str, Any]:
        """等待特定消息的響應"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if message_id in self.response_handlers:
                response = self.response_handlers.pop(message_id)
                return response
            time.sleep(0.1)

        raise TimeoutError(f"Message {message_id} timeout")

    def _handle_response(self, response: dict[str, Any]):
        """處理 Node.js 進程的響應"""
        message_id = response.get("id")
        self.log.debug(
            f"JS Plugin Manager received raw response: {response}"
        )  # 添加原始响应日志

        # 添加更嚴格的數據驗證
        if not isinstance(response, dict):
            self.log.error(
                f"JS Plugin Manager received invalid response type: {type(response)}, value: {response}"
            )
            return

        if "id" not in response:
            self.log.error(
                f"JS Plugin Manager received response without id: {response}"
            )
            return

        # 確保 success 字段存在
        if "success" not in response:
            self.log.warning(
                f"JS Plugin Manager received response without success field: {response}"
            )
            response["success"] = False

        # 如果有 result 字段，驗證其結構
        if "result" in response and response["result"] is not None:
            result = response["result"]
            if isinstance(result, dict):
                # 對搜索結果進行特殊處理
                if "data" in result and not isinstance(result["data"], list):
                    self.log.warning(
                        f"JS Plugin Manager received result with invalid data type: {type(result['data'])}, setting to empty list"
                    )
                    result["data"] = []

        if message_id:
            self.response_handlers[message_id] = response

    """------------------------------开放接口相关函数----------------------------------------"""

    def get_openapi_info(self) -> dict[str, Any]:
        """獲取開放接口配置信息
        Returns:
            Dict[str, Any]: 包含 OpenAPI 配置信息的字典，包括啟用狀態和搜索 URL
        """
        try:
            # 讀取配置文件中的 OpenAPI 配置信息
            if os.path.exists(self.plugins_config_path):
                with open(self.plugins_config_path, encoding="utf-8") as f:
                    config_data = json.load(f)
                # 返回 openapi_info 配置项
                return config_data.get("openapi_info", {})
            else:
                return {"enabled": False}
        except Exception as e:
            self.log.error(f"Failed to read OpenAPI info from config: {e}")
            return {}

    def toggle_openapi(self) -> dict[str, Any]:
        """切換開放接口配置狀態
        Returns: 切換後的配置信息
        """
        try:
            # 讀取配置文件中的 OpenAPI 配置信息
            if os.path.exists(self.plugins_config_path):
                with open(self.plugins_config_path, encoding="utf-8") as f:
                    config_data = json.load(f)

                # 獲取當前的 openapi_info 配置，如果沒有則初始化
                openapi_info = config_data.get("openapi_info", {})

                # 切換啟用狀態：和當前狀態取反
                current_enabled = openapi_info.get("enabled", False)
                openapi_info["enabled"] = not current_enabled

                # 更新配置數據
                config_data["openapi_info"] = openapi_info
                # 寫回配置文件
                with open(self.plugins_config_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
                return {"success": True}
            else:
                return {"success": False}
        except Exception as e:
            self.log.error(f"Failed to toggle OpenAPI config: {e}")
            # 出錯時返回默認配置
            return {"success": False, "error": str(e)}

    def update_openapi_url(self, openapi_url: str) -> dict[str, Any]:
        """更新開放接口地址
        Returns: 更新後的配置信息
        :type openapi_url: 新的接口地址
        """
        try:
            # 讀取配置文件中的 OpenAPI 配置信息
            if os.path.exists(self.plugins_config_path):
                with open(self.plugins_config_path, encoding="utf-8") as f:
                    config_data = json.load(f)

                # 獲取當前的 openapi_info 配置，如果沒有則初始化
                openapi_info = config_data.get("openapi_info", {})

                # 切換啟用狀態：和當前狀態取反
                # current_url = openapi_info.get("search_url", "")
                openapi_info["search_url"] = openapi_url

                # 更新配置數據
                config_data["openapi_info"] = openapi_info
                # 寫回配置文件
                with open(self.plugins_config_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
                return {"success": True}
            else:
                return {"success": False}
        except Exception as e:
            self.log.error(f"Failed to toggle OpenAPI config: {e}")
            # 出错时返回默认配置
            return {"success": False, "error": str(e)}

    """----------------------------------------------------------------------"""

    def _load_plugins(self):
        """加載所有插件"""
        if not os.path.exists(self.plugins_dir):
            os.makedirs(self.plugins_dir)

        # 讀取、加載插件配置Json
        if not os.path.exists(self.plugins_config_path):
            # 複製 plugins-config-example.json 模板，創建插件配置Json文件
            example_config_path = os.path.join(
                os.path.dirname(__file__), "plugins-config-example.json"
            )
            if os.path.exists(example_config_path):
                shutil.copy2(example_config_path, self.plugins_config_path)
            else:
                base_config = {
                    "account": "",
                    "password": "",
                    "enabled_plugins": [],
                    "plugins_info": [],
                    "openapi_info": {"enabled": False, "search_url": ""},
                }
                with open(self.plugins_config_path, "w", encoding="utf-8") as f:
                    json.dump(base_config, f, ensure_ascii=False, indent=2)
        # 輸出文件夾、配置文件地址
        self.log.info(f"Plugins directory: {self.plugins_dir}")
        self.log.info(f"Plugins config file: {self.plugins_config_path}")
        # 只加載指定的插件，避免加載所有插件導致超時
        # enabled_plugins = ['kw', 'qq-yuanli']  # 可以根據需要添加更多
        # 讀取配置文件配置
        enabled_plugins = self.get_enabled_plugins()
        for filename in os.listdir(self.plugins_dir):
            if filename.endswith(".js"):
                try:
                    plugin_name = os.path.splitext(filename)[0]
                    # 如果是重要插件或沒有指定重要插件列表，則加載
                    if not enabled_plugins or plugin_name in enabled_plugins:
                        try:
                            self.log.info(f"Loading plugin: {plugin_name}")
                            self.load_plugin(plugin_name)
                        except Exception as e:
                            self.log.error(
                                f"Failed to load important plugin {plugin_name}: {e}"
                            )
                            # 即使加載失敗也記錄插件信息
                            self.plugins[plugin_name] = {
                                "name": plugin_name,
                                "enabled": False,
                                "loaded": False,
                                "error": str(e),
                            }
                    else:
                        self.log.debug(
                            f"Skipping plugin (not in important list): {plugin_name}"
                        )
                        # 標記為未加載但可用
                        self.plugins[plugin_name] = {
                            "name": plugin_name,
                            "enabled": False,
                            "loaded": False,
                            "error": "Not loaded (not in important plugins list)",
                        }
                except Exception as e:
                    self.log.error(f"Failed to load plugin {filename}: {e}")
                    # 即使加載失敗也記錄插件信息
                    self.plugins[plugin_name] = {
                        "name": plugin_name,
                        "enabled": False,
                        "loaded": False,
                        "error": str(e),
                    }

    def load_plugin(self, plugin_name: str) -> bool:
        """加載單個插件"""
        plugin_file = os.path.join(self.plugins_dir, f"{plugin_name}.js")

        if not os.path.exists(plugin_file):
            raise FileNotFoundError(f"Plugin file not found: {plugin_file}")

        try:
            with open(plugin_file, encoding="utf-8") as f:
                js_code = f.read()

            response = self._send_message(
                {"action": "load", "name": plugin_name, "code": js_code}
            )

            if response["success"]:
                self.plugins[plugin_name] = {
                    "status": "loaded",
                    "load_time": time.time(),
                    "enabled": True,
                }
                self.log.info(f"Loaded JS plugin: {plugin_name}")
                return True
            else:
                self.log.error(
                    f"Failed to load JS plugin {plugin_name}: {response['error']}"
                )
                return False

        except Exception as e:
            self.log.error(f"Failed to load JS plugin {plugin_name}: {e}")
            return False

    def get_plugin_list(self) -> list[dict[str, Any]]:
        """獲取啟用的插件列表"""
        result = []
        try:
            # 讀取配置文件中的啟用插件列表
            if os.path.exists(self.plugins_config_path):
                with open(self.plugins_config_path, encoding="utf-8") as f:
                    config_data = json.load(f)
                plugin_infos = config_data.get("plugins_info", [])
                enabled_plugins = config_data.get("enabled_plugins", [])

                # 創建一個映射，用於快速查找插件在 enabled_plugins 中的位置
                enabled_order = {name: i for i, name in enumerate(enabled_plugins)}

                # 先按 enabled 屬性排序（True 在前）
                # 再按 enabled_plugins 順序排序（啟用的插件才參與此排序）
                def sort_key(plugin_info):
                    name = plugin_info["name"]
                    is_enabled = plugin_info.get("enabled", False)
                    order = (
                        enabled_order.get(name, len(enabled_plugins))
                        if is_enabled
                        else len(enabled_plugins)
                    )
                    # (-is_enabled) 將 True(1) 放到前面，False(0) 放到後面
                    # order 控制啟用插件間的相對順序
                    return -is_enabled, order

                result = sorted(plugin_infos, key=sort_key)
        except Exception as e:
            self.log.error(f"Failed to read enabled plugins from config: {e}")
        return result

    def get_enabled_plugins(self) -> list[str]:
        """獲取啟用的插件列表"""
        try:
            # 讀取配置文件中的啟用插件列表
            if os.path.exists(self.plugins_config_path):
                with open(self.plugins_config_path, encoding="utf-8") as f:
                    config_data = json.load(f)
                return config_data.get("enabled_plugins", [])
            else:
                return []
        except Exception as e:
            self.log.error(f"Failed to read enabled plugins from config: {e}")
            return []

    def search(self, plugin_name: str, keyword: str, page: int = 1, limit: int = 20):
        """
        執行音樂搜索
        :param plugin_name: 插件名稱 OR "OpenAPI"
        :param keyword: 搜索關鍵詞
        :param page: 頁碼
        :param limit: 每頁數量
        """
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.info(
            f"JS Plugin Manager starting search in plugin {plugin_name} for keyword: {keyword}"
        )
        # 發送 'search' 指令給 Node.js 進程
        response = self._send_message(
            {
                "action": "search",
                "pluginName": plugin_name,
                "params": {"keywords": keyword, "page": page, "limit": limit},
            }
        )

        self.log.debug(
            f"JS Plugin Manager search response: {response}"
        )  # 使用 debug 級別，減少日誌量

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager search failed in plugin {plugin_name}: {response['error']}"
            )
            # 添加詳細的錯誤信息
            self.log.error(f"JS Plugin Manager full error response: {response}")
            raise Exception(f"Search failed: {response['error']}")
        else:
            # 檢查返回的數據結構
            result_data = response["result"]
            self.log.debug(
                f"JS Plugin Manager search raw result: {result_data}"
            )  # 使用 debug 級別
            data_list = result_data.get("data", [])
            is_end = result_data.get("isEnd", True)
            self.log.info(
                f"JS Plugin Manager search completed in plugin {plugin_name}, isEnd: {is_end}, found {len(data_list)} results"
            )
            # 檢查數據類型是否正確
            if not isinstance(data_list, list):
                self.log.error(
                    f"JS Plugin Manager search returned invalid data type: {type(data_list)}, value: {data_list}"
                )
            else:
                self.log.debug(
                    f"JS Plugin Manager search data sample: {data_list[:2] if len(data_list) > 0 else 'No results'}"
                )
        return result_data

    async def openapi_search(self, url: str, keyword: str, limit: int = 10):
        """
        直接調用在線接口進行音樂搜索 (OpenAPI)
        用於聚合多源搜索
        :param url: 在線搜索接口地址
        :param keyword: 搜索關鍵詞 (支持 "歌曲-歌手" 格式)
        :param limit: 每頁數量
        """
        import asyncio

        import aiohttp

        try:
            # 如果關鍵詞包含 '-'，則提取歌手名、歌名
            if "-" in keyword:
                parts = keyword.split("-")
                keyword = parts[0]
                artist = parts[1]
            else:
                artist = ""
            # 構造請求參數
            params = {"type": "aggregateSearch", "keyword": keyword, "limit": limit}
            # 使用aiohttp發起異步HTTP GET請求
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    response.raise_for_status()  # 拋出HTTP錯誤
                    # 解析響應數據
                    raw_data = await response.json()

            self.log.info(f"在線接口返回Json: {raw_data}")

            # 檢查API調用是否成功
            if raw_data.get("code") != 200:
                raise Exception(
                    f"API request failed with code: {raw_data.get('code', 'unknown')}"
                )

            # 提取實際的搜索結果
            api_data = raw_data.get("data", {})
            results = api_data.get("results", [])

            # 轉換數據格式以匹配插件系統的期望格式
            converted_data = []
            for item in results:
                converted_item = {
                    "id": item.get("id", ""),
                    "title": item.get("name", ""),
                    "artist": item.get("artist", ""),
                    "album": item.get("album", ""),
                    "platform": "OpenAPI-" + item.get("platform"),
                    "isOpenAPI": True,
                    "url": item.get("url", ""),
                    "artwork": item.get("pic", ""),
                    "lrc": item.get("lrc", ""),
                }
                converted_data.append(converted_item)
            # 排序篩選
            unified_result = {"data": converted_data}
            # 調用優化函數
            optimized_result = self.optimize_search_results(
                unified_result,
                search_keyword=keyword,
                limit=limit,
                search_artist=artist,
            )
            results = optimized_result.get("data", [])
            # 返回統一格式的數據
            return {
                "success": True,
                "data": results,
                "total": len(results),
                "sources": {"OpenAPI": len(results)},
                "page": 1,
                "limit": limit,
            }

        except asyncio.TimeoutError as e:
            self.log.error(f"OpenAPI search timeout at URL {url}: {e}")
            return {
                "success": False,
                "error": f"OpenAPI search timeout: {str(e)}",
                "data": [],
                "total": 0,
                "sources": {},
                "page": 1,
                "limit": limit,
            }
        except Exception as e:
            self.log.error(f"OpenAPI search error at URL {url}: {e}")
            return {
                "success": False,
                "error": f"OpenAPI search error: {str(e)}",
                "data": [],
                "total": 0,
                "sources": {},
                "page": 1,
                "limit": limit,
            }

    def optimize_search_results(
        self,
        result_data: dict[str, Any],  # 搜索结果数据，字典类型，包含任意类型的值
        search_keyword: str = "",  # 搜索关键词，默认为空字符串
        search_artist: str = "",  # 搜索歌手名，默认为空字符串
        limit: int = 1,  # 返回结果数量限制，默认为1
    ) -> dict[str, Any]:  # 返回优化后的搜索结果，字典类型，包含任意类型的值
        """
        優化搜索結果
        根據關鍵詞、歌手名和平台權重對結果進行重新排序
        :param result_data: 原始搜索結果
        :param search_keyword: 關鍵詞
        :param search_artist: 歌手名
        :param limit: 返回限制
        :return: 排序後的數據
        """
        if not result_data or "data" not in result_data or not result_data["data"]:
            return result_data

        # 清理搜索關鍵詞和歌手名，去除首尾空格
        search_keyword = search_keyword.strip()
        search_artist = search_artist.strip()

        # 如果關鍵詞和歌手名都為空，則不進行排序
        if not search_keyword and not search_artist:
            return result_data  # 兩者都空才不排序

        # 獲取待處理的數據列表
        data_list = result_data["data"]
        self.log.info(f"列表信息：：{data_list}")
        # 預計算平台權重，啟用插件列表中的前9個插件有權重，排名越靠前權重越高
        enabled_plugins = self.get_enabled_plugins()
        plugin_weights = {p: 9 - i for i, p in enumerate(enabled_plugins[:9])}

        def calculate_match_score(item):
            """
            計算單個搜索結果的匹配分數
            參數:
                item: 單個搜索結果項
            返回:
                匹配分數，包含標題匹配分、藝術家匹配分和平台加分
            """
            # 獲取並標準化標題、藝術家和平台信息
            title = item.get("title", "").lower()
            artist = item.get("artist", "").lower()
            platform = item.get("platform", "")

            # 標準化搜索關鍵詞和藝術家名
            kw = search_keyword.lower()
            ar = search_artist.lower()

            # 歌名匹配分
            title_score = 0
            if kw:
                if kw == title:
                    title_score = 400
                elif title.startswith(kw):
                    title_score = 300
                elif kw in title:
                    title_score = 200

            # 歌手匹配分
            artist_score = 0
            if ar:
                if ar == artist:
                    artist_score = 1000
                elif artist.startswith(ar):
                    artist_score = 800
                elif ar in artist:
                    artist_score = 600

            platform_bonus = plugin_weights.get(platform, 0)
            return title_score + artist_score + platform_bonus

        sorted_data = sorted(data_list, key=calculate_match_score, reverse=True)
        self.log.info(f"排序後列表信息：：{sorted_data}")
        if 0 < limit < len(sorted_data):
            sorted_data = sorted_data[:limit]
        result_data["data"] = sorted_data
        return result_data

    def get_media_source(self, plugin_name: str, music_item: dict[str, Any], quality):
        """獲取媒體源"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting media source in plugin {plugin_name} for item: {music_item.get('title', 'unknown')} by {music_item.get('artist', 'unknown')}"
        )
        response = self._send_message(
            {
                "action": "getMediaSource",
                "pluginName": plugin_name,
                "musicItem": music_item,
                "quality": quality,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getMediaSource failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getMediaSource failed: {response['error']}")
        else:
            self.log.debug(
                f"JS Plugin Manager getMediaSource completed in plugin {plugin_name}, URL length: {len(response['result'].get('url', '')) if response['result'] else 0}"
            )

        return response["result"]

    def get_lyric(self, plugin_name: str, music_item: dict[str, Any]):
        """獲取歌詞"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting lyric in plugin {plugin_name} for music: {music_item.get('title', 'unknown')}"
        )
        response = self._send_message(
            {"action": "getLyric", "pluginName": plugin_name, "musicItem": music_item}
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getLyric failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getLyric failed: {response['error']}")

        return response["result"]

    def get_music_info(self, plugin_name: str, music_item: dict[str, Any]):
        """獲取音樂詳情"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting music info in plugin {plugin_name} for music: {music_item.get('title', 'unknown')}"
        )
        response = self._send_message(
            {
                "action": "getMusicInfo",
                "pluginName": plugin_name,
                "musicItem": music_item,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getMusicInfo failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getMusicInfo failed: {response['error']}")

        return response["result"]

    def get_album_info(
        self, plugin_name: str, album_info: dict[str, Any], page: int = 1
    ):
        """獲取專輯詳情"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting album info in plugin {plugin_name} for album: {album_info.get('title', 'unknown')}"
        )
        response = self._send_message(
            {
                "action": "getAlbumInfo",
                "pluginName": plugin_name,
                "albumInfo": album_info,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getAlbumInfo failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getAlbumInfo failed: {response['error']}")

        return response["result"]

    def get_music_sheet_info(
        self, plugin_name: str, playlist_info: dict[str, Any], page: int = 1
    ):
        """獲取歌單詳情"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting music sheet info in plugin {plugin_name} for playlist: {playlist_info.get('title', 'unknown')}"
        )
        response = self._send_message(
            {
                "action": "getMusicSheetInfo",
                "pluginName": plugin_name,
                "playlistInfo": playlist_info,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getMusicSheetInfo failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getMusicSheetInfo failed: {response['error']}")

        return response["result"]

    def get_artist_works(
        self,
        plugin_name: str,
        artist_item: dict[str, Any],
        page: int = 1,
        type_: str = "music",
    ):
        """获取作者作品"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting artist works in plugin {plugin_name} for artist: {artist_item.get('title', 'unknown')}"
        )
        response = self._send_message(
            {
                "action": "getArtistWorks",
                "pluginName": plugin_name,
                "artistItem": artist_item,
                "page": page,
                "type": type_,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getArtistWorks failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getArtistWorks failed: {response['error']}")

        return response["result"]

    def import_music_item(self, plugin_name: str, url_like: str):
        """导入单曲"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager importing music item in plugin {plugin_name} from: {url_like}"
        )
        response = self._send_message(
            {
                "action": "importMusicItem",
                "pluginName": plugin_name,
                "urlLike": url_like,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager importMusicItem failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"importMusicItem failed: {response['error']}")

        return response["result"]

    def import_music_sheet(self, plugin_name: str, url_like: str):
        """导入歌单"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager importing music sheet in plugin {plugin_name} from: {url_like}"
        )
        response = self._send_message(
            {
                "action": "importMusicSheet",
                "pluginName": plugin_name,
                "urlLike": url_like,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager importMusicSheet failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"importMusicSheet failed: {response['error']}")

        return response["result"]

    def get_top_lists(self, plugin_name: str):
        """获取榜单列表"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(f"JS Plugin Manager getting top lists in plugin {plugin_name}")
        response = self._send_message(
            {"action": "getTopLists", "pluginName": plugin_name}
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getTopLists failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getTopLists failed: {response['error']}")

        return response["result"]

    def get_top_list_detail(
        self, plugin_name: str, top_list_item: dict[str, Any], page: int = 1
    ):
        """获取榜单详情"""
        if plugin_name not in self.plugins:
            raise ValueError(f"Plugin {plugin_name} not found or not loaded")

        self.log.debug(
            f"JS Plugin Manager getting top list detail in plugin {plugin_name} for list: {top_list_item.get('title', 'unknown')}"
        )
        response = self._send_message(
            {
                "action": "getTopListDetail",
                "pluginName": plugin_name,
                "topListItem": top_list_item,
                "page": page,
            }
        )

        if not response["success"]:
            self.log.error(
                f"JS Plugin Manager getTopListDetail failed in plugin {plugin_name}: {response['error']}"
            )
            raise Exception(f"getTopListDetail failed: {response['error']}")

        return response["result"]

    # 启用插件
    def enable_plugin(self, plugin_name: str) -> bool:
        if plugin_name in self.plugins:
            self.plugins[plugin_name]["enabled"] = True
            # 读取、修改 插件配置json文件：① 将plugins_info属性中对于的插件状态改为禁用、2：将 enabled_plugins中对应插件移除
            # 同步更新配置文件
            try:
                # 使用自定义的配置文件路径
                config_file_path = self.plugins_config_path

                # 读取现有配置
                if os.path.exists(config_file_path):
                    with open(config_file_path, encoding="utf-8") as f:
                        config_data = json.load(f)

                    # 更新plugins_info中对应插件的状态
                    for plugin_info in config_data.get("plugins_info", []):
                        if plugin_info.get("name") == plugin_name:
                            plugin_info["enabled"] = True

                    # 添加到enabled_plugins中（如果不存在）
                    if "enabled_plugins" not in config_data:
                        config_data["enabled_plugins"] = []

                    if plugin_name not in config_data["enabled_plugins"]:
                        # 追加到list的第一个
                        config_data["enabled_plugins"].insert(0, plugin_name)

                    # 写回配置文件
                    with open(config_file_path, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, ensure_ascii=False, indent=2)

                    self.log.info(
                        f"Plugin config updated for enabled plugin {plugin_name}"
                    )
                    # 更新插件引擎
                    self.reload_plugins()

            except Exception as e:
                self.log.error(
                    f"Failed to update plugin config when enabling {plugin_name}: {e}"
                )
            return True
        return False

    # 禁用插件
    def disable_plugin(self, plugin_name: str) -> bool:
        if plugin_name in self.plugins:
            self.plugins[plugin_name]["enabled"] = False
            # 读取、修改 插件配置json文件：① 将plugins_info属性中对于的插件状态改为禁用、2：将 enabled_plugins中对应插件移除
            # 同步更新配置文件
            try:
                # 使用自定义的配置文件路径
                config_file_path = self.plugins_config_path

                # 读取现有配置
                if os.path.exists(config_file_path):
                    with open(config_file_path, encoding="utf-8") as f:
                        config_data = json.load(f)

                    # 更新plugins_info中对应插件的状态
                    for plugin_info in config_data.get("plugins_info", []):
                        if plugin_info.get("name") == plugin_name:
                            plugin_info["enabled"] = False

                    # 添加到enabled_plugins中（如果不存在）
                    if "enabled_plugins" not in config_data:
                        config_data["enabled_plugins"] = []

                    if plugin_name in config_data["enabled_plugins"]:
                        # 移除对应的插件名
                        config_data["enabled_plugins"].remove(plugin_name)

                    # 写回配置文件
                    with open(config_file_path, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, ensure_ascii=False, indent=2)

                    self.log.info(
                        f"Plugin config updated for enabled plugin {plugin_name}"
                    )
                    # 更新插件引擎
                    self.reload_plugins()
            except Exception as e:
                self.log.error(
                    f"Failed to update plugin config when enabling {plugin_name}: {e}"
                )
            return True
        return False

    # 卸载插件
    def uninstall_plugin(self, plugin_name: str) -> bool:
        """卸载插件：移除配置信息并删除插件文件"""
        if plugin_name in self.plugins:
            try:
                # 从内存中移除插件
                self.plugins.pop(plugin_name)

                # 使用自定义的配置文件路径
                config_file_path = self.plugins_config_path

                # 读取现有配置
                if os.path.exists(config_file_path):
                    with open(config_file_path, encoding="utf-8") as f:
                        config_data = json.load(f)

                    # 移除plugins_info属性中对应的插件项目
                    if "plugins_info" in config_data:
                        config_data["plugins_info"] = [
                            plugin_info
                            for plugin_info in config_data["plugins_info"]
                            if plugin_info.get("name") != plugin_name
                        ]

                    # 从enabled_plugins中移除插件（如果存在）
                    if (
                        "enabled_plugins" in config_data
                        and plugin_name in config_data["enabled_plugins"]
                    ):
                        config_data["enabled_plugins"].remove(plugin_name)

                    # 回写配置文件
                    with open(config_file_path, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, ensure_ascii=False, indent=2)

                    self.log.info(
                        f"Plugin config updated for uninstalled plugin {plugin_name}"
                    )

                # 删除插件文件夹中的指定插件文件
                plugin_file_path = os.path.join(self.plugins_dir, f"{plugin_name}.js")
                if os.path.exists(plugin_file_path):
                    os.remove(plugin_file_path)
                    self.log.info(f"Plugin file removed: {plugin_file_path}")
                else:
                    self.log.warning(f"Plugin file not found: {plugin_file_path}")

                return True
            except Exception as e:
                self.log.error(f"Failed to uninstall plugin {plugin_name}: {e}")
                return False
        return False

    def reload_plugins(self):
        """重新加载所有插件"""
        self.log.info("Reloading all plugins...")
        # 清空现有插件状态
        self.plugins.clear()
        # 重新加载插件
        self._load_plugins()
        self.log.info("Plugins reloaded successfully")

    def update_plugin_config(self, plugin_name: str, plugin_file: str):
        """更新插件配置文件"""
        try:
            # 使用自定义的配置文件路径
            config_file_path = self.plugins_config_path
            # 如果配置文件不存在，创建一个基础配置
            if not os.path.exists(config_file_path):
                base_config = {
                    "account": "",
                    "password": "",
                    "enabled_plugins": [],
                    "plugins_info": [],
                }
                with open(config_file_path, "w", encoding="utf-8") as f:
                    json.dump(base_config, f, ensure_ascii=False, indent=2)

            # 读取现有配置
            with open(config_file_path, encoding="utf-8") as f:
                config_data = json.load(f)

            # 检查是否已存在该插件信息
            plugin_exists = False
            for plugin_info in config_data.get("plugins_info", []):
                if plugin_info.get("name") == plugin_name:
                    plugin_exists = True
                    break

            # 如果不存在，则添加新的插件信息
            if not plugin_exists:
                new_plugin_info = {
                    "name": plugin_name,
                    "file": plugin_file,
                    "enabled": False,  # 默认不启用
                }
                if "plugins_info" not in config_data:
                    config_data["plugins_info"] = []
                config_data["plugins_info"].append(new_plugin_info)
                # 写回配置文件
                with open(config_file_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)

            self.log.info(f"Plugin config updated for {plugin_name}")

        except Exception as e:
            self.log.error(f"Failed to update plugin config: {e}")

    def shutdown(self):
        """关闭插件管理器"""
        if self.node_process:
            self.node_process.terminate()
            self.node_process.wait()
