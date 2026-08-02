# OmniVoice 客語 TTS 推論容器（SageMaker real-time endpoint）。
#
# 部署在 ml.g4dn.xlarge（T4）。基底選 CUDA 12.1：上游沒有釘死 torch 版本，
# 由 OmniVoice-hakka 的相依決定，而近期 torch wheel 以 cu121 為主。
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# 上游的預設分支是 master（不是 main），且沒有發任何版本 tag。要重現同一份推論行為
# 就以 commit SHA 覆寫這個值：build_and_push.sh --ref <sha>。
ARG OMNIVOICE_REF=master

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/opt/app \
    HF_HUB_OFFLINE=1

# ffmpeg 供 MP3 編碼；libsndfile 是 soundfile 的執行期依賴。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libsndfile1 \
        python3.10 \
        python3.10-dev \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel

# torch 必須是 cu121 的 wheel，這一段不能拿掉。
#
# 上游 pyproject 只宣告 `torch>=2.4`，真正的版本釘在 `[tool.uv] constraint-dependencies`
# 的 `torch==2.8.0`，並指定從 CUDA 12.8 的 index 取。那是 uv 才讀得到的欄位，pip 不認，
# 於是 pip 從 PyPI 解析到 torch 2.8.0 的預設 wheel——那是 cu128 build。
#
# 而 SageMaker g4dn 主機的驅動只到 CUDA 12.1，cu128 的 wheel 在上面會讓
# `torch.cuda.is_available()` 回 False。**它不會報錯**，只在啟動時印一行
# 「The NVIDIA driver on your system is too old (found version 12010)」，
# 然後 synthesizer.py 的 device 判斷就安靜地整個掉回 CPU + float32。
# 徵狀只有「慢 30 倍」：/ping 照過、音質正常、合成一句話從數秒變成三分鐘。
#
# 2.5.1 是 cu121 index 上的最後一版，同時滿足 omnivoice 與 transformers 的 torch>=2.4。
# 用 PIP_CONSTRAINT 讓後面每一次安裝在解析階段就認定它已滿足（PEP 440 的 `==2.5.1`
# 匹配帶 local version 的 `2.5.1+cu121`），而不是事後降版——降版會留下已依新版編譯的
# 其他套件。
RUN printf '%s\n' \
        "torch==2.5.1" \
        "torchaudio==2.5.1" \
    > /etc/pip-constraints.txt
ENV PIP_CONSTRAINT=/etc/pip-constraints.txt

RUN python3 -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu121 \
        --extra-index-url https://pypi.org/simple \
        torch==2.5.1+cu121 torchaudio==2.5.1+cu121

# 客語版本只發在 FormoSpeech 的 fork，PyPI 上的 omnivoice 不是同一個東西。
RUN python3 -m pip install --no-cache-dir \
    "git+https://github.com/FormoSpeech/OmniVoice-hakka.git@${OMNIVOICE_REF}"

COPY requirements-serving.txt /tmp/requirements-serving.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-serving.txt

# 建置期煙霧測試：把「只有上了 GPU 機器才會現形」的相依問題提前到 build 失敗。
#
# 建置機沒有 GPU，所以驗不到 `torch.cuda.is_available()`；但**驗得到 wheel 是為哪個
# CUDA 編的**，而那正是這個容器掉回 CPU 的原因。只要有任何一步把 torch 換成 PyPI 的
# 預設 wheel（cu128），這裡就會當場失敗，而不是等到上線後才發現慢了 30 倍。
RUN python3 -c "\
import torch; \
assert torch.version.cuda == '12.1', \
    f'torch was built for CUDA {torch.version.cuda}, expected 12.1 (see the PIP_CONSTRAINT note above)'; \
import torchaudio; \
assert torchaudio.__version__.startswith('2.5.1'), \
    f'torchaudio {torchaudio.__version__} does not match torch 2.5.1'"

WORKDIR /opt/app
COPY app/ /opt/app/app/
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

# SageMaker 以 8080 打 /ping 與 /invocations。
EXPOSE 8080
