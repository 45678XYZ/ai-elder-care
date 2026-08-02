import 'package:amazon_cognito_identity_dart_2/cognito.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'auth_backend.dart';

/// [AuthBackend] 的真實作：直接對 Cognito User Pool 講話，不經後端 API
/// （見 docs/framework.md；API Gateway 只驗 token，不管發 token）。
///
/// 用純 Dart 的 SRP 實作而不是 amplify_flutter，理由見 pubspec.yaml。密碼不會以明文
/// 離開裝置——SRP 只送 proof，這也是 terraform/cognito.tf 只開 `ALLOW_USER_SRP_AUTH`
/// 的原因；那份設定不需要為了 App 放寬。
///
/// 三個 auth 畫面完全不認識這個類別，它們只看得到 [AuthBackend]。
class CognitoAuthBackend implements AuthBackend {
  CognitoAuthBackend({
    required String userPoolId,
    required String clientId,
  }) : _pool = CognitoUserPool(
          userPoolId,
          clientId,
          // 預設是記憶體儲存，App 一關 refresh token 就沒了，下次啟動等於被登出。
          // 換成 SharedPreferences 才能跨啟動保留登入狀態（長輩不會每天重登）。
          storage: _PrefsStorage(),
        );

  final CognitoUserPool _pool;

  @override
  Future<SignUpOutcome> signUp({
    required String email,
    required String password,
  }) async {
    final username = _normalize(email);
    final data = await _guard(() => _pool.signUp(
          username,
          password,
          // user pool 設了 username_attributes = ["email"]，username 本身就是信箱；
          // 這裡仍明寫 email 屬性，因為 auto_verified_attributes 要靠它決定驗證碼寄去哪，
          // post_confirmation trigger 也要讀它訂閱 SNS（見 backend 的同名 handler）。
          userAttributes: [AttributeArg(name: 'email', value: username)],
        ));

    // userConfirmed 為 null 時當作「還沒驗證」：多跑一次驗證碼頁，比誤判成已完成
    // 而把人丟去登入然後撞 UserNotConfirmedException 好。
    return (data.userConfirmed ?? false)
        ? SignUpOutcome.done
        : SignUpOutcome.needsConfirmation;
  }

  @override
  Future<void> confirmSignUp({
    required String email,
    required String code,
  }) async {
    final ok =
        await _guard(() => _user(email).confirmRegistration(code.trim()));
    // 正常情況驗證碼錯會丟 CodeMismatchException；回 false 是理論上的另一條路，
    // 一樣當成驗證碼沒過，不要靜默放行。
    if (!ok) throw AuthException.of(AuthErrorCode.codeMismatch);
  }

  // 回傳型別寫死 void：SDK 這支宣告成 dynamic，直接餵給 [_guard] 會推不出泛型參數。
  @override
  Future<void> resendCode({required String email}) => _guard<void>(() async {
        await _user(email).resendConfirmationCode();
      });

  @override
  Future<void> forgotPassword({required String email}) => _guard<void>(() async {
        await _user(email).forgotPassword();
      });

  @override
  Future<void> confirmNewPassword({
    required String email,
    required String code,
    required String newPassword,
  }) => _guard<void>(() async {
        await _user(email).confirmPassword(code.trim(), newPassword);
      });

  @override
  Future<String> signIn({
    required String email,
    required String password,
  }) async {
    final details = AuthenticationDetails(
      username: _normalize(email),
      password: password,
    );
    final session = await _guard(() => _user(email).authenticateUser(details));

    // session 或 token 為 null 代表走到了需要額外挑戰的流程（MFA、強制改密碼、
    // NEW_PASSWORD_REQUIRED）。這個 user pool 都沒開，真的走到就是設定被改過了，
    // 不該假裝登入成功。
    final token = session?.getIdToken().getJwtToken();
    if (token == null) throw AuthException.of(AuthErrorCode.unknown);
    return token;
  }

  @override
  Future<void> signOut() async {
    final user = await _pool.getCurrentUser();
    // 只清本機的 token；不呼叫 globalSignOut（那會連同一帳號在別台裝置的
    // session 一起失效，不是使用者按「登出」時預期的事）。
    await user?.signOut();
  }

  /// 取現在有效的 ID token；過期時 [CognitoUser.getSession] 會用 refresh token 自動換新。
  ///
  /// refresh token 也過期（預設 30 天）或本機沒有 session 時回 null——此時只能重新登入，
  /// 這裡不丟例外：呼叫端是每次 API 呼叫前的 token provider，不是使用者觸發的動作。
  @override
  Future<String?> currentIdToken() async {
    try {
      final user = await _pool.getCurrentUser();
      if (user == null) return null;
      final session = await user.getSession();
      if (session == null || !session.isValid()) return null;
      return session.getIdToken().getJwtToken();
    } catch (_) {
      return null;
    }
  }

