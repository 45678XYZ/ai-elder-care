import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../shared/i18n/strings.dart';
import '../../shared/models/routine.dart';
import '../../shared/services/care_repository.dart';
import '../../shared/services/lunar_date.dart';
import '../../shared/services/routine_sync.dart';
import '../../shared/services/session_store.dart';
import '../../shared/services/taiwan_holiday.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/sign_out_button.dart';
import '../../shared/widgets/status_chip.dart';
import '../../theme/app_theme.dart';
import '../widgets/almanac_face.dart';
import '../widgets/calendar_tear.dart';
import '../widgets/greeting_slot.dart';
import '../widgets/lang_toggle.dart';
import 'calendar_enlarged.dart';

/// S4 `/elder/today` — 長者模式今日畫面。
///
/// 頁首是一張撕曆（左日期、右早安圖，見 [_CalendarSheet]），下半當日行程
/// （`GET /routines?elder_id=&date=`），可手動確認完成（`POST /routines/{id}/complete`）。
///
/// 長者規格：內文 >=24sp、觸控 >=60dp。可互動元素上限 3 在這一頁刻意放寬，
/// 理由見 [_RoutineRow]。
///
/// 三顆語言鈕（[ElderLangToggle] 說話、[ElderDialectToggle] 客語腔調、
/// [ElderTextLangToggle] 畫面文字）放在這一頁
/// 最底下，跟連結家人、登出同一區：它們是設定不是每日動作，長輩滑過所有行程才
/// 遇得到，日常使用踩不到。原本長者端完全不給切（只有照護者管理頁能改），但
/// 照護者設錯時長輩沒有自救的辦法。
class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key});

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> with WidgetsBindingObserver {
  late Future<DailyRoutineView> _future;

  /// 本地已確認完成的 routine——按下去立刻反映，不等重新拉整份清單。
  final _justCompleted = <String>{};

  /// 背景同步的節奏。
  ///
  /// 這一頁會**停在畫面上很久**：長輩開著它就放下手機，兩個 tab 又掛在
  /// StatefulNavigationShell 底下（切走再切回來 State 是留著的、initState 不會重跑）。
  /// 沒有這個計時器的話，照護者剛新增的行程、對話大腦剛寫進去的完成狀態，
  /// 都要等到 App 整個重啟才看得到。
  ///
  /// 60 秒是取捨：行程是分鐘級的事，更密沒有意義，只是多打後端。
  static const _syncInterval = Duration(seconds: 60);
  Timer? _syncTimer;

  @override
  void initState() {
    super.initState();
    _load();
    WidgetsBinding.instance.addObserver(this);
    _syncTimer = Timer.periodic(_syncInterval, (_) => _silentRefresh());
    // 兩個 tab 掛在 StatefulNavigationShell 底下，切走再切回來這個 State 是留著的、
    // initState 不會重跑。長輩在聊天頁講完「藥吃了」而行程被標成完成時，就是靠這個
    // 監聽把畫面換掉——否則切回來看到的還是切走前那份。
    RoutineSync.revision.addListener(_onRoutinesChanged);
    // 語言鈕就在這一頁上，按下去要立刻整頁換字，不能等切走再切回來。
    AppSession.textLangRevision.addListener(_onTextLangChanged);
  }

  @override
  void dispose() {
    _syncTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    RoutineSync.revision.removeListener(_onRoutinesChanged);
    AppSession.textLangRevision.removeListener(_onTextLangChanged);
    super.dispose();
  }

  /// 回到前台就同步一次。
  ///
  /// 手機鎖著的時候計時器不保證會跑，而長輩最常見的用法正是「放著、過一陣子再拿起來」
  /// ——那一刻看到的必須是新的，不能是睡前那份。
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _silentRefresh();
  }

  /// 背景重拉，**不讓畫面退回載入中**。
  ///
  /// 不能直接 `setState(_load)`：那會換掉 future，AsyncView 立刻顯示轉圈，
  /// 於是每 60 秒整頁閃一次。這裡先在背景把資料拿到手，成功才換上去。
  /// 失敗一律吞掉——維持舊資料遠比閃一個錯誤畫面好，長輩不需要知道某一次背景
  /// 同步沒成功。
  Future<void> _silentRefresh() async {
    try {
      final view = await _fetch();
      if (!mounted) return;
      setState(() {
        // 樂觀更新的那份要清掉，理由同 _onRoutinesChanged。
        _justCompleted.clear();
        _future = Future.value(view);
      });
    } catch (_) {
      // 靜默：畫面維持上一份成功的資料
    }
  }

  void _onTextLangChanged() {
    if (mounted) setState(() {});
  }

  void _onRoutinesChanged() {
    if (!mounted) return;
    setState(() {
      // 樂觀更新的那份要清掉：重拉之後狀態以資料來源為準，留著會蓋住真實狀態
      // （例如後端其實沒記成功，畫面卻永遠顯示已完成）。
      _justCompleted.clear();
      _load();
    });
  }

  void _load() {
    _future = _fetch();
  }

  Future<DailyRoutineView> _fetch() async {
    final date = _dateKey(DateTime.now());
    await AppSession.instance.ensureEldersLoaded();
    final elderId = AppSession.instance.selectedElderId;
    // 帳號還沒有對應的長者資料時回空清單，不丟錯：長者看到「今天沒有安排」是可理解的，
    // 看到一頁錯誤訊息加重試鈕不是。真的缺資料由 router 的 /setup 那條路處理。
    if (elderId == null) return DailyRoutineView(date: date);
    return CareRepo.instance.dailyRoutines(elderId: elderId, date: date);
  }

  void _reload() => setState(_load);

  static String _dateKey(DateTime d) =>
      '${d.year}-${_two(d.month)}-${_two(d.day)}';

  static String _two(int v) => v.toString().padLeft(2, '0');

  /// 確認完成。§13：成功要有明確回饋，長者確認尤其要——這裡用觸覺＋提示條＋狀態改變三重。
  Future<void> _complete(RoutineOccurrence o) async {
    HapticFeedback.mediumImpact();
    setState(() => _justCompleted.add(o.routineId));
    try {
      await CareRepo.instance.completeRoutine(o);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: AppColors.barDark,
          duration: const Duration(seconds: 3),
          content: Text(
            t1('「{}」已記錄完成', o.title),
            style: Theme.of(context)
                .textTheme
                .headlineSmall
                ?.copyWith(color: AppColors.onDark),
          ),
        ),
      );
    } catch (_) {
      // 失敗就把樂觀更新收回來，讓長者看得到它其實沒完成。
      if (mounted) setState(() => _justCompleted.remove(o.routineId));
    }
  }

  String _statusOf(RoutineOccurrence o) =>
      _justCompleted.contains(o.routineId) ? 'done' : o.status;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        // 頁面骨架（撕曆、語言鈕、連結家人、登出）一律畫出來，**不受行程載入結果影響**。
        //
        // 這裡原本是整頁交給 AsyncView、清單放在它的 builder 裡。後果是資料一旦不是
        // 「載入成功且非空」，整頁就只剩一行字或一個錯誤框：剛註冊的長輩必然沒有行程，
        // 於是他沒有登出鈕、沒有語言切換，連當下唯一該做的「連結家人」入口都不見——
        // 那是一條走不出去的死路。後端出錯時同理，而那正是最需要能登出重來的時候。
        //
        // 所以只有「今天的安排」那一段跟著 future 走，其餘永遠在。
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            // 這一頁凡是含畫面文字的 widget 都**不能加 const**：const 會被正規化
            // 成同一個實例，切換書寫語言後 setState 重建時 Flutter 比到
            // identical(new, old) 就整棵跳過，字永遠停在建立時那一版。
            // 撕曆裡有問候語（早安／恁早），登出鈕有「登出」，兩個都中招過。
            // 純排版的 SizedBox 沒有文字，維持 const 沒問題。
            // ignore: prefer_const_constructors
            TearableCalendarSheet(child: const _CalendarSheet()),
            const SizedBox(height: AppSpacing.xl),
            SectionHeader(t('今天的安排'), elderMode: true),
            const SizedBox(height: AppSpacing.md),
            AsyncView<DailyRoutineView>(
              future: _future,
              onRetry: _reload,
              elderMode: true,
              isEmpty: (v) => v.items.isEmpty,
              emptyIcon: Icons.event_available_outlined,
              emptyText: t('今天沒有安排喔'),
              builder: (context, view) {
                final items = view.items.toList()
                  ..sort((a, b) => a.scheduledAt.compareTo(b.scheduledAt));

                // Column 而非 ListView：它已經是外層 ListView 的一個孩子，
                // 再巢一層可捲動的會拿到無界高度而爆版。
                return Column(
                  children: [
                    for (final o in items) ...[
                      _RoutineRow(
                        key: ValueKey(o.routineId),
                        occurrence: o,
                        status: _statusOf(o),
                        onComplete: () => _complete(o),
                      ),
                      const SizedBox(height: AppSpacing.md),
                    ],
                  ],
                );
              },
            ),
            // 還沒有家人連結時才出現，連上之後就消失——這是一次性的設定，
            // 不該天天佔著長輩每日要看的畫面。放在最後，不跟「接下來要做什麼」搶注意力。
            if (!AppSession.instance.hasLinkedCaregiver) ...[
              const SizedBox(height: AppSpacing.xl),
              _LinkCaregiverEntry(
                onTap: () async {
                  await context.push('/elder/link');
                  // 從連結頁回來要重畫：連上了這張卡就該不見。
                  if (mounted) setState(() {});
                },
              ),
            ],
            // 以下兩個都放全頁最底下：長輩要滑過所有行程才遇得到，日常使用
            // 踩不到。這一頁本來就是長者端唯一適合擺它們的地方——聊天室的
            // 三個互動額度要留給麥克風、打字與分頁，不能再塞。
            const SizedBox(height: AppSpacing.xl),
            const ElderLangToggle(),
            const SizedBox(height: AppSpacing.lg),
            const ElderDialectToggle(),
            const SizedBox(height: AppSpacing.lg),
            const ElderTextLangToggle(),
            const SizedBox(height: AppSpacing.xl),
            // 不加 const，理由見本清單開頭。
            // ignore: prefer_const_constructors
            SignOutButton(elderMode: true),
          ],
        ),
      ),
    );
  }
}

