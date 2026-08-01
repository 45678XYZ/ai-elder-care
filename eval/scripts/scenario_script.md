# 智慧長照陪伴 App — 全情境對話腳本與系統運作規範

> **專案簡介**：本專案為整合「長者 AI 語音陪伴」與「照護者數據管理」之單一 App 平台。三大核心模組：**A 語音互動陪伴、B 生活記錄與智慧摘要、C 照護者資訊介面**（依登入角色切換模式）。
> **架構技術**：Flutter App / Python Lambda / AWS Bedrock Agent / SageMaker ASR & TTS / DynamoDB / EventBridge

---

## 一、專案簡介與角色設定 (Overview & Personas)

| 角色分類 | 代表人物 | 特徵與情境說明 | App 對應模組 |
| :--- | :--- | :--- | :--- |
| **長者 (Elder)** | **林陳奶奶** (82歲) | 輕度認知障礙與高血壓，獨居，平日喜歡聊往事與種花，時常忘記服藥與飲水。 | **模組 A** (語音陪伴)<br>**模組 B** (生活記錄擷取) |
| **照護者/家屬 (Caregiver)** | **林先生** (45歲，兒子) | 上班族，無法全天陪同，需透過 App 遠端查看母親每日情緒波動、用藥與飲食摘要。 | **模組 C** (照護資訊儀表板) |
| **AI 陪伴助手** | **小安** (Care AI Agent) | 由 AWS Bedrock Agent 與雙向廣東話/台語/國語 ASR/TTS 驅動，親切溫和、具引導性。 | **AgentCore Runtime**<br>SageMaker ASR / TTS |

---

## 二、模組全景與情境腳本設計 (Scenarios)

### 📍 情境一：晨間主動關懷與健康狀況問候 (Morning Wellness Check-in)
> **核心技術**：EventBridge 定時觸發 → Bedrock Agent 主動關懷 → SageMaker TTS → 語音串流 → ASR 意圖辨識

* **🤖 小安 (AI 陪伴語音)**：  
  > 「陳奶奶，早安！今天陽光真好。昨晚睡得好嗎？腳關節還有沒有像昨天那樣痠痛呢？」  
  > *(💡 **TTS 技術備註**：採用溫暖女聲廣播語調，嵌入長者記憶庫 Bedrock KB：對應昨日「腳關節痠痛」紀錄)*

* **👵 林陳奶奶 (長者)**：  
  > 「早啊小安……昨晚有比較好睡啦，不過剛起身覺得頭有點暈暈的，沒什麼胃口吃早餐。」  
  > *(💡 **ASR 技術備註**：Formo ASR 轉錄文字 ➔ feature_segmenter 斷句分區 ➔ 識別健康隱患：頭暈、食慾不振)*

* **🤖 小安 (AI 陪伴語音)**：  
  > 「頭暈暈的呀？奶奶您現在先慢一點站起來，先在床邊坐坐。請問今天早上量血壓了嗎？小安幫您記錄一下好嗎？」

* **👵 林陳奶奶 (長者)**：  
  > 「量過了，剛才收縮壓是 148，舒張壓 92。」

```json
// [SYSTEM LOG - feature_events-extraction]
{
  "extracted_event": {
    "category": "health_vital",
    "systolic": 148,
    "diastolic": 92,
    "symptoms": ["dizziness", "poor_appetite"]
  },
  "status": "SLIGHT_ELEVATED", // 高於基準值
  "action": "自動更新 DynamoDB 生命徵象資料表"
}
```

* **🤖 小安 (AI 陪伴語音)**：  
  > 「收縮壓 148 有一點點偏高喔！奶奶請先喝杯溫開水，等一下 8:30 記得要吃降血壓藥。我已經幫您登記好血壓值囉！」

---

### 📍 情境二：用藥提醒與日常生活事件萃取 (Medication & Daily Event Extraction)
> **核心技術**：模組 B 生活記錄萃取 pipeline (`extraction/` pipeline) → 結構化事件寫入 DynamoDB

* **🤖 小安 (AI 陪伴語音)**：  
  > 「奶奶，8 點半到了！藥盒裡的黃色降血壓藥跟白色胃藥要記得配溫開水吃喔。您吃了嗎？」

* **👵 林陳奶奶 (長者)**：  
  > 「剛才拿水吞下去了！對了，我隔壁李阿姨剛才送了她自己種的芭樂過來，我吃半顆，挺甜的。」

```text
[SYSTEM LOG - pairwise-v2 & extraction pipeline]
• segment_1: [Medication] "吃了黃色降血壓藥與白色胃藥" -> confirmed=True
• segment_2: [Social/Diet] "鄰居李阿姨拜訪，贈送芭樂，吃了半顆" -> category="diet_social"
```

* **🤖 小安 (AI 陪伴語音)**：  
  > 「太棒了，有按時吃藥！李阿姨真熱心，吃點新鮮芭樂能補充維生素 C 喔！不過芭樂比較有咬勁，奶奶要細嚼慢嚥喔。」

---

