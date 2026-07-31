import 'package:ai_elder_care/caregiver/screens/stats_screen.dart';
import 'package:ai_elder_care/caregiver/screens/timeline_screen.dart'
    show TimelineScreen, filterBarKey;
import 'package:ai_elder_care/shared/models/api_page.dart';
import 'package:ai_elder_care/shared/models/caregiver.dart';
import 'package:ai_elder_care/shared/models/chat_reply.dart';
import 'package:ai_elder_care/shared/models/daily_summary.dart';
import 'package:ai_elder_care/shared/models/elder.dart';
import 'package:ai_elder_care/shared/models/life_event.dart';
import 'package:ai_elder_care/shared/models/routine.dart';
import 'package:ai_elder_care/shared/models/stats.dart';
import 'package:ai_elder_care/shared/services/care_repository.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 資料來源那層接縫本身的測試。
///
/// 畫面全部改成透過 [CareRepo] 取資料，為的是後端上線那天只換一個實作、畫面不用動。
/// 那個承諾要能被驗證，否則哪天有人在畫面裡偷接一個直接呼叫，也沒有東西會擋下來。
///
/// 這裡塞一個假的 [CareRepository]，斷言畫面顯示的是**它**給的資料——換句話說，
/// 真後端接上時同一條路一樣走得通。
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppSession.instance
      ..elders = const []
      ..selectedElderId = null;
    CareRepo.overrideWith(_FakeRepo());
  });

  tearDown(() => CareRepo.overrideWith(null));

  Future<void> pump(WidgetTester tester, Widget screen) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildAppTheme(),
      home: MediaQuery(
        data: const MediaQueryData(
            size: Size(390, 3000), disableAnimations: true),
        child: screen,
      ),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('長者清單來自資料來源，不是寫死的假名冊', (tester) async {
    await AppSession.instance.loadElders();
    expect(AppSession.instance.elders.single.name, '測試阿嬤');
    // 沒選過長者時要自動選第一位，否則每個照護者畫面的 elder_id 都拿不到。
    expect(AppSession.instance.selectedElderId, 'eld_fake00000001');
  });

  testWidgets('統計畫面顯示資料來源給的數字', (tester) async {
    await pump(tester, const StatsScreen());
    // 42 是假資料獨有的值；看得到它就代表畫面確實走了 CareRepo 這條路。
    expect(find.textContaining('42'), findsWidgets);
  });

  testWidgets('時間軸顯示資料來源給的事件', (tester) async {
    await pump(tester, const TimelineScreen());
    expect(find.textContaining('假的資料來源給的事件'), findsOneWidget);
  });

  testWidgets('換分類會帶著 type 重新查詢，不是只在本地篩', (tester) async {
    final repo = _FakeRepo();
    CareRepo.overrideWith(repo);
    await pump(tester, const TimelineScreen());
    expect(repo.eventTypeQueries, [null]);

    // 指名過濾列裡的那顆「用藥」——事件卡上也有同名的分類膠囊。
    await tester.tap(find.descendant(
      of: find.byKey(filterBarKey),
      matching: find.text('用藥'),
    ));
    await tester.pumpAndSettle();
    // 分頁游標綁著取得它的那組查詢條件，換 type 一定要重查（見 TimelineScreen）。
    expect(repo.eventTypeQueries, [null, 'medication']);
  });
}

/// 只回得出「一眼看得出不是 demo 資料」的最小資料集。
class _FakeRepo implements CareRepository {
  /// 每次查事件用的 `type`，依序記下來——用來驗證換分類真的重查了。
  final eventTypeQueries = <String?>[];

  @override
  Future<ChatReply> chat({
    required String elderId,
    required String lang,
    required String text,
  }) async =>
      throw UnimplementedError();

  @override
  Future<void> closeChat() async {}

  @override
  Future<Caregiver> me({required String sub}) async =>
      const Caregiver(caregiverId: 'cg_fake0001', name: '測試家人');

  @override
  Future<List<Elder>> elders() async => const [
        Elder(
            elderId: 'eld_fake00000001', name: '測試阿嬤', langPreference: 'zh-TW'),
      ];

  @override
  Future<Elder> updateElder(
          String elderId, Map<String, dynamic> fields) async =>
      (await elders()).single;

  @override
  Future<CaregiverLink> linkCaregiver({
    required String elderId,
    required String caregiverId,
  }) async =>
      throw UnimplementedError();

  @override
  Future<List<Caregiver>> caregivers({required String elderId}) async =>
      const [];

  @override
  Future<List<Routine>> routines({required String elderId}) async => const [];

  @override
  Future<Routine> createRoutine({
    required String clientRequestId,
    required String elderId,
    required Map<String, dynamic> fields,
  }) async =>
      throw UnimplementedError();

  @override
  Future<Routine> updateRoutine(
    String routineId, {
    required String clientRequestId,
    required Map<String, dynamic> fields,
  }) async =>
      throw UnimplementedError();

  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) async =>
      DailyRoutineView(date: date);

  @override
  Future<RoutineOccurrence> completeRoutine(
    RoutineOccurrence occurrence, {
    String? date,
  }) async =>
      occurrence;

  @override
  Future<ApiPage<DailySummary>> summaries({
    required String elderId,
    String? from,
    String? to,
    String? nextToken,
  }) async =>
      const ApiPage(items: []);

  @override
  Future<DailySummary> generateSummary({
    required String elderId,
    String? date,
  }) async =>
      throw UnimplementedError();

  @override
  Future<ApiPage<LifeEvent>> events({
    required String elderId,
    String? from,
    String? to,
    String? type,
    String? nextToken,
  }) async {
    eventTypeQueries.add(type);
    return ApiPage(items: [
      LifeEvent(
        eventId: 'evt_fake1',
        elderId: elderId,
        ts: DateTime.now(),
        type: type ?? 'medication',
        detail: '假的資料來源給的事件',
        source: 'conversation',
      ),
    ]);
  }

  @override
  Future<Stats> stats({required String elderId, int days = 7}) async => Stats(
        elderId: elderId,
        today: const StatsToday(interactionCount: 42),
        period: StatsPeriod(days: days, interactionCount: 42, activeDays: 1),
      );
}
