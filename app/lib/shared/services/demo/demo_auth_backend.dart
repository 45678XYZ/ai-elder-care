import 'dart:convert';

import '../auth_backend.dart';
import '../password_validator.dart' as pw;

/// 記憶體版的認證後端，供 Cognito 部署前跑完整流程用。
///
/// 行為刻意模仿 Cognito 的關鍵特性，這樣接真實作時畫面不會突然壞掉：
/// - 註冊後帳號是「未驗證」狀態，此時登入會拿到 [AuthErrorCode.userNotConfirmed]
/// - 「查無此人」與「密碼錯」都回 [AuthErrorCode.notAuthorized]（不洩漏信箱是否註冊過）
/// - 驗證碼錯了不會消耗次數，但重寄有頻率限制
/// - 密碼規則對齊 terraform/cognito.tf 的 password_policy（>=8 碼、要有小寫與數字）
///
/// 驗證碼固定 [demoCode]——沒有真的寄信，測試與 demo 都要能預期地走完。
class DemoAuthBackend implements AuthBackend {
  DemoAuthBackend({this.latency = const Duration(milliseconds: 400)});

  /// 人為延遲，讓畫面的 loading 狀態在 demo 時看得見。
  final Duration latency;

  /// demo 用的固定驗證碼。真 Cognito 會寄六位亂數到信箱。
  static const demoCode = '123456';

  /// 重寄的最短間隔，模擬 Cognito 的頻率限制。
  static const resendCooldown = Duration(seconds: 30);

  final Map<String, _DemoAccount> _accounts = {};

  /// 最近一次 [signIn] 發出的 token，給 [currentIdToken] 用。
  /// demo 的 token 不會過期，所以「換新」就是原樣回傳同一份。
  String? _lastToken;

  /// 人為延遲。零延遲時直接回一個已完成的 Future，**不排 timer**——
  /// widget test 的假時鐘要靠 pump 才會前進，若在 pumpWidget 之前 await 一個
  /// 排了 timer 的 Future，它永遠不會完成，測試會一路卡到逾時。
  Future<void> _wait() => latency == Duration.zero
      ? Future<void>.value()
      : Future<void>.delayed(latency);

  @override
  Future<SignUpOutcome> signUp({
    required String email,
    required String password,
  }) async {
    await _wait();
    final key = _normalize(email);

    // 已註冊且已驗證 → 直接擋。已註冊但沒驗證完的，Cognito 會重寄驗證碼而不是報錯，
    // 這裡照做：使用者中途關掉 App 再回來註冊，不該就此卡死。
    final existing = _accounts[key];
    if (existing != null) {
      if (existing.confirmed) {
        throw AuthException.of(AuthErrorCode.usernameExists);
      }
      existing.lastSentAt = DateTime.now();
      return SignUpOutcome.needsConfirmation;
    }

    if (!isPasswordValid(password)) {
      throw AuthException.of(AuthErrorCode.invalidPassword);
    }

    _accounts[key] = _DemoAccount(
      email: key,
      password: password,
      sub: 'demo-sub-${_accounts.length + 1}',
      lastSentAt: DateTime.now(),
    );
    return SignUpOutcome.needsConfirmation;
  }

  @override
  Future<void> confirmSignUp({
    required String email,
    required String code,
  }) async {
    await _wait();
    final account = _accounts[_normalize(email)];
    // 查無此人也回驗證碼錯誤：這一頁的使用者已經在流程中，
    // 給「帳號不存在」只會讓人困惑，且同樣不該洩漏帳號是否存在。
    if (account == null || code.trim() != demoCode) {
      throw AuthException.of(AuthErrorCode.codeMismatch);
    }
    account.confirmed = true;
  }

  @override
  Future<void> resendCode({required String email}) async {
    await _wait();
    final account = _accounts[_normalize(email)];
    if (account == null) return; // 不透露帳號是否存在，靜默成功

    final last = account.lastSentAt;
    if (last != null && DateTime.now().difference(last) < resendCooldown) {
      throw AuthException.of(AuthErrorCode.limitExceeded);
    }
    account.lastSentAt = DateTime.now();
  }

  @override
  Future<void> forgotPassword({required String email}) async {
    await _wait();
    final account = _accounts[_normalize(email)];
    if (account == null) return; // 不透露帳號是否存在，靜默成功

    final last = account.lastSentAt;
    if (last != null && DateTime.now().difference(last) < resendCooldown) {
      throw AuthException.of(AuthErrorCode.limitExceeded);
    }
    account.lastSentAt = DateTime.now();
  }

  @override
  Future<void> confirmNewPassword({
    required String email,
    required String code,
    required String newPassword,
  }) async {
    await _wait();
    final account = _accounts[_normalize(email)];
    if (account == null || code.trim() != demoCode) {
      throw AuthException.of(AuthErrorCode.codeMismatch);
    }
    if (!pw.isPasswordValid(newPassword)) {
      throw AuthException.of(AuthErrorCode.invalidPassword);
    }
    account.password = newPassword;
  }

  @override
  Future<String> signIn({
    required String email,
    required String password,
  }) async {
    await _wait();
    final account = _accounts[_normalize(email)];
    if (account == null || account.password != password) {
      throw AuthException.of(AuthErrorCode.notAuthorized);
    }
    if (!account.confirmed) {
      // 畫面要靠這個錯誤把人導回驗證碼頁，而不是叫他重新註冊。
      throw AuthException.of(AuthErrorCode.userNotConfirmed);
    }
    return _lastToken =
        _fakeIdToken(sub: account.sub, elderId: account.elderId);
  }

  @override
  Future<void> signOut() async {
    await _wait();
    _lastToken = null;
  }

  /// demo 的 token 沒有效期，直接回上次登入那一份（沒登入為 null）。
  @override
  Future<String?> currentIdToken() async => _lastToken;

  /// 密碼規則，委派至 [pw.isPasswordValid]。保留 static 簽名以維持既有測試相容。
  static bool isPasswordValid(String password) => pw.isPasswordValid(password);

  /// demo 專用：把某個帳號變成已綁定的長者，用來預演長者模式。
  /// 真實情境由後端寫 elder_accounts 後、重新取 token 才會有 elder_id。
  void markAsElder({required String email, required String elderId}) {
    _accounts[_normalize(email)]?.elderId = elderId;
  }

  static String _normalize(String email) => email.trim().toLowerCase();

  /// 產生格式正確、簽章是假的 JWT，讓 [AuthService.parseIdentity] 解得出身分。
  /// 只有 demo 用——真 token 由 Cognito 簽發。
  static String _fakeIdToken({required String sub, String? elderId}) {
    // JWT 的 base64url 不帶 padding，[AuthService] 解碼前會自己 normalize。
    String encode(Map<String, dynamic> json) =>
        base64Url.encode(utf8.encode(jsonEncode(json))).replaceAll('=', '');
    return '${encode({'alg': 'none', 'typ': 'JWT'})}'
        '.${encode({
          'sub': sub,
          if (elderId != null) 'elder_id': elderId,
        })}'
        '.demo-signature';
  }
}

class _DemoAccount {
  _DemoAccount({
    required this.email,
    required this.password,
    required this.sub,
    this.lastSentAt,
  });

  final String email;
  String password;
  final String sub;
  bool confirmed = false;
  String? elderId;
  DateTime? lastSentAt;
}