### 📍 情境三：情緒舒緩與懷舊陪伴對話 (Emotional Support & Reminiscing)
> **核心技術**：模組 A 語音互動陪伴 → Bedrock Agent 語意情懷引導 + 懷舊衛教知識庫 (KB)

* **👵 林陳奶奶 (長者)**：  
  > 「唉……兒子這週說工作忙不回來了。一個人待在客廳，看著以前跟老伴去阿里山的照片，心裡空落落的……」  
  > *(💡 **Emotion Detection**：偵測到孤獨感與感傷情緒 Sadness/Loneliness 78%)*

* **🤖 小安 (AI 陪伴語音)**：  
  > 「奶奶，兒子沒能回來確實會讓人有點失望呢。不過他常跟我交代一定要好好照顧您喔！您剛才提到阿里山的照片，那是您跟爺爺去坐小火車看日出那次嗎？可以跟我講講那時的有趣事情嗎？」  
  > *(💡 **Prompt Policy**：使用懷舊治療 Reminiscence Therapy 引導長者回憶愉悅經驗，撫平孤獨感)*

* **👵 林陳奶奶 (長者)**：  
  > 「對啊！那時候火車開得很慢，路上好多櫻花盛開，他還幫我拍了一張抱著木雕的照片，那時候我們都還好年輕呢……」

* **🤖 小安 (AI 陪伴語音)**：  
  > 「聽起來真美好！櫻花樹下的照片一定很美。奶奶，我把這段美好的回憶記在您的專屬日誌裡，晚上也分享給志明（兒子）看，他一定會很開心聽到您的回憶故事。」

---

### 📍 情境四：傍晚生活摘要生成與照護者介面檢視 (Evening Summary & Caregiver View)
> **核心技術**：模組 B 自動生成 Daily Executive Summary → 模組 C 照護者 App 角色切換儀表板

```yaml
# [SUMMARY GENERATION - Bedrock AgentCore] (EventBridge Evening Pipeline 18:00)
target_user: "user_elder_0823"
daily_summary: "今日輕微頭暈，血壓 148/92 mmHg 略高；已按時服用降血壓藥與胃藥；鄰居拜訪互動良好；午後出現孤獨情緒，經阿里山旅遊懷舊話題引導後情緒大幅轉為平穩愉悅。"
risk_level: "LOW_MEDIUM" # 需關注血壓變化
```

* **👨‍💼 林先生 (照護者 - 切換模組 C 介面)**：  
  > *(打開 App 照護者模式，收到推播「媽媽今日生活與健康日誌已更新」)*  
  > 「查看儀表板：媽媽今天早上血壓 148 有點偏高，不過有按時吃藥。下午小安陪她聊了阿里山賞櫻的事，情緒安定。那我今晚撥個視訊電話陪她聊天好了！」

---

### 📍 情境五：健康異常警示與緊急推播處置 (Abnormal Alert & Immediate Escalation)
> **核心技術**：關鍵字與生理指標異常突發 → WebSocket / SNS 緊急推播至照護者 App

* **👵 林陳奶奶 (長者)**：  
  > 「小安……我胸口突然好悶、好痛，呼吸有點喘不過來……」

```json
// [HIGH PRIORITY ALERT DETECTED]
{
  "trigger_keyword": ["胸口悶", "胸口痛", "呼吸喘"],
  "severity": "CRITICAL",
  "action": "立即觸發 SNS 簡訊 + App 警報高分貝推播至照護者林先生手機，同時語音引導長者保持平躺。"
}
```

* **🤖 小安 (AI 陪伴語音)**：  
  > 「奶奶，請您現在馬上坐下或平躺，不要緊張，深呼吸。我已經立刻通知志明先生與緊急照護人員，幫您連線支援，請保持呼吸！」

* **👨‍💼 林先生 (照護者 App 警告視窗)**：  
  > 🚨 **【警報聲響起】** App 彈出紅字閃爍提示：「警告：母親回報胸悶胸痛！請立即確認！`[一鍵撥打 119]` `[一鍵連線家中有線攝影機]`」

---

## 三、系統互動流轉矩陣 (System Flow Matrix)

| 情境步驟 | 輸入端 (Input) | 後端處理模組 (Backend & AI Model) | 輸出端 (Output / Display) |
| :--- | :--- | :--- | :--- |
| **1. 語音輸入** | 長者台國語語音 PCM 串流 | Formo / SageMaker ASR (語音轉文字) | 即時逐字稿 (WebSocket Response) |
| **2. 意圖與情緒分析** | 文字串流 + 歷史上下文 | AWS Bedrock Agent (Claude 3.5 / AgentCore) | 情緒標籤、對話回應策略、對話分塊 |
| **3. 事件萃取** | 長者對話文字 | `backend/extraction/` pipeline (LLM Key-Value) | 結構化 JSON 存入 DynamoDB (用藥/血壓/飲食) |
| **4. 語音輸出** | Agent 生成對話文本 | SageMaker TTS (自然情感語調生成) | 長者 App 高音質陪伴語音播放 |
| **5. 照護者端同步** | DynamoDB 事件與摘要更新 | App Caregiver Mode (API Gateway + Cognito Auth) | 每日卡片摘要、血壓趨勢圖、緊急警報推播 |
