#!/usr/bin/env python3
import argparse
import json
import logging
import os
import signal

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

LOGO = r"""
 __  __  _                   __  __                 _
 \ \/ / (_)   __ _    ___   |  \/  |  _   _   ___  (_)   ___
  \  /  | |  / _` |  / _ \  | |\/| | | | | | / __| | |  / __|
  /  \  | | | (_| | | (_) | | |  | | | |_| | \__ \ | | | (__
 /_/\_\ |_|  \__,_|  \___/  |_|  |_|  \__,_| |___/ |_|  \___|
          {}
"""


sentry_sdk.init(
    # dsn="https://659690a901a37237df8097a9eb95e60f@github.hanxi.cc/sentry/4508470200434688",
    dsn="https://ffe4962642d04b29afe62ebd1a065231@glitchtip.hanxi.cc/1",
    integrations=[
        AsyncioIntegration(),
        LoggingIntegration(
            level=logging.WARNING,
            event_level=logging.ERROR,
        ),
    ],
    # debug=True,
)
ignore_logger("miservice")


def main():
    import uvicorn

    from xiaomusic import __version__
    from xiaomusic.config import Config
    from xiaomusic.httpserver import HttpInit
    from xiaomusic.httpserver import socketio_app as HttpApp
    from xiaomusic.xiaomusic import XiaoMusic

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        dest="port",
        help="监听端口",
    )
    parser.add_argument(
        "--hardware",
        dest="hardware",
        help="小爱音箱型号",
    )
    parser.add_argument(
        "--account",
        dest="account",
        help="xiaomi account",
    )
    parser.add_argument(
        "--password",
        dest="password",
        help="xiaomi password",
    )
    parser.add_argument(
        "--cookie",
        dest="cookie",
        help="xiaomi cookie",
    )
    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=None,
        help="show info",
    )
    parser.add_argument(
        "--config",
        dest="config",
        help="config file path",
    )
    parser.add_argument(
        "--ffmpeg_location",
        dest="ffmpeg_location",
        help="ffmpeg bin path",
    )
    parser.add_argument(
        "--enable_config_example",
        dest="enable_config_example",
        help="是否输出示例配置文件",
        action="store_true",
    )

    print(LOGO.format(f"XiaoMusic v{__version__} by: github.com/hanxi"))

    options = parser.parse_args()
    config = Config.from_options(options)

    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": f"%(asctime)s [{__version__}] [%(levelname)s] %(message)s",
                "datefmt": "[%X]",
                "use_colors": False,
            },
            "access": {
                "format": f"%(asctime)s [{__version__}] [%(levelname)s] %(message)s",
                "datefmt": "[%X]",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "level": "INFO",
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "access",
                "filename": config.log_file,
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 1,
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": [
                    "default",
                    "file",
    # 设置默认音乐目录和配置目录
    music_path = os.environ.get("XIAOMUSIC_MUSIC_PATH", "music")
    conf_path = os.environ.get("XIAOMUSIC_CONF_PATH", ".")
    
    # 初始化配置对象
    config_obj = Config()
    config_obj.port = port
    config_obj.config_file = config
    config_obj.public_port = public_port
    config_obj.verbose = verbose
    config_obj.music_path = music_path
    config_obj.conf_path = conf_path

    # 从文件加载配置，如果文件存在且有效
    try:
        config_obj.read_from_file(config_file=config)
    except Exception as e:
        print(f"Error loading config from file {config}: {e}")
        # Continue with default/CLI provided config if file loading fails

    # 创建并运行 XiaoMusic 实例
    xiaomusic = XiaoMusic(config_obj)
    
    # 启动 HTTP 服务
    try:
        HttpInit(xiaomusic)
        uvicorn.run(
            app=app,
            host="0.0.0.0",
            port=port,
            log_level="info" if not verbose else "debug",
        )
    except Exception as e:
        print(f"Failed to start xiaomusic: {e}")
        os._exit(0)  # 退出主进程

    # 捕获主进程的退出信号
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    run_server(port)


if __name__ == "__main__":
    main()
