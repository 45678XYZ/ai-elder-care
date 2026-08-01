import 'package:e_hakka_care/elder/screens/today_screen.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/models/routine.dart';
import 'package:e_hakka_care/shared/services/calendar_tear_store.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 長者能刪掉自己在對話裡加的行程，但不能刪家人替他排的。
///
/// 這條界線同時存在於三處，任何一處鬆掉都會出事：
/// 1. 後端 `DELETE /routines/{id}` 對長者限 `created_by=conversation`（真正的防線）
/// 2. 這一頁決定要不要畫出那顆鈕
/// 3. 卡片上的來源標示
///
/// 2 與 3 必須永遠給同一個答案——標著「家人幫我排的」卻有刪除鈕，長輩按下去
/// 會拿到一個他無從理解的失敗；反過來標著「我自己加的」卻沒有鈕，他會以為
/// App 壞了。所以這裡兩件事一起驗。
///
/// 誤刪的代價是不對稱的：家人排的多半是用藥與回診，刪掉之後提醒就不再響，
/// 而長輩不會知道自己刪過。因此測試也盯著「取消真的沒有送出去」。
void main() {
  const sub = 'sub-elder';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    // 撕曆動畫每天第一次開 App 才播，播的時候是一層全螢幕遮罩（見
    // TearableCalendarSheet），會把所有點擊吃掉。先標記成今天播過，
    // 等同長輩當天第二次打開——這一份測試要驗的是行程卡上的按鈕。
    await CalendarTearStore.instance.shouldPlayAndMark(DateTime.now());
    await AppSession.instance.loadForAccount(sub);
    AppSession.instance
      ..elders = [
        const Elder(elderId: 'eld_1', name: '陳阿蘭', langPreference: 'zh-TW')
      ]
      ..linkedCaregivers = const []
      ..selectedElderId = 'eld_1';
  });

  tearDown(() => CareRepo.overrideWith(null));

  /// 視窗給得很高，理由同 today_chrome_test：這一頁比一屏長，
  /// 預設 800×600 之下底部的東西不會被 build，find 會落空而誤判成「不見了」。
  Future<void> pump(WidgetTester tester) async {
    tester.view
      ..physicalSize = const Size(390, 4000)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      theme: buildAppTheme(),
      home: const TodayScreen(),
    ));
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));
  }

  testWidgets('照護者排的那筆：標「家人幫我排的」，沒有刪除鈕', (tester) async {
    final repo = _FakeRepo(items: [_occurrence(createdBy: 'caregiver')]);
    CareRepo.overrideWith(repo);
    await pump(tester);

    expect(find.text('家人幫我排的'), findsOneWidget);
    expect(find.text('我自己加的'), findsNothing);
    expect(find.text('刪掉這一項'), findsNothing);
  });

  testWidgets('長者自己加的那筆：標「我自己加的」，有刪除鈕', (tester) async {
    final repo = _FakeRepo(items: [_occurrence(createdBy: 'conversation')]);
    CareRepo.overrideWith(repo);
    await pump(tester);

    expect(find.text('我自己加的'), findsOneWidget);
    expect(find.text('刪掉這一項'), findsOneWidget);
  });

  testWidgets('createdBy 缺漏時當作不能刪', (tester) async {
    // 後端沒回這個欄位（舊版本、或欄位被漏掉）時，寧可少一顆鈕也不要讓長輩
    // 按下去吃 403。少一顆鈕他還有家人可以問，錯誤訊息他讀不懂。
    final repo = _FakeRepo(items: [_occurrence(createdBy: null)]);
    CareRepo.overrideWith(repo);
    await pump(tester);

    expect(find.text('刪掉這一項'), findsNothing);
    expect(find.text('家人幫我排的'), findsOneWidget);
  });

  testWidgets('按刪除會先問一次，選「不要刪」不送出', (tester) async {
    final repo = _FakeRepo(items: [_occurrence(createdBy: 'conversation')]);
    CareRepo.overrideWith(repo);
    await pump(tester);

    await tester.tap(find.text('刪掉這一項'));
    await tester.pumpAndSettle();
    // 確認對話框要問得夠清楚——長輩得知道刪掉之後提醒就不再響。
    expect(find.textContaining('要刪掉「散步」嗎？'), findsOneWidget);

    await tester.tap(find.text('不要刪'));
    await tester.pumpAndSettle();

    expect(repo.deleted, isEmpty, reason: '取消不該送出任何刪除');
  });

  testWidgets('確認之後才呼叫後端，帶 routine_id 與冪等鍵', (tester) async {
    final repo = _FakeRepo(items: [_occurrence(createdBy: 'conversation')]);
    CareRepo.overrideWith(repo);
    await pump(tester);

    await tester.tap(find.text('刪掉這一項'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('刪掉'));
    await tester.pumpAndSettle();

    expect(repo.deleted, ['rtn_self']);
    // client_request_id 必填，後端沒收到會回 400 MISSING_REQUEST_ID（api.md）。
    // 這一條釘的是它真的有被產出來並交下去，不是只在簽章上存在。
    expect(repo.requestIds.single, isNotEmpty);
  });

  testWidgets('刪除失敗要說出來，不能假裝刪掉了', (tester) async {
    final repo = _FakeRepo(
      items: [_occurrence(createdBy: 'conversation')],
      failDelete: true,
    );
    CareRepo.overrideWith(repo);
    await pump(tester);

    await tester.tap(find.text('刪掉這一項'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('刪掉'));
    await tester.pump();

    expect(find.text('刪不掉，等一下再試一次'), findsOneWidget);
    // 那一列還在——樂觀地把它拿掉會讓長輩以為刪成功了，下次打開又冒出來。
    expect(find.text('散步'), findsWidgets);
  });
}

RoutineOccurrence _occurrence({required String? createdBy}) =>
    RoutineOccurrence(
      routineId: createdBy == 'conversation' ? 'rtn_self' : 'rtn_care',
      title: '散步',
      type: 'activity',
      // 固定在今天稍晚，狀態才會是 pending 而不是隨執行時間飄成 missed。
      scheduledAt: DateTime.now().add(const Duration(hours: 2)),
      status: 'pending',
      createdBy: createdBy,
    );

/// 只換掉當日行程與刪除兩條路，其餘沿用 [DemoRepository]。
class _FakeRepo extends DemoRepository {
  _FakeRepo({required this.items, this.failDelete = false});

  final List<RoutineOccurrence> items;
  final bool failDelete;

  final deleted = <String>[];
  final requestIds = <String>[];

  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) async =>
      DailyRoutineView(date: date, items: items);

  @override
  Future<void> deleteRoutine(String routineId,
      {required String clientRequestId}) async {
    deleted.add(routineId);
    requestIds.add(clientRequestId);
    if (failDelete) throw Exception('離線');
  }
}
