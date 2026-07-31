import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 本機狀態的帳號隔離。
///
/// 兩個要同時成立的需求，原本互相打架：
/// - 同一個人登出再登入，不該被要求重走首次設定
/// - 換一個人登入，不該看到上一個人的稱呼、也不該被當成已設定過
///
/// 姓名那幾個 key 曾經是裝置層級的全域值，於是只能在登出時全部刪掉（連帶刪掉
/// 「已完成設定」旗標）才能滿足第二點——代價就是第一點失守。全部綁 sub 之後兩邊
/// 都成立，登出不必刪任何持久化資料。
void main() {
  const elderA = 'sub-elder-a';
  const elderB = 'sub-elder-b';

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  Future<void> setupAs(String sub,
      {required String name,
      required String nickname,
      String lang = 'zh-TW'}) async {
    await AppSession.instance.loadForAccount(sub);
    await AppSession.instance
        .saveSetup(name: name, nickname: nickname, lang: lang);
  }

  test('登出再登入，同一個帳號不必重走首次設定', () async {
    await setupAs(elderA, name: '陳阿蘭', nickname: '阿蘭嬤');
    expect(AppSession.instance.setupDone, isTrue);

    await AppSession.instance.clearForAccount(elderA);
    expect(AppSession.instance.setupDone, isFalse, reason: '登出後記憶體要歸零');

    await AppSession.instance.loadForAccount(elderA);
    expect(AppSession.instance.setupDone, isTrue, reason: '重登不該再進 /setup');
    expect(AppSession.instance.elderNickname, '阿蘭嬤', reason: '稱呼要回來');
  });

  test('換一個帳號登入，看不到上一個人的資料也要自己設定', () async {
    await setupAs(elderA, name: '陳阿蘭', nickname: '阿蘭嬤');
    await AppSession.instance.clearForAccount(elderA);

    await AppSession.instance.loadForAccount(elderB);
    expect(AppSession.instance.setupDone, isFalse, reason: 'B 沒設定過');
    expect(AppSession.instance.elderNickname, isEmpty, reason: '不該繼承 A 的稱呼');
    expect(AppSession.instance.elderName, isEmpty);
  });

  test('兩個帳號的資料各自獨立，互不覆蓋', () async {
    await setupAs(elderA, name: '陳阿蘭', nickname: '阿蘭嬤');
    await setupAs(elderB, name: '林金水', nickname: '阿水伯', lang: 'hak');

    await AppSession.instance.loadForAccount(elderA);
    expect(AppSession.instance.elderNickname, '阿蘭嬤');
    expect(AppSession.instance.lang, 'zh-TW');

    await AppSession.instance.loadForAccount(elderB);
    expect(AppSession.instance.elderNickname, '阿水伯');
    expect(AppSession.instance.lang, 'hak');
  });

  test('未登入時不從任何帳號借資料', () async {
    await setupAs(elderA, name: '陳阿蘭', nickname: '阿蘭嬤');

    await AppSession.instance.loadForAccount(null);
    expect(AppSession.instance.setupDone, isFalse);
    expect(AppSession.instance.elderNickname, isEmpty);
    expect(AppSession.instance.lang, 'zh-TW');
  });

  group('isHakka', () {
    // 與 displayName 同一個順序問題：selectedElder 目前是 DemoData 無條件灌進來的
    // 假名冊（永遠非 null、永遠 zh-TW），排在前面會蓋過本人選的語言。
    test('讀得到本人在 /setup 選的客語', () async {
      await setupAs(elderA, name: '林金水', nickname: '阿水伯', lang: 'hak');
      await AppSession.instance.loadForAccount(elderA);

      // 模擬假名冊已載入且是華語——舊順序在這裡會判成華語。
      await AppSession.instance.ensureEldersLoaded();

      expect(AppSession.instance.isHakka, isTrue,
          reason: '本人選了客語就該是客語，不該被名冊的 zh-TW 蓋掉');
    });

    test('沒選過客語時維持華語', () async {
      await setupAs(elderA, name: '陳阿蘭', nickname: '阿蘭嬤');
      await AppSession.instance.loadForAccount(elderA);
      expect(AppSession.instance.isHakka, isFalse);
    });
  });
}
