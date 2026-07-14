/// 裝置端語音辨識（speech_to_text）。
///
/// 中文（zh-TW）在裝置端辨識後以 text 送 /chat；
/// 客語（第二階段）錄音後以 audio base64 送 /chat 由後端辨識。
/// 含靜音偵測，支撐免手持對話迴圈。
class SpeechService {
  // TODO: startListening() / stopListening()（onResult、靜音自動結束）
}
