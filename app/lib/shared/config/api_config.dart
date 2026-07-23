/// 後端連線設定。
///
/// `baseUrl` 可在建置/執行時用 `--dart-define=API_BASE_URL=...` 覆寫，
/// 不必改程式碼。未指定時採預設值。
///
/// 預設指向本機 RAG PoC（FastAPI `uvicorn server:app`，預設 8000 埠）：
/// - Android 模擬器要用 `10.0.2.2` 才連得到「執行模擬器的那台電腦」的 localhost
///   （模擬器內的 `localhost` 是模擬器自己）。
/// - iOS 模擬器 / 桌面 / web 直接用 `localhost`。
/// - 實機測試改成電腦在區網的 IP（如 `http://192.168.x.x:8000`）。
///
/// 之後接上正式後端（API Gateway）時，改成 `--dart-define` 指定正式網址即可。
class ApiConfig {
  const ApiConfig._();

  /// RAG PoC 目前沒有 `/v1` 前綴；正式後端每個端點掛在 `/v1` 之下（見 docs/api.md）。
  /// baseUrl 只到主機與埠，路徑由各方法自己組。
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000',
  );
}
