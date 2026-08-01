# BreezyVoice 推論容器（SageMaker real-time endpoint）。
#
# 版本組合不是隨意挑的：BreezyVoice 的 ttsfrd 只有 cp310 的 linux_x86_64 wheel，
# 且 requirements.txt 釘死 torch 2.3.1+cu118，因此基底鎖在 CUDA 11.8 + Ubuntu 22.04
# （內建 Python 3.10）。換基底前請先確認這三者仍然相容。
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

# 上游沒有發版本 tag，以 commit 釘住才能重現同一份推論行為。
ARG BREEZYVOICE_REF=main

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    BREEZYVOICE_REPO=/opt/breezyvoice \
    PYTHONPATH=/opt/app:/opt/breezyvoice \
    HF_HUB_OFFLINE=1

# ffmpeg 供 MP3 編碼；sox 與 libsndfile 是 torchaudio/soundfile 的執行期依賴。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libsndfile1 \
        python3.10 \
        python3.10-dev \
        python3-pip \
        sox \
        libsox-dev \
    && rm -rf /var/lib/apt/lists/*

# setuptools 釘在 80.x：openai-whisper 等相依仍走舊式 setup.py 建 wheel，需要
# pkg_resources，而 setuptools 81 起已把它移除。
#
# 只在映像層裝舊版不夠：PEP 517 建置 wheel 時會開一個隔離環境並在裡面重裝
# setuptools，映像層的版本管不到。PIP_CONSTRAINT 是唯一能把限制帶進該隔離環境的
# 途徑，因此下面安裝相依時必須帶上它。
RUN python3 -m pip install --no-cache-dir --upgrade pip wheel \
    && python3 -m pip install --no-cache-dir "setuptools<81" \
    && echo "setuptools<81" > /etc/pip-constraints.txt

# CosyVoice 的 Matcha-TTS 以 submodule 掛入，少了它 frontend 會 import 失敗。
RUN git clone --recursive https://github.com/mtkresearch/BreezyVoice.git ${BREEZYVOICE_REPO} \
    && cd ${BREEZYVOICE_REPO} \
    && git checkout ${BREEZYVOICE_REF} \
    && git submodule update --init --recursive

# 依上游 README：先移除可能被相依套件帶進來的 CPU 版 onnxruntime，再裝 GPU 版。
RUN python3 -m pip uninstall -y onnxruntime || true
# openai-whisper==20231117 的 setup.py 直接 import pkg_resources。PEP 517 會在隔離環境
# 裡重裝 setuptools（81+，已無 pkg_resources），而實測 PIP_CONSTRAINT 並不會傳進該環境，
# 所以只能對這個套件關閉建置隔離，改用映像層already裝好的 setuptools<81。
# 先裝它（含相依）再裝 requirements.txt，讓後者把 torch 等版本收斂回釘住的值。
RUN python3 -m pip install --no-cache-dir --no-build-isolation openai-whisper==20231117
RUN python3 -m pip install --no-cache-dir -r ${BREEZYVOICE_REPO}/requirements.txt

COPY requirements-serving.txt /tmp/requirements-serving.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-serving.txt

WORKDIR /opt/app

# g2pW 首次使用會上網抓字典，且預設以「相對於工作目錄」的 G2PWModel/ 尋找。因此這一步
# 必須在 WORKDIR 設定之後執行，否則字典會落在建置當下的目錄，執行期在 /opt/app 找不到
# 而重新下載——endpoint 若沒有對外網路就會直接啟動失敗。
RUN HF_HUB_OFFLINE=0 python3 -c "\
from g2pw import G2PWConverter; \
G2PWConverter(style='pinyin', enable_non_tradional_chinese=True)"

COPY app/ /opt/app/app/
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

# SageMaker 以 8080 打 /ping 與 /invocations。
EXPOSE 8080
