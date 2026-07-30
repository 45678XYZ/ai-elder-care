import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../../shared/services/calendar_tear_store.dart';
import '../../shared/services/lunar_date.dart';
import '../../shared/services/taiwan_holiday.dart';
import '../../theme/app_theme.dart';
import 'almanac_face.dart';

/// 撕日曆動畫（規劃書 P2 / R1）。
///
/// 每天第一次進入畫面時，畫面壓暗，日曆放大到正中央，**昨天那一張**被往上撕走，
/// 露出今天。撕完停一下讓長輩看清日期，再淡出回到畫面。
///
/// 做成**全螢幕過場**而不是原地小動畫：原地播的話日曆只有指甲大，撕的動作看不清楚，
/// 等於白做。壓暗背景也讓長輩知道「現在是在看這件事」，不會分心。
///
/// **往上撕**：撕曆的上緣是裝訂處，紙從那裡被撕下、往上帶走，所以撕痕在上緣、
/// 位移是負的 Y。往下掉是「紙掉了」，不是「撕日曆」。
///
/// 撕走的那張印的是**昨天**的日期。這一段有已知取捨：使用者第一眼會看到昨天的
/// 日期，對認知功能退化的長輩可能造成短暫混淆（見規劃書 §2.3 問題一）。
/// 昨天那張只停留約 0.35 秒就開始被撕走，且全程有「正在被撕掉」的動作提示。
///
/// 全長約 2.2 秒；點畫面任一處即刻結束。系統開啟「減少動態效果」時完全不播。
class TearableCalendarSheet extends StatefulWidget {
  const TearableCalendarSheet({super.key, required this.child});

  /// 底層的日曆（靜止，永遠顯示今天）。
  final Widget child;

  @override
  State<TearableCalendarSheet> createState() => _TearableCalendarSheetState();
}

