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
  final String replyAudioUrl;

  /// 為 true 時 App 需重拉 routines 並重排本地通知；判定條件見 docs/api.md。
  final bool routinesUpdated;

  factory ChatReply.fromJson(Map<String, dynamic> json) => ChatReply(
        conversationId: json['conversation_id'] as String? ?? '',
        sessionId: json['session_id'] as String? ?? '',
        transcript: json['transcript'] as String? ?? '',
        replyText: json['reply_text'] as String? ?? '',
        replyAudioUrl: json['reply_audio_url'] as String? ?? '',
        routinesUpdated: json['routines_updated'] as bool? ?? false,
      );
}
