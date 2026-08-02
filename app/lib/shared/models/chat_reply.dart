/// 正式 `POST /chat` 端點的回應。欄位規格見 docs/api.md。
///
/// 長者端已經走這條：`CareRepo.chat()` →（真後端）`ApiRepository` → `ChatSession`
/// → [ApiClient.chat]。RAG PoC 的 `/ask`／[AskResult] 只剩 `DemoRepository` 在用，
/// 那是 demo 模式的行為，不是主線。
class ChatReply {
  const ChatReply({
    required this.conversationId,
    required this.sessionId,
    required this.transcript,
    required this.replyText,
    required this.replyAudioUrl,
    required this.replyAudioStatus,
    required this.routinesUpdated,
  });

  /// 本 turn 的 ID；同一個 `client_request_id` 重送回同一個值。
  final String conversationId;

  /// 本 turn 實際寫入的 session。**一律以此值覆蓋本地持有的 session id**——
  /// 原 session 若已 idle／關閉／達上限，後端會改用新建的 session 並在此回新 ID
  /// （判定規則見 docs/api.md）。
  final String sessionId;

  final String transcript;
  final String replyText;

  /// 回覆語音的 S3 presigned URL；有效期見 docs/api.md。
  ///
  /// [replyAudioStatus] 為 `pending` 時這個網址指向還沒生出來的物件，直接播會 404；
  /// 要等合成完成才播得出來（見 [ChatAudioStatus]）。
  final String replyAudioUrl;

  /// 語音就緒狀態。後端合成是非同步的（自建模型一段回覆要數十秒），
  /// 因此拿到回應的當下音訊通常還不存在。
  final ChatAudioStatus replyAudioStatus;

  /// 為 true 時 App 需重拉 routines 並重排本地通知；判定條件見 docs/api.md。
  final bool routinesUpdated;

  factory ChatReply.fromJson(Map<String, dynamic> json) => ChatReply(
        conversationId: json['conversation_id'] as String? ?? '',
        sessionId: json['session_id'] as String? ?? '',
        transcript: json['transcript'] as String? ?? '',
        replyText: json['reply_text'] as String? ?? '',
        replyAudioUrl: json['reply_audio_url'] as String? ?? '',
        replyAudioStatus:
            ChatAudioStatus.fromWire(json['reply_audio_status'] as String?),
        routinesUpdated: json['routines_updated'] as bool? ?? false,
      );
}


/// `reply_audio_status` 的三種值（見 docs/api.md）。
enum ChatAudioStatus {
  /// 合成已入列但音訊還沒好；在 presigned URL 效期內重試才拿得到。
  pending,

  /// 音訊已存在，可以直接播。
  ready,

  /// 這一輪不會有後端語音（沒有可用 provider、語言不支援或入列失敗）。
  unavailable;

  /// 未知或缺欄位一律當成 `unavailable`：寧可直接用裝置端 TTS 唸出來，
  /// 也不要讓長輩對著一個永遠不會就緒的網址空等。
  static ChatAudioStatus fromWire(String? raw) => switch (raw) {
        'pending' => ChatAudioStatus.pending,
        'ready' => ChatAudioStatus.ready,
        _ => ChatAudioStatus.unavailable,
      };
}
