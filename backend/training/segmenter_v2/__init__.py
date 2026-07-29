"""pairwise_v2 對話分塊模型的離線工作流。

這個套件只做離線的事：語料整理、embedding 快取、特徵抽取、訓練、導出 artifact、held-out 評測。
執行期推論在 `src/extraction/segmenter.py`，兩邊共用同一份 feature spec（見 `contract.py`），
避免訓練與推論的特徵定義漂移——那是舊版 pairwise 模型失效的主因之一。

不隨 Lambda 部署包出貨：`pyproject.toml` 的 `packages.find` 只收 `src*`，`training/` 只在本機
與 CI 執行。訓練依賴 `pip install -e ".[training]"`。

工作流順序與指示見 `docs/feature_segmenter-pairwise-v2.md`。
"""
