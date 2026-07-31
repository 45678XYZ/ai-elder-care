import 'package:ai_elder_care/caregiver/screens/elders_screen.dart';
import 'package:ai_elder_care/caregiver/screens/stats_screen.dart';
import 'package:ai_elder_care/caregiver/screens/summaries_screen.dart';
import 'package:ai_elder_care/caregiver/screens/timeline_screen.dart'
    show TimelineScreen, filterBarKey;
import 'package:ai_elder_care/elder/screens/chat_screen.dart';
import 'package:ai_elder_care/elder/screens/today_screen.dart';
import 'package:ai_elder_care/elder/widgets/greeting_slot.dart';
import 'package:ai_elder_care/shared/screens/role_select_screen.dart';
import 'package:ai_elder_care/shared/services/calendar_tear_store.dart';
import 'package:ai_elder_care/shared/services/care_repository.dart';
import 'package:ai_elder_care/shared/services/lunar_date.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 每個畫面都要能在**兩倍字級**下畫完而不 overflow——這是 CLAUDE.md 的全域硬約束
/// （「所有畫面須在 textScaler: TextScaler.linear(2.0) 下不 overflow」）。
///
/// Flutter 在 debug 下遇到 RenderFlex overflow 會丟 FlutterError，測試因此會失敗，
/// 所以這組測試等於把那條約束變成會擋下改動的機制，而不只是文件上的一句話。
void main() {
  setUp(() {
    // 標記撕曆動畫今天已播過。動畫期間底層包在 IgnorePointer 裡，語意節點會被
    // 一併排除，讓版面與無障礙斷言變得看時機而定。動畫本身由
    // calendar_tear_test.dart 單獨測，這裡只測版面。
    SharedPreferences.setMockInitialValues({
      'calendar_tear_last_shown': CalendarTearStore.dateKey(DateTime.now()),
    });
    // 每個測試都從乾淨的長者情境開始，避免互相影響。
    AppSession.instance
      ..elders = const []
      ..selectedElderId = null;
    // 資料來源也要重來一份：demo 那個實作是有狀態的（停用的行程會留在記憶體裡），
    // 沿用同一個實例的話，前一個測試停用掉的行程會出現在下一個測試的初始資料裡。
    CareRepo.overrideWith(null);
  });

  /// 掛上畫面並等資料載完（DemoData 有刻意的延遲）。
  ///
  /// [size] 預設用一般手機的邏輯尺寸而不是測試框架預設的 800×600——寬度才是 overflow
  /// 的真正壓力來源，600 寬會讓問題測不出來。內容斷言則用很高的視窗，讓 ListView
  /// 一次建完所有項目（否則捲不到的內容根本沒被 build，找不到不代表沒做）。
  Future<void> pumpScreen(
    WidgetTester tester,
    Widget screen, {
    double textScale = 1.0,
    Size size = const Size(390, 844),
  }) async {
    tester.view
      ..physicalSize = size
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: MediaQuery(
          data: MediaQueryData(
            size: size,
            textScaler: TextScaler.linear(textScale),
            // 關掉撕曆過場：進到今日畫面**每次**都會播，動畫期間底層被蓋住，
            // 版面與無障礙斷言會變成看時機而定。動畫本身由 calendar_tear_test.dart
            // 單獨測；這裡順便驗了「減少動態效果」那條路徑真的不播。
            disableAnimations: true,
          ),
          child: screen,
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// 內容斷言用：視窗夠高，ListView 會把所有項目都建出來。
  Future<void> pumpTall(WidgetTester tester, Widget screen) =>
      pumpScreen(tester, screen, size: const Size(390, 3000));

  /// 聊天畫面專用的掛載。
  ///
  /// 不能走 [pumpScreen]：麥克風的脈動外環是 `repeat()` 的動畫，永遠不會停，
  /// pumpAndSettle 會一路等到逾時。改推進固定的時間，長度要蓋過 DemoData 的
  /// 400ms 延遲（[_loadElder] 會等長輩資料），否則測試結束時 Timer 還沒完成，
  /// 框架會判定「widget tree 已 dispose 但 Timer 仍在」而失敗。
  ///
  /// 測試環境沒有語音／音訊外掛，畫面會退回「沒有麥克風」的打字備援路徑
  /// （見 _initSpeech 的 catch）。
  Future<void> pumpChat(
    WidgetTester tester, {
    double textScale = 1.0,
    Size size = const Size(390, 844),
  }) async {
    tester.view
      ..physicalSize = size
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: MediaQuery(
          data: MediaQueryData(
            size: size,
            textScaler: TextScaler.linear(textScale),
            disableAnimations: true,
          ),
          child: const ChatScreen(),
        ),
      ),
    );
    for (var i = 0; i < 12; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  final screens = <String, Widget Function()>{
    '長者今日': () => const TodayScreen(),
    '角色選擇': () => const RoleSelectScreen(),
    '照護者摘要': () => const SummariesScreen(),
    '照護者時間軸': () => const TimelineScreen(),
    '照護者統計': () => const StatsScreen(),
    '照護者管理': () => const EldersScreen(),
  };

  group('畫面能正常畫出來', () {
    for (final entry in screens.entries) {
      testWidgets(entry.key, (tester) async {
        await pumpScreen(tester, entry.value());
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('textScaler 2.0 下不 overflow', () {
    for (final entry in screens.entries) {
      testWidgets(entry.key, (tester) async {
        await pumpScreen(tester, entry.value(), textScale: 2.0);
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('聊天畫面', () {
    testWidgets('畫得出來', (tester) async {
      await pumpChat(tester);
      expect(tester.takeException(), isNull);
    });

    testWidgets('textScaler 2.0 下不 overflow', (tester) async {
      await pumpChat(tester, textScale: 2.0);
      expect(tester.takeException(), isNull);
    });

    testWidgets('開場就看得到健康資訊免責聲明', (tester) async {
      // AI 講的健康資訊只能參考，這句話要在長輩開口問之前就先看到。
      await pumpChat(tester);
      expect(find.text('我說的健康資訊只能參考，\n身體不舒服要看醫生喔'), findsOneWidget);
    });
  });

  group('今日畫面', () {
    testWidgets('載入後顯示當日行程與農民曆日期', (tester) async {
      await pumpTall(tester, const TodayScreen());

      expect(find.text('今天的安排'), findsOneWidget);
      expect(find.text('吃血壓藥'), findsWidgets);
      // 農民曆大日期
      expect(find.text('${DateTime.now().day}'), findsWidgets);
    });

    // 頁首撕曆的驗收：三種常見 Android 邏輯寬度都要放得下，日期三行不能被截掉。
    for (final width in [320.0, 360.0, 412.0]) {
      testWidgets('撕曆頁首在 ${width.toInt()}dp 寬不溢出', (tester) async {
        await pumpScreen(tester, const TodayScreen(), size: Size(width, 844));
        expect(tester.takeException(), isNull);

        final now = DateTime.now();
        final lunar = LunarDate.of(now);
        // 撕曆的每個元素都要在，小卡與放大檢視是同一套版面（不再有簡化版）。
        // 月份數字與大日期同數的日子（7/7）會找到兩個，所以用 AtLeastN。
        expect(find.text('${now.year}'), findsOneWidget);
        expect(find.text('歲次${lunar.ganZhiYear}年'), findsOneWidget);
        expect(find.text('${now.month}'), findsAtLeastNWidgets(1));
        expect(find.text('${now.day}'), findsAtLeastNWidgets(1));
        expect(find.textContaining('星期'), findsOneWidget);
        // 農曆是直排，逐字各自一個 Text——檢查第一個字在就好。
        expect(find.text('農'), findsAtLeastNWidgets(1));
      });
    }

    testWidgets('早安圖依時段換問候語', (tester) async {
      // semantics tree 預設不建，要顯式開；順便驗了螢幕報讀讀得到這些標籤。
      // 必須在 test body 內 dispose——框架的檢查早於 addTearDown 回呼。
      final handle = tester.ensureSemantics();
      await pumpScreen(tester, const TodayScreen());

      final expected = GreetingSlot.of(DateTime.now()).label;
      // 用語意標籤而不是可見文字：有圖時畫面上只有圖，問候語由 semanticLabel 承載。
      expect(find.semantics.byLabel(RegExp(expected)), findsOne);
      handle.dispose();
    });

    testWidgets('日曆兩面都可以點開放大', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpScreen(tester, const TodayScreen());

      expect(find.semantics.byLabel(RegExp('放大看日期')), findsOne);
      expect(find.semantics.byLabel(RegExp('放大看圖')), findsOne);
      handle.dispose();
    });

    testWidgets('TalkBack 一次讀完整句日期，不逐塊拆讀', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpScreen(tester, const TodayScreen());

      final now = DateTime.now();
      final lunar = LunarDate.of(now);
      expect(find.semantics.byLabel(RegExp(elderDateSpokenLabel(now, lunar))),
          findsOne);
      handle.dispose();
    });

    testWidgets('只有逾期那件給整寬大按鈕，其餘保持安靜', (tester) async {
      await pumpTall(tester, const TodayScreen());

      // 假資料裡逾期的只有「吃早餐」，所以整寬的「我完成了」只該有一顆。
      expect(find.text('我完成了'), findsOneWidget);
    });

    /// 「還沒到」那幾列的安靜打勾鈕。
    final quietChecks = find.byWidgetPredicate((w) =>
        w.key is ValueKey<String> &&
        (w.key! as ValueKey<String>).value.startsWith('quiet-check-'));

    testWidgets('還沒到的項目也能自己打勾', (tester) async {
      await pumpTall(tester, const TodayScreen());

      // 每一列都要能標記完成——長輩提早做完了不該沒地方記。
      // 假資料有兩筆「還沒到」（看醫生、量血壓）。
      expect(quietChecks, findsNWidgets(2));
    });

    /// 打勾後等畫面穩定。確認完成會跳 SnackBar，它排了一個 3 秒的關閉 timer——
    /// 不把那段時間推完，測試結束時會因為 timersPending 失敗。
    Future<void> settleAfterTap(WidgetTester tester) async {
      await tester.pumpAndSettle();
      await tester.pump(const Duration(seconds: 4));
      await tester.pumpAndSettle();
    }

    testWidgets('確認完成後該筆變為已完成', (tester) async {
      await pumpTall(tester, const TodayScreen());
      final before = tester.widgetList(find.text('已完成')).length;

      await tester.tap(find.text('我完成了'));
      await settleAfterTap(tester);

      expect(tester.widgetList(find.text('已完成')).length, before + 1);
    });

    testWidgets('打勾還沒到的項目也會變成已完成', (tester) async {
      await pumpTall(tester, const TodayScreen());
      final before = tester.widgetList(find.text('已完成')).length;

      await tester.tap(quietChecks.first);
      await settleAfterTap(tester);

      expect(tester.widgetList(find.text('已完成')).length, before + 1);
      // 打完勾那一列的按鈕要收起來，不能重複標記
      expect(quietChecks, findsOneWidget);
    });

    testWidgets('同一件事不會同時出現在兩個地方', (tester) async {
      await pumpTall(tester, const TodayScreen());

      // 舊版把最早未完成的那件另外做成一張卡，跟清單第一列是同一筆資料，
      // 同一屏出現兩次。拿掉之後每個標題都只該有一個。
      expect(find.text('吃早餐'), findsOneWidget);
    });
  });

  group('摘要畫面', () {
    testWidgets('七類固定全列，沒提到的顯示提示而不是留白', (tester) async {
      await pumpTall(tester, const SummariesScreen());

      // 假資料裡完整的那兩天有 null 分類（safety／activity／other）
      expect(find.text('今日對話未提及'), findsWidgets);
      // 七類的標籤每天都要出現，包含新增的安全類（三天摘要 → 每個標籤三次）
      for (final c in EventCategory.values) {
        expect(find.text(c.label), findsNWidgets(3));
      }
    });

    testWidgets('摘要不完整時，空的分類不說「未提及」', (tester) async {
      await pumpTall(tester, const SummariesScreen());

      // 「未提及」是一句斷言，只有摘要完整時才成立；還有對話沒納入時，那一類
      // 可能正好在沒整理到的段落裡。假資料今天那筆是 partial 且 other 為 null。
      expect(find.text('尚未整理到這一類'), findsWidgets);
    });

    testWidgets('partial 摘要要明講尚未涵蓋整天', (tester) async {
      await pumpTall(tester, const SummariesScreen());

      expect(find.textContaining('尚未涵蓋今天全部內容'), findsOneWidget);
    });
  });

  group('時間軸畫面', () {
    testWidgets('可用分類過濾', (tester) async {
      await pumpTall(tester, const TimelineScreen());

      expect(find.text('用藥'), findsWidgets);
      await tester.tap(find.text('睡眠').first);
      await tester.pumpAndSettle();

      // 過濾到只剩睡眠那一筆
      expect(find.textContaining('半夜起來一次'), findsOneWidget);
    });

    testWidgets('安全類事件會出現在時間軸', (tester) async {
      await pumpTall(tester, const TimelineScreen());

      expect(find.textContaining('扶著把手沒有跌倒'), findsWidgets);
    });

    testWidgets('安全類可以單獨過濾出來', (tester) async {
      await pumpTall(tester, const TimelineScreen());

      // 事件卡上也有「安全」膠囊，所以要指名過濾列裡的那顆
      final chip = find.descendant(
        of: find.byKey(filterBarKey),
        matching: find.text('安全'),
      );
      await tester.tap(chip);
      await tester.pumpAndSettle();

      expect(find.textContaining('扶著把手沒有跌倒'), findsOneWidget);
      // 其他分類要被濾掉
      expect(find.textContaining('膝蓋疼痛'), findsNothing);
    });

    testWidgets('七類的過濾膠囊全部看得到，不會被切在畫面外', (tester) async {
      // 分類滿七類之後，橫向捲動的過濾列一屏只放得下五顆，剩下的躲在畫面外而且
      // 沒有任何提示。用一般手機寬度驗，不是那個很高的測試視窗。
      await pumpScreen(tester, const TimelineScreen());
      final viewWidth = tester.view.physicalSize.width;

      for (final c in EventCategory.values) {
        final chip = find.descendant(
          of: find.byKey(filterBarKey),
          matching: find.text(c.label),
        );
        expect(chip, findsOneWidget, reason: '${c.label} 的膠囊不在過濾列裡');
        final rect = tester.getRect(chip);
        expect(rect.left, greaterThanOrEqualTo(0.0),
            reason: '${c.label} 的膠囊被切在畫面左外側');
        expect(rect.right, lessThanOrEqualTo(viewWidth),
            reason: '${c.label} 的膠囊被切在畫面右外側');
      }
    });

    testWidgets('還有下一頁時給載入更多', (tester) async {
      await pumpTall(tester, const TimelineScreen());

      expect(find.text('載入更早的紀錄'), findsOneWidget);
    });
  });

  group('統計畫面', () {
    testWidgets('圖表之外一定讀得到數字', (tester) async {
      await pumpTall(tester, const StatsScreen());

      expect(find.text('例行公事完成率'), findsOneWidget);
      // 每條長條都標了完成數
      expect(find.text('7 / 7'), findsOneWidget);

      // 可切換成純數字
      await tester.tap(find.text('看數字'));
      await tester.pumpAndSettle();
      expect(find.textContaining('次對話'), findsWidgets);
    });
  });

  group('語音語言', () {
    // 介面單一語言（華語），lang_preference 只決定語音走哪條路，
    // 而且只有照護者的管理頁能改——這兩條測試把「只在這裡可改」釘住。
    testWidgets('管理頁可以切換', (tester) async {
      await pumpTall(tester, const EldersScreen());

      expect(find.text('說話語言'), findsOneWidget);
      expect(find.text('華語'), findsOneWidget);
      expect(find.text('客語'), findsOneWidget);

      await tester.tap(find.text('客語'));
      await tester.pumpAndSettle();
      expect(find.textContaining('已改為客語'), findsOneWidget);
    });

    testWidgets('長者端沒有語言切換', (tester) async {
      await pumpTall(tester, const TodayScreen());

      expect(find.text('客語'), findsNothing);
      expect(find.text('華語'), findsNothing);
    });
  });

  group('管理畫面', () {
    testWidgets('列出例行公事並可停用', (tester) async {
      await pumpTall(tester, const EldersScreen());

      expect(find.text('吃血壓藥'), findsOneWidget);
      expect(find.text('停用'), findsWidgets);

      await tester.tap(find.text('停用').first);
      await tester.pumpAndSettle();

      expect(find.text('已停用'), findsOneWidget);
    });
  });
}
