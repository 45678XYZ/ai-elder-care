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

# 傳遞相依的版本上界集中在這裡。上游 requirements.txt 把直接相依都釘死了，卻沒有釘住
# 下面這幾個由它們帶進來的套件，於是 pip 會解析到與釘住的 torch 2.3.1 不相容的最新版：
#
# - setuptools：openai-whisper 等仍走舊式 setup.py 建 wheel，需要 pkg_resources，
#   而 setuptools 81 起已移除它。
# - ruamel.yaml：HyperPyYAML 1.2.2 宣告 `ruamel.yaml>=0.17.28` 而沒有上界。0.18 改了
#   Composer 的內部介面，載入模型 hyperparams 時會拋
#   `AttributeError: 'Loader' object has no attribute 'max_depth'`。
# - transformers：新版要求 torch>=2.4，裝上去不會報錯，而是印一行
#   「Disabling PyTorch because PyTorch >= 2.4 is required」然後停用整個 PyTorch 後端。
#
# 用 PIP_CONSTRAINT 讓每一次安裝在解析階段就避開這些版本，而不是事後再降版——降版會留下
# 已經依新版編譯的其他套件。
RUN printf '%s\n' \
        "setuptools<81" \
        "ruamel.yaml<0.18" \
        "transformers<4.45" \
    > /etc/pip-constraints.txt
ENV PIP_CONSTRAINT=/etc/pip-constraints.txt

RUN python3 -m pip install --no-cache-dir --upgrade pip wheel \
    && python3 -m pip install --no-cache-dir "setuptools<81"

# CosyVoice 的 Matcha-TTS 以 submodule 掛入，少了它 frontend 會 import 失敗。
RUN git clone --recursive https://github.com/mtkresearch/BreezyVoice.git ${BREEZYVOICE_REPO} \
    && cd ${BREEZYVOICE_REPO} \
    && git checkout ${BREEZYVOICE_REF} \
    && git submodule update --init --recursive

# onnxruntime 的 CPU／GPU 版本之爭在所有 pip 安裝結束之後才收尾（見下方那一段）。
# 這裡不處理：此刻移掉，後面的 requirements.txt 會原封不動再裝回來。

# torch 與 torchaudio 必須來自同一個 CUDA build，否則 import torchaudio 會直接拋
# RuntimeError。上游 requirements.txt 是 `--extra-index-url .../cu118` 搭配不帶 local
# version 的 `torch==2.3.1`：torch 只要先被別的套件裝過，pip 就判定該行已滿足而跳過，
# 把 PyPI 的 cu121 wheel 留在原地，torchaudio 卻是全新解析、選到 2.3.1+cu118——
# 兩邊 CUDA 版本就此分岔。因此改在這裡用帶 local version 的釘法從 cu118 index 一次裝定，
# 後續的 openai-whisper 與 requirements.txt 都會視為已滿足而不再更動它。
RUN python3 -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu118 \
        --extra-index-url https://pypi.org/simple \
        torch==2.3.1+cu118 torchaudio==2.3.1+cu118

# openai-whisper==20231117 的 setup.py 直接 import pkg_resources。PEP 517 會在隔離環境
# 裡重裝 setuptools（81+，已無 pkg_resources），而實測 PIP_CONSTRAINT 並不會傳進該環境，
# 所以只能對這個套件關閉建置隔離，改用映像層已裝好的 setuptools<81。
RUN python3 -m pip install --no-cache-dir --no-build-isolation openai-whisper==20231117
RUN python3 -m pip install --no-cache-dir -r ${BREEZYVOICE_REPO}/requirements.txt

COPY requirements-serving.txt /tmp/requirements-serving.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-serving.txt

