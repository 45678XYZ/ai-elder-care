import abc
import os
import boto3
import requests

class BaseTTS(abc.ABC):
    """
    語音合成 (TTS) 的統一抽象介面 (Interface)
    
    不論是使用 AWS Polly 還是外部的客語 API，所有的實作類別都必須繼承此類別，
    並實作 `synthesize` 方法。
    """
    
    @abc.abstractmethod
    def synthesize(self, text: str) -> bytes:
        """
        將傳入的文字合成語音。
        
        Args:
            text (str): 要合成語音的文字內容。
            
        Returns:
            bytes: 合成後音訊檔案的二進位資料 (例如 MP3 二進位串流)。
            
        Raises:
            RuntimeError: 當語音合成服務異常時拋出。
        """
        pass


class PollyChineseTTS(BaseTTS):
    """
    中文語音合成實作 (使用 AWS Polly Neural 引擎)
    由【楊敦棋 (負責中文)】實作與維護。
    """
    def __init__(self, voice_id: str = "Zhiyu"):
        """
        初始化 AWS Polly 用戶端。
        
        Args:
            voice_id (str): Polly 的語音角色 ID，預設為 "Zhiyu" (台灣中文女聲)。
        """
        # 建立 Polly client。預設會讀取 Lambda 運作時的 AWS Region 憑證。
        self.polly = boto3.client('polly')
        self.voice_id = voice_id

    def synthesize(self, text: str) -> bytes:
        try:
            # 呼叫 AWS Polly 合成語音
            response = self.polly.synthesize_speech(
                Engine='neural',       # 使用高品質的 Neural 引擎
                OutputFormat='mp3',    # 合成 MP3 格式
                Text=text,
                VoiceId=self.voice_id
            )
            # 從 Polly 回傳的串流中讀取二進位資料
            audio_bytes = response['AudioStream'].read()
            return audio_bytes
        except Exception as e:
            print(f"[Error] Polly Chinese TTS failed: {str(e)}")
            raise RuntimeError(f"Polly Chinese TTS failed: {str(e)}")


class ExternalHakkaTTS(BaseTTS):
    """
    客語語音合成實作 (串接 OmniVoice 客語 TTS API)
    由【隊友 (負責客語)】實作與維護。
    """
    def __init__(self):
        # OmniVoice 客語 API 網址與憑證，由 Lambda 環境變數注入
        self.api_url = os.environ.get('OMNIVOICE_TTS_API_URL', 'https://api.omnivoice.com.tw/tts')
        self.api_key = os.environ.get('OMNIVOICE_TTS_API_KEY', '')

    def synthesize(self, text: str) -> bytes:
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        payload = {
            "text": text,
            "speaker": "sixian",  # 四縣腔 (預設客語腔調)
            "format": "mp3"
        }
        
        try:
            # 呼叫外部 API，設定 timeout 防止因外部服務斷線而導致 Lambda 卡死
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=8)
            
            if response.status_code != 200:
                raise RuntimeError(f"Hakka API status code: {response.status_code}")
                
            return response.content
            
        except Exception as e:
            # 💡 關鍵容錯設計 (Fallback)：
            # 如果客語 API 斷線、超時或回應錯誤，自動降級為中文 Polly 發音，避免系統崩潰。
            print(f"[Warning] Hakka TTS failed, fallback to Chinese Polly: {str(e)}")
            fallback_engine = PollyChineseTTS()
            return fallback_engine.synthesize(text)


class TTSFactory:
    """
    TTS 簡單工廠類別。
    
    用來根據輸入的語言代碼 (zh-TW / hak)，自動分流並回傳對應的 TTS 實作實體。
    這可以隱藏具體的類別實作，讓外部調用端 (例如 chat.py) 的代碼保持整潔。
    """
    @staticmethod
    def get_tts_engine(lang: str) -> BaseTTS:
        """
        取得指定語言的 TTS 引擎。
        
        Args:
            lang (str): 語言代碼 (zh-TW 或 hak)。
            
        Returns:
            BaseTTS: 繼承自 BaseTTS 的具體 TTS 實體。
        """
        if lang == "zh-TW":
            return PollyChineseTTS()
        elif lang == "hak":
            return ExternalHakkaTTS()
        else:
            raise ValueError(f"Unsupported language code for TTS: {lang}")
