/// Cognito 認證服務。
///
/// 註冊／登入直接走 Cognito SDK，不經後端 API；
/// 取得 ID Token 供 ApiClient 使用，並依帳號角色（長者／照護者）決定模式。
class AuthService {
  // TODO: signIn() / signUp() / signOut()
  // TODO: idToken getter（自動更新）
  // TODO: role getter（elder | caregiver）
}
