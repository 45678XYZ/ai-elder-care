import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 長輩**用講的**改語言之後，主頁那顆「我說的話」要跟著換。
///
/// 那條路完全不經過 App 的按鈕：對話大腦的 `update_elder_profile` 直接改後端的
/// `lang_preference`，App 只能靠回話之後重讀長者檔案把新值撈回來。
///
/// 原本的判準是「重讀前後不一樣就套用」，實機上改不動。那個寫法有兩個破綻，而且
/// **兩個都是永久失效、不是延遲**：
///
///   1. 後端不保證讀得到剛寫進去的值。緊接著那次重讀拿到舊值 → 前後一樣 → 判定
///      沒變；下一次重讀拿到新值時「前」也已經是新值 → 又判定沒變。從此再也不會
///      套用。
///   2. 任何別的地方呼叫 `loadElders`（各畫面自己載、背景同步）都會先把「前」洗成
///      新值，效果一樣。
///
/// 改成記住「上次已經對過帳的後端值」。這一組把兩個破綻都釘住，順便釘住反面：
/// 長者自己按鈕選的那份不可以被後端反覆蓋回去。
void main() {
  const sub = 'sub-elder';

  late _LangRepo repo;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    repo = _LangRepo('zh-TW');
    CareRepo.overrideWith(repo);
    await AppSession.instance.loadForAccount(sub);
    await AppSession.instance.loadElders();
  });

  tearDown(() => CareRepo.overrideWith(null));

  test('後端改成客語，重讀之後套用到本機', () async {
    expect(AppSession.instance.isHakka, isFalse);

    repo.langPreference = 'hak';
    await AppSession.instance.refreshSelectedElder();

    expect(AppSession.instance.isHakka, isTrue);
  });

  test('第一次讀還是舊值，下一次才讀到新值——照樣要套用', () async {
    // 這是實機真正踩到的那條：工具已經改了，但緊接著那次重讀拿到的還是舊值。
    // 前後互比的寫法會在這裡把變更永久吃掉。
    await AppSession.instance.refreshSelectedElder(); // 拿到舊值，什麼都不該做
    expect(AppSession.instance.isHakka, isFalse);

    repo.langPreference = 'hak';
    await AppSession.instance.refreshSelectedElder();

    expect(AppSession.instance.isHakka, isTrue, reason: '晚一步讀到就晚一步套用，不是不套用');
  });

  test('中間被別的地方重讀過，變更不會被洗掉', () async {
    // 畫面各自載入、背景同步都會呼叫 loadElders。它先把 elders 換成新值之後，
    // 前後互比的寫法就再也看不到這次變更。
    repo.langPreference = 'hak';
    await AppSession.instance.loadElders();

    await AppSession.instance.refreshSelectedElder();

    expect(AppSession.instance.isHakka, isTrue);
  });

  test('長者按鈕選了華語，後端維持客語也不能蓋回去', () async {
    repo.langPreference = 'hak';
    await AppSession.instance.refreshSelectedElder();
    expect(AppSession.instance.isHakka, isTrue);

    // 長輩自己按了「中文」——實際在說話的人贏。
    await AppSession.instance.setLang('zh-TW');
    expect(AppSession.instance.isHakka, isFalse);

    // 之後每一輪都會重讀，後端那份沒再變過，就不該一直把他推回客語。
    await AppSession.instance.refreshSelectedElder();
    await AppSession.instance.refreshSelectedElder();

    expect(AppSession.instance.isHakka, isFalse, reason: '後端沒再變，就不是新的指示');
  });

  test('後端之後又改回華語，還是跟得上', () async {
    repo.langPreference = 'hak';
    await AppSession.instance.refreshSelectedElder();
    expect(AppSession.instance.isHakka, isTrue);

    repo.langPreference = 'zh-TW';
    await AppSession.instance.refreshSelectedElder();
    expect(AppSession.instance.isHakka, isFalse);
  });

  test('一開始就是客語的帳號，不算「長者選過了」', () async {
    // 第一次看到後端的值是「本來就這樣」，不是長輩剛剛用講的改掉。套用的話會順手
    // 把「選過了」設成 true，之後照護者改的值就再也蓋不過來。
    SharedPreferences.setMockInitialValues({});
    CareRepo.overrideWith(_LangRepo('hak'));
    await AppSession.instance.loadForAccount('sub-hakka');
    await AppSession.instance.loadElders();

    expect(AppSession.instance.isHakka, isTrue, reason: '沒選過時看的是後端那份');

    // 照護者把它改回華語：長者沒自己選過，就該跟著走。
    (CareRepo.instance as _LangRepo).langPreference = 'zh-TW';
    await AppSession.instance.refreshSelectedElder();
    expect(AppSession.instance.isHakka, isFalse);
  });
}

/// 只控制 `lang_preference` 的假資料層；其餘沿用 demo 的實作。
class _LangRepo extends DemoRepository {
  _LangRepo(this.langPreference);

  String langPreference;

  @override
  Future<List<Elder>> elders() async => [
        Elder(
          elderId: 'eld_test',
          name: '陳阿蘭',
          langPreference: langPreference,
        ),
      ];
}