class _TearableCalendarSheetState extends State<TearableCalendarSheet>
    with WidgetsBindingObserver {
  bool _showing = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // 等第一幀之後再判斷：這裡要讀 MediaQuery 與 Navigator，initState 當下拿不到。
    WidgetsBinding.instance.addPostFrameCallback((_) => _maybePlay());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// 第二個觸發點：App 留在背景、跨過午夜再回到前景。
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _maybePlay();
  }

  /// 一天只播一次，兩個觸發點都適用。
  ///
  /// 撕曆的隱喻本身不允許重播：撕走的是**昨天**那一張，同一天撕第二次等於昨天
  /// 那頁又長回來被撕一遍。而且這個畫面已經為了儀式感承擔一項代價——使用者第一眼
  /// 看到的是昨天的日期，對認知功能退化的長輩可能造成短暫混淆（見類別說明）。
  /// 那個代價一天付一次換「今天翻開了」的資訊還划算，同一天內重播就只剩混淆：
  /// 他十分鐘前才看過今天是幾號。
  Future<void> _maybePlay() async {
    if (_showing || !mounted) return;

    final isNewDay =
        await CalendarTearStore.instance.shouldPlayAndMark(DateTime.now());
    if (!isNewDay) return;
    if (!mounted) return;

    // 使用者選了「減少動態效果」就完全不播（規劃書 P3）。
    if (MediaQuery.of(context).disableAnimations) return;

    // 讓首屏先畫完再開演。跟清單、早安圖解碼搶同一幀，就是開頭那一下卡頓。
    // （字體已經打包在 assets，不必再等下載——見 AppTypography。）
    await SchedulerBinding.instance.endOfFrame;
    if (!mounted) return;

    _showing = true;
    await showCalendarTearStage(context);
    if (mounted) _showing = false;
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

/// 播放全螢幕撕曆過場，播完自動關閉。
Future<void> showCalendarTearStage(BuildContext context) {
  return showGeneralDialog<void>(
    context: context,
    barrierDismissible: false, // 由 _TearStage 自己處理點擊跳過
    barrierColor: Colors.transparent, // 壓暗效果自己畫，才能跟著動畫淡入
    transitionDuration: Duration.zero,
    // Material 不能省：對話框路由上沒有 Material 祖先時，Flutter 會把文字改用
    // 內建的錯誤樣式畫出來——就是那條黃色底線。透明式的 Material 只提供
    // 文字樣式的落腳處，不畫任何東西。
    pageBuilder: (_, __, ___) => const Material(
      type: MaterialType.transparency,
      child: _TearStage(),
    ),
  );
}

class _TearStage extends StatefulWidget {
  const _TearStage();

  @override
  State<_TearStage> createState() => _TearStageState();
}

class _TearStageState extends State<_TearStage>
    with SingleTickerProviderStateMixin {
  static const _total = Duration(milliseconds: 2200);

  // 時間軸（比例）：
  //   0     – 0.09  壓暗淡入，紙完整蓋著日曆
  //   0.09  – 0.16  紙的左上角微微掀起
  //   0.16  – 0.59  撕走（約 950ms，看得清楚的速度）
  //   0.59  – 0.86  停留，讓長輩看清今天的日期
  //   0.86  – 1.0   整幕淡出
  static const _dimEnd = 0.09;
  static const _liftStart = 0.09;
  static const _liftEnd = 0.16;
  static const _flyEnd = 0.59;
  static const _fadeSheetStart = 0.38; // 撕走過程的一半才開始淡出
  static const _holdEnd = 0.86;

  late final AnimationController _controller;
  late final Animation<double> _dim;
  late final Animation<double> _lift;
  late final Animation<double> _fly;
  late final Animation<double> _sheetFade;
  late final Animation<double> _exit;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: _total);

    _dim = CurvedAnimation(
        parent: _controller,
        curve: const Interval(0, _dimEnd, curve: Curves.easeOut));
    _lift = CurvedAnimation(
        parent: _controller,
        curve: const Interval(_liftStart, _liftEnd, curve: Curves.easeOut));
    // easeInQuad：越掉越快，像被重力帶走
    _fly = CurvedAnimation(
        parent: _controller,
        curve: const Interval(_liftEnd, _flyEnd, curve: Curves.easeInQuad));
    _sheetFade = CurvedAnimation(
        parent: _controller,
        curve: const Interval(_fadeSheetStart, _flyEnd, curve: Curves.easeIn));
    _exit = CurvedAnimation(
        parent: _controller,
        curve: const Interval(_holdEnd, 1, curve: Curves.easeIn));

    _controller.addStatusListener((status) {
      if (status == AnimationStatus.completed) _close();
    });
    _controller.forward();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _close() {
    if (mounted && Navigator.of(context).canPop()) Navigator.of(context).pop();
  }

  /// 點一下直接跳到結尾。長輩不該被動畫綁住。
  void _skip() {
    if (_controller.value >= _holdEnd) {
      _close();
    } else {
      _controller.value = _holdEnd;
      _controller.forward();
    }
  }

  /// 台灣日曆慣例：假日紅、平日藍。判斷集中在 [isTaiwanHoliday]，
  /// 不看 [LunarDate.festival]——那是套件的中國節日表，元宵、重陽在台灣不放假。
  static Color _colorOf(DateTime d) =>
      isTaiwanHoliday(d) ? AppColors.accentText : AppColors.calendarWeekday;

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final lunar = LunarDate.of(now);
    // 被撕走的是昨天那一張。
    final yesterday = now.subtract(const Duration(days: 1));
    final yesterdayLunar = LunarDate.of(yesterday);
    final width = MediaQuery.of(context).size.width * 0.78;

    // 兩張牌面**只建一次**，之後每一幀只做位移與淡出。
    // 原本整棵樹（含兩張牌面的字體排版與陰影）每幀重建，動畫就是這樣卡的：
    // 動起來的是 transform，重算的卻是整份排版。
    final today = RepaintBoundary(
      child: _StageCalendar(now: now, lunar: lunar, color: _colorOf(now)),
    );
    final sheet = RepaintBoundary(
      child: ClipPath(
        clipper: const _TornEdgeClipper(),
        child: _StageCalendar(
          now: yesterday,
          lunar: yesterdayLunar,
          color: _colorOf(yesterday),
        ),
      ),
    );

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: _skip,
      // FadeTransition 而不是 Opacity：opacity 等於 1 的時候不開圖層，
      // 整段動畫只有最後 0.3 秒才真的付出全螢幕透明度的代價。
      child: FadeTransition(
        opacity: Tween<double>(begin: 1, end: 0).animate(_exit),
        child: AnimatedBuilder(
          animation: _dim,
          // 壓暗背景。淡入而不是直接出現，長輩才不會覺得畫面「跳」了一下。
          builder: (context, child) => ColoredBox(
            color: Colors.black.withValues(alpha: 0.62 * _dim.value),
            child: child,
          ),
          child: Center(
            child: SizedBox(
              width: width,
              child: AspectRatio(
                aspectRatio: 0.82,
                child: Stack(
                  children: [
                    // 底層：今天的日曆，撕開後露出來
                    Positioned.fill(child: today),
                    // 上層：昨天那一張，被往上撕走
                    Positioned.fill(
                      child: AnimatedBuilder(
                        animation: _controller,
                        child: FadeTransition(
                          opacity: Tween<double>(begin: 1, end: 0)
                              .animate(_sheetFade),
                          child: sheet,
                        ),
                        builder: (context, child) => Transform(
                          // 繞左上角轉——那裡最接近裝訂處，紙會以那個點為軸被掀起
                          alignment: const Alignment(-0.85, -1),
                          transform: Matrix4.identity()
                            // Y 是負的：往上撕，不是往下掉
                            ..translateByDouble(30.0 * _fly.value,
                                -8 * _lift.value - 460 * _fly.value, 0, 1)
                            ..rotateZ((-2 * _lift.value + 10 * _fly.value) *
                                math.pi /
                                180),
                          child: child,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 過場中央的日曆。版面與首頁小卡、放大檢視是同一套（[AlmanacFace]），
/// 只是紙變大——這一刻長輩正在專心看它，字就該撐滿整張紙。
class _StageCalendar extends StatelessWidget {
  const _StageCalendar({
    required this.now,
    required this.lunar,
    required this.color,
  });

  final DateTime now;
  final LunarDate lunar;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: const BoxDecoration(
        color: AppColors.cardAlt,
        borderRadius: BorderRadius.all(AppRadius.cardLarge),
        boxShadow: AppShadows.cardRaised,
      ),
      child: AlmanacFace(date: now, lunar: lunar, color: color),
    );
  }
}

/// 上緣的撕痕。紙從這裡跟裝訂處分離，所以鋸齒畫在頂邊。
///
/// 齒高 3～4dp、齒距 7dp，用**固定種子**產生——每天的撕痕長得一樣才像同一本
/// 日曆，隨機亂數會讓人覺得每天換了一本。
class _TornEdgeClipper extends CustomClipper<Path> {
  const _TornEdgeClipper();

  /// 固定種子。改這個值會換一種撕痕形狀。
  static const seed = 42;

  @override
  Path getClip(Size size) {
    final random = math.Random(seed);
    final path = Path();
    const toothSpacing = 7.0;
    const baseDepth = 3.5;

    path.moveTo(0, baseDepth);
    var x = 0.0;
    var down = true;
    while (x < size.width) {
      x = math.min(x + toothSpacing, size.width);
      // ±1dp 微擾：完全規則的鋸齒看起來像裁的，不像撕的
      final jitter = random.nextDouble() * 2 - 1;
      path.lineTo(x, down ? baseDepth * 2 + jitter : jitter.abs());
      down = !down;
    }

    path.lineTo(size.width, size.height);
    path.lineTo(0, size.height);
    path.close();
    return path;
  }

  @override
  bool shouldReclip(covariant _TornEdgeClipper oldClipper) => false;
}
