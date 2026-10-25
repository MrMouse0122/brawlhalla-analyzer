#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_window.py：独立的 AI 聊天窗口（pywebview + WebView2/Edge 内核）。

打包为 ChatWindow.exe，由主程序（BrawlhallaAnalyzerV2）按需启动——
进程隔离，pywebview 独占本进程主线程（其硬性要求），用户看到的是
软件自带的独立窗口（非系统浏览器）。

用法：ChatWindow.exe [port] [问题]   （默认 8903；问题将预填到输入框）
"""
import sys
import urllib.parse
import webview

PORT = sys.argv[1] if len(sys.argv) > 1 else "8903"
Q = sys.argv[2] if len(sys.argv) > 2 else ""


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if Q:
        url += "?q=" + urllib.parse.quote(Q)
    webview.create_window(
        "Brawlhalla AI 教练",
        url,
        width=1080, height=800, min_size=(720, 520),
        background_color="#0a0a0a")
    webview.start()


if __name__ == "__main__":
    main()
