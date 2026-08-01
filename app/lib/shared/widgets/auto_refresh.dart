import 'dart:async';

import 'package:flutter/widgets.dart';

/// 讓一個畫面自己保持新鮮：看得到的時候定時重拉、回到前台補一次、切回這個分頁再補一次。
///
/// **為什麼需要它**：兩種模式的分頁都掛在 `StatefulNavigationShell` 底下，切走再切回來
/// State 是留著的、`initState` 不會重跑。照護者那四頁原本就是「initState 載一次就再也
/// 不動」——開著摘要頁等長輩講話，等到的永遠是打開那一刻的那份；而長輩剛完成的行程、
/// 剛被整理出來的事件，正是 demo 要證明的東西。長者主頁先解過同一題，這裡把那一套
/// 抽出來共用，順便讓四頁不必各抄一次計時器與生命週期觀察者。
///
/// **只在看得到的時候拉**：IndexedStack 會把所有分頁都建起來（只是不畫），四頁各開一個
/// 計時器就是每分鐘四次請求，其中三次沒有人在看。go_router 給不在前景的那幾支包了
/// `TickerMode(enabled: false)`，拿它當「現在有沒有被看到」的判準最省事，也不必要求
/// 使用端自己回報。
///
/// 使用端要做的只有實作 [autoRefresh]，並且**不可以讓畫面退回載入中**——直接
/// `setState(_load)` 會換掉 future，AsyncView 立刻顯示轉圈，於是每分鐘整頁閃一次。
/// 正確做法是先在背景把資料拿到手，成功才換上去（見各畫面的實作）。
mixin AutoRefreshState<T extends StatefulWidget> on State<T> {
  /// 背景重拉的節奏。
  ///
  /// 60 秒是取捨：摘要、事件、行程都是分鐘級的事，更密沒有意義，只是多打後端。
  Duration get autoRefreshInterval => const Duration(seconds: 60);

  /// 兩次背景重拉之間至少要隔這麼久。
  ///
  /// 「切回分頁就拉」遇上快速來回切分頁時，會變成連打好幾次同一個端點。
  static const _minGap = Duration(seconds: 5);

  /// 背景重拉一次。失敗請自行吞掉：維持上一份成功資料，遠比閃一個錯誤畫面好。
  Future<void> autoRefresh();

  /// 這一刻適不適合背景重拉。
  ///
  /// 預設一律適合。有「載入更多」這種會被重拉洗掉的畫面狀態時覆寫它，
  /// 否則使用者翻了三頁，六十秒後畫面自己彈回第一頁。
  bool get canAutoRefresh => true;

  Timer? _timer;

  /// 冷卻中就跳過這一次。
  ///
  /// 用 Timer 而不是記 `DateTime.now()` 相減：測試跑在假時鐘下，`tester.pump(60 秒)`
  /// 推得動計時器、推不動 `DateTime.now()`，用後者會讓「等六十秒該重拉一次」這種
  /// 測試永遠被冷卻擋掉。
  Timer? _cooldown;

  bool _visible = true;
  late final _AutoRefreshLifecycle _lifecycle;

  @override
  void initState() {
    super.initState();
    _lifecycle = _AutoRefreshLifecycle(_tick);
    WidgetsBinding.instance.addObserver(_lifecycle);
    _timer = Timer.periodic(autoRefreshInterval, (_) => _tick());
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final visible = TickerMode.valuesOf(context).enabled;
    final becameVisible = visible && !_visible;
    _visible = visible;
    // 切回這個分頁的那一刻就要看到新的，不能等下一次計時器——照護者切過去看一眼
    // 通常只停留幾秒。
    if (becameVisible) _tick();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _cooldown?.cancel();
    WidgetsBinding.instance.removeObserver(_lifecycle);
    super.dispose();
  }

  void _tick() {
    if (!mounted || !_visible || !canAutoRefresh) return;
    if (_cooldown?.isActive ?? false) return;
    _cooldown = Timer(_minGap, () {});
    unawaited(autoRefresh());
  }
}

/// 只為了「回到前台」這一件事的觀察者。
///
/// 手機鎖著的時候計時器不保證會跑，而照護者最常見的用法正是「放著、想到再拿起來看」
/// ——那一刻看到的必須是新的。
///
/// 不讓使用端自己 `with WidgetsBindingObserver`：那樣每個畫面都得記得 add／
/// removeObserver，漏一個就是 State 被回收之後還在收生命週期事件。
class _AutoRefreshLifecycle with WidgetsBindingObserver {
  _AutoRefreshLifecycle(this.onResumed);

  final VoidCallback onResumed;

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) onResumed();
  }
}