# onnxruntime 必須是 GPU 版，而且這一步一定要放在所有安裝的最後面。
#
# 上游 requirements.txt 有釘 `onnxruntime-gpu==1.16.0`，但同一份裡的 `g2pw` 沒有釘版本，
# 而 g2pw 的相依會把 PyPI 上的 `onnxruntime`（CPU 版）一起帶進來。兩個套件散出同一個
# `onnxruntime` 模組目錄，**後裝的覆蓋先裝的**，於是實際被 import 的是 CPU 版。
#
# 徵狀只有啟動時一行 warning：
#   Specified provider 'CUDAExecutionProvider' is not in available provider names.
#   Available providers: 'AzureExecutionProvider, CPUExecutionProvider'
# 而 CosyVoice 的 speech tokenizer 與聲紋比對都走 onnx，那兩段就整段在 CPU 上跑——
# GPU 使用率平均只有兩三成，合成一句話要一百多秒，但輸出完全正常。
#
# 1.16.0 是上游釘的版本，對應 CUDA 11.8 + cuDNN 8，與本映像的基底一致。
RUN python3 -m pip uninstall -y onnxruntime onnxruntime-gpu \
    && python3 -m pip install --no-cache-dir "onnxruntime-gpu==1.16.0"

# 建置期煙霧測試：把「只有在 endpoint 啟動時才會炸」的相依問題提前到 build 失敗。
# 這裡的每一項都對應一次實際發生過的 ping health check 失敗，不是預防性的檢查：
#
# 1. import torchaudio — torch 與 torchaudio 的 CUDA build 不一致會在此拋 RuntimeError。
# 2. is_torch_available() — transformers 太新時不會報錯，只會安靜地停用 PyTorch 後端，
#    等到推論才發現模型根本沒載入。
# 3. load_hyperpyyaml — ruamel.yaml 0.18+ 會在解析模型 hyperparams 時炸；用一份最小
#    的 YAML 就能走到同一條程式路徑，不需要模型權重。
# 4. torch.version.cuda 與 onnxruntime 的套件名 — 這兩項驗的是「有沒有掉回 CPU」。
#    建置機沒有 GPU，驗不到 `torch.cuda.is_available()`，但驗得到 wheel 是為哪個 CUDA
#    編的、以及散出 `onnxruntime` 模組的究竟是哪個套件。安靜掉回 CPU 的代價是慢 30 倍
#    而輸出完全正常，光看 /ping 與音檔都察覺不到，所以要在這裡擋。
#
# 模型權重來自 model.tar.gz、build 時不存在，因此這裡驗不到載入模型本身；那一段要靠
# scripts/verify_container.sh 在 GPU 機器上實際起容器打 /ping 才驗得到。
RUN python3 -c "\
import io; \
from importlib.metadata import distributions; \
import torch; \
import torchaudio; \
from cosyvoice.utils.file_utils import load_wav; \
import transformers; \
assert transformers.utils.is_torch_available(), 'transformers disabled the torch backend'; \
from hyperpyyaml import load_hyperpyyaml; \
assert load_hyperpyyaml(io.StringIO('probe: 1'))['probe'] == 1; \
assert torch.version.cuda == '11.8', f'torch was built for CUDA {torch.version.cuda}, expected 11.8'; \
installed = {(d.metadata['Name'] or '').lower() for d in distributions()}; \
assert 'onnxruntime-gpu' in installed, 'onnxruntime-gpu is missing'; \
assert 'onnxruntime' not in installed, 'the cpu onnxruntime is still installed and shadows the gpu build'"

WORKDIR /opt/app

# g2pW 首次使用會上網抓字典，且預設以「相對於工作目錄」的 G2PWModel/ 尋找。因此這一步
# 必須在 WORKDIR 設定之後執行，否則字典會落在建置當下的目錄，執行期在 /opt/app 找不到
# 而重新下載——endpoint 若沒有對外網路就會直接啟動失敗。
#
# 只呼叫 `download_model`，不建 `G2PWConverter`：後者在下載完之後會接著開一個
# InferenceSession，而 g2pw 0.1.1 開 session 時完全不帶 providers，換上
# onnxruntime-gpu 之後必定拋 ValueError（詳見 app/synthesizer.py 的
# `_onnx_default_providers`）。字典本身在那之前就已經下載完成了，這裡只要它。
RUN python3 -c "\
import os; \
from g2pw.api import download_model; \
download_model('G2PWModel/'); \
assert os.path.exists(os.path.join('G2PWModel', 'version')), 'g2pW dictionary was not downloaded'"

COPY app/ /opt/app/app/
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

# SageMaker 以 8080 打 /ping 與 /invocations。
EXPOSE 8080
