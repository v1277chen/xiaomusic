#!/usr/bin/env python3
"""
JS 插件适配器
将 MusicFree JS 插件的数据格式转换为 xiaomusic 接口规范
"""

import logging


class JSAdapter:
    """
    JS 插件數據適配器
    負責將 MusicFree JS 插件返回的各種數據格式 (搜索結果、媒體源、歌詞等)
    轉換為 Xiaomusic 系統內部統一的數據格式
    """

    def __init__(self, xiaomusic):
        self.xiaomusic = xiaomusic
        self.log = logging.getLogger(__name__)

    def format_search_results(
        self, plugin_results: list[dict], plugin_name: str
    ) -> list[str]:
        """
        格式化搜索結果為 xiaomusic 格式，並緩存到 all_music 字典中
        :param plugin_results: 插件返回的原始搜索結果列表
        :param plugin_name: 插件名稱
        :return: 格式化後的音樂 ID 列表
        """
        formatted_ids = []
        for item in plugin_results:
            if not isinstance(item, dict):
                self.log.warning(f"Invalid item format in plugin {plugin_name}: {item}")
                continue

            # 構造符合 xiaomusic 格式的音樂項
            # 生成唯一的 music_id (online_插件名_ID)
            music_id = self._generate_music_id(
                plugin_name, item.get("id", ""), item.get("songmid", "")
            )
            music_item = {
                "id": music_id,
                "title": item.get("title", item.get("name", "")),
                "artist": self._extract_artists(item),
                "album": item.get("album", item.get("albumName", "")),
                "source": "online",
                "plugin_name": plugin_name,
                "original_data": item,  # 保存原始數據供後續獲取播放鏈接使用
                "duration": item.get("duration", 0),
                "cover": item.get(
                    "artwork", item.get("cover", item.get("albumPic", ""))
                ),
                "url": item.get("url", ""),
                "lyric": item.get("lyric", item.get("lrc", "")),
                "quality": item.get("quality", ""),
            }

            # 添加到 all_music 字典中，以便後續通過 ID 查找
            self.xiaomusic.all_music[music_id] = music_item
            formatted_ids.append(music_id)

        return formatted_ids

    def format_media_source_result(
        self, media_source_result: dict, music_item: dict
    ) -> dict:
        """
        格式化媒體源結果
        提取播放 URL 和必要的 HTTP 請求頭 (如 Cookie, Referer)
        """
        if not media_source_result:
            return {}

        formatted = {
            "url": media_source_result.get("url", ""),
            "headers": media_source_result.get("headers", {}),
            "userAgent": media_source_result.get(
                "userAgent", media_source_result.get("user_agent", "")
            ),
        }

        return formatted

    def format_lyric_result(self, lyric_result: dict) -> str:
        """格式化歌詞結果為 lrc 格式字符串"""
        if not lyric_result:
            return ""

        # 獲取原始歌詞和翻譯
        raw_lrc = lyric_result.get("rawLrc", lyric_result.get("raw_lrc", ""))
        translation = lyric_result.get("translation", "")

        # 如果有翻譯，合併歌詞和翻譯
        if translation and raw_lrc:
            # 這裡可以實現歌詞和翻譯的合併邏輯
            return f"{raw_lrc}\n{translation}"

        return raw_lrc or translation or ""

    def format_album_info_result(self, album_info_result: dict) -> dict:
        """格式化專輯信息結果"""
        if not album_info_result:
            return {}

        formatted = {
            "isEnd": album_info_result.get("isEnd", True),
            "musicList": self.format_search_results(
                album_info_result.get("musicList", []), "album"
            ),
            "albumItem": {
                "title": album_info_result.get("albumItem", {}).get("title", ""),
                "artist": album_info_result.get("albumItem", {}).get("artist", ""),
                "cover": album_info_result.get("albumItem", {}).get("cover", ""),
                "description": album_info_result.get("albumItem", {}).get(
                    "description", ""
                ),
            },
        }

        return formatted

    def format_music_sheet_info_result(self, music_sheet_result: dict) -> dict:
        """格式化音樂單信息結果"""
        if not music_sheet_result:
            return {}

        formatted = {
            "isEnd": music_sheet_result.get("isEnd", True),
            "musicList": self.format_search_results(
                music_sheet_result.get("musicList", []), "playlist"
            ),
            "sheetItem": {
                "title": music_sheet_result.get("sheetItem", {}).get("title", ""),
                "cover": music_sheet_result.get("sheetItem", {}).get("cover", ""),
                "description": music_sheet_result.get("sheetItem", {}).get(
                    "description", ""
                ),
            },
        }

        return formatted

    def format_artist_works_result(self, artist_works_result: dict) -> dict:
        """格式化藝術家作品結果"""
        if not artist_works_result:
            return {}

        formatted = {
            "isEnd": artist_works_result.get("isEnd", True),
            "data": self.format_search_results(
                artist_works_result.get("data", []), "artist"
            ),
        }

        return formatted

    def format_top_lists_result(self, top_lists_result: list[dict]) -> list[dict]:
        """格式化榜單列表結果"""
        if not top_lists_result:
            return []

        formatted = []
        for group in top_lists_result:
            formatted_group = {"title": group.get("title", ""), "data": []}

            for item in group.get("data", []):
                formatted_item = {
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "coverImg": item.get("coverImg", item.get("cover", "")),
                }
                formatted_group["data"].append(formatted_item)

            formatted.append(formatted_group)

        return formatted

    def format_top_list_detail_result(self, top_list_detail_result: dict) -> dict:
        """格式化榜單詳情結果"""
        if not top_list_detail_result:
            return {}

        formatted = {
            "isEnd": top_list_detail_result.get("isEnd", True),
            "musicList": self.format_search_results(
                top_list_detail_result.get("musicList", []), "toplist"
            ),
            "topListItem": top_list_detail_result.get("topListItem", {}),
        }

        return formatted

    def _generate_music_id(
        self, plugin_name: str, item_id: str, fallback_id: str = ""
    ) -> str:
        """生成唯一音樂ID"""
        if item_id:
            return f"online_{plugin_name}_{item_id}"
        else:
            # 如果沒有 id，嘗試使用其他標識符
            return f"online_{plugin_name}_{fallback_id}"

    def _extract_artists(self, item: dict) -> str:
        """提取藝術家信息"""
        # 嘗試多種可能的藝術家字段
        artist_fields = ["artist", "artists", "singer", "author", "creator", "singers"]

        for field in artist_fields:
            if field in item:
                value = item[field]
                if isinstance(value, list):
                    # 如果是藝術家列表，連接為字符串
                    artists = []
                    for artist in value:
                        if isinstance(artist, dict):
                            artists.append(artist.get("name", str(artist)))
                        else:
                            artists.append(str(artist))
                    return ", ".join(artists)
                elif isinstance(value, dict):
                    # 如果是藝術家對象
                    return value.get("name", str(value))
                elif value:
                    return str(value)

        # 如果沒有找到藝術家信息，返回默認值
        return "未知藝術家"

    def convert_music_item_for_plugin(self, music_item: dict) -> dict:
        """將 xiaomusic 音樂項轉換為插件兼容格式"""
        # 如果原始數據存在，優先使用原始數據
        if isinstance(music_item, dict) and "original_data" in music_item:
            return music_item["original_data"]

        # 否則構造一個基本的音樂項
        converted = {
            "id": music_item.get("id", ""),
            "title": music_item.get("title", ""),
            "artist": music_item.get("artist", ""),
            "album": music_item.get("album", ""),
            "url": music_item.get("url", ""),
            "duration": music_item.get("duration", 0),
            "artwork": music_item.get("cover", ""),
            "lyric": music_item.get("lyric", ""),
            "quality": music_item.get("quality", ""),
        }

        return converted
