import 'dart:convert';

import 'package:ai_elder_care/shared/services/auth_service.dart';
import 'package:flutter_test/flutter_test.dart';

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
}
