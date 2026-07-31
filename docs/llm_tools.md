# Bedrock Agent ?ºæ…§å·¥å…·ç®?(Action Groups / Tools) è¦æ ¼èªªæ???

?¬æ?ä»¶å?ç¾©ä? **Amazon Bedrock Agent (Claude 3.5)** ?ªå?èª¿ç”¨?„å?ç«¯å·¥?·ï?Action Groups / Toolsï¼‰ã€‚ç•¶?·è€…åœ¨èªéŸ³å°è©±ä¸­æ??°è??Œä?è¡Œå…¬äº‹ï??¨è—¥?é?è¡€å£“ç?ï¼‰ã€æ??Œæ–°å»ºè?ç¨‹ï?ç´„æ??ç??«ç?ï¼‰ã€ç›¸?œç??å??‚ï?Agent ?ƒè‡ª?•é¸?‡ä¸¦?¼å«å°æ??„å·¥?·ã€?

?€?‰å·¥?·ç??·è??è¼¯?‡é€é?ä¸€?‹å…±?¨ç? **Tools Lambda** (?–ç›´?¥åœ¨ `chat` å°ˆæ?ä¸­èª¿?? ?²è?ï¼Œä¸¦?´æ¥è®€å¯?DynamoDB ??`routines` ??`events` è¡¨ã€?

---

## 1. å·¥å…·æ¸…å–®??LLM èª¿ç”¨å¥‘æ?

| å·¥å…·?ç¨± (Tool Name) | ?Ÿèƒ½?è¿° (Description for LLM) | èª¿ç”¨å¥‘æ? (Triggering Intent) |
|---|---|---|
| `get_today_routines` | ?–å??·è€…æ?å®šæ—¥?Ÿç?ä¾‹è?è¡Œç??‡å??ç??‹ã€?| ?·è€…å?ï¼šã€Œæ?ä»Šå¤©?„è??ƒä?éº¼è—¥ï¼Ÿã€æ? AI ?€è¦ä¸»?•é??·ä??¥è?ç¨‹æ???|
| `complete_routine` | å°‡ç‰¹å®šè?ç¨‹æ?è¨˜ç‚ºå·²å??ï?ä¸¦è??„ç?æ´»ä?ä»¶ã€?| ?·è€…èªªï¼šã€Œæ??ƒé?è¡€å£“è—¥äº†ã€æ??Œæ??›é?å®Œè?ç³–ä??ã€?|
| `create_routine` | å¹«é•·?…å»ºç«‹ä??‹æ–°?„ä?è¡Œè?ç¨‹æ??®æ¬¡?é???| ?·è€…èªªï¼šã€Œå¹«?‘è?ä¸‹é€±ä??©ä?ä¹é?è¦ç??«ç??æ??Œæ??å¤©ä¸‹å?è¦æ•£æ­¥ã€ã€?|
| `get_recent_events` | ?¥è©¢?·è€…è??Ÿç??Ÿæ´»äº‹ä»¶?‡å¥åº·è??„æ­·?²ã€?| ?·è€…å?ï¼šã€Œæ??™é€±æ?æ»‘å€’é??ï??æ??Œæ??¨å¤©?šé??ƒä?ä»€éº¼ï??ã€?|
| `get_elder_profile` | ?¥è©¢?·è€…ç??‹äºº?±ç¨±?å?å¥½å?å¥½ã€å¥åº·æ³¨?ä??…è?å®¶å±¬?å“¡??| ?·è€…å?ï¼šã€Œä??¥é??‘å¥³?’å«ä»€éº¼å?å­—å?ï¼Ÿã€æ? AI ä¸»å??²è?è¦ªå?å°è©±?‚ã€?|
| `remind_pending_routines` | ?¥è©¢?·è€…ä??¥å??ªå??ç?å¾…è¾¦è¡Œç?ä¸¦å??³æ??’ä??…ã€?| ?·è€…å?ï¼šã€Œæ??„æ?ä»€éº¼ä??…æ??šå?ï¼Ÿã€æ? AI ?€è¦ä¸»?•é€²è?è¡Œç??é??‚ã€?|
| `notify_caregiver` | ?¼é€?AWS SNS ?³æ?ç·Šæ€¥è­¦?±ã€ä?è¡Œè?ç¨‹å ±?Šæ??¥åº·?˜è??³ç…§è­·è€…ã€?| ?·è€…å?? è??’ã€èƒ¸?›ã€é ­?ˆç?ç·Šæ€¥ç?æ³ï??–é??¨æ’­?¥å ±?‚ã€?|

---

## 2. ?„å·¥?·è??¼è??ƒæ•¸?¶æ? (JSON Schema)

### 2.7 `notify_caregiver` (?¼é€ç…§è­·è€…é€šçŸ¥)
*   **LLM ?è¿°**ï¼š`Send immediate SNS alert to the caregiver when the elder experiences emergencies (falls, chest pain, dizziness) or needs routine/summary reports.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        },
        "category": {
          "type": "string",
          "enum": ["emergency", "routine", "summary"],
          "description": "?šçŸ¥é¡åˆ¥ï¼šemergency (è·Œå€’ä??©ç??¥è­¦??, routine (è¡Œç?å®Œæ??€??, summary (æ¯æ—¥?¥åº·?˜è?)"
        },
        "message": {
          "type": "string",
          "description": "è¦æ¨?­çµ¦?§è­·?…ç?è©³ç´°è¨Šæ¯?§å®¹"
        }
      },
      "required": ["elder_id", "category", "message"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "status": "success",
      "elder_id": "eld_001",
      "category": "emergency",
      "message_id": "95a12345-6789-0123-4567-890123456789",
      "detail": "å·²æ??Ÿç™¼??emergency ?šçŸ¥çµ¦ç…§è­·è€?
    }
    ```

---

## 3. å°è©±å¼•å??‡å·¥?·èª¿?¨å¯¦ä¾?

ä»¥ä?å±•ç¤º Bedrock Agent å¦‚ä??¨è??·è€…ç?å°è©±ä¸­æ??¢èª¿?¨ä?è¿°å·¥?·ï?

### å¯¦ä? Aï¼šé•·?…è©¢?è?ç¨???èª¿ç”¨ `get_today_routines`
*   **?·è€?*ï¼šã€Œæ?ä»Šå¤©?ƒé?è¡€å£“è—¥äº†å?ï¼Ÿã€?
*   **Agent (Claude)**ï¼?(?¤å??å??ºæŸ¥è©¢ä??¥ç”¨?¥ç???*
    *   **?¼å«å·¥å…·**ï¼š`get_today_routines(elder_id="eld_001", date="2026-07-20")`
    *   **å·¥å…·?å‚³**ï¼šå??³å?è¡€å£“è—¥ `rtn_001` ??`status: pending`??
*   **Agent ?è?**ï¼šã€Œé˜¿?­å¬¤ï¼Œæ??‹ä?ä»Šå¤©?„è?å£“è—¥?„æ??‰å??”ï??¾åœ¨å·²ç??é?äº†ï?è¦è?å¾—è?å¿«å??¥å??‚ã€?

### å¯¦ä? Bï¼šé•·?…å£?­å??è?ç¨???èª¿ç”¨ `complete_routine`
*   **?·è€?*ï¼šã€Œå¥½?¦ï??‘ç¾?¨æ?è¡€å£“è—¥?ä??»ä??‚ã€?
*   **Agent (Claude)**ï¼?(?¤å??å??ºå??ä??¥ç”¨??*
    *   **?¼å«å·¥å…·**ï¼š`complete_routine(elder_id="eld_001", routine_id="rtn_001", date="2026-07-20", completed_by="conversation")`
    *   **å·¥å…·?å‚³**ï¼š`{ "status": "success", "routine_id": "rtn_001" }`
*   **Agent ?è?**ï¼šã€Œå¤ªæ£’ä?ï¼æ??‰æ??ƒè—¥èº«é??æ??¥å¥åº·åº·?‚æ?å·²ç?å¹«ä??Šé€™å€‹è?ç¨‹è?ä¸‹ä?äº†å??‚ã€?

### å¯¦ä? Cï¼šé•·?…æ–°å¢æœªä¾†è?ç¨???èª¿ç”¨ `create_routine`
*   **?·è€?*ï¼šã€Œæ?å¤©ä??ˆä?é»ï??¿æ?è¦å¸¶?‘å»?‹å??Ÿç??«ç??‚ã€?
*   **Agent (Claude)**ï¼?(?¤å??å??ºå»ºç«‹æ–°?„å–®æ¬¡è?ç¨?*
    *   **?¼å«å·¥å…·**ï¼š`create_routine(elder_id="eld_001", title="?‹å??Ÿç?", type="other", schedule={"freq": "once", "date": "2026-07-21", "time": "15:00"})`
    *   **å·¥å…·?å‚³**ï¼š`{ "status": "success", "routine_id": "rtn_003" }`
*   **Agent ?è?**ï¼šã€Œé˜¿?­å¬¤ï¼Œæ?å·²ç?å¹«ä?è¨˜ä?ä¾†ä?ï¼æ?å¤©ä??ˆä?é»å»?‹å??Ÿç??«ç?ï¼Œåˆ°?‚å€™æ??ƒå??é?ä½ ã€‚ã€?

### å¯¦ä? Dï¼šé•·?…å?? è??’ç??¥ç?æ³???èª¿ç”¨ `notify_caregiver`
*   **?·è€?*ï¼šã€Œå??©æ?ï¼Œæ??›å??¨æµ´å®¤ä?å°å??”å€’ä?ï¼Œè…³å¥½ç?ç«™ä?èµ·ä?...??
*   **Agent (Claude)**ï¼?(?¤å??å??ºè??’æ¥µåº¦ç??¥ç?æ³?*
    *   **?¼å«å·¥å…·**ï¼š`notify_caregiver(elder_id="eld_001", category="emergency", message="?·è€…å?? åœ¨æµ´å®¤è·Œå€’ï??³éƒ¨?‡ç?ç«™ç??°é›£??)`
    *   **å·¥å…·?å‚³**ï¼š`{ "status": "success", "category": "emergency", "message_id": "msg_998877" }`
*   **Agent ?è?**ï¼šã€Œé˜¿?­å¬¤ï¼è??¨å??åœ¨?Ÿåœ°?ƒè¬ä¸è??¥è?ç«™èµ·ä¾†ã€‚æ?å·²ç?ç«‹åˆ»?¼é€ç??¥è­¦?±é€šçŸ¥å¿—æ?äº†ï?ä»–å?å¿«å°±?ƒé?å¿ƒæ‚¨ï¼ã€?

---

## 2. ?„å·¥?·è??¼è??ƒæ•¸?¶æ? (JSON Schema)

?ºä?è®?Bedrock Agent ?½å?ç²¾æ?è¾¨è??‡ç??å??¸ï??„å·¥?·ç? API è¦æ ¼å®šç¾©å¦‚ä?ï¼?

### 2.1 `get_today_routines` (?¥è©¢ä»Šæ—¥è¡Œç?)
*   **LLM ?è¿°**ï¼š`Retrieve a list of scheduled routines and their completion status for a specific elder on a given date.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        },
        "date": {
          "type": "string",
          "description": "?¥è©¢?„æ—¥?Ÿï??¼å???YYYY-MM-DDï¼Œä?å¦?2026-07-20"
        }
      },
      "required": ["elder_id", "date"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "date": "2026-07-20",
      "items": [
        {
          "routine_id": "rtn_001",
          "title": "?ƒè?å£“è—¥",
          "type": "medication",
          "scheduled_at": "2026-07-20T09:00:00+08:00",
          "status": "pending"
        },
        {
          "routine_id": "rtn_002",
          "title": "?è?å£?,
          "type": "other",
          "scheduled_at": "2026-07-20T19:00:00+08:00",
          "status": "done",
          "completed_at": "2026-07-20T09:05:00+08:00"
        }
      ]
    }
    ```

---

### 2.2 `complete_routine` (ç¢ºè?å®Œæ?è¡Œç?)
*   **LLM ?è¿°**ï¼š`Mark a specific routine as completed and log a life event for the elder.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        },
        "routine_id": {
          "type": "string",
          "description": "è¦å??ç?è¡Œç? IDï¼Œä?å¦?rtn_001"
        },
        "date": {
          "type": "string",
          "description": "å®Œæ??„æ—¥?Ÿï??¼å???YYYY-MM-DDï¼Œä?å¦?2026-07-20"
        },
        "completed_by": {
          "type": "string",
          "enum": ["conversation", "elder", "caregiver"],
          "description": "å®Œæ?æ­¤è?ç¨‹ç?è§’è‰²ï¼Œå£èªå??±ä?å¾‹å¡« conversation"
        }
      },
      "required": ["elder_id", "routine_id", "date", "completed_by"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "status": "success",
      "message": "Routine rtn_001 marked as done.",
      "routine_id": "rtn_001",
      "completed_at": "2026-07-20T10:15:22+08:00"
    }
    ```

---

### 2.3 `create_routine` (å»ºç??°è?ç¨?
*   **LLM ?è¿°**ï¼š`Create a new scheduled routine (either one-time or recurring) for the elder.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        },
        "title": {
          "type": "string",
          "description": "è¡Œç??„æ?é¡Œæ??§å®¹ï¼Œä?å¦‚ï??ƒè?å£“è—¥?ç?å¿ƒè?ç§‘ã€è??’å???­¥"
        },
        "type": {
          "type": "string",
          "enum": ["diet", "activity", "sleep", "medication", "wellbeing", "other"],
          "description": "è¡Œç?é¡å??†é?"
        },
        "schedule": {
          "type": "object",
          "properties": {
            "freq": {
              "type": "string",
              "enum": ["daily", "weekly", "once"],
              "description": "?»ç?ï¼šæ??¥ã€æ??±ã€å–®æ¬?
            },
            "date": {
              "type": "string",
              "description": "å¦‚æ??¯å–®æ¬?once)è¡Œç?ï¼Œå??ˆæ?ä¾›æ—¥??YYYY-MM-DDï¼›æ??¥æ?æ¯é€±å???
            },
            "time": {
              "type": "string",
              "description": "è¡Œç??‚é?ï¼Œæ ¼å¼ç‚º HH:MMï¼Œä?å¦?15:30"
            },
            "weekday": {
              "type": "integer",
              "minimum": 1,
              "maximum": 7,
              "description": "å¦‚æ??¯æ???weekly)è¡Œç?ï¼Œå??ˆæ?ä¾›æ??Ÿå¹¾ï¼?=?±ä?ï¼?=?±æ—¥ï¼?
            }
          },
          "required": ["freq", "time"]
        }
      },
      "required": ["elder_id", "title", "type", "schedule"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "status": "success",
      "routine_id": "rtn_003",
      "title": "?‹é†«??,
      "scheduled_at": "2026-07-21T15:00:00+08:00"
    }
    ```

---

### 2.4 `get_recent_events` (?¥è©¢?Ÿæ´»äº‹ä»¶æ­·å²)
*   **LLM ?è¿°**ï¼š`Retrieve recent life events, activities, and recorded health signals for the elder.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        },
        "event_type": {
          "type": "string",
          "description": "?¯é¸?„ä?ä»¶é??‹é?æ¿¾ï?ä¾‹å?ï¼šroutine_completion, wellbeing, activity, family, diet, other"
        }
      },
      "required": ["elder_id"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "status": "success",
      "count": 2,
      "data": [
        {
          "event_id": "evt_001",
          "elder_id": "eld_001",
          "type": "routine_completion",
          "detail": "å®Œæ??ƒè?å£“è—¥",
          "ts": "2026-07-20T09:05:00+08:00"
        }
      ]
    }
    ```

---

### 2.5 `get_elder_profile` (?¥è©¢?·è€…å?å¥½è??‹äººæª”æ?)
*   **LLM ?è¿°**ï¼š`Retrieve personal preferences, hobbies, health notes, and family members of the elder.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        }
      },
      "required": ["elder_id"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "status": "success",
      "data": {
        "elder_id": "eld_001",
        "name": "?—é˜¿??,
        "nickname": "?¿è˜­å¬?,
        "health_notes": ["?‰é?è¡€å£“æ­·??, "å·¦è??œç?ä¸é©"],
        "family": [{"name": "å°æ?", "relation": "?’å?"}],
        "preferences": {"tea": "é«˜å±±?é???, "music": "?§é??›ç??¸é???}
      }
    }
    ```

---

### 2.6 `remind_pending_routines` (ä¸»å??é?å¾…è¾¦è¡Œç?)
*   **LLM ?è¿°**ï¼š`Check and retrieve pending scheduled routines for the elder to generate warm reminders.`
*   **è¼¸å…¥?ƒæ•¸ (Input Parameters)**ï¼?
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": {
          "type": "string",
          "description": "?·è€…ç??¯ä?è­˜åˆ¥ IDï¼Œä?å¦?eld_001"
        },
        "date": {
          "type": "string",
          "description": "?¥è©¢?„æ—¥?Ÿï??¼å???YYYY-MM-DD"
        }
      },
      "required": ["elder_id"]
    }
    ```
*   **?å‚³è³‡æ? (Output JSON)**ï¼?
    ```json
    {
      "status": "success",
      "date": "2026-07-20",
      "pending_count": 1,
      "pending_routines": [
        {
          "routine_id": "rtn_001",
          "title": "?ƒæ??“è?å£“è—¥",
          "scheduled_at": "2026-07-20T19:00:00+08:00",
          "status": "pending"
        }
      ]
    }
    ```

---

## 3. å°è©±å¼•å??‡å·¥?·èª¿?¨å¯¦ä¾?

ä»¥ä?å±•ç¤º Bedrock Agent å¦‚ä??¨è??·è€…ç?å°è©±ä¸­æ??¢èª¿?¨ä?è¿°å·¥?·ï?

### å¯¦ä? Aï¼šé•·?…è©¢?è?ç¨???èª¿ç”¨ `get_today_routines`
*   **?·è€?*ï¼šã€Œæ?ä»Šå¤©?ƒé?è¡€å£“è—¥äº†å?ï¼Ÿã€?
*   **Agent (Claude)**ï¼?(?¤å??å??ºæŸ¥è©¢ä??¥ç”¨?¥ç???*
    *   **?¼å«å·¥å…·**ï¼š`get_today_routines(elder_id="eld_001", date="2026-07-20")`
    *   **å·¥å…·?å‚³**ï¼šå??³å?è¡€å£“è—¥ `rtn_001` ??`status: pending`??
*   **Agent ?è?**ï¼šã€Œé˜¿?­å¬¤ï¼Œæ??‹ä?ä»Šå¤©?„è?å£“è—¥?„æ??‰å??”ï??¾åœ¨å·²ç??é?äº†ï?è¦è?å¾—è?å¿«å??¥å??‚ã€?

### å¯¦ä? Bï¼šé•·?…å£?­å??è?ç¨???èª¿ç”¨ `complete_routine`
*   **?·è€?*ï¼šã€Œå¥½?¦ï??‘ç¾?¨æ?è¡€å£“è—¥?ä??»ä??‚ã€?
*   **Agent (Claude)**ï¼?(?¤å??å??ºå??ä??¥ç”¨??*
    *   **?¼å«å·¥å…·**ï¼š`complete_routine(elder_id="eld_001", routine_id="rtn_001", date="2026-07-20", completed_by="conversation")`
    *   **å·¥å…·?å‚³**ï¼š`{ "status": "success", "routine_id": "rtn_001" }`
*   **Agent ?è?**ï¼šã€Œå¤ªæ£’ä?ï¼æ??‰æ??ƒè—¥èº«é??æ??¥å¥åº·åº·?‚æ?å·²ç?å¹«ä??Šé€™å€‹è?ç¨‹è?ä¸‹ä?äº†å??‚ã€?

### å¯¦ä? Cï¼šé•·?…æ–°å¢æœªä¾†è?ç¨???èª¿ç”¨ `create_routine`
*   **?·è€?*ï¼šã€Œæ?å¤©ä??ˆä?é»ï??¿æ?è¦å¸¶?‘å»?‹å??Ÿç??«ç??‚ã€?
*   **Agent (Claude)**ï¼?(?¤å??å??ºå»ºç«‹æ–°?„å–®æ¬¡è?ç¨?*
    *   **?¼å«å·¥å…·**ï¼š`create_routine(elder_id="eld_001", title="?‹å??Ÿç?", type="other", schedule={"freq": "once", "date": "2026-07-21", "time": "15:00"})`
    *   **å·¥å…·?å‚³**ï¼š`{ "status": "success", "routine_id": "rtn_003" }`
*   **Agent ?è?**ï¼šã€Œé˜¿?­å¬¤ï¼Œæ?å·²ç?å¹«ä?è¨˜ä?ä¾†ä?ï¼æ?å¤©ä??ˆä?é»å»?‹å??Ÿç??«ç?ï¼Œåˆ°?‚å€™æ??ƒå??é?ä½ ã€‚ã€?



### 2.8 `update_routine` (æ›´æ–°ä¾‹è¡Œè¡Œç¨‹)
*   **LLM æè¿°**ï¼š`Update an existing scheduled routine (e.g., change time, title, or frequency) for the elder.`
*   **è¼¸å…¥åƒæ•¸ (Input Parameters)**ï¼š
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": { "type": "string" },
        "routine_id": { "type": "string" },
        "title": { "type": "string" },
        "type": { "type": "string" },
        "time": { "type": "string" },
        "freq": { "type": "string" },
        "date": { "type": "string" },
        "remind": { "type": "boolean" },
        "active": { "type": "boolean" }
      },
      "required": ["elder_id", "routine_id"]
    }
    ```

### 2.9 `deactivate_routine` (åœç”¨ä¾‹è¡Œè¡Œç¨‹)
*   **LLM æè¿°**ï¼š`Deactivate or cancel an existing scheduled routine for the elder.`
*   **è¼¸å…¥åƒæ•¸ (Input Parameters)**ï¼š
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": { "type": "string" },
        "routine_id": { "type": "string" }
      },
      "required": ["elder_id", "routine_id"]
    }
    ```

### 2.10 `get_daily_summaries` (æŸ¥è©¢æ¯æ—¥å¥åº·æ‘˜è¦)
*   **LLM æè¿°**ï¼š`Retrieve recent daily health summaries for the elder.`
*   **è¼¸å…¥åƒæ•¸ (Input Parameters)**ï¼š
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": { "type": "string" },
        "days": { "type": "integer" }
      },
      "required": ["elder_id"]
    }
    ```

### 2.11 `get_recent_conversations` (æŸ¥è©¢å°è©±ç´€éŒ„)
*   **LLM æè¿°**ï¼š`Retrieve recent conversation history between the elder and the agent.`
*   **è¼¸å…¥åƒæ•¸ (Input Parameters)**ï¼š
    ```json
    {
      "type": "object",
      "properties": {
        "elder_id": { "type": "string" }
      },
      "required": ["elder_id"]
    }
    ```
