import 'dart:async';

import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/caregiver/screens/timeline_screen.dart';
import 'package:e_hakka_care/shared/models/api_page.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/models/life_event.dart';
import 'package:e_hakka_care/shared/models/routine.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 照護者切長輩／換分類時，還在路上的舊請求不可以套用到新畫面上。
///
/// 這四頁都是「開著不動、背景每 60 秒重拉」（`AutoRefreshState`），而切長輩走的是
/// `onElderChanged → _reload()`。兩者是兩個並行的請求，**後回來的贏**——前一位的
/// 那趟比較慢時，畫面標題已經是新長輩，內容卻是上一位的。
///
/// 管理頁最傷：它的 `_fetch` 除了換掉清單還會 `syncRoutines`，把**上一位長輩的
/// 服藥提醒**排進這支手機；而照護者看著上一位的行程清單按刪除，刪掉的是那一位的
/// 資料（`deleteRoutine` 走 routine_id，後端不會攔）。
///
/// 時間軸還多一條路：「載入更早的紀錄」那顆鈕在載入中會 disable，但分類膠囊與
/// 長輩切換器不會。舊的一頁回來時 `addAll` 進已經被清空的清單，兩位長輩的紀錄
/// 就混在同一條時間軸上。
void main() {
  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount('sub-caregiver');
    AppSession.instance
      ..elders = const [
        Elder(elderId: 'eld_a', name: '陳阿蘭', langPreference: 'zh-TW'),
        Elder(elderId: 'eld_b', name: '林阿明', langPreference: 'zh-TW'),
      ]
      ..linkedCaregivers = const []
      ..selectedElderId = 'eld_a';
  });

  tearDown(() => CareRepo.overrideWith(null));

  Future<void> pump(WidgetTester tester, Widget screen) async {
    tester.view
      ..physicalSize = const Size(390, 2400)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(theme: buildAppTheme(), home: screen));
    for (var i = 0; i < 20; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  testWidgets('管理頁：切長輩後，前一位那趟晚回來也不會蓋掉畫面', (tester) async {
    final repo = _SlowRoutinesRepo();
    CareRepo.overrideWith(repo);
    await pump(tester, const EldersScreen());

    // 阿蘭的先回來，畫面是她的。
    repo.complete('eld_a');
    await tester.pump();
    expect(find.text('阿蘭的行程'), findsOneWidget);

    // 六十秒後背景重拉又對阿蘭發了一次，這一趟先不回應——它就是等一下會晚到的那個。
    await tester.pump(const Duration(seconds: 60));

    // 照護者在這個當下切到阿明。走真正的切換路徑（點頁首那一列 → 在面板裡選人）：
    // 畫面是靠 CareHeader 的 onElderChanged 才會重載，直接改 selectedElderId
    // 不會觸發任何請求，那樣測到的就不是真的那條路。
    await tester.tap(find.textContaining('正在看'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('林阿明'));
    // 這裡不能 pumpAndSettle：切完之後 AsyncView 會顯示轉圈，那是永不停止的動畫。
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }

    // 阿明的先回來，接著阿蘭那趟才慢慢回來。
    repo.complete('eld_b');
    await tester.pump();
    repo.complete('eld_a');
    await tester.pump();

    expect(find.text('阿明的行程'), findsOneWidget);
    expect(find.text('阿蘭的行程'), findsNothing, reason: '晚到的舊長輩結果不可以蓋掉現在選的這位');
    // 「排進手機的提醒是哪一位的」沒有另外斷言：`NotificationService.instance`
    // 是 static final、沒有測試接縫攔得到。不過 `_fetch` 裡那行 `syncRoutines`
    // 與換掉 `_routines` 是**同一個 if 底下的兩行**，上面這條斷言走的就是那個
    // 分支。要直接盯提醒的話得先給 NotificationService 開一個注入點。
  });

  testWidgets('時間軸：載入更多途中換分類，舊那一頁不會混進來', (tester) async {
    final repo = _SlowEventsRepo();
    CareRepo.overrideWith(repo);
    await pump(tester, const TimelineScreen());

    repo.complete('全部-第1頁');
    await tester.pump();
    expect(find.text('全部-第1頁'), findsOneWidget);

    // 按「載入更早的紀錄」，還沒回來就換分類。
    await tester.tap(find.text('載入更早的紀錄'));
    await tester.pump();
    // `.first` 是篩選膠囊：事件卡上的分類標籤也寫「用藥」（假事件刻意設成
    // medication 才穿得過本地再篩那一層），兩者同字。篩選列在 Column 裡排在
    // 清單前面，所以第一個必定是膠囊。
    await tester.tap(find.text('用藥').first);
    await tester.pump();

    // 順序是這條測試的重點：**新分類的第一頁先到，過期的那一頁最後才到**。
    // 反過來的話新的第一頁會 `clear()` 把舊資料洗掉，bug 被蓋住、測試白過。
    repo.complete('用藥-第1頁');
    await tester.pump();
    repo.complete('全部-第2頁');
    await tester.pump();

    expect(find.text('用藥-第1頁'), findsOneWidget);
    expect(find.text('全部-第2頁'), findsNothing, reason: '換分類後，舊分類的下一頁不可以接到新清單後面');
  });

  testWidgets('管理頁：還沒綁長輩時不給新增入口', (tester) async {
    AppSession.instance
      ..elders = const []
      ..selectedElderId = null;
    CareRepo.overrideWith(_NoElderRepo());
    await pump(tester, const EldersScreen());

    // 按得下去卻一定失敗的鈕不該存在：`POST /routines` 要 elder_id，
    // 而剛註冊的照護者必然還沒有長輩。
    expect(find.text('新增'), findsNothing);
    expect(find.text('新增例行公事'), findsNothing);
    // 但要告訴他現在該做什麼。
    expect(find.text('還沒有綁定的長輩'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

/// 手動控制每一趟 `routines()` 什麼時候回來，好把「舊的晚到」演出來。
class _SlowRoutinesRepo extends DemoRepository {
  final _pending = <String, List<Completer<List<Routine>>>>{};

  @override
  Future<List<Routine>> routines({required String elderId}) {
    final c = Completer<List<Routine>>();
    _pending.putIfAbsent(elderId, () => []).add(c);
    return c.future;
  }

  /// 放行某位長輩最早那一筆還沒回應的請求。
  void complete(String elderId) {
    final queue = _pending[elderId];
    if (queue == null || queue.isEmpty) return;
    final title = elderId == 'eld_a' ? '阿蘭的行程' : '阿明的行程';
    final list = [
      Routine(
        routineId: 'rtn_$elderId',
        elderId: elderId,
        title: title,
        type: 'medication',
        schedule: const RoutineSchedule(freq: 'daily', time: '09:00'),
        createdBy: 'caregiver',
      ),
    ];
    queue.removeAt(0).complete(list);
  }
}

/// 手動控制每一趟 `events()` 什麼時候回來。回應內容標明「哪一類的第幾頁」，
/// 混進來的話一眼看得出是哪一筆。
///
/// **依標籤放行而不是先進先出**：這條測試要演的正是「舊的比新的晚到」，
/// 順序固定就演不出來。
class _SlowEventsRepo extends DemoRepository {
  final _pending = <String, Completer<ApiPage<LifeEvent>>>{};

  @override
  Future<ApiPage<LifeEvent>> events({
    required String elderId,
    String? from,
    String? to,
    String? type,
    String? nextToken,
  }) {
    final label =
        '${type == null ? '全部' : '用藥'}-第${nextToken != null ? 2 : 1}頁';
    final c = Completer<ApiPage<LifeEvent>>();
    _pending[label] = c;
    return c.future;
  }

  void complete(String label) {
    final c = _pending.remove(label);
    // 不靜靜跳過：標籤打錯時要當場看得出來，否則測試會用一個從沒發生過的
    // 情境「通過」。
    if (c == null) throw StateError('沒有等待中的請求：$label');
    c.complete(ApiPage(
      items: [
        LifeEvent(
          eventId: 'evt_$label',
          elderId: 'eld_a',
          ts: DateTime(2026, 8, 2, 10),
          // 一律用 medication：時間軸在後端篩過之後還會本地再篩一次
          // （`_visible`，為了 demo 資料）。舊那一頁若是別的分類，會被本地篩
          // 藏起來，畫面上看不到就等於這條測試在測空氣——而「全部」的查詢本來
          // 就會回用藥事件，這樣設定同時也是真的。
          type: 'medication',
          detail: label,
          source: 'conversation',
        ),
      ],
      // 第一頁一律留游標，才有「載入更早的紀錄」可以按。
      nextToken: label.endsWith('第2頁') ? null : 'cursor',
    ));
  }
}

class _NoElderRepo extends DemoRepository {
  @override
  Future<List<Elder>> elders() async => const [];
}