/// 撕曆頁首——一張紙分兩面：左邊日期，右邊早安圖。
///
/// ```
/// ┌──────────┬──────────────┐
/// │   27     │              │
/// │ 星期一    │    早安圖     │
/// │ 農曆六月十四│              │
/// └──────────┴──────────────┘
/// ```
///
/// 寬度用 flex 1:1 分,不寫死 dp——Android 邏輯寬度 320～412 都有,寫死會爆版。
/// 原本是 2:3(日期窄、早安圖寬),但頂列要放到 14sp 讀得出來就需要更多寬度,
/// 兩面各半是「頂列完整」與「早安圖不過小」之間的平衡點。
///
/// 高度由整排的 5:2 比例算出來,再夾在 120～160dp:低於 120 日期數字卡不下,
/// 高於 160 會擠掉下方清單的第一列。
///
/// 兩面都可以點開放大檢視;它們是同一張紙,撕頁動畫要整排一起撕走。
class _CalendarSheet extends StatelessWidget {
  const _CalendarSheet();

  static const _paneGap = 8.0;

  /// Hero tag。放大檢視要用同一組 tag 才轉場得起來。
  static const dateHeroTag = 'calendar_card_hero';
  static const greetingHeroTag = 'morning_image_hero';

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final lunar = LunarDate.of(now);

