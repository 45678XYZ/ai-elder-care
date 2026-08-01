import 'package:ai_elder_care/shared/services/auth_backend.dart';
import 'package:ai_elder_care/shared/services/auth_service.dart';
import 'package:ai_elder_care/shared/services/demo_auth_backend.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 假的認證後端要在幾個關鍵行為上跟 Cognito 一致，否則接真實作時畫面才會爆。
/// 這裡把那些一致性寫成測試，換實作時可以直接拿同一組跑。
void main() {
  late DemoAuthBackend backend;

  setUp(() {
    backend = DemoAuthBackend(latency: Duration.zero);
    // AuthService 會把 token 與已宣告身分寫進 shared_preferences，
    // 測試環境沒有真外掛，要給假的初始值（每個測試都從空的開始）。
    SharedPreferences.setMockInitialValues({});
  });

  Future<void> register(String email, [String password = 'secret123']) =>
      backend.signUp(email: email, password: password);

  group('註冊', () {
    test('註冊後需要驗證信箱', () async {
      final outcome =
          await backend.signUp(email: 'a@example.com', password: 'secret123');
      expect(outcome, SignUpOutcome.needsConfirmation);
    });

    test('密碼太短會被擋，且訊息講得出規則', () async {
      expect(
        () => backend.signUp(email: 'a@example.com', password: 'abc1'),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.invalidPassword)),
      );
    });

    test('沒有數字的密碼也不行', () async {
      expect(
        () => backend.signUp(email: 'a@example.com', password: 'abcdefgh'),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.invalidPassword)),
      );
    });

    test('信箱不分大小寫，也不受前後空白影響', () async {
      await register('A@Example.com ');
      expect(
        () => backend.signUp(email: 'a@example.com', password: 'secret123'),
        returnsNormally,
      );
      // 同一個帳號，所以驗證碼對這兩種寫法都有效
      await backend.confirmSignUp(
          email: ' a@EXAMPLE.com', code: DemoAuthBackend.demoCode);
      final token =
          await backend.signIn(email: 'a@example.com', password: 'secret123');
      expect(token, isNotEmpty);
    });

    test('已驗證的信箱再註冊會說已經註冊過', () async {
      await register('a@example.com');
      await backend.confirmSignUp(
          email: 'a@example.com', code: DemoAuthBackend.demoCode);

      expect(
        () => backend.signUp(email: 'a@example.com', password: 'secret123'),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.usernameExists)),
      );
    });

    test('還沒驗證完就重新註冊，等同重寄驗證碼而不是報錯', () async {
      await register('a@example.com');
      final outcome =
          await backend.signUp(email: 'a@example.com', password: 'secret123');
      expect(outcome, SignUpOutcome.needsConfirmation);
    });
  });

  group('信箱驗證', () {
    test('驗證碼錯了會說錯', () async {
      await register('a@example.com');
      expect(
        () => backend.confirmSignUp(email: 'a@example.com', code: '000000'),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.codeMismatch)),
      );
    });

    test('查無此帳號時不透露帳號不存在，只說驗證碼不對', () async {
      expect(
        () => backend.confirmSignUp(
            email: 'nobody@example.com', code: DemoAuthBackend.demoCode),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.codeMismatch)),
      );
    });

    test('重寄太快會被擋', () async {
      await register('a@example.com');
      expect(
        () => backend.resendCode(email: 'a@example.com'),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.limitExceeded)),
      );
    });

    test('對不存在的帳號重寄不報錯，避免用它來試探帳號存不存在', () async {
      await expectLater(
          backend.resendCode(email: 'nobody@example.com'), completes);
    });
  });

  group('登入', () {
    test('沒驗證信箱不能登入，而且要能分辨這種情況', () async {
      await register('a@example.com');
      expect(
        () => backend.signIn(email: 'a@example.com', password: 'secret123'),
        throwsA(isA<AuthException>()
            .having((e) => e.code, 'code', AuthErrorCode.userNotConfirmed)),
      );
    });

    test('密碼錯與查無此人回同一種錯誤，不洩漏信箱是否註冊過', () async {
      await register('a@example.com');
      await backend.confirmSignUp(
          email: 'a@example.com', code: DemoAuthBackend.demoCode);

      Future<AuthErrorCode> codeOf(String email, String password) async {
        try {
          await backend.signIn(email: email, password: password);
          fail('應該要失敗');
        } on AuthException catch (e) {
          return e.code;
        }
      }

      expect(await codeOf('a@example.com', 'wrong123'),
          await codeOf('nobody@example.com', 'secret123'));
    });

    test('驗證完就能登入，拿到的 token 解得出身分', () async {
      await register('a@example.com');
      await backend.confirmSignUp(
          email: 'a@example.com', code: DemoAuthBackend.demoCode);
      final token =
          await backend.signIn(email: 'a@example.com', password: 'secret123');

      final identity = AuthService.parseIdentity(token);
      expect(identity, isNotNull);
      // 沒有 elder_id claim 的帳號一律是照護者，判準與後端 auth.py 相同。
      expect(identity!.role, UserRole.caregiver);
      expect(identity.userId, isNotEmpty);
    });

    test('綁定成 elder 的帳號登入後是長者身分', () async {
      await register('grandma@example.com');
      await backend.confirmSignUp(
          email: 'grandma@example.com', code: DemoAuthBackend.demoCode);
      backend.markAsElder(
          email: 'grandma@example.com', elderId: 'eld_123456789abc');

      final token = await backend.signIn(
          email: 'grandma@example.com', password: 'secret123');
      final identity = AuthService.parseIdentity(token);

      expect(identity!.role, UserRole.elder);
      expect(identity.elderId, 'eld_123456789abc');
    });
  });

  group('AuthService', () {
    test('登入成功會記住 token', () async {
      final service = AuthService()..backend = backend;
      await register('a@example.com');
      await backend.confirmSignUp(
          email: 'a@example.com', code: DemoAuthBackend.demoCode);

      expect(service.isSignedIn, isFalse);
      await service.signIn(email: 'a@example.com', password: 'secret123');
      expect(service.isSignedIn, isTrue);
      expect(service.identity?.role, UserRole.caregiver);
    });

    test('登出會清掉 token', () async {
      final service = AuthService()..backend = backend;
      await register('a@example.com');
      await backend.confirmSignUp(
          email: 'a@example.com', code: DemoAuthBackend.demoCode);
      await service.signIn(email: 'a@example.com', password: 'secret123');

      await service.signOut();
      expect(service.isSignedIn, isFalse);
      expect(service.idToken, isNull);
    });

    test('登入狀態跨啟動保留', () async {
      final first = AuthService()..backend = backend;
      await register('a@example.com');
      await backend.confirmSignUp(
          email: 'a@example.com', code: DemoAuthBackend.demoCode);
      await first.signIn(email: 'a@example.com', password: 'secret123');

      // 模擬重新啟動：新的 service 實例只靠 restore() 取回狀態。
      final restored = AuthService()..backend = backend;
      await restored.restore();
      expect(restored.isSignedIn, isTrue);
    });

    test('壞掉的 token 在還原時被丟掉，視為未登入', () async {
      SharedPreferences.setMockInitialValues({'auth_id_token': 'not-a-jwt'});
      final service = AuthService()..backend = backend;
      await service.restore();

      expect(service.isSignedIn, isFalse);
      expect(service.effectiveRole, isNull);
    });
  });

  group('身分宣告', () {
    /// 登入一個沒有 elder_id claim 的帳號（照護者或還沒綁定的長者都是這樣）。
    Future<AuthService> signedIn(String email) async {
      final service = AuthService()..backend = backend;
      await register(email);
      await backend.confirmSignUp(email: email, code: DemoAuthBackend.demoCode);
      await service.signIn(email: email, password: 'secret123');
      return service;
    }

    test('沒有 elder_id 也沒選過 → 還沒宣告身分', () async {
      final service = await signedIn('a@example.com');
      expect(service.effectiveRole, isNull);
    });

    test('選過就記住，跨啟動也還在', () async {
      final service = await signedIn('a@example.com');
      await service.chooseRole(UserRole.caregiver);
      expect(service.effectiveRole, UserRole.caregiver);

      final restored = AuthService()..backend = backend;
      await restored.restore();
      expect(restored.effectiveRole, UserRole.caregiver);
    });

    test('換帳號登入不沿用上一個人的選擇', () async {
      final first = await signedIn('a@example.com');
      await first.chooseRole(UserRole.caregiver);

      // 同一台裝置換人：重新啟動會還原上一個人的宣告，但 sub 不同，不該採用——
      // 否則長輩借家人的手機登入，會被直接丟進照護者模式。
      await register('b@example.com');
      await backend.confirmSignUp(
          email: 'b@example.com', code: DemoAuthBackend.demoCode);
      final second = AuthService()..backend = backend;
      await second.restore();
      await second.signIn(email: 'b@example.com', password: 'secret123');

      expect(first.effectiveRole, UserRole.caregiver);
      expect(second.effectiveRole, isNull);
    });

    test('token 有 elder_id 時直接是長者，不看本機選擇', () async {
      final service = await signedIn('grandma@example.com');
      await service.chooseRole(UserRole.caregiver);

      backend.markAsElder(
          email: 'grandma@example.com', elderId: 'eld_123456789abc');
      await service.signIn(email: 'grandma@example.com', password: 'secret123');

      expect(service.effectiveRole, UserRole.elder);
    });
  });
}
