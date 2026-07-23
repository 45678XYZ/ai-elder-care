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

  @override
  String toString() => 'ApiException(status=$statusCode, code=$code): $message';
}