    // 台灣日曆慣例：假日紅、平日藍。判斷集中在 [isTaiwanHoliday]（含國定假日、
    // 農曆假日與補假），不看 [LunarDate.festival]——那是套件的中國節日表，
    // 元宵、重陽在台灣不放假，照它塗色會把上班日畫成紅的。
    final calColor =
        isTaiwanHoliday(now) ? AppColors.accentText : AppColors.calendarWeekday;

    return LayoutBuilder(
      builder: (context, constraints) {
        final height = (constraints.maxWidth / 2.5).clamp(120.0, 160.0);
        // 一面的長寬比。放大檢視照這個比例放大，維持「放大＝同一張變大」。
        final paneAspect = (constraints.maxWidth - _paneGap) / 2 / height;
        return SizedBox(
          height: height,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(
                child: _TappablePane(
                  heroTag: dateHeroTag,
                  label: t('放大看日期'),
                  onTap: () => showEnlargedDate(context,
                      now: now,
                      lunar: lunar,
                      color: calColor,
                      aspectRatio: paneAspect),
                  child: _DatePane(now: now, lunar: lunar, color: calColor),
                ),
              ),
              const SizedBox(width: _paneGap),
              Expanded(
                child: _TappablePane(
                  heroTag: greetingHeroTag,
                  label: t('放大看圖'),
                  onTap: () => showEnlargedGreeting(context,
                      now: now, aspectRatio: paneAspect),
                  child: _GreetingPane(now: now),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

/// 可點開放大的面板外框。
///
/// Hero 包在最外層、點擊區蓋滿整面——長輩點哪裡都算數，不必瞄準小圖示。
class _TappablePane extends StatelessWidget {
  const _TappablePane({
    required this.heroTag,
    required this.label,
    required this.onTap,
    required this.child,
  });

  final String heroTag;
  final String label;
  final VoidCallback onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: label,
      child: GestureDetector(
        onTap: onTap,
        child: Hero(
          tag: heroTag,
          // 轉場途中用一般 Material，避免 Hero 飛行時把陰影也一起插值成怪形狀。
          flightShuttleBuilder: (_, __, ___, ____, toContext) =>
              Material(color: Colors.transparent, child: child),
          child: child,
        ),
      ),
    );
  }
}

/// 日期面板——牌面本身是 [AlmanacFace]（三個尺寸共用同一套版面），
/// 這裡只負責那張紙：紙色、圓角、陰影與螢幕報讀的整句日期。
class _DatePane extends StatelessWidget {
  const _DatePane(
      {required this.now, required this.lunar, required this.color});

  final DateTime now;
  final LunarDate lunar;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      // 牌面用最白的紙色，跟下方一般卡片（card）區隔開，像一張撕曆貼在頁面上
      color: AppColors.cardAlt,
      padding: const EdgeInsets.all(AppSpacing.sm),
      radius: AppRadius.cardLarge,
      shadows: AppShadows.cardRaised,
      semanticLabel: elderDateSpokenLabel(now, lunar),
      child: AlmanacFace(date: now, lunar: lunar, color: color),
    );
  }
}

/// 螢幕報讀用的完整日期語句。TalkBack 要一次讀完，不要逐塊拆讀。
String elderDateSpokenLabel(DateTime now, LunarDate lunar) {
  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
  return '${now.year}年${now.month}月${now.day}日 '
      '星期${weekdays[now.weekday - 1]} '
      '農曆${lunar.monthDay}，歲次${lunar.ganZhiYear}年'
      '${lunar.highlight == null ? '' : '，${lunar.highlight}'}';
}

/// 早安圖——依時段換一張（分界見 [GreetingSlot]）。
///
/// 圖檔放 `assets/images/greeting_{morning,afternoon,evening}.*`，**沒放也不會壞**：
/// 找不到檔案就退回色塊加大字（見 [_GreetingFallback]），所以圖可以晚點才補。
///
/// 裁切用 `BoxFit.cover` 配 `Alignment.topCenter`：素材多半是正方形、祝福語印在
/// 下緣，而這一面是 3:2 橫式。對齊上緣剛好保住主體與「早安」大字，順便把下緣那行
/// 讀不到的小字裁掉——縮到約 192dp 寬之後，那種字級長輩本來就看不見。
class _GreetingPane extends StatelessWidget {
  const _GreetingPane({required this.now});

  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final g = GreetingSlot.of(now);
    return AppCard(
      color: AppColors.avatarBg,
      padding: EdgeInsets.zero, // 圖要滿版貼齊卡片邊緣
      radius: AppRadius.cardLarge,
      shadows: AppShadows.cardRaised,
      semanticLabel: g.label,
      child: ClipRRect(
        borderRadius: const BorderRadius.all(AppRadius.cardLarge),
        child: Image.asset(
          g.asset,
          fit: BoxFit.cover,
          width: double.infinity,
          height: double.infinity,
          alignment: Alignment.topCenter,
          errorBuilder: (context, _, __) =>
              _GreetingFallback(label: g.text, icon: g.icon),
        ),
      ),
    );
  }
}

/// 沒有圖檔時的替代：暖色底加時段問候。
class _GreetingFallback extends StatelessWidget {
  const _GreetingFallback({required this.label, required this.icon});

