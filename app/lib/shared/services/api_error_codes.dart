/// 後端錯誤 body 的 `error.code`——前端做 UX 分支的穩定識別碼。
///
/// 契約見 docs/api.md「錯誤格式」：HTTP 狀態碼做粗分類，`code` 做細分類，
/// 一個狀態碼下可有多個 code。`message` 可能調整，程式一律判 [ApiErrorCodes] 的值，
/// 不要比對訊息文字。
class ApiErrorCodes {
  const ApiErrorCodes._();

  // ---- 400 ----

  /// 參數缺漏、格式錯誤，或傳了 server-owned／未知欄位。
  static const String invalidParameter = 'INVALID_PARAMETER';

  /// 單句音檔超過 60 秒。UI 應在到點前收音，不該讓長者錄完才被退。
  static const String audioTooLong = 'AUDIO_TOO_LONG';

  /// `POST /routines/{id}/complete` 指定的日期該 routine 無排程。
  static const String routineNotScheduled = 'ROUTINE_NOT_SCHEDULED';

  // ---- 403 ----

  /// 存取未綁定的長者。注意 close endpoint 不用此碼——見 [sessionNotFound]。
  static const String forbidden = 'FORBIDDEN';

  // ---- 404 ----

  static const String elderNotFound = 'ELDER_NOT_FOUND';
  static const String routineNotFound = 'ROUTINE_NOT_FOUND';

  /// session 不存在**或不屬於該長者**都回這個碼（api.md 明訂不以 403 區分，
  /// 避免洩漏 session 是否存在），所以收到時不代表一定是「打錯 ID」。
  static const String sessionNotFound = 'SESSION_NOT_FOUND';

  /// 綁定照護者時查無此 ID（不存在，或那個帳號不是照護者）。
  ///
  /// 與 [elderNotFound] 不同，這個碼**可以**直接告訴使用者是 ID 打錯了：
  /// 照護者 ID 本來就是拿來給人抄寫的，不存在這回事沒有洩漏疑慮。
  static const String caregiverNotFound = 'CAREGIVER_NOT_FOUND';

  // ---- 409 ----

  /// 同一請求仍在處理中（turn lease 未到期，或 session 尚未收斂）。
  /// 可退避後**重試同一個請求**，不可換新的 `client_request_id`。
  static const String requestInProgress = 'REQUEST_IN_PROGRESS';

  /// 同一個 `client_request_id` 配上不同的內容。這是呼叫端的 bug——
  /// 冪等鍵必須與內容一一對應，換內容就要換新的 ID。
  static const String idempotencyConflict = 'IDEMPOTENCY_CONFLICT';

  // ---- 500 ----

  static const String internalError = 'INTERNAL_ERROR';
}
