"""CLI 与 Web 共用的参数校验器,保证两个入口的规则永不漂移。"""

from __future__ import annotations

import argparse
import os
import re


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是正整数") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def serial_port(value: str) -> str:
    # 与设备 Adapter 相同的平台模式,避免把任意设备路径传给 idf.py。
    pattern = r"COM[1-9]\d*" if os.name == "nt" else r"/dev/tty(?:USB|ACM|S)\d+"
    if not re.fullmatch(pattern, value):
        raise argparse.ArgumentTypeError(
            "串口名必须是 COM3 之类的合法串口名"
        )
    return value


def target_chip(value: str) -> str:
    # 芯片名只接受小写标识符,杜绝任何命令选项注入。
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise argparse.ArgumentTypeError(
            "目标芯片必须是 esp32、esp32s3 之类的小写标识符"
        )
    return value
