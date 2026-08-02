/// 認證後端介面。
///
/// 把「怎麼跟 Cognito 講話」與「畫面怎麼走」切開：畫面只依賴這個介面，
/// 目前跑 [DemoAuthBackend]，等 User Pool 部署好換成 Cognito 實作即可，
/// 三個 auth 畫面一行都不用改。
///
/// 方法命名與參數刻意貼齊 Cognito 的 API（SignUp / ConfirmSignUp /
/// ResendConfirmationCode / InitiateAuth），換實作時是一對一對映，
/// 不需要在中間多一層轉譯。
abstract interface class AuthBackend {
  /// 註冊。Cognito 在 `auto_verified_attributes` 含 email 時會寄出驗證碼，
  /// 此時回 [SignUpOutcome.needsConfirmation]，畫面接著進驗證碼頁。
  Future<SignUpOutcome> signUp({
    required String email,
    required String password,
  });

  /// 送出信箱驗證碼。成功後帳號才可登入。
  Future<void> confirmSignUp({required String email, required String code});

  /// 重寄驗證碼。Cognito 對此有頻率限制，過快會拿到 [AuthErrorCode.limitExceeded]。
  Future<void> resendCode({required String email});

  /// 發送重設密碼驗證碼到信箱。Cognito 的 ForgotPassword API。
  /// 不論信箱是否存在都靜默成功（不洩漏帳號是否註冊過）。
  Future<void> forgotPassword({required String email});

  /// 用驗證碼確認新密碼。Cognito 的 ConfirmForgotPassword API。
  Future<void> confirmNewPassword({
    required String email,
    required String code,
    required String newPassword,
  });

  /// 登入，回傳 **ID token**（不是 access token——`sub` 與 `elder_id` claim 只在 ID token 內，
  /// 見 [AuthService.parseIdentity]）。
  Future<String> signIn({required String email, required String password});

  Future<void> signOut();

  /// 目前這一刻可用的 ID token；沒登入或 session 已經救不回來時回 null。
  ///
  /// 與 [signIn] 分開是因為 Cognito 的 ID token 只有一小時效期，而登入狀態要跨啟動保留
  /// （長輩不會每天重新登入）——光把登入當下那一份存起來，隔天每一支 API 都會 401。
  /// 實作端負責在過期時用 refresh token 換新，呼叫端只管每次要用之前問一次。
  Future<String?> currentIdToken();
}

/// 註冊後的下一步。
enum SignUpOutcome {
  /// 需要輸入信箱驗證碼。
  needsConfirmation,

  /// 已直接完成（user pool 未要求驗證時）。
  done,
}

/// 認證錯誤碼。值取自 Cognito 的例外名稱，換成真實作時可直接對映。
enum AuthErrorCode {
  /// 信箱已被註冊。
  usernameExists,

  /// 密碼不符合 user pool 的 password policy。
  invalidPassword,

  /// 驗證碼錯誤。
  codeMismatch,

  /// 驗證碼過期。
  expiredCode,

  /// 帳號存在但還沒完成信箱驗證——登入時遇到要導去驗證碼頁，不是叫人重新註冊。
  userNotConfirmed,

  /// 帳號或密碼錯誤。Cognito 對「查無此人」與「密碼錯」都回這個，
  /// 避免洩漏某個信箱是否註冊過；畫面的文案也要跟著含糊。
  notAuthorized,

  /// 重寄／重試過於頻繁。
  limitExceeded,

  /// 連不上或逾時。
  network,

  /// 其他未預期的錯誤。
  unknown,
}

/// 認證流程的錯誤。[message] 是可直接顯示給使用者的繁中訊息。
class AuthException implements Exception {
  const AuthException(this.code, this.message);

  final AuthErrorCode code;
  final String message;

  /// 由錯誤碼產生預設文案。訊息集中在這裡而不是散在各畫面，
  /// 才不會同一種錯誤在登入頁和驗證頁講出兩種說法。
  factory AuthException.of(AuthErrorCode code) =>
      AuthException(code, _messages[code] ?? _messages[AuthErrorCode.unknown]!);

  static const _messages = {
    AuthErrorCode.usernameExists: '這個信箱已經註冊過了，直接登入就好',
    AuthErrorCode.invalidPassword: '密碼至少 8 個字，要有英文字母和數字',
    AuthErrorCode.codeMismatch: '驗證碼錯誤，請再確認一次',
    AuthErrorCode.expiredCode: '驗證碼過期了，請重新寄一次',
    AuthErrorCode.userNotConfirmed: '這個帳號還沒完成信箱驗證',
    AuthErrorCode.notAuthorized: '信箱或密碼錯誤',
    AuthErrorCode.limitExceeded: '太頻繁了，請等一下再試',
    AuthErrorCode.network: '連不上網路，請檢查連線後再試一次',
    AuthErrorCode.unknown: '發生問題，請稍後再試一次',
  };

  @override
  String toString() => 'AuthException($code): $message';
}
