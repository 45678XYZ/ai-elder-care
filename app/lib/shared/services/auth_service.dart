import 'dart:convert';

import 'auth_backend.dart';
import 'demo_auth_backend.dart';

/// 使用者角色。判定方式與後端 `backend/src/shared/auth.py` 一致：
/// ID token 帶 `elder_id` → 長者，否則照護者。
enum UserRole { elder, caregiver }

/// 從 Cognito ID token 解出的呼叫者身分（對應後端的 `Caller`）。
class CognitoIdentity {
  const CognitoIdentity({
    required this.role,
    required this.userId,
    this.elderId,
  });

  final UserRole role;

  /// Cognito `sub`；照護者即綁定於 `elders.caregiver_ids` 的值。
  final String? userId;

  /// 長者才有；照護者為 null。
  final String? elderId;
}

/// Cognito 認證服務。
///
/// 註冊／登入直接走 Cognito SDK（不經後端 API，見 docs/framework.md）；取得 **ID token**
/// 給 [ApiClient] 當 Bearer，並依 token 的 `elder_id` claim 決定進長者或照護者模式——
/// 判定與後端 `auth.py` 完全一致。
///
/// 目前只實作「解析 ID token → 身分／角色」這段純邏輯（不需後端或 User Pool）；實際登入
/// 需接上 Cognito SDK 與 pool 設定，見檔尾 TODO。
class AuthService {
  AuthService();

  /// 全域單例。登入狀態是整個 App 共用的（router 分流、ApiClient 取 token 都要），
  /// 不該讓各畫面各持一份。
  static final AuthService instance = AuthService();

  /// 實際跟 Cognito 講話的實作。目前是 [DemoAuthBackend]，
  /// User Pool 部署後換成 Cognito 實作即可，畫面不用改。
  AuthBackend backend = DemoAuthBackend();

  String? _idToken;

  /// 目前的 Cognito ID token；未登入為 null。供 [ApiClient] 的 `tokenProvider` 取用。
  String? get idToken => _idToken;

  bool get isSignedIn => _idToken != null;

  /// 登入並記住 token。回傳解析後的身分，讓呼叫端據此決定進哪個模式。
  ///
  /// token 解不出身分時視為未登入——寧可讓使用者重試，也不要帶著一個
  /// 讀不出角色的 token 進到某個模式。
  Future<CognitoIdentity> signIn({
    required String email,
    required String password,
  }) async {
    final token = await backend.signIn(email: email, password: password);
    final parsed = parseIdentity(token);
    if (parsed == null) {
      _idToken = null;
      throw AuthException.of(AuthErrorCode.unknown);
    }
    _idToken = token;
    return parsed;
  }

  Future<void> signOut() async {
    await backend.signOut();
    _idToken = null;
  }

  /// 目前登入者的身分（角色 + sub + elderId）；未登入或 token 格式不對為 null。
  CognitoIdentity? get identity {
    final token = _idToken;
    return token == null ? null : parseIdentity(token);
  }

  /// 解析 ID token 的 claims → 身分。與後端 `auth.py` 一致：有 `elder_id` 為長者，
  /// 否則照護者。token 格式不對時回 null。
  ///
  /// 只認 `elder_id` 這一個 claim：它由 pre-token-generation trigger 注入
  /// （見 `backend/src/handlers/pre_token_generation.py`），不是 Cognito 自訂屬性，
  /// 所以沒有 `custom:elder_id` 這種寫法。
  static CognitoIdentity? parseIdentity(String idToken) {
    final claims = _decodeJwtClaims(idToken);
    if (claims == null) return null;

    final raw = claims['elder_id'];
    final elderId = (raw is String && raw.isNotEmpty) ? raw : null;
    final sub = claims['sub'];

    return CognitoIdentity(
      role: elderId != null ? UserRole.elder : UserRole.caregiver,
      userId: sub is String ? sub : null,
      elderId: elderId,
    );
  }

  /// 解 JWT 的 payload（中段）為 claims map；格式不對回 null。
  ///
  /// 只解碼、不驗簽章——token 由 Cognito 簽發、API Gateway 的 Cognito authorizer 負責驗證，
  /// App 端僅需讀 claims 決定 UI 導向。
  static Map<String, dynamic>? _decodeJwtClaims(String jwt) {
    final parts = jwt.split('.');
    if (parts.length != 3) return null;
    try {
      final payload =
          utf8.decode(base64Url.decode(base64Url.normalize(parts[1])));
      final decoded = jsonDecode(payload);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    }
  }

  // ---- 以下需接上 Cognito SDK 與 User Pool 設定，尚未實作 ----

  // TODO(cognito): 接 amplify_auth_cognito，需 terraform/cognito.tf 提供 User Pool ID /
  //   App Client ID / region。App 端不需要為 elder_id 做任何設定——長者身分存在後端的
  //   elder_accounts 表（sub→elder_id），由 pre-token trigger 在發 token 時注入。
  // TODO(cognito): signIn(email, password) → 設 _idToken；signUp(...)；signOut() → 清 _idToken。
  // TODO(cognito): token 自動更新——過期時用 refresh token 換新（或每次 API 呼叫前取最新）。
  // TODO(cognito): 接上後，[ApiClient] 建構時 tokenProvider 指向 `() async => idToken`
  //   （務必給 ID token，非 access token——sub 與 custom:elder_id 只在 ID token 內）。
}
