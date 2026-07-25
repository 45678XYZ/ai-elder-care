import 'dart:async';

import 'package:uuid/uuid.dart';

import '../models/chat_reply.dart';
import '../models/session_close.dart';
import 'api_client.dart';
import 'api_error_codes.dart';
import 'api_exception.dart';

/// 一位長者的對話 session：代管 `session_id` 生命週期與 `client_request_id` 冪等鍵。
///
/// 存在的理由是 docs/api.md 的一條硬規則——realtime 業務一旦提交成功，該 turn 就永遠是
/// completed；**其後即使 HTTP 傳輸失敗，App 也必須以原 `client_request_id` 重送查明結果，
/// 不可改用新的**，否則會重複建立行程或事件。這件事不能散落在 UI，故集中在此。
///
/// 用法：
/// ```dart
/// final chat = ChatSession(api: api, elderId: id, lang: 'zh-TW');
/// final reply = await chat.send(text: '我吃過藥了');
/// // 離開畫面／切換長者前：
/// await chat.close();
/// ```
///
/// 一位長者一個實例；切換長者要先 [close] 再建新的（session 屬於特定長者，混用會 403）。
class ChatSession {
  ChatSession({
    required ApiClient api,
    required this.elderId,
    required this.lang,
    Duration retryBaseDelay = const Duration(milliseconds: 400),
    int maxAttempts = 4,
  })  : _api = api,
        _retryBaseDelay = retryBaseDelay,
        _maxAttempts = maxAttempts;

  final ApiClient _api;
  final String elderId;

  /// `zh-TW` | `hak`；決定後端 ASR 與 TTS 的語言。
  final String lang;

  /// 重試的基礎間隔，每次加倍。
  final Duration _retryBaseDelay;

  /// 單次呼叫的總嘗試次數（含第一次）。
  final int _maxAttempts;

  static const _uuid = Uuid();

  String? _sessionId;

  /// 尚未取得最終結果的冪等鍵，與其對應的輸入指紋。
  ///
  /// 重試耗盡時**不清掉**：使用者若重送同一句話，必須沿用原 ID 才不會重複副作用。
  String? _pendingRequestId;
  int? _pendingInputFingerprint;

  /// 目前的 session；還沒送出第一句話時為 null。
  String? get sessionId => _sessionId;

  /// 有 session 可關（[close] 才有意義）。
  bool get hasOpenSession => _sessionId != null;

  /// 送一句話並取得 AI 回覆。[text] 與 [audioBase64] 擇一。
  ///
  /// 暫時性失敗（連線失敗、409 `REQUEST_IN_PROGRESS`、5xx）會以**同一個** `client_request_id`
  /// 自動退避重送；重試耗盡時丟出最後一個 [ApiException]。此時該次輸入的冪等鍵會留著——
  /// 使用者重送同一句話會沿用它，後端據此回放原結果而不是做第二次。
  Future<ChatReply> send({
    String? text,
    String? audioBase64,
    String audioFormat = 'm4a',
  }) async {
    final fingerprint = Object.hash(text, audioBase64, audioFormat);
    // 內容與上次未完成的輸入相同 → 同一次輸入的重送，沿用原 ID；否則是新的一句話。
    final requestId =
        (_pendingRequestId != null && _pendingInputFingerprint == fingerprint)
            ? _pendingRequestId!
            : _uuid.v4();
    _pendingRequestId = requestId;
    _pendingInputFingerprint = fingerprint;

    final reply = await _withRetry(() => _api.chat(
          clientRequestId: requestId,
          elderId: elderId,
          lang: lang,
          sessionId: _sessionId,
          text: text,
          audioBase64: audioBase64,
          audioFormat: audioFormat,
        ));

    // 後端可能因原 session 已 idle／關閉／達上限而換一個新的，一律以回應為準。
    if (reply.sessionId.isNotEmpty) _sessionId = reply.sessionId;
    _pendingRequestId = null;
    _pendingInputFingerprint = null;
    return reply;
  }

  /// 關閉目前 session：停止追加 turn 並啟動離線事件整理。
  ///
  /// 停止免手持互動、離開對話畫面、切換長者前呼叫。沒有 session 時回 null、不打 API。
  ///
  /// 仍有 turn 在處理時後端回 409 `REQUEST_IN_PROGRESS`，這裡會退避重送同一個呼叫
  /// （close 靠 session 狀態冪等，不帶冪等鍵）。重試耗盡時**不丟例外**：後端的
  /// idle closer 最終會收斂這個 session，不該因此擋住使用者離開畫面。
  Future<SessionCloseResult?> close() async {
    final id = _sessionId;
    if (id == null) return null;

    try {
      final result = await _withRetry(() => _api.closeSession(id));
      _sessionId = null;
      return result;
    } on ApiException catch (e) {
      // 後端已經不認得這個 session（不存在或不屬於此長者），本地也就沒有東西可關。
      if (e.code == ApiErrorCodes.sessionNotFound) {
        _sessionId = null;
        return null;
      }
      // 其餘（含重試耗盡）交給 idle closer 收斂；清掉本地 ID，下一句話會開新 session。
      _sessionId = null;
      return null;
    }
  }

  /// 暫時性失敗時退避重試；[ApiException.isRetryable] 以外的錯誤直接往上丟。
  Future<T> _withRetry<T>(Future<T> Function() call) async {
    var delay = _retryBaseDelay;
    for (var attempt = 1;; attempt++) {
      try {
        return await call();
      } on ApiException catch (e) {
        if (!e.isRetryable || attempt >= _maxAttempts) rethrow;
        await Future<void>.delayed(delay);
        delay *= 2;
      }
    }
  }
}
