/// 正式 `POST /chat` 端點的回應。欄位規格見 docs/api.md。
///
/// 目前後端尚未接上，僅 [ApiClient.chat] 的型別骨架引用；App 實際串接的是 RAG PoC 的 [AskResult]。
class ChatReply {
  const ChatReply({
    required this.conversationId,
    required this.transcript,
    required this.replyText,
    required this.replyAudioUrl,
    required this.routinesUpdated,
  });

  final String conversationId;

  final String transcript;
  final String replyText;

  /// 回覆語音的 S3 presigned URL；有效期見 docs/api.md。
  final String replyAudioUrl;

  /// 為 true 時 App 需重拉 routines 並重排本地通知；判定條件見 docs/api.md。
  final bool routinesUpdated;

  factory ChatReply.fromJson(Map<String, dynamic> json) => ChatReply(
        conversationId: json['conversation_id'] as String? ?? '',
        transcript: json['transcript'] as String? ?? '',
        replyText: json['reply_text'] as String? ?? '',
        replyAudioUrl: json['reply_audio_url'] as String? ?? '',
        routinesUpdated: json['routines_updated'] as bool? ?? false,
      );
}