  final String label;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      color: AppColors.avatarBg,
      alignment: Alignment.center,
      padding: const EdgeInsets.all(12),
      child: FittedBox(
        fit: BoxFit.scaleDown,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 44, color: AppColors.avatarFg),
            const SizedBox(width: AppSpacing.md),
            Text(label,
                style: text.headlineLarge?.copyWith(color: AppColors.avatarFg)),
          ],
        ),
      ),
    );
  }
}

/// 「連結家人」入口。只在還沒有任何家人連結時出現。
///
/// 用外框而非實心卡：它不是今天要做的事，視覺上要退到行程後面，
/// 但仍維持 60dp 以上的觸控範圍與 >=24sp 的字。
class _LinkCaregiverEntry extends StatelessWidget {
  const _LinkCaregiverEntry({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Semantics(
      button: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.card),
        child: Container(
          constraints: const BoxConstraints(minHeight: 60),
          padding: const EdgeInsets.all(AppSpacing.lg),
          decoration: const BoxDecoration(
            color: AppColors.card,
            borderRadius: BorderRadius.all(AppRadius.card),
            boxShadow: AppShadows.card,
          ),
          child: Row(
            children: [
              const Icon(Icons.group_add_outlined,
                  size: 32, color: AppColors.inkSecondary),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(t('連結家人'),
                    style: text.headlineSmall
                        ?.copyWith(color: AppColors.inkSecondary)),
              ),
              const Icon(Icons.chevron_right,
                  size: 32, color: AppColors.chevron),
            ],
          ),
        ),
      ),
    );
  }
}

