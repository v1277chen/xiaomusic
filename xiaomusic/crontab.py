import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger

from xiaomusic.holiday import is_off_day, is_working_day


class CustomCronTrigger(BaseTrigger):
    """
    自定義觸發器
    擴展了標準 Cron 觸發器，支持 "workday" (工作日) 和 "offday" (休息日) 特殊標記
    格式示例： "0 8 * * * #workday" (僅在工作日的 8:00 觸發)
    """

    def __init__(self, cron_expression, holiday_checker=None):
        self.cron_expression = cron_expression
        self.holiday_checker = holiday_checker

        # 分離表達式和註釋
        expr_parts = cron_expression.split("#", 1)
        self.base_expression = expr_parts[0].strip()
        self.annotation = expr_parts[1].strip().lower() if len(expr_parts) > 1 else ""

        # 檢查註釋中是否包含特殊值
        self.check_workday = "workday" in self.annotation
        self.check_offday = "offday" in self.annotation

        # 構建基礎Cron觸發器
        try:
            self.base_trigger = CronTrigger.from_crontab(self.base_expression)
        except Exception as e:
            raise ValueError(f"無效的Cron表達式: {self.base_expression}") from e

    def get_next_fire_time(self, previous_fire_time, now):
        # 獲取基礎Cron表達式的下一個觸發時間
        next_time = self.base_trigger.get_next_fire_time(previous_fire_time, now)

        if not next_time:
            return None

        # 如果需要檢查工作日/休息日
        if self.check_workday or self.check_offday:
            year = next_time.year
            month = next_time.month
            day = next_time.day

            if self.check_workday:
                valid = is_working_day(year, month, day)
            else:  # check_offday
                valid = is_off_day(year, month, day)

            # 如果日期有效，返回時間；否則尋找下一個有效時間 (遞歸調用)
            if valid:
                return next_time
            else:
                return self.get_next_fire_time(next_time, next_time)

        return next_time


class Crontab:
    """
    定時任務管理器
    基於 AsyncIOScheduler，負責管理系統中的所有定時任務 (播放、停止、刷新列表等)
    """
    def __init__(self, log):
        self.log = log
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.start()

    def add_job(self, expression, job):
        """
        添加通用任務
        :param expression: cron 表達式
        :param job: 異步任務函數
        """
        try:
            # 檢查表達式中是否包含註釋標記
            if "#" in expression and (
                "workday" in expression.lower() or "offday" in expression.lower()
            ):
                trigger = CustomCronTrigger(expression)
            else:
                trigger = CronTrigger.from_crontab(expression)

            self.scheduler.add_job(job, trigger)
        except ValueError as e:
            self.log.error(f"Invalid crontab expression {e}")
        except Exception as e:
            self.log.exception(f"Exception {e}")

    # 添加關機任務
    def add_job_stop(self, expression, xiaomusic, did, **kwargs):
        async def job():
            await xiaomusic.stop(did, "notts")

        self.add_job(expression, job)

    # 添加播放任務
    def add_job_play(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.play(did, arg1)

        self.add_job(expression, job)

    # 添加播放列表任務
    def add_job_play_music_list(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.play_music_list(did, arg1)

        self.add_job(expression, job)

    # 添加語音播放 TTS 任務
    def add_job_tts(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.do_tts(did, arg1)

        self.add_job(expression, job)

    # 刷新本地音樂列表任務
    def add_job_refresh_music_list(self, expression, xiaomusic, **kwargs):
        async def job():
            await xiaomusic.gen_music_list()

        self.add_job(expression, job)

    # 設置音量任務
    def add_job_set_volume(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.set_volume(did, arg1)

        self.add_job(expression, job)

    # 設置播放類型任務
    def add_job_set_play_type(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            play_type = int(arg1)
            await xiaomusic.set_play_type(did, play_type, False)

        self.add_job(expression, job)

    # 開啟或關閉獲取對話記錄 (輪詢開關)
    def add_job_set_pull_ask(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            if arg1 == "enable":
                xiaomusic.config.enable_pull_ask = True
            else:
                xiaomusic.config.enable_pull_ask = False

        self.add_job(expression, job)

    # 更新網絡歌單任務
    def add_job_refresh_web_music_list(self, expression, xiaomusic, **kwargs):
        async def job():
            await xiaomusic.refresh_web_music_list()
            await xiaomusic.gen_music_list()

        self.add_job(expression, job)

    # 重新初始化
    def add_job_reinit(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            xiaomusic.reinit()

        self.add_job(expression, job)

    def add_job_cron(self, xiaomusic, cron):
        expression = cron["expression"]  # cron 計劃格式
        name = cron["name"]  # stop, play, play_music_list, tts
        did = cron.get("did", "")
        arg1 = cron.get("arg1", "")
        jobname = f"add_job_{name}"
        func = getattr(self, jobname, None)
        if callable(func):
            func(expression, xiaomusic, did=did, arg1=arg1)
            self.log.info(
                f"crontab add_job_cron ok. did:{did}, name:{name}, arg1:{arg1} expression:{expression}"
            )
        else:
            self.log.error(
                f"'{self.__class__.__name__}' object has no attribute '{jobname}'"
            )

    # 清空任務
    def clear_jobs(self):
        for job in self.scheduler.get_jobs():
            try:
                job.remove()
            except Exception as e:
                self.log.exception(f"Execption {e}")

    # 重新加載計劃任務
    def reload_config(self, xiaomusic):
        self.clear_jobs()

        crontab_json = xiaomusic.config.crontab_json
        if not crontab_json:
            return

        try:
            cron_list = json.loads(crontab_json)
            for cron in cron_list:
                self.add_job_cron(xiaomusic, cron)
            self.log.info("crontab reload_config ok")
        except Exception as e:
            self.log.exception(f"Execption {e}")
