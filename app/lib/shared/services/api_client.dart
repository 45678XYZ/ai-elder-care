/// 後端 API 客戶端。規格見 docs/api.md。
///
/// 共通慣例：
/// - 所有請求帶 Authorization: Bearer <Cognito ID Token>
/// - 錯誤 body 為 { "error": { "code", "message" } }（401 除外，由 API Gateway 回應）
/// - 列表分頁：?limit= 與 ?next_token=
class ApiClient {
  // TODO: chat() — POST /chat（text 或 audio base64 ≤60s）
  // TODO: getElders() / getElder() / createElder() / updateElder()
  // TODO: getSummaries() / generateSummary()
  // TODO: getEvents()
  // TODO: getRoutines()（定義列表 / ?date= 當日視圖）
  // TODO: createRoutine() / updateRoutine() / completeRoutine()
  // TODO: getStats()
}