/// 行程列。每一列都可以自己打勾。
///
/// 原本只有最上面那張「接下來」卡片有按鈕，是為了守長者模式「單頁可互動元素 <=3」，
/// 但代價是長輩 19:00 量完血壓沒地方標記——那條規則的用意是不要讓人在一堆**不同**
/// 功能裡挑，而不是禁止同一個動作重複出現。整份清單只有一種操作（打勾自己那件），
/// 認知負擔跟三顆不同按鈕不是同一回事，所以這裡刻意放寬。
///
/// 輕重由狀態承擔：逾期給紅框加整寬大按鈕，還沒到的只給右側一顆安靜的圓形勾，
/// 已完成的沒有按鈕。
class _RoutineRow extends StatelessWidget {
  const _RoutineRow({
    super.key,
    required this.occurrence,
    required this.status,
    required this.onComplete,
  });

  final RoutineOccurrence occurrence;
  final String status;
  final VoidCallback onComplete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final done = status == 'done';
    final missed = status == 'missed';

    return AppCard(
      color: done ? AppColors.nest : AppColors.card,
      padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 16),
      border: missed ? Border.all(color: AppColors.accent, width: 2) : null,
      semanticLabel: '${occurrence.title}，'
          '${_timeLabel(occurrence.scheduledAt)}，'
          '${RoutineStatusStyle.from(status).label}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  occurrence.title,
                  style: text.headlineSmall?.copyWith(
                    color: done ? AppColors.inkSecondary : AppColors.ink,
                    decoration: done ? TextDecoration.lineThrough : null,
                    decorationColor: AppColors.inkSecondary,
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Text(_timeLabel(occurrence.scheduledAt),
                  style: text.headlineSmall
                      ?.copyWith(color: AppColors.inkSecondary)),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          // 換行擺放，textScaler 2.0 下狀態膠囊不會跟標題擠在同一列
          Row(
            children: [
              Flexible(child: RoutineStatusChip(status, elderMode: true)),
              if (!done && !missed) ...[
                const SizedBox(width: AppSpacing.sm),
                _QuietCheckButton(
                  key: ValueKey('quiet-check-${occurrence.routineId}'),
                  onTap: onComplete,
                  title: occurrence.title,
                ),
              ],
            ],
          ),
          // 逾期才給整寬大按鈕：這是現在最該處理的一件，值得佔畫面。
          if (missed) ...[
            const SizedBox(height: AppSpacing.lg),
            SizedBox(
              width: double.infinity,
              height: 72, // >=60dp
              child: FilledButton.icon(
                onPressed: onComplete,
                icon: const Icon(Icons.check, size: 32),
                label: Text(t('我完成了'),
                    style: text.headlineMedium?.copyWith(color: Colors.white)),
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.accentText,
                  foregroundColor: Colors.white,
                  shape: const RoundedRectangleBorder(
                    borderRadius: BorderRadius.all(AppRadius.field),
                  ),
                ).copyWith(
                  overlayColor:
                      const WidgetStatePropertyAll(AppColors.accentPressed),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 還沒到的那幾列用的打勾鈕。60dp 觸控但視覺安靜——它們不該跟逾期那件搶注意力，
/// 可是長輩提早做完了要有地方標記。
class _QuietCheckButton extends StatelessWidget {
  const _QuietCheckButton(
      {super.key, required this.onTap, required this.title});

  final VoidCallback onTap;
  final String title;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: t1('標記「{}」完成', title),
      child: InkWell(
        onTap: onTap,
        customBorder: const CircleBorder(),
        child: Container(
          width: 60,
          height: 60,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(color: AppColors.accent, width: 2),
          ),
          alignment: Alignment.center,
          child: const Icon(Icons.check, size: 30, color: AppColors.accentText),
        ),
      ),
    );
  }
}

String _timeLabel(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
