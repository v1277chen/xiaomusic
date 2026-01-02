// API 基礎配置
const API = {
    // 獲取音樂列表
    async getMusicList() {
        const response = await fetch('/musiclist');
        return response.json();
    },

    // 獲取多個音樂信息
    async getMusicInfos(songNames) {
        if (!Array.isArray(songNames)) {
            throw new Error('songNames must be an array');
        }

        const queryParams = songNames
            .map(name => `name=${encodeURIComponent(name)}`)
            .join('&');

        const response = await fetch(`/musicinfos?${queryParams}&musictag=true`);
        return response.json();
    },

    // 獲取音樂信息
    async getMusicInfo(songName) {
        const response = await fetch(`/musicinfo?name=${encodeURIComponent(songName)}&musictag=true`);
        return response.json();
    },

    // 獲取當前播放狀態
    async getPlayingStatus(did = 'web_device') {
        const response = await fetch(`/playingmusic?did=${did}`);
        const data = await response.json();
        localStorage.setItem('cur_music', data.cur_music);
        localStorage.setItem('cur_playlist', data.cur_playlist);
        return data;
    },

    // 播放歌單中的歌曲
    async playMusicFromList(did = 'web_device', listname, musicname) {
        const response = await fetch('/playmusiclist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ did, listname, musicname })
        });
        return response.json();
    },

    // 發送控制命令
    async sendCommand(did = 'web_device', cmd) {
        const response = await fetch('/cmd', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ did, cmd })
        });
        return response.json();
    },

    // 設置音量
    async setVolume(did = 'web_device', volume) {
        const response = await fetch('/setvolume', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ did, volume })
        });
        return response.json();
    },

    // 獲取音量
    async getVolume(did = 'web_device') {
        const response = await fetch(`/getvolume?did=${did}`);
        return response.json();
    },

    // 獲取設定
    async getSettings() {
        const response = await fetch('/getsetting');
        return response.json();
    },

    // 保存設定
    async saveSettings(settings) {
        const response = await fetch('/savesetting', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(settings)
        });
        return response.text();
    },

    // 獲取所有自定義歌單
    async getPlaylistNames() {
        const response = await fetch('/playlistnames');
        return response.json();
    },

    // 獲取歌單中的歌曲
    async getPlaylistMusics(name) {
        const response = await fetch(`/playlistmusics?name=${encodeURIComponent(name)}`);
        return response.json();
    },

    // 新增歌單
    async addPlaylist(name) {
        const response = await fetch('/playlistadd', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ name })
        });
        return response.json();
    },

    // 刪除歌單
    async deletePlaylist(name) {
        const response = await fetch('/playlistdel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ name })
        });
        return response.json();
    },

    // 修改歌單名稱
    async updatePlaylistName(oldName, newName) {
        const response = await fetch('/playlistupdatename', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ oldname: oldName, newname: newName })
        });
        return response.json();
    },

    // 歌單添加歌曲
    async addMusicToPlaylist(playlistName, musicList) {
        const response = await fetch('/playlistaddmusic', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ name: playlistName, music_list: musicList })
        });
        return response.json();
    },

    // 歌單刪除歌曲
    async removeMusicFromPlaylist(playlistName, musicList) {
        const response = await fetch('/playlistdelmusic', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ name: playlistName, music_list: musicList })
        });
        return response.json();
    },

    // 播放命令
    commands: {
        PLAY_PAUSE: '暫停播放',
        PLAY_CONTINUE: '繼續播放',
        PLAY_PREVIOUS: '上一首',
        PLAY_NEXT: '下一首',
        PLAY_MODE_SEQUENCE: '順序播放',
        PLAY_MODE_RANDOM: '隨機播放',
        PLAY_MODE_SINGLE: '單曲循環'
    }
};

// 導出 API 對象
window.API = API; 