  /// 不指定 storage：[CognitoUser] 沒給時會沿用 `pool.storage`，也就是建構時那份
  /// [_PrefsStorage]。多傳一份新的反而會把 pool 的 storage 換掉（見套件的 cognito_user.dart）。
  CognitoUser _user(String email) => CognitoUser(_normalize(email), _pool);

  /// 與 [DemoAuthBackend] 一致的正規化。Cognito 的 email 別名本身不分大小寫，
  /// 但前後空白會被當成帳號的一部分——長輩用的鍵盤很容易多帶一個空格。
  static String _normalize(String email) => email.trim().toLowerCase();

  /// 把 SDK 的例外統一轉成 [AuthException]，畫面只認得後者。
  ///
  /// 不另外攔 `SocketException`／`ClientException`：SDK 內部已經把所有連線失敗都包成
  /// [CognitoClientException] 了（見套件的 `src/client.dart`），攔不到，交給 [_mapCode]。
  static Future<T> _guard<T>(Future<T> Function() action) async {
    try {
      return await action();
    } on CognitoClientException catch (e) {
      throw AuthException.of(_mapCode(e));
    } on AuthException {
      rethrow;
    } catch (_) {
      throw AuthException.of(AuthErrorCode.unknown);
    }
  }

  /// Cognito 例外名稱 → [AuthErrorCode]。[AuthErrorCode] 當初就是照這份名單取名的，
  /// 所以這裡幾乎是一對一。
  static AuthErrorCode _mapCode(CognitoClientException e) {
    switch (e.code) {
      case 'UsernameExistsException':
        return AuthErrorCode.usernameExists;
      case 'InvalidPasswordException':
        return AuthErrorCode.invalidPassword;
      case 'CodeMismatchException':
        return AuthErrorCode.codeMismatch;
      case 'ExpiredCodeException':
        return AuthErrorCode.expiredCode;
      case 'UserNotConfirmedException':
        return AuthErrorCode.userNotConfirmed;
      // 查無此人與密碼錯誤共用同一個碼，才不會洩漏某個信箱有沒有註冊過
      // （[AuthErrorCode.notAuthorized] 的說明）。
      case 'NotAuthorizedException':
      case 'UserNotFoundException':
        return AuthErrorCode.notAuthorized;
      case 'LimitExceededException':
      case 'TooManyRequestsException':
      case 'TooManyFailedAttemptsException':
        return AuthErrorCode.limitExceeded;
      // 連線失敗也走這個例外（SDK 的 client.dart 把 post 包在 try 裡）：DNS 解不出來是
      // 'NetworkError'，其餘傳輸層失敗是 'Unknown error'。注意後者跟伺服器回錯但沒帶
      // x-amzn-errortype 時用的 'UnknownError'**只差一個空格**，是兩回事——
      // 前者要叫使用者檢查網路，後者不能。
      case 'NetworkError':
      case 'Unknown error':
        return AuthErrorCode.network;
      // InvalidParameterException 是個雜燴：密碼不合規、信箱格式不對都會回它。
      // 只在訊息確實在講密碼時才說「密碼不符規則」，否則寧可講通則——
      // 對著打錯信箱的人說「密碼至少 8 個字」只會讓他一直改密碼。
      case 'InvalidParameterException':
        return (e.message ?? '').toLowerCase().contains('password')
            ? AuthErrorCode.invalidPassword
            : AuthErrorCode.unknown;
      default:
        return AuthErrorCode.unknown;
    }
  }
}

/// 用 SharedPreferences 實作 SDK 的儲存介面，讓 session（含 refresh token）跨啟動存活。
///
/// key 加前綴是為了跟 App 自己存的東西分開：SDK 的 key 形如
/// `CognitoIdentityServiceProvider.<clientId>.<username>.idToken`，不加前綴雖然不會撞，
/// 但清帳號資料時分不出哪些是它的。
class _PrefsStorage extends CognitoStorage {
  static const _prefix = 'cognito_';

  @override
  Future<dynamic> getItem(String key) async {
    final p = await SharedPreferences.getInstance();
    return p.getString('$_prefix$key');
  }

  @override
  Future<dynamic> setItem(String key, dynamic value) async {
    final p = await SharedPreferences.getInstance();
    await p.setString('$_prefix$key', value.toString());
    return value;
  }

  @override
  Future<dynamic> removeItem(String key) async {
    final p = await SharedPreferences.getInstance();
    final existing = p.getString('$_prefix$key');
    await p.remove('$_prefix$key');
    return existing;
  }

  @override
  Future<void> clear() async {
    final p = await SharedPreferences.getInstance();
    // 只清自己的前綴，不能用 p.clear()——那會一併清掉長者資料與提醒設定。
    final keys = p.getKeys().where((k) => k.startsWith(_prefix)).toList();
    for (final key in keys) {
      await p.remove(key);
    }
  }
}
