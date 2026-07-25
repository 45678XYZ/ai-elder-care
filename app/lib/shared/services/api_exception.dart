import 'api_error_codes.dart';

/// 後端回傳非 2xx，或連線/解析失敗時丟出。
///
/// 正式後端錯誤 body 為 `{ "error": { "code", "message" } }`（見 docs/api.md）；
/// [code] 為該處的穩定識別碼，無法解析（連線失敗、PoC 端點格式不同）時為 null。
class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode, this.code});

  /// 給人讀的訊息；可直接顯示在 UI。
  final String message;

  /// HTTP 狀態碼；連線層失敗（根本沒收到回應）時為 null。
  final int? statusCode;

  /// 錯誤 body 內的 `error.code`，供前端做 UX 分支；無則為 null。
  final String? code;

  /// 根本沒收到回應（連線失敗、逾時）。此時無法判斷後端是否已處理，
  /// 對有冪等鍵的端點必須以**原 `client_request_id`** 重送查明結果。
  bool get isNetworkFailure => statusCode == null;

  /// 同一請求仍在處理中：退避後重試同一個請求即可。
  bool get isInProgress => code == ApiErrorCodes.requestInProgress;

  /// 冪等鍵撞到不同內容——呼叫端的 bug，重試無用。
  bool get isIdempotencyConflict => code == ApiErrorCodes.idempotencyConflict;

  /// token 缺漏或無效（401 由 API Gateway 直接擋，body 非本專案格式故無 [code]）。
  bool get isUnauthorized => statusCode == 401;

  /// 值得重試的暫時性失敗：連線層失敗、處理中、或後端 5xx。
  bool get isRetryable =>
      isNetworkFailure ||
      isInProgress ||
      (statusCode != null && statusCode! >= 500);

  @override
  String toString() => 'ApiException(status=$statusCode, code=$code): $message';
}
