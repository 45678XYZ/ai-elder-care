import 'dart:convert';

import 'package:ai_elder_care/elder/screens/today_screen.dart';
import 'package:ai_elder_care/shared/services/api_client.dart';
import 'package:ai_elder_care/shared/services/calendar_tear_store.dart';
import 'package:ai_elder_care/shared/services/care_repository.dart';
import 'package:ai_elder_care/shared/services/demo_repository.dart';
import 'package:ai_elder_care/shared/services/routine_sync.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// demo 分鏡 Act 2 的那格證據：長輩講完「藥吃了」，切回今日畫面，該筆**自己打勾了**，
/// 沒有任何人按過完成鍵。
///
/// 這條路橫跨三個東西——對話回 `routines_updated`、[RoutineSync] 廣播、今日畫面重拉，
/// 任何一段斷掉畫面都不會變，而且斷了不會有人報錯。所以整條一起測。
void main() {
  /// 回覆固定文字的假 RAG PoC，讓 [DemoRepository.chat] 不用真的連本機伺服器。
  DemoRepository demoRepo() => DemoRepository(
        api: ApiClient(
          httpClient: MockClient((_) async => http.Response(
                jsonEncode({'answer': '好，我幫妳記下來了。', 'sources': []}),
                200,
                headers: {'content-type': 'application/json; charset=utf-8'},
              )),
        ),
      );

  setUp(() {
    SharedPreferences.setMockInitialValues({
      // 關掉撕曆過場，否則今日畫面的內容會被蓋住。
      'calendar_tear_last_shown': CalendarTearStore.dateKey(DateTime.now()),
    });
    AppSession.instance
      ..elders = const []
      ..selectedElderId = null;
    RoutineSync.resetForTest();
  });

  tearDown(() => CareRepo.overrideWith(null));

  group('對話完成行程', () {
    test('講到某筆行程做完了 → routines_updated 為 true，該筆變成已完成', () async {
      final repo = demoRepo();
      CareRepo.overrideWith(repo);

      final reply = await repo.chat(
        elderId: 'eld_x',
        lang: 'zh-TW',
        text: '我早餐吃完了',
      );

      expect(reply.routinesUpdated, isTrue);
      final view = await repo.dailyRoutines(elderId: 'eld_x', date: _today());
      final item = view.items.firstWhere((o) => o.title == '吃早餐');
      expect(item.status, 'done');
      // 不是誰按的，是對話裡認出來的——時間軸與摘要要能分辨這兩者。
      expect(item.completedBy, 'conversation');
      // 回話要跟剛剛發生的事一致。衛教問答端點不知道有行程這回事，拿它的答案來回
      // 會湊出「嘴上說找不到答案、行程卻打勾了」的矛盾畫面。
      expect(reply.replyText, contains('吃早餐'));
    });

    test('講已經完成的那件事，不會誤中另一筆名字相近的', () async {
      final repo = demoRepo();
      // demo 資料裡「吃血壓藥」一開始就是完成的，而「量血壓」還沒。先到先得的比對
      // 會跳過前者、撞上後者——講「藥吃了」卻把「量血壓」打勾。
      final reply = await repo.chat(
        elderId: 'eld_x',
        lang: 'zh-TW',
        text: '血壓藥吃了啦，剛剛配溫水吃的',
      );

      expect(reply.routinesUpdated, isFalse);
      final view = await repo.dailyRoutines(elderId: 'eld_x', date: _today());
      expect(view.items.firstWhere((o) => o.title == '量血壓').status, 'pending');
    });

    test('沒講到行程的閒聊不會亂打勾', () async {
      final repo = demoRepo();
      final reply = await repo.chat(
        elderId: 'eld_x',
        lang: 'zh-TW',
        text: '今天天氣真好，我坐在門口曬太陽',
      );

      expect(reply.routinesUpdated, isFalse);
      final view = await repo.dailyRoutines(elderId: 'eld_x', date: _today());
      expect(view.items.where((o) => o.status == 'done').length, 1); // 只有初始那筆
    });

    test('同一筆不會被完成兩次', () async {
      final repo = demoRepo();
      await repo.chat(elderId: 'eld_x', lang: 'zh-TW', text: '血壓藥吃了');
      final second =
          await repo.chat(elderId: 'eld_x', lang: 'zh-TW', text: '血壓藥吃了');
      // 已經是 done 的不再重新標記，否則每講一次就多一次「剛剛完成」的通報。
      expect(second.routinesUpdated, isFalse);
    });
  });

  group('今日畫面', () {
    testWidgets('行程在別的畫面被完成後，切回來要看得到', (tester) async {
      final repo = demoRepo();
      CareRepo.overrideWith(repo);

      // 視窗要真的夠高，ListView 才會把所有項目都建出來——只設 MediaQuery 不夠，
      // 實際的 render surface 仍是預設的 800×600，捲不到的項目根本沒被 build，
      // 找不到不代表沒做。
      const size = Size(390, 3000);
      tester.view
        ..physicalSize = size
        ..devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: const MediaQuery(
          data: MediaQueryData(size: size, disableAnimations: true),
          child: TodayScreen(),
        ),
      ));
      await tester.pumpAndSettle();

      // 「量血壓」初始是 pending，畫面上還看得到它待辦。
      expect(find.text('量血壓'), findsWidgets);
      final doneBefore = _doneCount(tester);

      // 長輩在聊天頁講了一句 → 後端（這裡是 demo）把它標成完成 → 廣播。
      final reply = await repo.chat(
        elderId: 'eld_x',
        lang: 'zh-TW',
        text: '血壓量好了，高的一百三十幾',
      );
      expect(reply.routinesUpdated, isTrue);
      RoutineSync.revision.value++;
      await tester.pumpAndSettle();

      // 這個畫面的 State 從頭到尾沒有被重建過（沒人按完成、也沒重進頁面），
      // 已完成的數量卻多了一筆——這正是 Act 2 要給評審看的那件事。
      expect(_doneCount(tester), doneBefore + 1);
    });
  });
}

/// 畫面上顯示「已完成」的筆數。
int _doneCount(WidgetTester tester) =>
    tester.widgetList(find.text('已完成')).length;

String _today() {
  final n = DateTime.now();
  return '${n.year}-${_two(n.month)}-${_two(n.day)}';
}

String _two(int v) => v.toString().padLeft(2, '0');
