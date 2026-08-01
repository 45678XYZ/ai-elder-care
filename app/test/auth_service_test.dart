import 'dart:convert';

import 'package:e_hakka_care/shared/services/auth_service.dart';
import 'package:e_hakka_care/shared/services/demo_auth_backend.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 拼一個假的 ID token：`header.payload.signature`，只有中段是真的。
///
/// [AuthService.parseIdentity] 只 base64 解碼讀 claims、不驗簽章（簽章由 API Gateway 的
/// Cognito authorizer 負責），所以簽章段隨便填即可——這也是這組測試不需要真 Cognito 的原因。
String _fakeIdToken(Map<String, dynamic> claims) {
  String seg(Map<String, dynamic> m) =>
      base64Url.encode(utf8.encode(jsonEncode(m))).replaceAll('=', '');
  return '${seg({
        'alg': 'RS256',
        'typ': 'JWT'
      })}.${seg(claims)}.not-a-real-signature';
}

void main() {
  group('角色判定', () {
    test('token 帶 elder_id → 長者', () {
      final identity = AuthService.parseIdentity(_fakeIdToken({
        'sub': 'cognito-sub-1',
        'elder_id': 'eld_a1b2c3d4e5f6',
      }));

      expect(identity?.role, UserRole.elder);
      expect(identity?.elderId, 'eld_a1b2c3d4e5f6');
      expect(identity?.userId, 'cognito-sub-1');
    });

    test('token 沒有 elder_id → 照護者', () {
      final identity = AuthService.parseIdentity(_fakeIdToken({
        'sub': 'cognito-sub-2',
        'email': 'caregiver@example.invalid',
      }));

      expect(identity?.role, UserRole.caregiver);
      expect(identity?.elderId, isNull);
      expect(identity?.userId, 'cognito-sub-2');
    });

    test('elder_id 是空字串 → 照護者（等同沒有這個 claim）', () {
      final identity = AuthService.parseIdentity(_fakeIdToken({
        'sub': 'cognito-sub-3',
        'elder_id': '',
      }));

      expect(identity?.role, UserRole.caregiver);
      expect(identity?.elderId, isNull);
    });

    test('只有 custom:elder_id → 照護者', () {
      // 後端 auth.py 只讀 elder_id（pre-token trigger 注入的那個），
      // 不再認 custom:elder_id。這裡把「不要把 fallback 加回來」鎖住。
      final identity = AuthService.parseIdentity(_fakeIdToken({
        'sub': 'cognito-sub-4',
        'custom:elder_id': 'eld_a1b2c3d4e5f6',
      }));

      expect(identity?.role, UserRole.caregiver);
      expect(identity?.elderId, isNull);
    });
  });

  group('壞掉的 token 不讓 App 掛掉', () {
    test('不是三段 → null', () {
      expect(AuthService.parseIdentity('not.a-jwt'), isNull);
      expect(AuthService.parseIdentity(''), isNull);
    });

    test('payload 不是合法 base64 → null', () {
      expect(AuthService.parseIdentity('aaa.!!!not-base64!!!.bbb'), isNull);
    });

    test('payload 不是 JSON 物件 → null', () {
      final notAnObject =
          base64Url.encode(utf8.encode('"just a string"')).replaceAll('=', '');
      expect(AuthService.parseIdentity('aaa.$notAnObject.bbb'), isNull);
    });
  });

  test('未登入時沒有身分', () {
    expect(AuthService().identity, isNull);
    expect(AuthService().idToken, isNull);
  });

  /// 註冊頁宣告身分 → 第一次登入時轉正。
  ///
  /// 這段的重點是**時序**：註冊當下還沒有 token，拿不到 sub，所以身分只能先按 email 暫存；
  /// 兌現的時機在 signIn 之後。這裡把整條路走完，因為問題只會出現在銜接處。
  group('註冊時宣告的身分', () {
    const email = 'grandma@example.com';
    const password = 'secret123';

    late AuthService auth;
    late DemoAuthBackend backend;

    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
      backend = DemoAuthBackend(latency: Duration.zero);
      auth = AuthService()..backend = backend;
    });

    /// 走完註冊 → 宣告身分 → 驗證信箱 → 登入。順序跟註冊頁一致：
    /// 先 signUp 成功才宣告身分（註冊失敗就不該留下暫存值）。
    Future<CognitoIdentity> signUpAndSignIn(UserRole role,
        {String signUpEmail = email, String signInEmail = email}) async {
      await backend.signUp(email: signUpEmail, password: password);
      await auth.declarePendingRole(email: signUpEmail, role: role);
      await backend.confirmSignUp(
          email: signUpEmail, code: DemoAuthBackend.demoCode);
      return auth.signIn(email: signInEmail, password: password);
    }

    test('選長輩 → token 真的帶 elder_id，身分來自 claim 而不是本機狀態', () async {
      final identity = await signUpAndSignIn(UserRole.elder);

      // 格式對齊 docs/framework.md 的 eld_<12-lowercase-hex>
      expect(identity.elderId, matches(RegExp(r'^eld_[0-9a-f]{12}$')));
      expect(identity.role, UserRole.elder);
      expect(auth.effectiveRole, UserRole.elder);

      // 沒有寫入本機宣告：長者身分完全靠 claim 成立，這樣 demo 走的判定路徑
      // 跟接上 Cognito 之後一模一樣。
      final p = await SharedPreferences.getInstance();
      expect(p.getString('auth_chosen_role'), isNull);
    });

    test('選家人 → 沒有 claim，改用暫存的宣告（綁這次登入的 sub）', () async {
      final identity = await signUpAndSignIn(UserRole.caregiver);

      expect(identity.elderId, isNull);
      expect(auth.effectiveRole, UserRole.caregiver);

      final p = await SharedPreferences.getInstance();
      expect(p.getString('auth_chosen_role'), 'caregiver');
      expect(p.getString('auth_chosen_role_sub'), identity.userId);
    });

    test('暫存的宣告在採用後就清掉，不會被下一個人沿用', () async {
      await signUpAndSignIn(UserRole.caregiver);

      final p = await SharedPreferences.getInstance();
      expect(p.getString('auth_pending_role_$email'), isNull);
    });

    test('信箱大小寫與前後空白不影響對得上', () async {
      // 註冊打「 Grandma@Example.com 」、登入打小寫，要指到同一筆暫存。
      final identity = await signUpAndSignIn(
        UserRole.caregiver,
        signUpEmail: ' Grandma@Example.com ',
        signInEmail: 'grandma@example.com',
      );

      expect(auth.effectiveRole, UserRole.caregiver);
      expect(identity.userId, isNotNull);
    });

    test('沒宣告過身分就登入 → effectiveRole 是 null（要走選身分的退路）', () async {
      await backend.signUp(email: email, password: password);
      await backend.confirmSignUp(email: email, code: DemoAuthBackend.demoCode);
      await auth.signIn(email: email, password: password);

      expect(auth.effectiveRole, isNull);
    });
  });
}
